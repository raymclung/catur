"""
Impor PGN ke database.

Dua masalah utama yang ditangani modul ini:

1. IDENTITAS PEMAIN. Nama di PGN tidak konsisten. Kalau ada FIDE ID, itu yang
   dipakai sebagai identitas dan pemainnya ditandai terverifikasi. Kalau tidak
   ada, terpaksa dicocokkan dari nama — dan itu dicatat sebagai belum
   terverifikasi, bukan dianggap pasti benar.

2. DUPLIKAT. Tiap partai dihitung sidik jarinya dari isi partai itu sendiri,
   lalu database yang menolak kalau sidik jarinya sudah ada. Jadi impor boleh
   dijalankan berulang kali, termasuk untuk turnamen yang masih berlangsung.

Keduanya sekarang tinggal di modulnya sendiri — `app/impor_pemain.py` dan
`app/impor_hash.py`, dengan pembantu murni di `app/impor_bantu.py`. Berkas ini
memegang alurnya: membaca PGN, membuat/menemukan turnamen, lalu menulis partai.
"""

from __future__ import annotations

import hashlib
import io
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path

import chess
import chess.pgn

from .db import sekarang
from .format_partai import format_dari_kontrol_waktu
from .sumber_impor import catat as catat_sumber, sudah_selesai

# Modul ini dulu 900 baris dan memegang tiga urusan sekaligus (audit 25 Sep
# 2026). Dua di antaranya sekarang berkas sendiri; namanya tetap diteruskan
# dari sini supaya pemanggil lama — belasan skrip dan tes — tidak perlu diubah.
from .impor_bantu import (EVENT_GENERIK, EVENT_PAPAN, GELAR,        # noqa: F401
                          KOSONG, bersih, eco_baku, ke_elo, ke_int, normalisasi_nama,
                          tahun_dari_tanggal, turnamen_dari_header)
from .impor_hash import (bahan_hash, cari_kembar_lintas_sumber,      # noqa: F401
                         hash_partai, hitung_ulang_hash,
                         nama_belakang, periksa_jejak, sidik_partai)
from .impor_pemain import (_belakang_depan, _depan_cocok,           # noqa: F401
                           dapatkan_atau_buat_pemain, perbarui_elo_terakhir,
                           tandai_akun_pribadi)

HASIL_SAH = ("1-0", "0-1", "1/2-1/2", "*")
POLA_TANGGAL = re.compile(r"\d{4}\.(\d\d|\?\?)\.(\d\d|\?\?)")
# Tag [Variant] yang masih catur biasa (huruf kecil, tanpa spasi). Chess960
# ikut: 1.852 partai GM (turnamen Freestyle di TWIC dan siaran) sudah ada.
VARIAN_SAH = ("standard", "fromposition", "chess960", "960")


def _teks_tertelan(game: chess.pgn.Game) -> str | None:
    """
    Teks partai-partai berikutnya yang tertelan komentar `{` yang tidak ditutup.

    python-chess membaca komentar sampai `}` berikutnya, jadi partai sesudahnya
    ikut menjadi isi komentar dan hilang tanpa jejak (audit 28 Sep 2026: lima
    partai, yang ke-2 berkomentar tak ditutup -> "diproses 2", M3-M5 lenyap).
    """
    node = game
    while node is not None:
        for teks in (node.comment, getattr(node, "starting_comment", "")):
            i = (teks or "").find("[Event ")
            if i >= 0:
                return teks[i:]
        node = node.variations[0] if node.variations else None
    return None


@dataclass
class HasilImpor:
    baru: int = 0
    duplikat: int = 0
    diperbarui: int = 0   # partai broadcast yang tadinya belum selesai
    dilewati: int = 0
    alasan_dilewati: dict = None

    def __post_init__(self):
        if self.alasan_dilewati is None:
            self.alasan_dilewati = {}

    def lewati(self, alasan: str) -> None:
        self.dilewati += 1
        self.alasan_dilewati[alasan] = self.alasan_dilewati.get(alasan, 0) + 1

    def gabung(self, lain: "HasilImpor") -> None:
        self.baru += lain.baru
        self.duplikat += lain.duplikat
        self.diperbarui += lain.diperbarui
        self.dilewati += lain.dilewati
        for k, v in lain.alasan_dilewati.items():
            self.alasan_dilewati[k] = self.alasan_dilewati.get(k, 0) + v

    @property
    def total(self) -> int:
        return self.baru + self.duplikat + self.diperbarui + self.dilewati


