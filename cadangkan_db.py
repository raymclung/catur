"""
Cadangkan database ke data/backup/.

Kenapa perlu: data/catur.db tidak masuk git (307 MB, turunan), tapi isinya
tidak semuanya murah dibuat ulang. Partai bisa diimpor ulang dalam setengah
jam; hasil analisis engine (tabel analisis/analisis_langkah) butuh ~40 detik
per partai Stockfish. Sekali salinan mingguan jauh lebih murah daripada
menghitung ulang.

Cara: `VACUUM INTO`, bukan menyalin berkas dan bukan `Connection.backup()`.
Menyalin berkas saat server hidup bisa menghasilkan salinan rusak karena WAL.
`backup()` pernah dipakai di sini, tapi ia MENGULANG DARI AWAL setiap kali
sumbernya ditulis — pada database 4,8 GB dengan batch engine berjalan ia tidak
pernah selesai (diukur 25 Sep 2026). `VACUUM INTO` membaca satu snapshot di
dalam transaksi baca, tidak pernah mengulang, dan ikut memadatkan berkasnya.

Contoh:
    python cadangkan_db.py                # cadangkan, simpan 5 terakhir
    python cadangkan_db.py --simpan 10
    python cadangkan_db.py --daftar       # lihat cadangan yang ada
    python cadangkan_db.py --inti         # ekspor inti + salin ke folder OneDrive
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import sqlite3
import time
from datetime import datetime
from pathlib import Path

from app.db import FOLDER_DATA, PATH_DB

AKAR = Path(__file__).resolve().parent
FOLDER_BACKUP = FOLDER_DATA / "backup"
POLA = "catur-*.db"
# `POLA` saja juga cocok dengan `catur-inti-*.db` (ekspor inti --tanpa-gz di
# folder yang sama), dan karena "catur-inti" > "catur-2026..." berkas inti
# dianggap cadangan TERBARU: rotasi lalu membuang cadangan penuh, termasuk
# yang baru saja dibuat (audit 28 Sep 2026). Hanya nama bercap waktu murni.
NAMA_CADANGAN = re.compile(r"catur-\d{8}-\d{6}\.db")
SIMPAN_DEFAULT = 5

# Sisa ruang minimal di drive cadangan. Rotasi "simpan N terakhir" saja tidak
# cukup: database tumbuh ±90 MB/hari, jadi N berkas hari ini lebih kecil
# daripada N berkas bulan depan, dan drive bisa penuh tanpa peringatan
# (audit 25 Sep 2026). Kalau sisa ruang di bawah ini, cadangan TERLAMA
# dibuang lebih dulu — satu cadangan terbaru tidak pernah ikut dibuang.
SISAKAN_GB_DEFAULT = 20.0

# Folder cloud untuk ekspor inti. Klien OneDrive mengunggah apa pun yang
# ditaruh di bawah folder ini, jadi "mengunggah" = menyalin ke sini. Path-nya
# dibaca dari variabel lingkungan yang dipasang klien OneDrive sendiri
# (CLAUDE.md bagian 6: tanpa path Windows yang dikeraskan). Kalau variabelnya
# tidak ada (mesin lain, CI), tidak ada yang disalin dan itu dilaporkan.
FOLDER_CLOUD = (Path(os.environ["OneDrive"]) / "catur-cadangan"
                if os.environ.get("OneDrive") else None)
POLA_INTI = "catur-inti-*.db.gz"
SIMPAN_INTI_DEFAULT = 4


def nama_cadangan(waktu: datetime | None = None) -> str:
    return "catur-" + (waktu or datetime.now()).strftime("%Y%m%d-%H%M%S") + ".db"


def daftar_cadangan(folder: Path = FOLDER_BACKUP) -> list[Path]:
    """Cadangan yang ada, dari yang paling lama ke yang paling baru."""
    if not folder.is_dir():
        return []
    return sorted(b for b in folder.glob(POLA) if NAMA_CADANGAN.fullmatch(b.name))


def sisa_gb(folder: Path) -> float:
    """Sisa ruang drive tempat folder itu berada, dalam GB."""
    import shutil
    acuan = folder if folder.exists() else folder.parent
    return shutil.disk_usage(acuan).free / 2**30


def drive_sama(a: Path, b: Path) -> bool:
    """Dua path di drive/perangkat yang sama? Cadangan di drive yang sama
    dengan databasenya tidak melindungi dari drive rusak."""
    try:
        if os.name == "nt":
            return Path(a).resolve().drive.upper() == Path(b).resolve().drive.upper()
        return Path(a).resolve().stat().st_dev == Path(b).resolve().stat().st_dev
    except OSError:
        return False


def rapikan(folder: Path, simpan: int, sisakan_gb: float = 0.0) -> list[Path]:
    """
    Buang cadangan terlama sampai (a) tersisa `simpan` berkas, dan (b) sisa
    ruang drive minimal `sisakan_gb`. Yang paling baru tidak pernah dibuang.
    Kembalikan daftar berkas yang dihapus.
    """
    dibuang = []
    lama = daftar_cadangan(folder)
    for berkas in (lama[:-simpan] if simpan > 0 else []):
        berkas.unlink()
        dibuang.append(berkas)
    if sisakan_gb > 0:
        sisa = daftar_cadangan(folder)
        while len(sisa) > 1 and sisa_gb(folder) < sisakan_gb:
            tertua = sisa.pop(0)
            tertua.unlink()
            dibuang.append(tertua)
    return dibuang


def cadangkan(sumber: Path = PATH_DB, folder: Path = FOLDER_BACKUP,
              simpan: int = SIMPAN_DEFAULT, periksa: bool = True,
              sisakan_gb: float = 0.0) -> Path:
    """
    Buat satu cadangan, periksa integritasnya, lalu buang yang paling lama
    sampai tersisa `simpan` berkas. Mengembalikan path cadangan baru.
    Melempar RuntimeError kalau salinan tidak lolos pemeriksaan — cadangan
    yang rusak lebih berbahaya daripada tidak ada, karena memberi rasa aman.

    Memakai `VACUUM INTO`, bukan `Connection.backup()` (diganti 25 Sep 2026).
    Alasannya terukur: `backup()` menyalin halaman sambil membaca, dan kalau
    sumbernya berubah di tengah jalan SQLite **mengulang dari halaman
    pertama**. Pada database 4,8 GB dengan dua batch engine yang menulis tiap
    ±30 detik, itu berarti tidak pernah selesai — diukur: 19,5 detik CPU per
    30 detik selama 6 menit, berkas tujuan mandek di 1,6 GB. Cadangan yang
    tidak pernah selesai persis sama bahayanya dengan cadangan rusak, dan
    justru gagal pada saat paling dibutuhkan: ketika database sedang sibuk.

    `VACUUM INTO` berjalan di dalam satu transaksi baca, jadi ia melihat satu
    snapshot yang konsisten dan tidak pernah mengulang. Di mode WAL pembaca
    tidak memblokir penulis, jadi batch engine tetap jalan. Bonusnya berkasnya
    ikut dipadatkan. Butuh SQLite 3.27+ (di mesin ini 3.49.1).
    """
    if not Path(sumber).is_file():
        raise FileNotFoundError(f"database tidak ada: {sumber}")
    folder.mkdir(parents=True, exist_ok=True)
    tujuan = folder / nama_cadangan()
    sementara = tujuan.with_suffix(".part")
    # VACUUM INTO menolak menulis ke berkas yang sudah ada. Sisa dari
    # percobaan yang gagal karena itu harus dibuang lebih dulu.
    sementara.unlink(missing_ok=True)
    Path(str(sementara) + "-journal").unlink(missing_ok=True)

    asal = sqlite3.connect(f"file:{Path(sumber).as_posix()}?mode=ro", uri=True)
    try:
        asal.execute("VACUUM INTO ?", (str(sementara),))
    finally:
        asal.close()

    if periksa:
        salinan = sqlite3.connect(sementara)
        try:
            hasil = salinan.execute("PRAGMA integrity_check").fetchone()[0]
        finally:
            salinan.close()
        if hasil != "ok":
            sementara.unlink(missing_ok=True)
            raise RuntimeError(f"cadangan tidak lolos integrity_check: {hasil}")

    sementara.replace(tujuan)      # nama final hanya setelah utuh dan lolos periksa

    rapikan(folder, simpan, sisakan_gb)
    return tujuan


# ---------------------------------------------------------------- inti
#
# Ray memilih cloud (25 Sep 2026), tapi mengunggah 4,5 GB tiap pekan tidak
# masuk akal: OneDrive gratis 5 GB, databasenya tumbuh ±90 MB/hari, dan
# unggahannya berjam-jam. Yang benar-benar TIDAK BISA dibuat ulang jauh lebih
# kecil. Yang sengaja DITINGGAL di ekspor ini, beserta alasannya:
#   * partai GM (TWIC/PGN Mentor/broadcast) — bisa diunduh lagi dari sumbernya
#   * tabel `posisi` (indeks pembukaan)     — dibangun ulang `indeks_posisi.py`
#   * analisis partai GM                    — mahal (berminggu-minggu engine),
#                                             tapi tetap bisa dihitung ulang
# Yang DIBAWA: partai sendiri beserta analisisnya (unduhan Lichess/Chess.com
# bisa hilang kalau akunnya ditutup), catatan latihan (tidak ada sumber lain
# sama sekali), posisi tersimpan, dan jejak sumber_impor.
TABEL_INTI = ("pemain", "turnamen", "partai", "analisis", "analisis_langkah",
              "posisi_tersimpan", "latihan_jawaban", "sumber_impor")


def ekspor_inti(sumber: Path = PATH_DB, folder: Path = FOLDER_BACKUP,
                mampatkan: bool = True) -> Path:
    """
    Ekspor bagian yang tidak bisa dibuat ulang ke berkas kecil (opsional .gz).

    Ukurannya cukup untuk diunggah ke cloud tiap pekan, dan hasilnya tetap
    database SQLite biasa: buka dengan aplikasi ini juga bisa.
    """
    from app.db import buat_skema

    folder.mkdir(parents=True, exist_ok=True)
    tujuan = folder / ("catur-inti-" + datetime.now().strftime("%Y%m%d-%H%M%S") + ".db")
    if tujuan.exists():
        tujuan.unlink()

    # uri=True perlu supaya ATTACH di bawah boleh memakai "file:...?mode=ro"
    # (tanpa itu SQLite membaca seluruh URI sebagai nama berkas).
    conn = sqlite3.connect(f"file:{tujuan.as_posix()}", uri=True)
    buat_skema(conn)
    conn.execute("PRAGMA foreign_keys = OFF")     # urutan salin tidak perlu dipikirkan
    conn.execute("ATTACH DATABASE ? AS asal", (f"file:{Path(sumber).as_posix()}?mode=ro",))
    milik = ("SELECT id FROM asal.partai g WHERE EXISTS "
             "(SELECT 1 FROM asal.pemain p WHERE p.akun_pribadi = 1 "
             " AND p.id IN (g.putih_id, g.hitam_id))")
    saring = {
        "partai": f"WHERE id IN ({milik})",
        "analisis": f"WHERE partai_id IN ({milik})",
        "analisis_langkah": f"WHERE partai_id IN ({milik})",
        "turnamen": f"WHERE id IN (SELECT turnamen_id FROM asal.partai WHERE id IN ({milik}))",
    }
    for tabel in TABEL_INTI:
        # JANGAN "SELECT *": urutan kolom di database lama BERBEDA dari skema
        # baru — `akun_pribadi` dan `format` dulu ditambahkan lewat ALTER TABLE
        # jadi menempel di belakang. Tersalin dengan SELECT *, nilainya masuk ke
        # kolom yang salah dan langsung kena CHECK (terbukti 25 Sep 2026).
        # Kolomnya dicocokkan per nama, dan yang tidak ada di sumber dibiarkan
        # memakai nilai bawaan skema.
        kolom_baru = [r[1] for r in conn.execute(f"PRAGMA main.table_info({tabel})")]
        kolom_asal = {r[1] for r in conn.execute(f"PRAGMA asal.table_info({tabel})")}
        pakai = [k for k in kolom_baru if k in kolom_asal]
        daftar = ", ".join(f'"{k}"' for k in pakai)
        conn.execute(f"INSERT INTO main.{tabel} ({daftar}) SELECT {daftar} "
                     f"FROM asal.{tabel} " + saring.get(tabel, ""))
    # Posisi tersimpan disalin semua, tapi partainya hanya partai sendiri:
    # posisi dari partai GM akan menunjuk partai yang tidak ikut. Skema aslinya
    # memang ON DELETE SET NULL (posisinya tetap, asalnya lepas), jadi di sini
    # diperlakukan sama (audit 28 Sep 2026: foreign_key_check tidak kosong).
    conn.execute("UPDATE main.posisi_tersimpan SET partai_id = NULL WHERE partai_id "
                 "IS NOT NULL AND partai_id NOT IN (SELECT id FROM main.partai)")
    conn.commit()
    conn.execute("DETACH DATABASE asal")
    conn.execute("PRAGMA foreign_keys = ON")
    rusak = conn.execute("PRAGMA integrity_check").fetchone()[0]
    menggantung = conn.execute("PRAGMA foreign_key_check").fetchall()
    if rusak != "ok" or menggantung:
        conn.close()
        raise RuntimeError(f"ekspor inti tidak lolos pemeriksaan: integrity={rusak}, "
                           f"foreign_key_check={menggantung[:5]}")
    conn.execute("VACUUM")
    conn.close()

    if not mampatkan:
        return tujuan
    import gzip
    import shutil as _sh
    gz = tujuan.with_suffix(".db.gz")
    with open(tujuan, "rb") as masuk, gzip.open(gz, "wb", compresslevel=6) as keluar:
        _sh.copyfileobj(masuk, keluar, length=1024 * 1024)
    tujuan.unlink()
    return gz


def buang_inti_lama(folder: Path, simpan: int = SIMPAN_INTI_DEFAULT) -> list[Path]:
    """Sisakan `simpan` ekspor inti terbaru di folder itu (0 = simpan semua)."""
    if simpan <= 0:
        return []
    # Nama memuat cap waktu, jadi urutan nama = urutan waktu.
    lama = sorted(folder.glob(POLA_INTI))[:-simpan]
    for b in lama:
        b.unlink()
    return lama


def salin_ke_cloud(berkas: Path, folder: Path, simpan: int = SIMPAN_INTI_DEFAULT) -> Path:
    """
    Salin ekspor inti ke folder yang disinkronkan klien cloud, buang yang lama.

    Disalin ke nama sementara lalu di-rename: klien OneDrive mulai mengunggah
    begitu berkas muncul, dan berkas yang masih ditulis bisa terunggah
    setengah. Ukuran dicocokkan sebelum rename supaya salinan yang terpotong
    (drive penuh) tidak pernah tampak sebagai cadangan yang sah.

    Bukti selesai yang bisa diperiksa orang lain: berkas dengan nama ini ada
    di folder cloud, ukurannya sama dengan sumbernya.
    """
    folder.mkdir(parents=True, exist_ok=True)
    tujuan = folder / berkas.name
    sementara = folder / (berkas.name + ".part")
    shutil.copyfile(berkas, sementara)
    if sementara.stat().st_size != berkas.stat().st_size:
        sementara.unlink()
        raise RuntimeError(f"salinan ke {folder} terpotong (drive penuh?)")
    os.replace(sementara, tujuan)
    buang_inti_lama(folder, simpan)
    return tujuan


def main() -> int:
    p = argparse.ArgumentParser(description="Cadangkan database ke data/backup/")
    p.add_argument("--db", type=Path, default=PATH_DB)
    p.add_argument("--folder", type=Path, default=FOLDER_BACKUP)
    p.add_argument("--simpan", type=int, default=SIMPAN_DEFAULT,
                   help=f"jumlah cadangan yang dipertahankan (default {SIMPAN_DEFAULT})")
    p.add_argument("--sisakan-gb", type=float, default=SISAKAN_GB_DEFAULT,
                   help=f"buang cadangan terlama kalau sisa ruang drive di bawah ini "
                        f"(default {SISAKAN_GB_DEFAULT:.0f} GB; 0 = matikan)")
    p.add_argument("--tanpa-periksa", action="store_true",
                   help="lewati PRAGMA integrity_check (lebih cepat, kurang aman)")
    p.add_argument("--daftar", action="store_true", help="tampilkan cadangan yang ada saja")
    p.add_argument("--inti", action="store_true",
                   help="ekspor kecil berisi yang tidak bisa dibuat ulang (partai sendiri + "
                        "analisisnya, catatan latihan, posisi tersimpan) — untuk diunggah ke cloud")
    p.add_argument("--tanpa-gz", action="store_true", help="--inti: jangan dimampatkan")
    p.add_argument("--cloud", type=Path, default=FOLDER_CLOUD, metavar="FOLDER",
                   help="--inti: salin hasilnya ke folder ini (bawaan: folder OneDrive; "
                        "kalau OneDrive tidak ada, tidak disalin)")
    p.add_argument("--tanpa-cloud", action="store_true", help="--inti: jangan salin ke cloud")
    p.add_argument("--simpan-inti", type=int, default=SIMPAN_INTI_DEFAULT,
                   help=f"--inti: ekspor inti yang dipertahankan, lokal maupun cloud "
                        f"(default {SIMPAN_INTI_DEFAULT})")
    args = p.parse_args()

    if args.inti:
        mulai = time.time()
        hasil = ekspor_inti(args.db, args.folder, mampatkan=not args.tanpa_gz)
        print(f"Ekspor inti : {hasil}")
        print(f"Ukuran      : {hasil.stat().st_size / 1048576:.1f} MB "
              f"(database penuh {Path(args.db).stat().st_size / 1073741824:.1f} GB)")
        print(f"Waktu       : {time.time() - mulai:.0f} detik")
        buang_inti_lama(args.folder, args.simpan_inti)
        if args.tanpa_cloud or not args.cloud or args.tanpa_gz:
            print("Salin berkas ini ke folder cloud (OneDrive/Google Drive).")
            return 0
        di_cloud = salin_ke_cloud(hasil, args.cloud, args.simpan_inti)
        print(f"Cloud       : {di_cloud} ({di_cloud.stat().st_size / 1048576:.1f} MB)")
        return 0

    if args.daftar:
        semua = daftar_cadangan(args.folder)
        if not semua:
            print(f"Belum ada cadangan di {args.folder}")
            return 0
        for b in semua:
            print(f"  {b.stat().st_size / 1048576:7.1f} MB  {b.name}")
        return 0

    if drive_sama(args.db, args.folder):
        print(f"PERINGATAN: cadangan berada di drive yang SAMA dengan databasenya "
              f"({Path(args.db).resolve().drive or 'drive yang sama'}). Aman dari kesalahan "
              f"tulis dan hapus, TIDAK aman dari drive rusak.")

    mulai = time.time()
    tujuan = cadangkan(args.db, args.folder, args.simpan, periksa=not args.tanpa_periksa,
                       sisakan_gb=args.sisakan_gb)
    print(f"Cadangan : {tujuan}")
    print(f"Ukuran   : {tujuan.stat().st_size / 1048576:.1f} MB")
    print(f"Periksa  : {'dilewati' if args.tanpa_periksa else 'integrity_check ok'}")
    print(f"Waktu    : {time.time() - mulai:.1f} detik")
    print(f"Tersisa  : {len(daftar_cadangan(args.folder))} cadangan (maks {args.simpan})")
    print(f"Sisa ruang: {sisa_gb(args.folder):.0f} GB"
          + (f" (batas {args.sisakan_gb:.0f} GB)" if args.sisakan_gb else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
