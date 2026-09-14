"""
Verifikasi Stockfish 18: identitas, pemakaian RAM nyata, hasil analisis,
dan memastikan prosesnya benar-benar mati setelah selesai.

Jalankan:  python cek_engine.py
"""

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

import chess

from catur_analysis.engine import DALAM, HEMAT, NORMAL, Stockfish, info_engine


def ram_proses_mb(pid):
    """
    Baca pemakaian RAM sebuah proses lewat tasklist bawaan Windows.
    Dipakai supaya tidak perlu library tambahan seperti psutil.
    """
    if pid is None:
        return None
    try:
        keluaran = subprocess.check_output(
            ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
            text=True,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        return None

    if "No tasks" in keluaran or not keluaran.strip():
        return None

    # Kolom terakhir CSV berisi "12.345 K"
    kolom = keluaran.strip().split('","')
    if len(kolom) < 5:
        return None
    angka = kolom[-1].strip('"\r\n ').replace(" K", "").replace(".", "").replace(",", "")
    try:
        return round(int(angka) / 1024, 1)
    except ValueError:
        return None


def hitung_stockfish_jalan():
    """Berapa proses stockfish.exe yang sedang hidup di sistem."""
    try:
        keluaran = subprocess.check_output(
            ["tasklist", "/FI", "IMAGENAME eq stockfish.exe", "/FO", "CSV", "/NH"],
            text=True,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        return -1
    if "No tasks" in keluaran or not keluaran.strip():
        return 0
    return len([b for b in keluaran.strip().splitlines() if b.strip()])


def garis(judul):
    print()
    print("=" * 62)
    print(judul)
    print("=" * 62)


def main():
    garis("1. IDENTITAS ENGINE")
    info = info_engine()
    for kunci, nilai in info.items():
        print(f"  {kunci:10}: {nilai}")

    garis("2. PROSES SEBELUM MULAI")
    print(f"  stockfish.exe yang jalan: {hitung_stockfish_jalan()}")

    garis("3. ANALISIS POSISI AWAL (profil HEMAT)")
    with Stockfish(profil=HEMAT) as sf:
        papan = chess.Board()
        hasil = sf.evaluasi(papan)[0]

        pid = sf.pid()
        ram = ram_proses_mb(pid)

        print(f"  profil     : {sf.profil.nama} "
              f"(Hash {sf.profil.hash_mb} MB, {sf.profil.threads} thread, "
              f"depth {sf.profil.depth})")
        print(f"  PID engine : {pid}")
        print(f"  RAM nyata  : {ram} MB  (perkiraan: {sf.profil.perkiraan_ram_mb} MB)")
        print(f"  evaluasi   : {hasil['cp']} cp pada depth {hasil['depth']}")
        print(f"  variasi    : {' '.join(hasil['pv'][:8])}")

    garis("4. POSISI TAKTIS - CEK ENGINE MENEMUKAN LANGKAH BENAR")
    # Legal's Mate: Putih jalan, ada mat dalam 2 langkah.
    fen_mat = "r1bqkbnr/pppp1ppp/2n5/4p3/2B1P3/5Q2/PPPP1PPP/RNB1K1NR w KQkq - 4 4"
    with Stockfish(profil=HEMAT) as sf:
        papan = chess.Board(fen_mat)
        hasil = sf.evaluasi(papan, depth=12)[0]
        print(f"  posisi   : {fen_mat}")
        print(f"  mate     : {hasil['mate']} (angka positif = Putih yang mat)")
        print(f"  variasi  : {' '.join(hasil['pv'][:4])}")
        cocok = hasil["mate"] is not None and hasil["mate"] > 0
        print(f"  hasil    : {'OK - mat terdeteksi' if cocok else 'GAGAL'}")

    garis("5. RAM PROFIL NORMAL - CEK MODEL PERKIRAAN")
    with Stockfish(profil=NORMAL) as sf:
        sf.evaluasi(chess.Board(), depth=12)
        ram = ram_proses_mb(sf.pid())
        perkiraan = sf.profil.perkiraan_ram_mb
        selisih = round(ram - perkiraan, 1) if ram else None
        print(f"  Hash 128 MB -> RAM nyata {ram} MB, perkiraan {perkiraan} MB, "
              f"selisih {selisih} MB")

    garis("6. MULTIPV - TIGA LANGKAH TERBAIK")
    with Stockfish(profil=HEMAT) as sf:
        papan = chess.Board()
        papan.push_san("e4")
        for i, baris in enumerate(sf.evaluasi(papan, depth=14, multipv=3), start=1):
            langkah = baris["pv"][0] if baris["pv"] else "?"
            print(f"  {i}. {langkah:6} {baris['cp']:+5} cp   "
                  f"lanjutan: {' '.join(baris['pv'][1:5])}")

    garis("7. PENOLAKAN PENGATURAN BERLEBIHAN")
    from catur_analysis.engine import EngineError, ProfilResource

    boros = ProfilResource("boros", hash_mb=4096, threads=32, depth=30)
    try:
        Stockfish(profil=boros)
        print("  GAGAL - pengaturan boros tidak ditolak")
    except EngineError as e:
        print(f"  OK - ditolak: {e}")

    garis("8. PROSES SETELAH SELESAI")
    sisa = hitung_stockfish_jalan()
    print(f"  stockfish.exe yang jalan: {sisa}")
    print(f"  hasil: {'OK - tidak ada proses tertinggal' if sisa == 0 else 'PERINGATAN - ada proses tersisa'}")

    garis("PROFIL YANG TERSEDIA")
    for p in (HEMAT, NORMAL, DALAM):
        print(f"  {p.nama:8} Hash {p.hash_mb:4} MB | {p.threads} thread | "
              f"depth {p.depth:2} | perkiraan RAM ~{p.perkiraan_ram_mb} MB")


if __name__ == "__main__":
    main()