# --------------------------------------------------------------------------
# Pembersihan nilai dari PGN
# --------------------------------------------------------------------------


# --------------------------------------------------------------------------
# Sidik jari partai
# --------------------------------------------------------------------------


# --------------------------------------------------------------------------
# Pemain dan turnamen
# --------------------------------------------------------------------------


def isi_format_kosong(conn: sqlite3.Connection, ulang: bool = False) -> int:
    """
    Backfill kolom format (skema v6) untuk partai yang belum punya. Satu
    transaksi; 47 ribu baris selesai dalam hitungan detik. Aman diulang.
    ulang=True menghitung ulang SEMUA baris (dipakai setelah aturan
    format_dari_kontrol_waktu berubah).
    """
    baris = conn.execute(
        "SELECT id, kontrol_waktu, sumber FROM partai"
        + ("" if ulang else " WHERE format IS NULL")
    ).fetchall()
    conn.executemany(
        "UPDATE partai SET format = ? WHERE id = ?",
        [(format_dari_kontrol_waktu(r["kontrol_waktu"], r["sumber"]), r["id"]) for r in baris],
    )
    conn.commit()
    return len(baris)


def dapatkan_atau_buat_turnamen(
    conn: sqlite3.Connection,
    nama: str | None,
    tahun: int | None,
    lokasi: str | None = None,
    sumber_url: str | None = None,
) -> int | None:
    """Kembalikan id turnamen, atau None kalau PGN tidak menyebutkan nama."""
    n = bersih(nama)
    if not n:
        return None

    baris = conn.execute(
        "SELECT id FROM turnamen WHERE nama = ? AND tahun IS ?", (n, tahun)
    ).fetchone()
    if baris:
        return baris["id"]

    cur = conn.execute(
        "INSERT INTO turnamen(nama, tahun, lokasi, sumber_url, dibuat_pada) "
        "VALUES (?,?,?,?,?)",
        (n, tahun, bersih(lokasi), bersih(sumber_url), sekarang()),
    )
    return cur.lastrowid


# --------------------------------------------------------------------------
# Impor satu partai
# --------------------------------------------------------------------------


