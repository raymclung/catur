"""
Cadangkan database ke data/backup/.

Kenapa perlu: data/catur.db tidak masuk git (307 MB, turunan), tapi isinya
tidak semuanya murah dibuat ulang. Partai bisa diimpor ulang dalam setengah
jam; hasil analisis engine (tabel analisis/analisis_langkah) butuh ~40 detik
per partai Stockfish. Sekali salinan mingguan jauh lebih murah daripada
menghitung ulang.

Cara: backup API SQLite (Connection.backup), bukan menyalin file. Menyalin
file saat server hidup bisa menghasilkan salinan rusak karena WAL; backup API
membaca snapshot yang konsisten walau ada penulis lain.

Contoh:
    python cadangkan_db.py                # cadangkan, simpan 5 terakhir
    python cadangkan_db.py --simpan 10
    python cadangkan_db.py --daftar       # lihat cadangan yang ada
"""

from __future__ import annotations

import argparse
import sqlite3
import time
from datetime import datetime
from pathlib import Path

from app.db import PATH_DB

AKAR = Path(__file__).resolve().parent
FOLDER_BACKUP = AKAR / "data" / "backup"
POLA = "catur-*.db"
SIMPAN_DEFAULT = 5


def nama_cadangan(waktu: datetime | None = None) -> str:
    return "catur-" + (waktu or datetime.now()).strftime("%Y%m%d-%H%M%S") + ".db"


def daftar_cadangan(folder: Path = FOLDER_BACKUP) -> list[Path]:
    """Cadangan yang ada, dari yang paling lama ke yang paling baru."""
    return sorted(folder.glob(POLA)) if folder.is_dir() else []


def cadangkan(sumber: Path = PATH_DB, folder: Path = FOLDER_BACKUP,
              simpan: int = SIMPAN_DEFAULT, periksa: bool = True) -> Path:
    """
    Buat satu cadangan, periksa integritasnya, lalu buang yang paling lama
    sampai tersisa `simpan` berkas. Mengembalikan path cadangan baru.
    Melempar RuntimeError kalau salinan tidak lolos pemeriksaan — cadangan
    yang rusak lebih berbahaya daripada tidak ada, karena memberi rasa aman.
    """
    if not Path(sumber).is_file():
        raise FileNotFoundError(f"database tidak ada: {sumber}")
    folder.mkdir(parents=True, exist_ok=True)
    tujuan = folder / nama_cadangan()
    sementara = tujuan.with_suffix(".part")

    asal = sqlite3.connect(f"file:{Path(sumber).as_posix()}?mode=ro", uri=True)
    salinan = sqlite3.connect(sementara)
    try:
        asal.backup(salinan, pages=4096)
        if periksa:
            hasil = salinan.execute("PRAGMA integrity_check").fetchone()[0]
            if hasil != "ok":
                raise RuntimeError(f"cadangan tidak lolos integrity_check: {hasil}")
    finally:
        salinan.close()
        asal.close()

    sementara.replace(tujuan)      # nama final hanya setelah utuh dan lolos periksa

    lama = daftar_cadangan(folder)
    for berkas in lama[:-simpan] if simpan > 0 else []:
        berkas.unlink()
    return tujuan


def main() -> int:
    p = argparse.ArgumentParser(description="Cadangkan database ke data/backup/")
    p.add_argument("--db", type=Path, default=PATH_DB)
    p.add_argument("--folder", type=Path, default=FOLDER_BACKUP)
    p.add_argument("--simpan", type=int, default=SIMPAN_DEFAULT,
                   help=f"jumlah cadangan yang dipertahankan (default {SIMPAN_DEFAULT})")
    p.add_argument("--tanpa-periksa", action="store_true",
                   help="lewati PRAGMA integrity_check (lebih cepat, kurang aman)")
    p.add_argument("--daftar", action="store_true", help="tampilkan cadangan yang ada saja")
    args = p.parse_args()

    if args.daftar:
        semua = daftar_cadangan(args.folder)
        if not semua:
            print(f"Belum ada cadangan di {args.folder}")
            return 0
        for b in semua:
            print(f"  {b.stat().st_size / 1048576:7.1f} MB  {b.name}")
        return 0

    mulai = time.time()
    tujuan = cadangkan(args.db, args.folder, args.simpan, periksa=not args.tanpa_periksa)
    print(f"Cadangan : {tujuan}")
    print(f"Ukuran   : {tujuan.stat().st_size / 1048576:.1f} MB")
    print(f"Periksa  : {'dilewati' if args.tanpa_periksa else 'integrity_check ok'}")
    print(f"Waktu    : {time.time() - mulai:.1f} detik")
    print(f"Tersisa  : {len(daftar_cadangan(args.folder))} cadangan (maks {args.simpan})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
