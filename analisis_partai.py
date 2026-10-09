"""
Analisis partai yang ada di database dengan Stockfish, simpan hasilnya.

Ini tempat analisis PARTAI PENUH dijalankan (semua langkah, akurasi, turning
point, disimpan ke tabel analisis). Satu partai butuh puluhan detik sampai
beberapa menit — bukan pekerjaan untuk sebuah request HTTP.

Sejak 10 Sep 2026 server web juga menjalankan SATU engine untuk evaluasi
satu posisi (papan analisis, app/engine_server.py, ~1,5 detik per permintaan),
tapi tidak pernah menganalisis partai penuh dan tidak menulis database.
Aturan mesin ini: maksimal dua proses engine bersamaan — skrip ini dan engine
server; jangan menjalankan dua batch sekaligus. Lihat ARSITEKTUR.md (Phase 5).

Contoh:
    python analisis_partai.py --id 11669 11657
    python analisis_partai.py --sumber lichess-broadcast --terbaru 20
    python analisis_partai.py --gelar-lawan GM IM --terbaru 50
    python analisis_partai.py --lawan-min-elo 2600 --lihat-saja
    python analisis_partai.py --sumber chesscom --terbaru 30 --profil normal
    python analisis_partai.py --sumber chesscom --lawan-min-elo 2201 --hasil kalah

Partai yang sudah dianalisis dilewati (pakai --ulang untuk memaksa).
Tiap partai disimpan begitu selesai, jadi Ctrl+C kapan pun aman: yang sudah
selesai tetap tersimpan, jalankan lagi untuk melanjutkan.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import os
import re
import shlex
import sqlite3
import sys
import time
from pathlib import Path

AKAR = Path(__file__).resolve().parent
sys.path.insert(0, str(AKAR / "src"))

import chess.engine
import chess.pgn

from ambil_partai import AKUN
from app.analisis_db import simpan_laporan, statistik_analisis
from app.db import FOLDER_DATA, PATH_DB, buat_skema, koneksi
from app.format_partai import FORMAT_VALID
from app.repositori import pola_like
from app.kunci_penulis import tunggu_sampai_bebas
from app.impor import normalisasi_nama
from catur_analysis.engine import PROFIL, EngineError, Stockfish
from catur_analysis.partai import analisis_partai

# WNM ikut sejak 25 Sep 2026: gelarnya ada di database (dan datang dari
# Chess.com), tapi dulu tidak bisa dipilih di --gelar-lawan maupun halaman
# Mesin, jadi partai lawan ber-WNM tidak pernah bisa disaring.
GELAR_VALID = ("GM", "IM", "FM", "CM", "WGM", "WIM", "WFM", "WCM", "NM", "WNM")

# Detik per ply untuk estimasi waktu, dipakai hanya kalau belum ada riwayat.
#
# Angka pertama (0,12) ternyata meleset 4x: diukur dari partai 7 langkah yang
# posisinya masih pembukaan dan murah. Partai GM 67 langkah di depth 16 makan
# 126 detik = ~0,95 s/ply. Karena itu estimasi utama diambil dari durasi
# analisis yang sudah tersimpan (lihat detik_per_ply_terukur), dan angka di
# bawah ini cuma cadangan untuk profil yang belum pernah dipakai.
# Dipakai kalau belum ada riwayat analisis untuk profil itu. SETIAP profil di
# catur_analysis.engine.PROFIL harus punya entri di sini — profil "cepat"
# sempat ditambahkan tanpa entri, dan perintah pertamanya langsung mati
# KeyError sebelum satu partai pun dianalisis (13 Sep 2026).
# cepat: diukur 2,7 detik untuk partai ±80 ply = ±0,034 detik/ply; dibulatkan ke atas.
DETIK_PER_PLY_CADANGAN = {"cepat": 0.05, "hemat": 1.0, "normal": 4.0, "dalam": 15.0}

# Engine yang mogok sekali dinyalakan ulang dan batch lanjut; mogok beruntun
# sebanyak ini berarti masalahnya di engine (berkas rusak, RAM habis), bukan
# di partainya, jadi batch berhenti daripada berputar tanpa hasil.
MAKS_MOGOK_BERUNTUN = 3

# Profil yang dianggap "masih kasar" oleh --dangkal: depth 10, dan evaluasi
# siaran Lichess yang tidak menyimpan langkah terbaik sama sekali.
PROFIL_DANGKAL = ("cepat", "lichess")


RIWAYAT_PERKIRAAN = 500     # analisis terbaru yang dipakai untuk perkiraan awal


def detik_per_ply_terukur(conn, profil: str) -> float | None:
    """
    Detik per ply dari analisis TERBARU dengan profil yang sama.

    Dulu seluruh riwayat: profil cepat terbaca 0,025 dtk/ply, sebagian besar
    dari 13-22 Sep saat satu batch jalan sendirian, padahal dengan dua batch
    dan batas CPU 25% yang terukur 0,06-0,07. Perkiraan "68 menit" untuk
    batch yang nyatanya ±200 menit (audit 27 Sep 2026).
    """
    r = conn.execute(
        """
        SELECT SUM(durasi_detik) / SUM(jumlah_langkah * 2.0) FROM (
            SELECT a.durasi_detik, g.jumlah_langkah
            FROM analisis a JOIN partai g ON g.id = a.partai_id
            WHERE a.profil = ? AND a.durasi_detik IS NOT NULL
            ORDER BY a.id DESC LIMIT ?
        )
        """,
        (profil, RIWAYAT_PERKIRAAN),
    ).fetchone()[0]
    return float(r) if r else None


class PerkiraanSisa:
    """
    Sisa waktu batch dari partai yang BENAR-BENAR dianalisis baru-baru ini.

    Dulu (waktu sejak mulai / partai ke-i) x sisa partai: partai yang dilewati
    ikut dihitung, start engine ikut, dan satu kali macet 4 menit melipatkan
    perkiraan berlipat-lipat untuk ratusan partai (log 27 Sep: 69 -> 416 m).
    """

    def __init__(self, daftar: list[dict], jendela: int = 30):
        from collections import deque
        self.ply_sisa_setelah = []
        total = sum(p["jumlah_langkah"] * 2 for p in daftar)
        for p in daftar:
            total -= p["jumlah_langkah"] * 2
            self.ply_sisa_setelah.append(total)
        self.terakhir = deque(maxlen=jendela)

    def catat(self, durasi: float, jumlah_langkah: int) -> None:
        self.terakhir.append((durasi, jumlah_langkah * 2))

    def menit(self, i: int) -> float | None:
        """Sisa menit setelah partai ke-i (1-based); None kalau belum ada data."""
        ply = sum(p for _, p in self.terakhir)
        if not ply:
            return None
        return sum(d for d, _ in self.terakhir) / ply * self.ply_sisa_setelah[i - 1] / 60


def nama_normal_saya(conn=None) -> list[str]:
    """
    Nama pemain yang dihitung sebagai "saya" untuk --gelar-lawan, --lawan-min-elo
    dan --hasil.

    Sumber kebenarannya kolom pemain.akun_pribadi (skema v6): RyuMcLungG diunduh
    tapi BUKAN akun Ray (keputusan SESI.md bagian 6), jadi daftar dari
    ambil_partai.AKUN saja akan kelebihan satu akun. Kalau tandanya belum
    pernah diisi, jatuh kembali ke daftar AKUN supaya perintah lama tetap jalan.
    """
    if conn is not None:
        nama = [r[0] for r in conn.execute(
            "SELECT nama_normal FROM pemain WHERE akun_pribadi = 1")]
        if nama:
            return nama
    return [normalisasi_nama(a.username) for a in AKUN]


def pilih_partai(conn, args) -> list[dict]:
    """
    Bangun query pemilihan partai dari argumen. Semua nilai lewat parameter.
    Partai yang sudah dianalisis disingkirkan kecuali --ulang.
    """
    syarat, params = [], []

    if args.id:
        syarat.append(f"g.id IN ({','.join('?' * len(args.id))})")
        params += args.id

    # --bagian K/N: hanya partai dengan id % N == K. Beberapa batch dengan
    # saringan sama tapi bagian berbeda dijamin tidak pernah memilih partai
    # yang sama — dipakai penjaga antrean untuk mengisi slot kosong.
    bagian = getattr(args, "bagian", None)
    if bagian:
        k, n = (int(x) for x in bagian.split("/"))
        syarat.append("g.id % ? = ?")
        params += [n, k]

    if args.sumber == "gm":
        # Label khusus: semua sumber partai GM sekaligus, sama persis dengan
        # /api/posisi?sumber=gm. Tanpa ini, "semua sumber" di kelompok Partai GM
        # berarti tanpa saringan sumber — dan partai akun Ray ikut terjaring
        # (permintaan frontend 13 Sep 00:25).
        from app.posisi import SUMBER_GM
        syarat.append(f"g.sumber IN ({','.join('?' * len(SUMBER_GM))})")
        params += list(SUMBER_GM)
    elif args.sumber:
        syarat.append("g.sumber = ?")
        params.append(args.sumber)

    if getattr(args, "format_", None):
        syarat.append("g.format = ?")
        params.append(args.format_)

    # --eco: satu kode (A22), awalan (A), atau rentang (A10-A39). Dipakai untuk
    # menganalisis partai GM di pembukaan yang benar-benar Ray mainkan —
    # pelajaran dari partai sepembukaan jauh lebih terpakai daripada partai
    # acak. --repertoire = keempat rentang di app/eco.py sekaligus.
    rentang = list(getattr(args, "rentang_eco", None) or [])
    if getattr(args, "repertoire", False):
        from app.eco import REPERTOIRE
        rentang += [(dari, sampai) for _, dari, sampai in REPERTOIRE]
    if rentang:
        bagian = []
        for dari, sampai in rentang:
            bagian.append("substr(g.eco, 1, 3) BETWEEN ? AND ?")
            params += [dari, sampai]
        syarat.append("(" + " OR ".join(bagian) + ")")

    # --pemain: cocok kalau salah satu pemain mengandung potongan nama yang
    # dinormalisasi ("Carlsen" -> carlsen; "Nakamura, Hikaru" -> nakamura hikaru).
    # Beberapa nama = salah satu cukup. Untuk partai GM di TWIC/broadcast.
    if getattr(args, "pemain", None):
        bagian = []
        for nama in args.pemain:
            pola = pola_like(normalisasi_nama(nama))
            bagian.append("(pw.nama_normal LIKE ? ESCAPE '\\' OR pb.nama_normal LIKE ? ESCAPE '\\')")
            params += [pola, pola]
        syarat.append("(" + " OR ".join(bagian) + ")")

    # --min-elo: KEDUA pemain minimal sekian (partai antar-GM kuat).
    if getattr(args, "min_elo", None):
        syarat.append("g.elo_putih >= ? AND g.elo_hitam >= ?")
        params += [args.min_elo, args.min_elo]

    saya = nama_normal_saya(conn)
    tanda_saya = ",".join("?" * len(saya))

    # --hasil: dilihat dari sisi Ray, bukan dari sisi Putih. Dipakai untuk
    # mendahulukan partai KALAH (keputusan Ray, B11): partai yang kalah yang
    # paling banyak mengajari, dan halaman Kekalahan butuh analisisnya.
    if getattr(args, "hasil", None):
        if args.hasil == "kalah":
            syarat.append(f"((pw.nama_normal IN ({tanda_saya}) AND g.hasil = '0-1') OR "
                          f" (pb.nama_normal IN ({tanda_saya}) AND g.hasil = '1-0'))")
            params += saya + saya
        elif args.hasil == "menang":
            syarat.append(f"((pw.nama_normal IN ({tanda_saya}) AND g.hasil = '1-0') OR "
                          f" (pb.nama_normal IN ({tanda_saya}) AND g.hasil = '0-1'))")
            params += saya + saya
        elif args.hasil == "menang-kalah":
            # Ray, 21 Sep 2026: "maksud semua adalah partai yang menang dan
            # kalah" — remis dibuang karena partai remis paling sedikit
            # mengajari, tapi partai menang TIDAK ikut dibuang seperti dulu.
            syarat.append(f"((pw.nama_normal IN ({tanda_saya}) OR "
                          f"  pb.nama_normal IN ({tanda_saya})) AND g.hasil IN ('1-0', '0-1'))")
            params += saya + saya
        else:   # remis
            syarat.append(f"((pw.nama_normal IN ({tanda_saya}) OR "
                          f"  pb.nama_normal IN ({tanda_saya})) AND g.hasil = '1/2-1/2')")
            params += saya + saya

    if args.gelar_lawan:
        gelar = [g.upper() for g in args.gelar_lawan]
        tanda_gelar = ",".join("?" * len(gelar))
        syarat.append(
            f"((pw.nama_normal IN ({tanda_saya}) AND pb.gelar IN ({tanda_gelar})) OR "
            f" (pb.nama_normal IN ({tanda_saya}) AND pw.gelar IN ({tanda_gelar})))"
        )
        params += saya + gelar + saya + gelar

    if args.lawan_min_elo:
        syarat.append(
            f"((pw.nama_normal IN ({tanda_saya}) AND g.elo_hitam >= ?) OR "
            f" (pb.nama_normal IN ({tanda_saya}) AND g.elo_putih >= ?))"
        )
        params += saya + [args.lawan_min_elo] + saya + [args.lawan_min_elo]

    # --dangkal: kebalikan dari bawaan — justru HANYA partai yang sudah
    # dianalisis, tapi dengan profil kasar (depth 10 `cepat`, atau evaluasi
    # siaran `lichess` yang tidak punya langkah terbaik). Dipakai untuk tahap
    # "analisis mendalam" partai sendiri tanpa mengulang yang sudah depth 16 —
    # tanpa ini, --ulang ikut mengerjakan 635 partai blitz/rapid yang sudah
    # dalam, sekitar 7 jam engine terbuang (diukur 18 Sep 2026).
    if getattr(args, "dangkal", False):
        syarat.append(f"a.profil IN ({','.join('?' * len(PROFIL_DANGKAL))})")
        params += list(PROFIL_DANGKAL)
    elif not args.ulang:
        syarat.append("a.partai_id IS NULL")

    # Partai yang belum selesai ('*') tidak dianalisis, kecuali Ray menyebut
    # id-nya sendiri. Dua alasan, dan keduanya berdiri sendiri:
    #   * waktunya terbuang — partai broadcast yang tertangkap saat siaran
    #     masih berjalan akan masuk lagi dengan hasil lengkap, dan dedup
    #     (app/impor.py) membuang yang '*', sehingga analisisnya ikut
    #     terhapus lewat ON DELETE CASCADE;
    #   * laporannya menyesatkan — 'sebab kalah' tidak berarti apa-apa pada
    #     partai yang belum punya hasil.
    # Diukur 25 Sep 2026: 16 partai '*' tersisa, 13 di antaranya sudah
    # terlanjur dianalisis.
    if not args.id:
        syarat.append("g.hasil <> '*'")

    syarat.append("g.jumlah_langkah >= ?")
    params.append(args.min_langkah)

    where = "WHERE " + " AND ".join(syarat) if syarat else ""
    limit = f"LIMIT {int(args.terbaru)}" if args.terbaru else ""
    # --terlama: mulai dari ujung yang lain. Dua batch dengan saringan sama,
    # satu dari yang terbaru dan satu dari yang terlama, tidak memilih partai
    # yang sama selama sisanya lebih dari dua kali --terbaru; kalau keduanya
    # akhirnya bertemu, partai yang sudah dianalisis batch lain dilewati di
    # jalankan(). Ray, 27 Sep 2026: "saya ingin membantu lebih cepat".
    urut = "ASC" if getattr(args, "terlama", False) else "DESC"

    baris = conn.execute(
        f"""
        SELECT g.id, g.pgn, g.jumlah_langkah, g.hasil, g.eco,
               pw.nama AS putih, pb.nama AS hitam,
               pw.gelar AS putih_gelar, pb.gelar AS hitam_gelar,
               g.elo_putih, g.elo_hitam
        FROM partai g
        JOIN pemain pw ON pw.id = g.putih_id
        JOIN pemain pb ON pb.id = g.hitam_id
        LEFT JOIN analisis a ON a.partai_id = g.id
        {where}
        ORDER BY g.tanggal {urut}, g.id {urut}
        {limit}
        """,
        params,
    ).fetchall()
    return [dict(b) for b in baris]


def _persen(akurasi: float | None) -> str:
    """Akurasi untuk log; None = pihak itu belum melangkah."""
    return "   - " if akurasi is None else f"{akurasi:5.1f}%"


def label(p: dict) -> str:
    def nm(nama, gelar):
        return (f"{gelar} " if gelar else "") + nama[:18]
    return f"#{p['id']:<6} {nm(p['putih'], p['putih_gelar']):<22} - {nm(p['hitam'], p['hitam_gelar']):<22} {p['hasil']:<7} {p['jumlah_langkah']:>3} lgk"


# Catatan kemajuan per proses, supaya halaman Mesin bisa menampilkan kemajuan
# batch yang dijalankan dari TERMINAL. Halaman itu hanya tahu log batch yang
# diluncurkannya sendiri; batch dari terminal mengarahkan keluarannya ke mana
# saja, jadi satu-satunya jalan adalah proses ini melaporkan diri.
# Berkasnya dihapus saat batch selesai; kalau proses mati mendadak, berkas
# yatim dibersihkan oleh pembacanya karena PID-nya sudah tidak hidup.
FOLDER_KEMAJUAN = FOLDER_DATA / "laporan"

# Klaim "partai ini sedang saya analisis", satu berkas per partai. Dua batch
# searah dulu berjalan beriringan dan sama-sama menganalisis partai yang sama:
# cek "sudah dianalisis" hanya menolong kalau yang lain SUDAH selesai (27 Sep
# 2026: 66 dari 67 partai batch 6804 juga dikerjakan 22436). Berkas dibuat
# dengan mode "x" (eksklusif, dijamin sistem operasi), jadi juga berlaku untuk
# batch yang dijalankan dari terminal. Isinya PID + waktu lahir proses, supaya
# klaim batch yang sudah mati bisa diambil alih walau PID-nya dipakai ulang.
FOLDER_KLAIM = FOLDER_DATA / "klaim-analisis"


def _identitas_proses(pid: int) -> str:
    from app.proses import waktu_lahir_proses              # noqa: PLC0415
    lahir = waktu_lahir_proses(pid)
    return f"{pid} {lahir:.0f}" if lahir is not None else str(pid)


def _pemilik_hidup(isi: str) -> bool:
    from app.proses import _proses_ada, waktu_lahir_proses  # noqa: PLC0415
    bagian = isi.split()
    if not bagian or not bagian[0].isdigit():
        return False
    pid = int(bagian[0])
    if not _proses_ada(pid):
        return False
    if len(bagian) < 2:
        return True
    lahir = waktu_lahir_proses(pid)
    return lahir is None or abs(lahir - float(bagian[1])) < 2


def klaim_partai(partai_id: int) -> bool:
    """True kalau partai ini boleh dianalisis proses ini (klaim didapat)."""
    FOLDER_KLAIM.mkdir(parents=True, exist_ok=True)
    path = FOLDER_KLAIM / str(partai_id)
    saya = _identitas_proses(os.getpid())
    for _ in range(3):
        try:
            with path.open("x", encoding="utf-8") as f:
                f.write(saya)
            return True
        except FileExistsError:
            try:
                isi = path.read_text(encoding="utf-8")
            except OSError:
                continue                     # sedang ditulis/dihapus proses lain
            if isi.split()[:1] == [str(os.getpid())]:
                return True
            if _pemilik_hidup(isi):
                return False
            try:
                path.unlink()                # klaim batch yang sudah mati
            except OSError:
                pass
    return False


def lepas_klaim(partai_id: int | None) -> None:
    if partai_id is None:
        return
    path = FOLDER_KLAIM / str(partai_id)
    try:
        if path.read_text(encoding="utf-8").split()[:1] == [str(os.getpid())]:
            path.unlink()
    except OSError:
        pass


def _path_kemajuan(pid: int | None = None) -> Path:
    import os
    return FOLDER_KEMAJUAN / f".kemajuan-{pid or os.getpid()}.json"


def tulis_kemajuan(selesai: int, dari: int, sisa_menit: float | None,
                   perintah: str, mulai: str) -> None:
    """Tulis kemajuan batch ini. Kegagalan menulis TIDAK boleh menghentikan
    analisis — ini catatan bantu, bukan data."""
    import json
    try:
        FOLDER_KEMAJUAN.mkdir(parents=True, exist_ok=True)
        _path_kemajuan().write_text(json.dumps({
            "selesai": selesai, "dari": dari,
            "sisa_menit": None if sisa_menit is None else int(sisa_menit),
            "perintah": perintah, "mulai": mulai,
        }, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


def hapus_kemajuan() -> None:
    try:
        _path_kemajuan().unlink()
    except OSError:
        pass


def _rentang_eco(nilai: list[str], p) -> list[tuple[str, str]]:
    """
    Ubah 'A22' / 'A' / 'A10-A39' jadi pasangan (dari, sampai) untuk BETWEEN.

    Satu huruf berarti seluruh hurufnya: 'A' -> A00..A99. Kode salah bentuk
    dihentikan di sini dengan pesan jelas, bukan diam-diam tidak cocok apa pun.
    """
    hasil = []
    for v in nilai:
        t = v.strip().upper()
        m = re.fullmatch(r"([A-E])(\d{2})?(?:-([A-E])(\d{2}))?", t)
        if not m:
            p.error(f"--eco tidak dikenal: {v!r} (contoh: A22, A, A10-A39)")
        huruf, angka, huruf2, angka2 = m.groups()
        if huruf2:
            hasil.append((f"{huruf}{angka or '00'}", f"{huruf2}{angka2}"))
        elif angka:
            hasil.append((t, t))
        else:
            hasil.append((f"{huruf}00", f"{huruf}99"))
    return hasil


def buat_parser() -> argparse.ArgumentParser:
    """Argumen CLI; dipakai juga untuk membaca ulang perintah batch yang sedang jalan."""
    p = argparse.ArgumentParser(description="Analisis partai di database dengan Stockfish")
    pilih = p.add_argument_group("pemilihan partai (minimal satu)")
    pilih.add_argument("--id", nargs="*", type=int, help="id partai tertentu")
    # Label sumber TIDAK dibatasi daftar tetap: halaman Impor membiarkan Ray
    # memberi label sendiri (bawaan "unggahan"), jadi daftar tertulis di sini
    # pasti ketinggalan. Yang sah = label yang benar-benar ada di database,
    # diperiksa setelah koneksi terbuka.
    pilih.add_argument("--sumber", metavar="LABEL",
                       help="label sumber seperti tersimpan di database "
                            "(twic, pgnmentor, lichess-broadcast, lichess-user, "
                            "chesscom, unggahan, ...), atau 'gm' = semua sumber "
                            "partai GM sekaligus")
    pilih.add_argument("--pemain", nargs="*", metavar="NAMA",
                       help="partai yang dimainkan pemain ini (potongan nama, mis. Carlsen)")
    pilih.add_argument("--min-elo", type=int, metavar="ELO",
                       help="kedua pemain minimal rating sekian (partai antar-GM)")
    pilih.add_argument("--gelar-lawan", nargs="*", choices=GELAR_VALID, metavar="GELAR",
                       help="partai Ray melawan pemain bergelar ini")
    pilih.add_argument("--lawan-min-elo", type=int, metavar="ELO",
                       help="partai Ray melawan rating minimal sekian")
    pilih.add_argument("--eco", nargs="*", metavar="KODE",
                       help="kode ECO, awalan, atau rentang: A22 / A / A10-A39 "
                            "(boleh beberapa)")
    pilih.add_argument("--repertoire", action="store_true",
                       help="pembukaan repertoire Ray sekaligus: English, Catalan, "
                            "Ragozin, Sicilian")
    pilih.add_argument("--hasil", choices=["kalah", "menang", "remis", "menang-kalah"],
                       help="hasil dari sisi Ray (mis. --hasil kalah untuk halaman Kekalahan; "
                            "menang-kalah = semua partai berhasil tegas, tanpa remis)")
    pilih.add_argument("--format", choices=list(FORMAT_VALID), dest="format_",
                       help="bullet / blitz / rapid / klasik / tanpa (kolom partai.format)")
    pilih.add_argument("--terbaru", type=int, metavar="N", help="batasi N partai terbaru")
    pilih.add_argument("--terlama", action="store_true",
                       help="kerjakan dari partai terlama dulu (--terbaru N jadi N partai terlama)")
    pilih.add_argument("--min-langkah", type=int, default=10,
                       help="lewati partai lebih pendek dari ini (default 10)")
    pilih.add_argument("--ulang", action="store_true", help="analisis ulang yang sudah ada")
    pilih.add_argument("--dangkal", action="store_true",
                       help="hanya partai yang analisisnya masih kasar "
                            f"(profil {'/'.join(PROFIL_DANGKAL)}) — untuk memperdalam "
                            "tanpa mengulang yang sudah depth 16")
    pilih.add_argument("--bagian", metavar="K/N",
                       help="hanya partai dengan id %% N == K (mis. 0/4), supaya beberapa batch "
                            "sejenis tidak memilih partai yang sama")

    p.add_argument("--profil", choices=sorted(PROFIL), default="hemat",
                   help="profil resource engine (default: hemat)")
    p.add_argument("--depth", type=int, default=None, help="timpa depth profil")
    p.add_argument("--lihat-saja", action="store_true", help="tampilkan daftar, jangan analisis")
    p.add_argument("--tanpa-tunggu", action="store_true",
                   help="jangan menunggu impor/indeks selesai (bawaan: menunggu, karena "
                        "batch yang mulai di tengah impor bisa mati terkunci)")
    p.add_argument("--db", type=Path, default=PATH_DB)
    return p


def argumen_dari_perintah(perintah: str) -> argparse.Namespace | None:
    """
    Saringan batch yang sedang jalan, dibaca ulang dari baris perintahnya.

    Halaman Mesin memakainya untuk tahu partai mana yang sedang dikerjakan
    batch lain. Membandingkan teks perintah saja tidak cukup: 27 Sep 2026
    `--sumber gm --terbaru 2000` lolos di samping `--terbaru 2000` karena
    tulisannya beda, padahal keduanya memilih partai yang sama persis.
    None kalau perintahnya tidak bisa dibaca.
    """
    kata = [k.strip('"') for k in shlex.split(perintah, posix=False)]
    awal = next((i for i, k in enumerate(kata) if k.replace("\\", "/").endswith("analisis_partai.py")),
                None)
    if awal is None:
        return None
    p = buat_parser()
    try:
        with contextlib.redirect_stderr(io.StringIO()):
            args = p.parse_args(kata[awal + 1:])
            args.rentang_eco = _rentang_eco(args.eco, p) if args.eco else []
    except SystemExit:
        return None
    return args


def main() -> int:
    p = buat_parser()
    args = p.parse_args()

    args.rentang_eco = _rentang_eco(args.eco, p) if args.eco else []
    if args.dangkal and args.profil in PROFIL_DANGKAL:
        p.error("--dangkal dengan profil yang juga dianggap kasar tidak memperdalam apa pun: hasilnya tetap kasar dan partai yang sama terpilih lagi selamanya. Pakai --profil hemat atau lebih teliti.")
    if args.bagian:
        try:
            k, n = (int(x) for x in args.bagian.split("/"))
            if not 0 <= k < n:
                raise ValueError
        except ValueError:
            p.error(f"--bagian harus K/N dengan 0 <= K < N, bukan {args.bagian!r}")

    if not any([args.id, args.sumber, args.pemain, args.min_elo,
                args.gelar_lawan, args.lawan_min_elo, args.terbaru,
                args.hasil, args.format_, args.eco, args.repertoire]):
        p.error("pilih partainya dulu: --id / --sumber / --pemain / --min-elo / --gelar-lawan / "
                "--lawan-min-elo / --terbaru. Tanpa saringan = 47 ribu partai = berhari-hari.")

    # Batch analisis TIDAK mengambil kunci penulis (tiga batch sekaligus boleh),
    # tapi tidak boleh mulai di tengah impor besar.
    if not args.lihat_saja and not args.tanpa_tunggu:
        tunggu_sampai_bebas()

    conn = koneksi(args.db)
    # Batch analisis boleh menunggu lama: lebih baik satu penyimpanan tertahan
    # 2 menit daripada seluruh batch mati karena impor sedang memegang kunci.
    conn.execute("PRAGMA busy_timeout = 120000")
    buat_skema(conn)

    if args.sumber and args.sumber != "gm":
        ada = [r[0] for r in conn.execute(
            "SELECT DISTINCT sumber FROM partai WHERE sumber IS NOT NULL ORDER BY 1")]
        if args.sumber not in ada:
            conn.close()
            p.error(f"sumber {args.sumber!r} tidak ada di database. "
                    f"Yang ada: {', '.join(ada) if ada else '(database kosong)'}")

    daftar = pilih_partai(conn, args)
    profil = PROFIL[args.profil]
    depth = args.depth or profil.depth
    total_ply = sum(p_["jumlah_langkah"] * 2 for p_ in daftar)
    terukur = detik_per_ply_terukur(conn, args.profil)
    per_ply = terukur if terukur else DETIK_PER_PLY_CADANGAN[args.profil]
    perkiraan = total_ply * per_ply * (depth / profil.depth) ** 1.5
    dasar = "dari riwayat" if terukur else "cadangan, belum ada riwayat"

    print(f"{len(daftar)} partai dipilih  |  profil {args.profil}, depth {depth}, "
          f"RAM ~{profil.perkiraan_ram_mb} MB  |  perkiraan {perkiraan / 60:.0f} menit "
          f"({per_ply:.2f} s/ply, {dasar})")
    if not daftar:
        print("Tidak ada yang perlu dianalisis (semua sudah, atau saringan terlalu ketat).")
        conn.close()
        return 0

    if args.lihat_saja:
        for p_ in daftar:
            print("  " + label(p_))
        conn.close()
        return 0

    selesai = 0
    gagal = 0
    mulai = time.time()
    perintah_ini = "analisis_partai.py " + " ".join(sys.argv[1:])
    jam_mulai = time.strftime("%Y-%m-%d %H:%M")
    tulis_kemajuan(0, len(daftar), None, perintah_ini, jam_mulai)

    mogok_beruntun = 0
    perkiraan_sisa = PerkiraanSisa(daftar)
    klaim_aktif = None
    try:
        with Stockfish(profil=profil) as sf:
            nama_engine = sf.nama_engine
            print(f"engine: {nama_engine}\n")

            for i, p_ in enumerate(daftar, start=1):
                lepas_klaim(klaim_aktif)
                klaim_aktif = None
                if not klaim_partai(p_["id"]):
                    print(f"  [{i}/{len(daftar)}] {label(p_)}  sedang dianalisis batch lain, dilewati")
                    continue
                klaim_aktif = p_["id"]
                # Daftar dipilih sekali di awal, padahal batch bisa berjalan
                # berjam-jam sementara pembantu tablet/HP (app/pembantu.py) atau
                # batch lain ikut mengerjakan partai yang sama. Diperiksa ulang
                # tepat sebelum dihitung supaya engine tidak mengulang kerja.
                if not (args.ulang or getattr(args, "dangkal", False)) and conn.execute(
                        "SELECT 1 FROM analisis WHERE partai_id = ?", (p_["id"],)).fetchone():
                    print(f"  [{i}/{len(daftar)}] {label(p_)}  sudah dianalisis di tempat lain, dilewati")
                    continue
                # --dangkal: yang sudah DIPERDALAM di tempat lain (batch lain,
                # pembantu tablet) sejak daftar dipilih juga dilewati. Dulu
                # pemeriksaan ini mati total di mode dangkal (audit 27 Sep).
                if getattr(args, "dangkal", False) and not args.ulang:
                    r = conn.execute("SELECT profil FROM analisis WHERE partai_id = ?",
                                     (p_["id"],)).fetchone()
                    if r and r[0] not in PROFIL_DANGKAL:
                        print(f"  [{i}/{len(daftar)}] {label(p_)}  sudah diperdalam di tempat lain, dilewati")
                        continue
                game = chess.pgn.read_game(io.StringIO(p_["pgn"]))
                if game is None:
                    gagal += 1
                    print(f"  [{i}/{len(daftar)}] {label(p_)}  PGN tidak terbaca")
                    continue

                t0 = time.time()
                try:
                    lap = analisis_partai(game, sf, depth=depth)
                    mogok_beruntun = 0
                except (EngineError, chess.engine.EngineError) as e:
                    # chess.engine.EngineError (termasuk EngineTerminatedError
                    # saat Stockfish mati) BUKAN turunan EngineError milik kita.
                    # Dulu lolos dari sini dan membunuh seluruh batch (12 Sep:
                    # dua batch mati dengan EngineTerminatedError). Sekarang
                    # partai ini dilewati dan engine dinyalakan ulang.
                    gagal += 1
                    mogok_beruntun += 1
                    print(f"  [{i}/{len(daftar)}] {label(p_)}  engine: {e!r}", flush=True)
                    if mogok_beruntun >= MAKS_MOGOK_BERUNTUN:
                        print(f"\nEngine mogok {mogok_beruntun} kali berturut-turut; batch "
                              "dihentikan. Masalahnya di engine, bukan di partai.")
                        break
                    sf.stop()
                    sf.start()
                    continue
                durasi = time.time() - t0

                try:
                    simpan_laporan(conn, p_["id"], lap, nama_engine, args.profil, durasi,
                                   lapor=lambda pesan: print(f"      {pesan}", flush=True))
                except sqlite3.Error as e:
                    # Satu kegagalan simpan tidak boleh membuang sisa batch:
                    # partai ini dilewati, sisanya jalan terus, dan perintah
                    # yang sama bisa diulang untuk menambalnya.
                    gagal += 1
                    print(f"  [{i}/{len(daftar)}] {label(p_)}  gagal disimpan: {e}")
                    continue
                selesai += 1
                perkiraan_sisa.catat(durasi, p_["jumlah_langkah"])

                rp, rh = lap.ringkasan_putih, lap.ringkasan_hitam
                sisa_menit = perkiraan_sisa.menit(i) or 0.0
                print(f"  [{i}/{len(daftar)}] {label(p_)}  "
                      f"{_persen(rp.akurasi)} / {_persen(rh.akurasi)}  "
                      f"?? {rp.blunder + rh.blunder}  {durasi:5.1f}s  sisa ~{sisa_menit:.0f}m")
                tulis_kemajuan(i, len(daftar), sisa_menit, perintah_ini, jam_mulai)
    except KeyboardInterrupt:
        print("\nDihentikan. Yang sudah selesai tetap tersimpan; "
              "jalankan perintah yang sama untuk melanjutkan.")
    finally:
        # Juga saat batch mati karena galat: berkas kemajuan yang tertinggal
        # membuat halaman Mesin menampilkan batch hantu.
        hapus_kemajuan()
        lepas_klaim(klaim_aktif)
    st = statistik_analisis(conn)
    conn.close()
    print()
    print(f"selesai {selesai}, gagal {gagal}, {(time.time() - mulai) / 60:.1f} menit  |  "
          f"database: {st['partai_dianalisis']}/{st['partai_total']} partai dianalisis")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