def impor_satu(
    conn: sqlite3.Connection,
    game: chess.pgn.Game,
    sumber: str,
    sumber_url_bawaan: str | None = None,
    nama_turnamen_paksa: str | None = None,
) -> str:
    """
    Masukkan satu partai. Kembalikan 'baru', 'duplikat', atau alasan dilewati.

    `nama_turnamen_paksa` menang atas tag Event. Dipakai impor ronde siaran:
    satu ronde broadcast SELALU satu turnamen, sedangkan tag Event-nya bisa
    berbeda antar papan (lihat EVENT_PAPAN).
    """
    h = game.headers
    bahan = bahan_hash(game)
    if bahan is None:
        if not bersih(h.get("White")) or not bersih(h.get("Black")):
            return "lewati:nama pemain kosong"
        return "lewati:tidak ada langkah"

    nama_putih, nama_hitam = bahan["putih"], bahan["hitam"]
    langkah, langkah_uci = bahan["langkah"], bahan["langkah_uci"]
    hasil, tanggal = bahan["hasil"], bahan["tanggal"]

    # Pemeriksaan isi (audit 28 Sep 2026): semua ini dulu diterima apa adanya.
    varian = bersih(h.get("Variant"))
    if varian and varian.lower().replace(" ", "") not in VARIAN_SAH:
        # Crazyhouse/Atomic/Three-check/Antichess: langkahnya bukan catur biasa,
        # dan posisinya mencemari penjelajah dengan kunci FEN standar.
        return f"lewati:varian {varian} (bukan catur standar)"
    if h.get("FEN") and not game.board().is_valid():
        return "lewati:posisi awal [FEN] tidak sah"
    if hasil not in HASIL_SAH:
        return f"lewati:hasil tidak sah ({hasil})"
    if tanggal and not POLA_TANGGAL.fullmatch(tanggal):
        tanggal = None          # "abcd" bukan tanggal: simpan NULL, jangan mengarang

    sidik = hash_partai(nama_putih, nama_hitam, tanggal, hasil, langkah_uci)
    if conn.execute("SELECT 1 FROM partai WHERE hash_unik = ?", (sidik,)).fetchone():
        return "duplikat"

    # Siaran Lichess 2026.07.20 (Biel) menulis 'Tureshbayev, Anuar' sebagai
    # Putih DAN Hitam dengan FIDE ID yang sama: data sumber rusak, dan partai
    # melawan diri sendiri mengacaukan statistik pemain (audit 27 Sep 2026).
    fide_p, fide_h = bersih(h.get("WhiteFideId")), bersih(h.get("BlackFideId"))
    if (normalisasi_nama(nama_putih) == normalisasi_nama(nama_hitam) and fide_p == fide_h) \
            or (fide_p and fide_p == fide_h):
        # FIDE ID sama = orang yang sama, walau namanya ditulis beda
        # ("Kembar A" / "Kembar B" dengan FideId 999 dulu lolos).
        return "lewati:putih dan hitam pemain yang sama"

    # Partai yang sama dari sumber lain dengan TANGGAL berbeda lolos dari hash
    # (lihat cari_kembar_lintas_sumber). Partai ber-[FEN] dilewati: langkah
    # yang sama dari posisi awal berbeda bukan partai yang sama.
    if not h.get("FEN") and cari_kembar_lintas_sumber(
            conn, nama_putih, nama_hitam, tanggal, hasil, langkah_uci, sumber,
            (len(langkah) + 1) // 2) is not None:
        return "duplikat"

    putih_id = dapatkan_atau_buat_pemain(
        conn, nama_putih, h.get("WhiteFideId"), h.get("WhiteTitle"),
        h.get("WhiteCountry"), ke_elo(h.get("WhiteElo")),
    )
    hitam_id = dapatkan_atau_buat_pemain(
        conn, nama_hitam, h.get("BlackFideId"), h.get("BlackTitle"),
        h.get("BlackCountry"), ke_elo(h.get("BlackElo")),
    )
    if putih_id == hitam_id:
        # Dua nama yang dicocokkan ke satu pemain (lewat FIDE ID atau inisial).
        return "lewati:putih dan hitam pemain yang sama"

    # Site di broadcast adalah kota ("Stavanger NOR"); di Lichess/Chess.com
    # adalah URL partai, dan itu bukan lokasi turnamen.
    site = bersih(h.get("Site"))
    nama_dari_header, url_turnamen = turnamen_dari_header(h)
    turnamen_id = dapatkan_atau_buat_turnamen(
        conn,
        bersih(nama_turnamen_paksa) or nama_dari_header,
        tahun_dari_tanggal(tanggal),
        lokasi=None if (site or "").startswith("http") else site,
        sumber_url=url_turnamen,
    )
    babak = bersih(h.get("Round"))
    jumlah_langkah = (len(langkah) + 1) // 2

    sumber_url = bersih(h.get("Link")) or bersih(h.get("Site")) or sumber_url_bawaan
    if sumber_url and not str(sumber_url).startswith("http"):
        sumber_url = sumber_url_bawaan

    # Partai broadcast yang tersimpan saat masih berjalan (hasil '*') punya
    # hash berbeda dari versi selesainya, karena langkah dan hasilnya berubah.
    # Tanpa jalur ini, tiap impor ulang menambah satu versi basi.
    lama = _partai_belum_selesai(conn, turnamen_id, babak, putih_id, hitam_id,
                                 tanggal, langkah_uci)
    if lama is not None:
        if jumlah_langkah < (lama["jumlah_langkah"] or 0):
            return "lewati:versi lebih lama dari yang tersimpan"
        conn.execute(
            "UPDATE partai SET tanggal = ?, hasil = ?, eco = ?, pembukaan = ?, "
            "elo_putih = ?, elo_hitam = ?, kontrol_waktu = ?, format = ?, jumlah_langkah = ?, "
            "pgn = ?, sumber_url = ?, hash_unik = ?, diimpor_pada = ? WHERE id = ?",
            (
                tanggal, hasil, eco_baku(h.get("ECO")), bersih(h.get("Opening")),
                ke_elo(h.get("WhiteElo")), ke_elo(h.get("BlackElo")),
                bersih(h.get("TimeControl")),
                format_dari_kontrol_waktu(bersih(h.get("TimeControl")), sumber), jumlah_langkah,
                str(game), sumber_url, sidik, sekarang(), lama["id"],
            ),
        )
        # Analisis dan indeks posisi dibuat dari versi '*' yang TERPOTONG.
        # Audit 27 Sep 2026: 103 partai siaran selesai membawa analisis dari
        # 28-96% langkahnya, jadi akurasi dan blundernya salah. Analisis lama
        # dibuang supaya partai ini kembali ke antrean analisis; posisinya
        # dibangun ulang dari PGN lengkap sekarang juga (impor dari terminal
        # tidak selalu menjalankan indeks sesudahnya).
        from .analisis_db import hapus_analisis          # noqa: PLC0415
        from .posisi import indeks_partai                # noqa: PLC0415
        hapus_analisis(conn, lama["id"], commit=False)
        conn.execute("DELETE FROM posisi WHERE partai_id = ?", (lama["id"],))
        indeks_partai(conn, lama["id"], str(game))
        return "diperbarui"

    try:
        conn.execute(
            "INSERT INTO partai(turnamen_id, putih_id, hitam_id, tanggal, babak, "
            "hasil, eco, pembukaan, elo_putih, elo_hitam, kontrol_waktu, format, "
            "jumlah_langkah, pgn, sumber, sumber_url, hash_unik, diimpor_pada) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                turnamen_id, putih_id, hitam_id, tanggal, babak,
                hasil, eco_baku(h.get("ECO")), bersih(h.get("Opening")),
                ke_elo(h.get("WhiteElo")), ke_elo(h.get("BlackElo")),
                bersih(h.get("TimeControl")),
                format_dari_kontrol_waktu(bersih(h.get("TimeControl")), sumber), jumlah_langkah,
                str(game), sumber, sumber_url, sidik, sekarang(),
            ),
        )
    except sqlite3.IntegrityError:
        # Bisa terjadi kalau partai yang sama muncul dua kali dalam satu file.
        return "duplikat"

    return "baru"


