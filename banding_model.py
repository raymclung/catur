"""
Banding dua model Ollama untuk tugas menerjemahkan pertanyaan jadi SQL.

Dijalankan dari akar proyek Catur. Baca-saja terhadap catur.db, jadi aman
walaupun ada impor/analisis yang sedang berjalan.

Yang diukur per pertanyaan: waktu, SQL yang dihasilkan, apakah lolos pagar
`periksa_sql()`, apakah SQL-nya jalan, dan hasilnya. Untuk beberapa
pertanyaan ada `kunci` — angka yang dihitung lewat SQL tulisan tangan,
supaya jawaban model bisa dinilai benar/salah, bukan cuma "kelihatan masuk
akal".
"""

from __future__ import annotations

import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app import tanya_db  # noqa: E402

DB = Path("data/catur.db")

# (label, pertanyaan, sql_kunci atau None)
SOAL = [
    (
        "1 mudah",
        "Berapa total partai di database?",
        "SELECT COUNT(*) FROM partai",
    ),
    (
        "2 mudah",
        "Berapa banyak pemain yang bergelar GM?",
        "SELECT COUNT(*) FROM pemain WHERE gelar = 'GM'",
    ),
    (
        "3 sedang",
        "Berapa banyak partai untuk setiap sumber? Urutkan dari terbanyak.",
        None,
    ),
    (
        "4 sedang",
        "Siapa 5 pemain dengan partai terbanyak? Tampilkan nama dan jumlah partainya.",
        None,
    ),
    (
        "5 sulit",
        "Berapa persen partai yang dimenangkan putih, dihitung per sumber?",
        None,
    ),
    (
        "6 sulit",
        "Pembukaan apa yang paling sering dipakai Carlsen ketika dia bermain putih? "
        "Tampilkan 5 teratas dengan jumlahnya.",
        None,
    ),
    (
        "7 sulit",
        "Pemain mana yang punya rata-rata akurasi terbaik menurut tabel analisis? "
        "Hanya pemain dengan minimal 20 partai yang sudah dianalisis.",
        None,
    ),
]


def kunci_jawaban(sql: str) -> object:
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    try:
        return con.execute(sql).fetchone()[0]
    finally:
        con.close()


def uji(model: str) -> dict:
    print(f"\n{'#' * 72}\n#  {model}\n{'#' * 72}")
    ringkas = {"model": model, "lolos": 0, "gagal": 0, "detik": 0.0, "benar": 0, "dinilai": 0}
    for label, pertanyaan, sql_kunci in SOAL:
        print(f"\n[{label}] {pertanyaan}")
        t0 = time.time()
        try:
            h = tanya_db.tanya(pertanyaan, model=model, db=DB, batas=8)
        except Exception as e:
            dt = time.time() - t0
            ringkas["gagal"] += 1
            ringkas["detik"] += dt
            print(f"   GAGAL ({dt:.0f}s) {type(e).__name__}: {e}")
            continue
        dt = time.time() - t0
        ringkas["lolos"] += 1
        ringkas["detik"] += dt
        print(f"   SQL ({dt:.0f}s): {' '.join(h['sql'].split())}")
        for b in h["baris"][:5]:
            print(f"      {b}")
        if sql_kunci:
            ringkas["dinilai"] += 1
            benar = kunci_jawaban(sql_kunci)
            didapat = h["baris"][0][0] if h["baris"] else None
            cocok = str(didapat) == str(benar)
            ringkas["benar"] += int(cocok)
            print(f"   kunci={benar:,}  didapat={didapat}  {'COCOK' if cocok else 'SALAH'}")
    return ringkas


def main() -> int:
    model = sys.argv[1:] or ["qwen2.5-coder:3b", "qwen2.5-coder:7b"]
    hasil = [uji(m) for m in model]
    print(f"\n{'=' * 72}\nRINGKASAN\n{'=' * 72}")
    print(f"{'model':<22} {'lolos':>6} {'gagal':>6} {'total s':>9} {'rata s':>8} {'benar':>10}")
    for r in hasil:
        n = r["lolos"] + r["gagal"]
        rata = r["detik"] / n if n else 0
        nilai = f"{r['benar']}/{r['dinilai']}" if r["dinilai"] else "-"
        print(f"{r['model']:<22} {r['lolos']:>6} {r['gagal']:>6} "
              f"{r['detik']:>9.0f} {rata:>8.1f} {nilai:>10}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
