"""
Saring partai berdasarkan kekuatan lawan.

Tujuannya menjawab pertanyaan yang konkret: apa yang berbeda ketika lawannya
jauh lebih kuat? Kalau kesalahan muncul jauh lebih awal di partai-partai itu,
penyebabnya kemungkinan besar persiapan pembukaan, bukan kemampuan main.

Contoh pemakaian:
    python saring_partai.py --min-rating-lawan 2800
    python saring_partai.py --min-selisih 250
    python saring_partai.py --min-selisih 250 --keluaran lawan-kuat
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from ambil_partai import AKUN, FOLDER_PGN

AKAR = Path(__file__).resolve().parent

# Semua username milik Ray, huruf kecil, untuk mencocokkan header PGN.
USERNAME_SAYA = {a.username.lower() for a in AKUN}

POLA_TAG = re.compile(r'^\[(\w+)\s+"(.*)"\]\s*$')


def baca_partai(teks: str):
    """
    Pisahkan teks PGN jadi partai satu per satu.

    Sengaja tidak memakai chess.pgn di sini: kita cuma perlu membaca header,
    dan mem-parse seluruh langkah untuk ribuan partai jauh lebih lambat.
    """
    partai = []
    baris_sekarang: list[str] = []
    sudah_ada_langkah = False

    for baris in teks.splitlines():
        kosong = not baris.strip()
        adalah_tag = baris.lstrip().startswith("[")

        # Tag baru setelah bagian langkah berarti partai berikutnya dimulai.
        if adalah_tag and sudah_ada_langkah:
            partai.append("\n".join(baris_sekarang).strip())
            baris_sekarang = []
            sudah_ada_langkah = False

        baris_sekarang.append(baris)
        if not adalah_tag and not kosong:
            sudah_ada_langkah = True

    if baris_sekarang:
        sisa = "\n".join(baris_sekarang).strip()
        if sisa:
            partai.append(sisa)
    return partai


def ambil_tag(teks_partai: str) -> dict:
    tag = {}
    for baris in teks_partai.splitlines():
        m = POLA_TAG.match(baris)
        if m:
            tag[m.group(1)] = m.group(2)
        elif tag and baris.strip() and not baris.lstrip().startswith("["):
            break
    return tag


def ke_int(nilai: str) -> int | None:
    """Rating bisa berisi '?', kosong, atau tanda lain. Kembalikan None kalau
    tidak bisa dibaca — jangan menebak angkanya."""
    if not nilai:
        return None
    m = re.match(r"^\s*(\d{3,4})", nilai)
    return int(m.group(1)) if m else None


def info_partai(tag: dict) -> dict | None:
    """
    Tentukan sisi mana yang dimainkan Ray, lalu ambil rating kedua pihak.
    Mengembalikan None kalau tidak bisa dipastikan — lebih baik dilewati
    daripada salah hitung.
    """
    putih = tag.get("White", "").lower()
    hitam = tag.get("Black", "").lower()

    if putih in USERNAME_SAYA and hitam not in USERNAME_SAYA:
        warna_saya = "putih"
    elif hitam in USERNAME_SAYA and putih not in USERNAME_SAYA:
        warna_saya = "hitam"
    else:
        return None

    rating_saya = ke_int(tag.get("WhiteElo" if warna_saya == "putih" else "BlackElo", ""))
    rating_lawan = ke_int(tag.get("BlackElo" if warna_saya == "putih" else "WhiteElo", ""))
    if rating_saya is None or rating_lawan is None:
        return None

    hasil = tag.get("Result", "")
    if hasil == "1/2-1/2":
        skor = 0.5
    elif (hasil == "1-0") == (warna_saya == "putih"):
        skor = 1.0 if hasil in ("1-0", "0-1") else 0.0
    else:
        skor = 0.0

    return {
        "warna_saya": warna_saya,
        "rating_saya": rating_saya,
        "rating_lawan": rating_lawan,
        "selisih": rating_lawan - rating_saya,
        "lawan": tag.get("Black" if warna_saya == "putih" else "White", ""),
        "skor": skor,
        "pembukaan": tag.get("Opening", "") or tag.get("ECO", ""),
    }


def main() -> int:
    p = argparse.ArgumentParser(description="Saring partai berdasarkan kekuatan lawan")
    p.add_argument("--min-rating-lawan", type=int, default=None,
                   help="ambil partai dengan rating lawan minimal sekian")
    p.add_argument("--min-selisih", type=int, default=None,
                   help="ambil partai dengan lawan minimal sekian poin lebih tinggi")
    p.add_argument("--sumber", type=Path, default=FOLDER_PGN,
                   help="folder berisi file PGN")
    p.add_argument("--keluaran", default="lawan-kuat",
                   help="nama subfolder hasil saringan")
    args = p.parse_args()

    if args.min_rating_lawan is None and args.min_selisih is None:
        print("Pilih minimal satu saringan: --min-rating-lawan atau --min-selisih",
              file=sys.stderr)
        return 1

    folder_keluaran = args.sumber / args.keluaran
    folder_keluaran.mkdir(parents=True, exist_ok=True)

    total_dibaca = total_lolos = total_dilewati = 0
    ringkas_per_file = []

    for berkas in sorted(args.sumber.glob("*.pgn")):
        teks = berkas.read_text(encoding="utf-8", errors="replace")
        semua = baca_partai(teks)
        lolos = []
        dilewati = 0

        for satu in semua:
            info = info_partai(ambil_tag(satu))
            if info is None:
                dilewati += 1
                continue
            if args.min_rating_lawan and info["rating_lawan"] < args.min_rating_lawan:
                continue
            if args.min_selisih is not None and info["selisih"] < args.min_selisih:
                continue
            lolos.append((satu, info))

        total_dibaca += len(semua)
        total_lolos += len(lolos)
        total_dilewati += dilewati

        if lolos:
            tujuan = folder_keluaran / berkas.name
            tujuan.write_text("\n\n".join(s for s, _ in lolos) + "\n", encoding="utf-8")
            rat = [i["rating_lawan"] for _, i in lolos]
            sel = [i["selisih"] for _, i in lolos]
            skor = sum(i["skor"] for _, i in lolos) / len(lolos)
            ringkas_per_file.append({
                "berkas": berkas.name,
                "jumlah": len(lolos),
                "dari": len(semua),
                "rating_lawan_min": min(rat),
                "rating_lawan_maks": max(rat),
                "selisih_rata": sum(sel) / len(sel),
                "skor_rata": skor,
            })

    print("=" * 74)
    saringan = []
    if args.min_rating_lawan:
        saringan.append(f"rating lawan >= {args.min_rating_lawan}")
    if args.min_selisih is not None:
        saringan.append(f"selisih >= +{args.min_selisih}")
    print(f"SARINGAN: {' dan '.join(saringan)}")
    print("=" * 74)

    if not ringkas_per_file:
        print("  Tidak ada partai yang lolos saringan ini.")
    else:
        print(f"  {'File':<34}{'Lolos':>7}{'Rating lawan':>15}{'Selisih':>9}{'Skor':>7}")
        print("  " + "-" * 70)
        for r in ringkas_per_file:
            print(f"  {r['berkas']:<34}{r['jumlah']:>4}/{r['dari']:<4}"
                  f"{r['rating_lawan_min']:>7}-{r['rating_lawan_maks']:<7}"
                  f"{r['selisih_rata']:>+8.0f}{r['skor_rata']:>7.0%}")

    print()
    print(f"  Total dibaca : {total_dibaca}")
    print(f"  Lolos        : {total_lolos}")
    if total_dilewati:
        print(f"  Dilewati     : {total_dilewati} (nama pemain atau rating tidak terbaca)")
    if total_lolos:
        print(f"  Tersimpan di : {folder_keluaran.relative_to(AKAR)}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