def _partai_belum_selesai(conn, turnamen_id, babak, putih_id, hitam_id,
                          tanggal=None, langkah_uci=""):
    """
    Cari partai yang sama yang tersimpan dalam keadaan belum selesai ('*').

    Dua jalur:

    1. turnamen + babak + pasangan pemain — identitas partai broadcast dari
       sumber yang sama. Menuntut turnamen DAN babak terisi: partai online Ray
       semuanya ber-Round "-", jadi tidak pernah tersentuh.

    2. Lintas sumber: pasangan pemain + tanggal, dan langkah yang tersimpan
       harus merupakan AWALAN dari langkah yang baru. Ini menangani partai
       yang diambil dari Lichess saat masih berjalan lalu muncul versi
       selesainya di TWIC dengan nama turnamen berbeda.

       Jalur ini HANYA berlaku kalau kedua pemain punya FIDE ID — identitas
       yang pasti, dan itu memang cuma ada di data broadcast/TWIC. Partai
       online Ray (tanpa FIDE ID) tidak pernah tersentuh, jadi dua partai
       cepat melawan lawan yang sama di hari yang sama tidak saling menimpa.
       Syarat awalan langkah adalah pengaman kedua.
    """
    import io

    if turnamen_id is not None and babak is not None:
        r = conn.execute(
            "SELECT id, jumlah_langkah FROM partai "
            "WHERE turnamen_id = ? AND babak = ? AND putih_id = ? AND hitam_id = ? "
            "AND hasil = '*'",
            (turnamen_id, babak, putih_id, hitam_id),
        ).fetchone()
        if r is not None:
            return r

    if not tanggal or not langkah_uci:
        return None
    for r in conn.execute(
        "SELECT g.id, g.jumlah_langkah, g.pgn FROM partai g "
        "JOIN pemain pw ON pw.id = g.putih_id "
        "JOIN pemain pb ON pb.id = g.hitam_id "
        "WHERE g.putih_id = ? AND g.hitam_id = ? AND g.tanggal = ? AND g.hasil = '*' "
        "AND pw.fide_id IS NOT NULL AND pb.fide_id IS NOT NULL",
        (putih_id, hitam_id, tanggal),
    ).fetchall():
        g = chess.pgn.read_game(io.StringIO(r["pgn"]))
        if g is None:
            continue
        lama_uci = " ".join(m.uci() for m in g.mainline_moves())
        if lama_uci and langkah_uci.startswith(lama_uci):
            return r
    return None



