"""
Bangun indeks posisi untuk penjelajah pembukaan.

Satu-satunya yang menulis tabel `posisi`. Bisa dihentikan (Ctrl+C) dan
dilanjutkan: partai yang sudah punya baris posisi dilewati.

Contoh:
    python indeks_posisi.py                 # semua partai yang belum terindeks
    python indeks_posisi.py --batas 1000    # coba dulu sebagian
    python indeks_posisi.py --maks-ply 30
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from app.db import PATH_DB, buat_skema, koneksi
from app.kunci_penulis import KunciPenulis, KunciTerpakai, penulis_berjalan
from app.posisi import (MAKS_PLY_DEFAULT, id_belum_terindeks, indeks_partai,
                        statistik_indeks)


def main() -> int:
    """Indeks = penulis besar: ambil kunci dulu (CLAUDE.md bagian 7)."""
    lain = penulis_berjalan()
    if lain:
        print("Penulis besar lain sedang berjalan, indeks tidak dijalankan:", file=sys.stderr)
        for b in lain:
            print("  ..." + b, file=sys.stderr)
        return 1
    try:
        with KunciPenulis("indeks_posisi.py"):
            return _indeks()
    except KunciTerpakai as e:
        print(e, file=sys.stderr)
        return 1


def _indeks() -> int:
    p = argparse.ArgumentParser(description="Bangun indeks posisi pembukaan")
    p.add_argument("--batas", type=int, default=0, help="maksimal partai (0 = semua)")
    p.add_argument("--maks-ply", type=int, default=MAKS_PLY_DEFAULT,
                   help=f"ply pertama yang diindeks per partai (default {MAKS_PLY_DEFAULT})")
    p.add_argument("--db", type=Path, default=PATH_DB)
    args = p.parse_args()

    conn = koneksi(args.db)
    buat_skema(conn)

    ids = id_belum_terindeks(conn, args.batas)
    st = statistik_indeks(conn)
    print(f"terindeks {st['partai_terindeks']}/{st['partai_total']} partai, "
          f"{st['baris_posisi']} baris  |  akan diproses: {len(ids)}")
    if not ids:
        conn.close()
        return 0

    mulai = time.time()
    baris = 0
    selesai = 0
    try:
        for i, pid in enumerate(ids, start=1):
            pgn = conn.execute("SELECT pgn FROM partai WHERE id = ?", (pid,)).fetchone()[0]
            baris += indeks_partai(conn, pid, pgn, args.maks_ply)
            selesai += 1
            if i % 500 == 0:
                conn.commit()
                laju = i / (time.time() - mulai)
                print(f"  {i}/{len(ids)}  {baris} baris  {laju:.0f} partai/detik  "
                      f"sisa ~{(len(ids) - i) / laju / 60:.1f} menit")
    except KeyboardInterrupt:
        print("\nDihentikan; yang sudah selesai tersimpan.")
    conn.commit()

    st = statistik_indeks(conn)
    conn.close()
    print(f"\nselesai {selesai} partai, {baris} baris dalam {(time.time() - mulai) / 60:.1f} menit")
    print(f"indeks: {st['partai_terindeks']}/{st['partai_total']} partai, {st['baris_posisi']} baris")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
