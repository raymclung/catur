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
"""

from __future__ import annotations

import hashlib
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path

import chess
import chess.pgn

from .db import sekarang
from .format_partai import format_dari_kontrol_waktu
from .sumber_impor import catat as catat_sumber, sudah_selesai

# Gelar yang sering menempel di nama pemain dan harus dibuang saat
# menormalisasi, supaya "GM Carlsen, Magnus" dan "Carlsen, Magnus" dikenali
# sebagai orang yang sama.
GELAR = {"gm", "im", "fm", "cm", "wgm", "wim", "wfm", "wcm", "nm", "lm"}

# Nilai yang artinya "tidak diketahui" di PGN. Diperlakukan sebagai kosong,
# bukan disimpan apa adanya sebagai teks '?'.
KOSONG = {"", "?", "??", "-", "unknown", "n/a"}

# Event yang BUKAN turnamen: partai lepas di Lichess ("rated bullet game") dan
# Chess.com ("Live Chess"). Kalau dijadikan turnamen, tabel turnamen berisi
# baris seperti "rated bullet game / 2025" dengan ribuan partai yang tidak
# berarti apa-apa. Informasinya tidak hilang: kecepatan ada di kontrol_waktu,
# dan PGN utuh tetap tersimpan.
EVENT_GENERIK = re.compile(r"^(rated|casual)\s+\w+\s+game$|^live chess$", re.I)


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

    @property
    def total(self) -> int:
        return self.baru + self.duplikat + self.diperbarui + self.dilewati


# --------------------------------------------------------------------------
# Pembersihan nilai dari PGN
# --------------------------------------------------------------------------


def bersih(nilai: str | None) -> str | None:
    """Kembalikan None untuk nilai yang artinya 'tidak diketahui'."""
    if nilai is None:
        return None
    n = nilai.strip()
    return None if n.lower() in KOSONG else n


def ke_int(nilai: str | None) -> int | None:
    n = bersih(nilai)
    if n is None:
        return None
    m = re.match(r"^\s*(\d{1,4})", n)
    return int(m.group(1)) if m else None


def normalisasi_nama(nama: str) -> str:
    """
    Bentuk nama yang dipakai untuk mencocokkan pemain tanpa FIDE ID.

    'GM Carlsen, Magnus'  ->  'carlsen magnus'
    'Carlsen,M.'          ->  'carlsen m'
    """
    n = nama.lower()
    n = re.sub(r"[^\w\s]", " ", n)            # tanda baca jadi spasi
    kata = [k for k in n.split() if k not in GELAR]
    return " ".join(kata)


def turnamen_dari_header(h) -> tuple[str | None, str | None]:
    """
    Tentukan (nama turnamen, URL turnamen) dari header PGN.

    Tag ChessComTournament (ditambahkan ambil_partai.py, isinya URL API
    Chess.com) didahulukan, karena Event di Chess.com selalu "Live Chess".
    Namanya diambil dari ujung URL, mis. 'arena-kings-31036071'.

    Event generik (lihat EVENT_GENERIK) tidak dianggap turnamen.
    """
    url = bersih(h.get("ChessComTournament"))
    if url:
        return url.rstrip("/").rsplit("/", 1)[-1], url
    event = bersih(h.get("Event"))
    if not event or EVENT_GENERIK.match(event):
        return None, None
    return event, None


def tahun_dari_tanggal(tanggal: str | None) -> int | None:
    """Ambil tahun dari 'YYYY.MM.DD'. PGN membolehkan bagian tak diketahui
    ditulis '????', jadi kegagalan di sini normal dan bukan error."""
    t = bersih(tanggal)
    if not t:
        return None
    m = re.match(r"^(\d{4})", t)
    return int(m.group(1)) if m else None


# --------------------------------------------------------------------------
# Sidik jari partai
# --------------------------------------------------------------------------


def nama_belakang(nama: str) -> str:
    """
    Nama belakang yang dinormalisasi, untuk sidik jari partai.

    Sumber berbeda menulis orang yang sama dengan panjang berbeda:
    Lichess 'Anand, Viswanathan', TWIC 'Anand,V'. Nama lengkap yang
    dinormalisasi tetap beda ('anand viswanathan' vs 'anand v'), tapi nama
    belakangnya sama. Kalau ada koma, nama belakang = bagian sebelum koma;
    kalau tidak (username, atau 'Pranesh M'), = kata pertama.
    """
    n = nama.split(",")[0] if "," in nama else nama
    kata = normalisasi_nama(n).split()
    return kata[0] if kata else ""


def hash_partai(putih: str, hitam: str, tanggal: str | None,
                hasil: str, langkah_uci: str) -> str:
    """
    Sidik jari sebuah partai.

    Dihitung dari isi partainya — nama belakang kedua pemain, tanggal, hasil,
    dan seluruh rangkaian langkah. Sengaja TIDAK memakai teks PGN mentah
    (spasi/komentar/urutan tag bisa beda untuk partai yang sama) dan TIDAK
    memakai nama lengkap (lihat nama_belakang: TWIC menyingkat nama depan,
    sehingga 14 partai GCL 2026 sempat lolos sebagai "baru" padahal sudah ada
    dari broadcast Lichess).

    Kalau fungsi ini berubah, semua hash lama harus dihitung ulang lewat
    hitung_ulang_hash() — kalau tidak, partai lama tidak lagi terdeteksi
    sebagai duplikat.
    """
    bahan = "|".join([
        nama_belakang(putih),
        nama_belakang(hitam),
        bersih(tanggal) or "",
        hasil,
        langkah_uci,
    ])
    return hashlib.sha256(bahan.encode("utf-8")).hexdigest()



def bahan_hash(game: chess.pgn.Game) -> dict | None:
    """
    Field yang masuk ke hash_partai(), diambil dari satu tempat.

    Dipakai impor_satu() **dan** periksa_jejak(): kalau dua-duanya mengambil
    sendiri dari header, satu hari nanti audit akan menghitung hash yang beda
    tipis dari impor (mis. lupa UTCDate) dan melaporkan partai yang sebenarnya
    ada sebagai "tidak ada di database". None = partai tidak bisa dipakai.
    """
    h = game.headers
    putih, hitam = bersih(h.get("White")), bersih(h.get("Black"))
    if not putih or not hitam:
        return None
    langkah = list(game.mainline_moves())
    if not langkah:
        return None
    return {
        "putih": putih,
        "hitam": hitam,
        "tanggal": bersih(h.get("Date")) or bersih(h.get("UTCDate")),
        "hasil": bersih(h.get("Result")) or "*",
        "langkah": langkah,
        "langkah_uci": " ".join(m.uci() for m in langkah),
    }


def sidik_partai(game: chess.pgn.Game) -> str | None:
    """hash_unik sebuah partai PGN, persis seperti impor menghitungnya."""
    b = bahan_hash(game)
    if b is None:
        return None
    return hash_partai(b["putih"], b["hitam"], b["tanggal"], b["hasil"], b["langkah_uci"])


def hitung_ulang_hash(conn: sqlite3.Connection, lapor_tiap: int = 0) -> dict:
    """
    Hitung ulang hash_unik semua partai dengan rumus hash_partai() yang
    sekarang. Dipanggil setelah rumusnya berubah.

    Kalau dua baris ternyata punya hash baru yang sama (duplikat yang dulu
    lolos), satu dihapus. Yang dipertahankan: yang hasilnya bukan '*', lalu
    yang langkahnya lebih banyak, lalu id terkecil. Penghapusan ikut membuang
    analisisnya (ON DELETE CASCADE) — tidak ada analisis yatim.
    """
    import io

    baris = conn.execute(
        "SELECT id, pgn, tanggal, hasil, jumlah_langkah FROM partai ORDER BY id"
    ).fetchall()

    hash_baru: dict[str, dict] = {}
    perubahan = 0
    dihapus = 0

    for i, r in enumerate(baris, start=1):
        game = chess.pgn.read_game(io.StringIO(r["pgn"]))
        if game is None:
            continue
        h = game.headers
        langkah_uci = " ".join(m.uci() for m in game.mainline_moves())
        sidik = hash_partai(h.get("White", ""), h.get("Black", ""),
                            r["tanggal"], r["hasil"], langkah_uci)
        kandidat = {"id": r["id"], "hasil": r["hasil"],
                    "langkah": r["jumlah_langkah"] or 0}

        lama = hash_baru.get(sidik)
        if lama is None:
            hash_baru[sidik] = kandidat
            continue

        # Tabrakan: pilih yang dipertahankan.
        def peringkat(x):
            return (x["hasil"] != "*", x["langkah"], -x["id"])
        menang, kalah = (kandidat, lama) if peringkat(kandidat) > peringkat(lama) else (lama, kandidat)
        hash_baru[sidik] = menang
        conn.execute("DELETE FROM partai WHERE id = ?", (kalah["id"],))
        dihapus += 1
        if lapor_tiap and i % lapor_tiap == 0:
            print(f"    {i}/{len(baris)} diperiksa, {dihapus} duplikat dihapus")

    # Dua tahap supaya UNIQUE tidak menabrak hash lama milik baris lain
    # yang belum sempat diganti.
    conn.execute("UPDATE partai SET hash_unik = 'sementara-' || id")
    for sidik, x in hash_baru.items():
        cur = conn.execute(
            "UPDATE partai SET hash_unik = ? WHERE id = ? AND hash_unik != ?",
            (sidik, x["id"], sidik),
        )
        perubahan += cur.rowcount
    conn.commit()
    return {"diperiksa": len(baris), "dihapus": dihapus, "hash_diperbarui": perubahan}


# --------------------------------------------------------------------------
# Pemain dan turnamen
# --------------------------------------------------------------------------


def _belakang_depan(nama: str):
    """
    ('Moroni, Luca Jr') -> ('moroni', ['luca', 'jr']).

    Batas nama belakang diambil dari **koma**, bukan dari posisi kata. Menebak
    lewat posisi salah untuk nama depan lebih dari satu kata: 'Moroni, Luca Jr'
    sempat terbaca sebagai nama belakang 'moroni luca'. Tanpa koma -> None,
    karena urutan kata tidak bisa dipastikan (username, 'Wei Yi').
    """
    if "," not in nama:
        return None
    belakang, depan = nama.split(",", 1)
    b = normalisasi_nama(belakang)
    d = normalisasi_nama(depan).split()
    if not b or not d:
        return None
    return b, d


def _depan_cocok(pendek: list[str], panjang: list[str]) -> bool:
    """
    Benar kalau `pendek` adalah singkatan dari `panjang`.

    Tiga bentuk singkatan yang dipakai TWIC, semuanya dari data nyata:

      'l'  -> ['luca', 'jr']            awalan kata pertama
      'hj' -> ['hjorvar', 'steinn']     awalan kata pertama, dua huruf
      'tl' -> ['tigran', 'l']           deret inisial

    Singkatan bernomor ('vl3', dipakai TWIC untuk membedakan dua orang senama)
    tidak diterima: melepas angkanya justru menyatukan yang sengaja dibedakan.
    """
    if pendek == panjang:
        return True
    if len(pendek) != 1:
        return False
    s = pendek[0]
    if not s.isalpha() or len(s) > 3:
        return False
    if panjang[0].startswith(s):
        return True
    if len(s) <= len(panjang) and all(panjang[i].startswith(s[i]) for i in range(len(s))):
        return True
    return False


def _cocok_lewat_inisial(conn: sqlite3.Connection, nama: str,
                         n_normal: str) -> int | None:
    """
    Cocokkan nama yang depannya disingkat dengan nama lengkap, atau sebaliknya.

    'Aronian,L' dan 'Aronian, Levon' adalah orang yang sama, tapi bentuk
    normalnya beda ('aronian l' vs 'aronian levon') sehingga pencocokan biasa
    gagal dan lahir pemain ganda. Diukur sebelum impor PGN Mentor: 499 nama
    akan menjadi duplikat, menyangkut 50 ribu kemunculan partai.

    Aturan sengaja ketat, karena menggabungkan dua orang berbeda jauh lebih
    merusak daripada membiarkan satu duplikat:

      * hanya untuk nama berkoma ('Belakang, Depan') di kedua sisi;
      * nama belakang sama persis setelah normalisasi;
      * satu sisi harus singkatan dari sisi lain (lihat _depan_cocok);
      * hanya diterima kalau calonnya **tepat satu**. 'Smith,J' yang bisa
        berarti John atau Jane dibiarkan jadi pemain baru.

    Kembalikan id pemain yang cocok, atau None.
    """
    pisah = _belakang_depan(nama)
    if pisah is None:
        return None
    belakang, depan = pisah

    calon = []
    for baris in conn.execute(
        "SELECT id, nama, nama_normal FROM pemain WHERE nama_normal LIKE ?",
        (belakang + " %",),
    ):
        pisah_lain = _belakang_depan(baris["nama"])
        if pisah_lain is None:
            continue
        belakang_lain, depan_lain = pisah_lain
        if belakang_lain != belakang:
            continue
        # yang lebih pendek hurufnya adalah calon singkatan
        a, b = (depan, depan_lain) if len(" ".join(depan)) <= len(" ".join(depan_lain))             else (depan_lain, depan)
        cocok = _depan_cocok(a, b)
        if cocok and depan != depan_lain:
            calon.append(baris["id"])

    return calon[0] if len(calon) == 1 else None


def dapatkan_atau_buat_pemain(
    conn: sqlite3.Connection,
    nama: str,
    fide_id: str | None = None,
    gelar: str | None = None,
    negara: str | None = None,
    elo: int | None = None,
) -> int:
    """
    Cari pemain, buat kalau belum ada. Kembalikan id-nya.

    Urutan pencarian:
      1. Kalau ada FIDE ID -> cari berdasarkan itu. Identitas pasti.
      2. Kalau tidak -> cari berdasarkan nama yang dinormalisasi.

    Catatan tentang risiko yang disengaja: pencocokan lewat nama bisa keliru
    kalau ada dua orang berbeda dengan nama sama. Risiko itu diterima karena
    alternatifnya (membuat pemain baru tiap kali nama berulang) menghasilkan
    ratusan duplikat yang jauh lebih merepotkan. Yang penting, pemain hasil
    pencocokan nama TIDAK ditandai terverifikasi.
    """
    nama = nama.strip()
    n_normal = normalisasi_nama(nama)
    fid = bersih(fide_id)

    if fid:
        baris = conn.execute("SELECT id FROM pemain WHERE fide_id = ?", (fid,)).fetchone()
        if baris:
            _perbarui_pemain(conn, baris["id"], gelar, negara, elo)
            return baris["id"]

        # Mungkin pemain ini sudah pernah masuk lewat PGN tanpa FIDE ID.
        # Kalau ketemu berdasarkan nama, lengkapi datanya dan naikkan statusnya
        # jadi terverifikasi.
        baris = conn.execute(
            "SELECT id FROM pemain WHERE nama_normal = ? AND fide_id IS NULL",
            (n_normal,),
        ).fetchone()
        if baris:
            conn.execute(
                "UPDATE pemain SET fide_id = ?, terverifikasi = 1 WHERE id = ?",
                (fid, baris["id"]),
            )
            _perbarui_pemain(conn, baris["id"], gelar, negara, elo)
            return baris["id"]
    else:
        baris = conn.execute(
            "SELECT id FROM pemain WHERE nama_normal = ? ORDER BY id LIMIT 1",
            (n_normal,),
        ).fetchone()
        if baris:
            _perbarui_pemain(conn, baris["id"], gelar, negara, elo)
            return baris["id"]

        # Nama depan disingkat di satu sumber, lengkap di sumber lain:
        # PGN Mentor menulis 'Aronian,L', database punya 'Aronian, Levon'.
        id_inisial = _cocok_lewat_inisial(conn, nama, n_normal)
        if id_inisial is not None:
            _perbarui_pemain(conn, id_inisial, gelar, negara, elo)
            return id_inisial

    cur = conn.execute(
        "INSERT INTO pemain(nama, nama_normal, fide_id, negara, gelar, "
        "elo_terakhir, terverifikasi, dibuat_pada) VALUES (?,?,?,?,?,?,?,?)",
        (nama, n_normal, fid, bersih(negara), bersih(gelar), elo,
         1 if fid else 0, sekarang()),
    )
    return cur.lastrowid


def _perbarui_pemain(conn, pemain_id: int, gelar, negara, elo) -> None:
    """
    Lengkapi kolom yang masih kosong. Data yang sudah ada tidak ditimpa.

    Catatan: elo sengaja TIDAK diperbarui di sini. Kalau diperbarui tiap partai,
    nilai akhirnya berasal dari partai yang kebetulan diproses terakhir, bukan
    dari partai terbaru — dan urutan pemrosesan file tidak selalu urut waktu.
    Elo diisi belakangan lewat perbarui_elo_terakhir().
    """
    conn.execute(
        "UPDATE pemain SET "
        "  gelar = COALESCE(gelar, ?), "
        "  negara = COALESCE(negara, ?) "
        "WHERE id = ?",
        (bersih(gelar), bersih(negara), pemain_id),
    )


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


def tandai_akun_pribadi(conn: sqlite3.Connection, usernames: list[str],
                        kecualikan: list[str] = ()) -> int:
    """
    Tandai pemain yang namanya = username akun pemilik (permintaan #7).

    Dicocokkan lewat nama_normal dan hanya pemain TANPA FIDE ID — akun online
    tidak punya FIDE ID; kalau ada GM yang kebetulan bernama sama dan sudah
    terverifikasi, dia tidak ikut tertandai. `kecualikan` = username yang
    tandanya harus DICABUT (keputusan SESI.md bagian 6: RyuMcLungG bukan akun
    Ray walau partainya diunduh). Kembalikan jumlah pemain yang tertandai.
    """
    if kecualikan:
        normal = [normalisasi_nama(u) for u in kecualikan]
        conn.execute(
            f"UPDATE pemain SET akun_pribadi = 0 "
            f"WHERE nama_normal IN ({','.join('?' * len(normal))})",
            normal,
        )
    if usernames:
        normal = [normalisasi_nama(u) for u in usernames]
        conn.execute(
            f"UPDATE pemain SET akun_pribadi = 1 "
            f"WHERE nama_normal IN ({','.join('?' * len(normal))}) AND fide_id IS NULL",
            normal,
        )
    conn.commit()
    return conn.execute("SELECT COUNT(*) FROM pemain WHERE akun_pribadi = 1").fetchone()[0]


def perbarui_elo_terakhir(conn: sqlite3.Connection) -> int:
    """
    Isi kolom elo_terakhir dari partai TERBARU tiap pemain.

    Dijalankan sekali setelah impor selesai, bukan per partai.

    Yang perlu disadari saat membaca angkanya: satu pemain punya rating berbeda
    untuk tiap kontrol waktu (bullet, blitz, rapid). Kolom ini hanya menyimpan
    elo pada partai terakhir yang tercatat, apa pun kontrol waktunya. Jadi ini
    "elo di partai terakhir", bukan "rating pemain saat ini".
    """
    cur = conn.execute(
        """
        UPDATE pemain SET elo_terakhir = (
            SELECT elo FROM (
                SELECT putih_id AS pid, elo_putih AS elo, tanggal
                  FROM partai WHERE elo_putih IS NOT NULL
                UNION ALL
                SELECT hitam_id, elo_hitam, tanggal
                  FROM partai WHERE elo_hitam IS NOT NULL
            )
            WHERE pid = pemain.id
            ORDER BY tanggal DESC
            LIMIT 1
        )
        """
    )
    conn.commit()
    return cur.rowcount


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
) -> str:
    """
    Masukkan satu partai. Kembalikan 'baru', 'duplikat', atau alasan dilewati.
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

    sidik = hash_partai(nama_putih, nama_hitam, tanggal, hasil, langkah_uci)
    if conn.execute("SELECT 1 FROM partai WHERE hash_unik = ?", (sidik,)).fetchone():
        return "duplikat"

    putih_id = dapatkan_atau_buat_pemain(
        conn, nama_putih, h.get("WhiteFideId"), h.get("WhiteTitle"),
        h.get("WhiteCountry"), ke_int(h.get("WhiteElo")),
    )
    hitam_id = dapatkan_atau_buat_pemain(
        conn, nama_hitam, h.get("BlackFideId"), h.get("BlackTitle"),
        h.get("BlackCountry"), ke_int(h.get("BlackElo")),
    )

    # Site di broadcast adalah kota ("Stavanger NOR"); di Lichess/Chess.com
    # adalah URL partai, dan itu bukan lokasi turnamen.
    site = bersih(h.get("Site"))
    nama_turnamen, url_turnamen = turnamen_dari_header(h)
    turnamen_id = dapatkan_atau_buat_turnamen(
        conn,
        nama_turnamen,
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
                tanggal, hasil, bersih(h.get("ECO")), bersih(h.get("Opening")),
                ke_int(h.get("WhiteElo")), ke_int(h.get("BlackElo")),
                bersih(h.get("TimeControl")),
                format_dari_kontrol_waktu(bersih(h.get("TimeControl")), sumber), jumlah_langkah,
                str(game), sumber_url, sidik, sekarang(), lama["id"],
            ),
        )
        return "diperbarui"

    try:
        conn.execute(
            "INSERT INTO partai(turnamen_id, putih_id, hitam_id, tanggal, babak, "
            "hasil, eco, pembukaan, elo_putih, elo_hitam, kontrol_waktu, format, "
            "jumlah_langkah, pgn, sumber, sumber_url, hash_unik, diimpor_pada) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                turnamen_id, putih_id, hitam_id, tanggal, babak,
                hasil, bersih(h.get("ECO")), bersih(h.get("Opening")),
                ke_int(h.get("WhiteElo")), ke_int(h.get("BlackElo")),
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



def periksa_jejak(conn: sqlite3.Connection, berkas: list[Path],
                  url_per_berkas: dict[str, str | None] | None = None,
                  sumber: str = "pgnmentor", tulis: bool = False,
                  lapor=None) -> list[dict]:
    """
    Cocokkan ulang berkas PGN ke baris database lewat hash, dan (kalau tulis)
    lengkapi jejak asalnya.

    Kenapa ada: koleksi pgnmentor diimpor tanpa `sumber_url` dan tanpa baris
    `sumber_impor`, jadi pertanyaan wajib "partai ini dari mana?" (CLAUDE.md
    bagian 7) tidak bisa dijawab, dan tidak ada bukti impornya tuntas.
    Jejaknya masih bisa dipulihkan karena hash partai dihitung dari isi
    partai, bukan dari nama berkas: parse ulang berkasnya, hitung hash yang
    sama dengan impor, lalu cocokkan.

    Per berkas dilaporkan: `partai` di berkas, `cocok` (baris bersumber ini
    yang baru dihitung di berkas ini), `juga_koleksi_lain` (barisnya sudah
    dihitung/dilabeli koleksi sebelumnya — partai Carlsen-Anand ada di dua
    koleksi, dan tanpa hitungan ini jumlah `cocok` melebihi jumlah barisnya
    sendiri), `duplikat_sumber_lain` (hash-nya sudah ada dari TWIC dsb — memang
    ditolak saat impor), `tidak_ada` (TIDAK ada di database: ini yang berarti
    impornya belum tuntas), `tanpa_langkah` (tidak bisa dihitung).

    Yang ditulis hanya kalau `tulis` dan URL-nya ada: `sumber_url` untuk baris
    yang masih NULL, plus satu baris `sumber_impor` per berkas. URL tidak
    pernah dikarang — pemanggil yang memeriksanya (impor_pgn.py --jejak
    memakai HEAD), dan berkas tanpa URL sah tetap NULL. Baris `sumber_impor`
    yang sudah ada tidak ditulis ulang, supaya angkanya tidak berlipat kalau
    audit dijalankan dua kali.
    """
    url_per_berkas = url_per_berkas or {}
    peta: dict[str, tuple[int, str, str | None]] = {
        r["hash_unik"]: (r["id"], r["sumber"], r["sumber_url"])
        for r in conn.execute("SELECT hash_unik, id, sumber, sumber_url FROM partai")
    }
    terlihat: set[int] = set()      # id yang sudah dihitung di jalannya ini
    hasil = []

    for f in berkas:
        url = url_per_berkas.get(f.name)
        s = {"berkas": f.name, "url": url, "partai": 0, "tanpa_langkah": 0,
             "cocok": 0, "juga_koleksi_lain": 0, "duplikat_sumber_lain": 0,
             "tidak_ada": 0, "url_ditulis": 0, "sumber_impor": "tidak ditulis"}
        perlu_url: list[int] = []

        with f.open(encoding="utf-8", errors="replace") as fh:
            while True:
                game = chess.pgn.read_game(fh)
                if game is None:
                    break
                s["partai"] += 1
                sidik = sidik_partai(game)
                if sidik is None:
                    s["tanpa_langkah"] += 1
                    continue
                baris = peta.get(sidik)
                if baris is None:
                    s["tidak_ada"] += 1
                    continue
                partai_id, sumber_baris, url_baris = baris
                if sumber_baris != sumber:
                    s["duplikat_sumber_lain"] += 1
                    continue
                if partai_id in terlihat or url_baris:
                    s["juga_koleksi_lain"] += 1   # partai yang sama ada di dua koleksi
                    continue
                s["cocok"] += 1
                terlihat.add(partai_id)
                if url:
                    perlu_url.append(partai_id)

        if tulis and url and perlu_url:
            conn.executemany(
                "UPDATE partai SET sumber_url = ? WHERE id = ? AND sumber_url IS NULL",
                [(url, i) for i in perlu_url],
            )
            s["url_ditulis"] = len(perlu_url)
        if tulis:
            if sudah_selesai(conn, sumber, f.name):
                s["sumber_impor"] = "sudah ada, tidak ditimpa"
            else:
                catat_sumber(conn, sumber, f.name, f.stem, url,
                             jumlah_partai=s["partai"],
                             jumlah_lolos=s["partai"] - s["tanpa_langkah"],
                             jumlah_baru=s["cocok"])
                s["sumber_impor"] = "dicatat"
            conn.commit()

        hasil.append(s)
        if lapor:
            lapor(s)

    return hasil


def impor_teks(
    conn: sqlite3.Connection,
    teks: str,
    sumber: str,
    sumber_url_bawaan: str | None = None,
    batas: int = 0,
    lapor_tiap: int = 0,
) -> HasilImpor:
    """Impor semua partai dari teks PGN."""
    import io

    fh = io.StringIO(teks)
    return _impor_stream(conn, fh, sumber, sumber_url_bawaan, batas, lapor_tiap)


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


def _impor_stream(conn, fh, sumber, sumber_url_bawaan, batas, lapor_tiap) -> HasilImpor:
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

        status = impor_satu(conn, game, sumber, sumber_url_bawaan)
        if status == "baru":
            hasil.baru += 1
        elif status == "duplikat":
            hasil.duplikat += 1
        elif status == "diperbarui":
            hasil.diperbarui += 1
        else:
            hasil.lewati(status.split(":", 1)[-1])

        # Commit berkala, bukan sekali di akhir. Kalau impor 8.000 partai
        # terhenti di tengah, yang sudah masuk tetap tersimpan.
        if hasil.total % 200 == 0:
            conn.commit()
            if lapor_tiap and hasil.total % lapor_tiap == 0:
                print(f"    {hasil.total} diproses "
                      f"({hasil.baru} baru, {hasil.duplikat} duplikat, "
                      f"{hasil.diperbarui} diperbarui)")

    conn.commit()
    return hasil