def impor_teks(
    conn: sqlite3.Connection,
    teks: str,
    sumber: str,
    sumber_url_bawaan: str | None = None,
    batas: int = 0,
    lapor_tiap: int = 0,
    nama_turnamen_paksa: str | None = None,
) -> HasilImpor:
    """Impor semua partai dari teks PGN."""
    fh = io.StringIO(teks)
    return _impor_stream(conn, fh, sumber, sumber_url_bawaan, batas, lapor_tiap,
                         nama_turnamen_paksa)


def impor_file(
    conn: sqlite3.Connection,
    path: Path,
    sumber: str,
    sumber_url_bawaan: str | None = None,
    batas: int = 0,
    lapor_tiap: int = 0,
) -> HasilImpor:
    with Path(path).open(encoding="utf-8", errors="replace") as fh:
        return _impor_stream(conn, fh, sumber, sumber_url_bawaan, batas, lapor_tiap)


def _impor_stream(conn, fh, sumber, sumber_url_bawaan, batas, lapor_tiap,
                  nama_turnamen_paksa=None) -> HasilImpor:
    hasil = HasilImpor()
    while True:
        if batas and hasil.total >= batas:
            break
        try:
            game = chess.pgn.read_game(fh)
        except Exception as e:
            hasil.lewati(f"PGN rusak: {type(e).__name__}")
            continue
        if game is None:
            break

        # python-chess tidak melempar error untuk langkah ilegal — dia
        # mencatatnya di game.errors dan memotong sisa langkahnya. Partai
        # terpotong seperti itu (ada di TWIC sesekali) lebih baik dilewati
        # daripada disimpan seolah-olah utuh.
        if game.errors:
            hasil.lewati("PGN rusak: langkah ilegal")
            continue

        tertelan = _teks_tertelan(game)
        if tertelan is not None:
            # Partai ini sendiri terpotong (langkahnya berhenti di komentar),
            # tapi partai yang tertelan masih utuh: dibaca ulang dari sana.
            hasil.lewati("PGN rusak: komentar { tidak ditutup")
            sisa = _impor_stream(conn, io.StringIO(tertelan), sumber, sumber_url_bawaan,
                                 0, 0, nama_turnamen_paksa)
            hasil.gabung(sisa)
            continue

        # SAVEPOINT per partai: galat di tengah satu partai (mis. teks dengan
        # surrogate yang tidak bisa disimpan) dulu jadi 500 SETELAH ratusan
        # partai sebelumnya ter-commit tanpa indeks. Kini partai itu saja yang
        # dibatalkan dan dilewati dengan alasannya.
        conn.execute("SAVEPOINT satu_partai")
        try:
            status = impor_satu(conn, game, sumber, sumber_url_bawaan,
                                nama_turnamen_paksa)
            conn.execute("RELEASE satu_partai")
        except (sqlite3.Error, ValueError, UnicodeError) as e:
            conn.execute("ROLLBACK TO satu_partai")
            conn.execute("RELEASE satu_partai")
            status = f"lewati:galat saat menyimpan ({type(e).__name__})"
        if status == "baru":
            hasil.baru += 1
        elif status == "duplikat":
            hasil.duplikat += 1
        elif status == "diperbarui":
            hasil.diperbarui += 1
        else:
            hasil.lewati(status.split(":", 1)[-1])

        # Commit berkala, bukan sekali di akhir. Kalau impor 8.000 partai
        # terhenti di tengah, yang sudah masuk tetap tersimpan. Tiap 50, bukan
        # 200: 200 partai makan ±4 detik, hampir menyentuh busy_timeout 5 detik
        # penulis lain (simpan jawaban latihan dijawab 500 selama unggahan web,
        # audit 28 Sep 2026).
        if hasil.total % 50 == 0:
            conn.commit()
            if lapor_tiap and hasil.total % lapor_tiap == 0:
                print(f"    {hasil.total} diproses "
                      f"({hasil.baru} baru, {hasil.duplikat} duplikat, "
                      f"{hasil.diperbarui} diperbarui)")

    conn.commit()
    return hasil
