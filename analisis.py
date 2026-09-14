"""
Analisis partai catur dari file PGN.

Contoh pemakaian:
    python analisis.py data/pgn/partai.pgn
    python analisis.py data/pgn/partai.pgn --profil normal --depth 18
    python analisis.py data/pgn/partai.pgn --json data/laporan/hasil.json
    python analisis.py data/pgn/banyak.pgn --game 3
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

import chess.pgn

from catur_analysis.engine import PROFIL, Stockfish
from catur_analysis.partai import (
    KLASIFIKASI_ID,
    LaporanPartai,
    analisis_partai,
)

# Penanda teks untuk tiap klasifikasi. Sengaja ASCII supaya tidak berantakan
# di terminal Windows yang encoding-nya bukan UTF-8.
TANDA = {
    "blunder": "??",
    "mistake": "?",
    "inaccuracy": "?!",
    "baik": "  ",
    "terbaik": "!",
    "buku": " b",
}


def format_eval(cp: int, mat: int | None) -> str:
    """
    Evaluasi dalam bentuk yang biasa dipakai di papan analisis.
    Selalu dari sudut pandang PUTIH: nilai positif berarti Putih unggul.
    """
    if mat is not None:
        if mat == 0:
            return "#"          # mat sudah terjadi
        return f"M{mat}" if mat > 0 else f"-M{abs(mat)}"
    return f"{cp / 100:+.2f}"


def cetak_laporan(lap: LaporanPartai, tampilkan_semua: bool) -> None:
    print()
    print("=" * 70)
    print(f"  {lap.putih or '?'}  vs  {lap.hitam or '?'}    {lap.hasil}")
    print("=" * 70)

    baris_meta = [
        ("Event", lap.event),
        ("Tanggal", lap.tanggal),
        ("Babak", lap.babak),
        ("Pembukaan", f"{lap.eco} {lap.pembukaan}".strip()),
        ("Elo", f"{lap.elo_putih or '?'} - {lap.elo_hitam or '?'}"),
        ("Sumber", lap.site),
    ]
    for label, nilai in baris_meta:
        # Metadata yang tidak ada di PGN ditulis apa adanya sebagai "-",
        # tidak dikarang.
        print(f"  {label:<10}: {nilai if nilai else '-'}")
    print(f"  {'Depth':<10}: {lap.depth}")

    # ---- ringkasan pemain ----
    print()
    print("-" * 70)
    print(f"  {'':<22}{'Akurasi':>9}{'Rata loss':>11}{'??':>5}{'?':>5}{'?!':>5}")
    print("-" * 70)
    for r in (lap.ringkasan_putih, lap.ringkasan_hitam):
        if r is None:
            continue
        nama = (r.nama or r.warna.capitalize())[:20]
        print(f"  {nama:<22}{r.akurasi:>8.1f}%{r.rata_kehilangan_cp:>11.1f}"
              f"{r.blunder:>5}{r.mistake:>5}{r.inaccuracy:>5}")

    # ---- turning point ----
    tp = lap.turning_point
    print()
    print("-" * 70)
    if tp is None:
        print("  TURNING POINT : tidak ada langkah yang menggeser peluang menang")
        print("                  lebih dari 15% - partainya berjalan stabil.")
    else:
        titik = "." if tp.warna == "putih" else "..."
        sebelum_putih = (tp.eval_sebelum_cp if tp.warna == "putih"
                         else -tp.eval_sebelum_cp)
        print(f"  TURNING POINT : langkah {tp.nomor_langkah}{titik}{tp.san}")
        print(f"                  eval {format_eval(sebelum_putih, None)} "
              f"-> {format_eval(tp.eval_putih_cp, tp.mat_sesudah)} "
              f"(sudut pandang Putih)")
        if tp.langkah_terbaik_san and tp.langkah_terbaik_san != tp.san:
            print(f"                  seharusnya: {tp.langkah_terbaik_san}")

    # ---- daftar langkah ----
    print()
    print("-" * 70)
    judul = "SEMUA LANGKAH" if tampilkan_semua else "LANGKAH YANG BERMASALAH"
    print(f"  {judul}")
    print("-" * 70)

    ditampilkan = 0
    for l in lap.langkah:
        if not tampilkan_semua and not l.adalah_kesalahan:
            continue
        ditampilkan += 1
        titik = "." if l.warna == "putih" else "..."
        nomor = f"{l.nomor_langkah}{titik}"
        eval_txt = format_eval(l.eval_putih_cp, l.mat_sesudah)
        baris = (f"  {nomor:>7} {l.san:<8} {TANDA[l.klasifikasi]:<3}"
                 f" {eval_txt:>7}")
        if l.adalah_kesalahan and l.langkah_terbaik_san:
            baris += (f"   (-{l.kehilangan_cp} cp, lebih baik: "
                      f"{l.langkah_terbaik_san})")
        print(baris)

    if ditampilkan == 0:
        print("  (tidak ada kesalahan terdeteksi)")

    print()
    print(f"  Keterangan: {' | '.join(f'{TANDA[k].strip()} {v}' for k, v in KLASIFIKASI_ID.items() if TANDA[k].strip())}")


def main() -> int:
    p = argparse.ArgumentParser(description="Analisis partai catur dari PGN")
    p.add_argument("pgn", type=Path, help="file PGN yang mau dianalisis")
    p.add_argument("--profil", choices=sorted(PROFIL), default="normal",
                   help="profil resource engine (default: normal)")
    p.add_argument("--depth", type=int, default=None,
                   help="kedalaman analisis, menimpa setelan profil")
    p.add_argument("--game", type=int, default=1,
                   help="partai ke berapa dalam file PGN (default: 1)")
    p.add_argument("--lewati-pembukaan", type=int, default=0, metavar="PLY",
                   help="lewati N ply pertama, ditandai sebagai teori")
    p.add_argument("--semua", action="store_true",
                   help="tampilkan semua langkah, bukan hanya yang bermasalah")
    p.add_argument("--json", type=Path, default=None,
                   help="simpan hasil lengkap ke file JSON")
    args = p.parse_args()

    if not args.pgn.is_file():
        print(f"File PGN tidak ditemukan: {args.pgn}", file=sys.stderr)
        return 1

    with args.pgn.open(encoding="utf-8", errors="replace") as fh:
        game = None
        for _ in range(args.game):
            game = chess.pgn.read_game(fh)
            if game is None:
                break

    if game is None:
        print(f"Partai ke-{args.game} tidak ada di file itu.", file=sys.stderr)
        return 1

    jumlah_langkah = sum(1 for _ in game.mainline_moves())
    if jumlah_langkah == 0:
        print("Partai ini tidak punya langkah.", file=sys.stderr)
        return 1

    profil = PROFIL[args.profil]
    depth = args.depth or profil.depth

    print(f"Menganalisis {jumlah_langkah} langkah pada depth {depth} "
          f"(profil {profil.nama}, perkiraan RAM ~{profil.perkiraan_ram_mb} MB)...")

    mulai = time.time()
    with Stockfish(profil=profil) as sf:
        laporan = analisis_partai(game, sf, depth=depth,
                                  lewati_langkah_awal=args.lewati_pembukaan)
    durasi = time.time() - mulai

    cetak_laporan(laporan, tampilkan_semua=args.semua)
    print(f"\n  Selesai dalam {durasi:.1f} detik "
          f"({durasi / jumlah_langkah:.2f} detik per langkah)")

    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        with args.json.open("w", encoding="utf-8") as fh:
            json.dump(laporan.ke_dict(), fh, indent=2, ensure_ascii=False)
        print(f"  JSON tersimpan: {args.json}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
