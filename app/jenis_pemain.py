"""
Tandai baris `pemain` yang bukan satu manusia: mesin catur dan partai konsultasi.

Kenapa perlu (temuan Ray, 25 Sep 2026): koleksi PGN Mentor memuat partai
manusia melawan program — `Comp Deep Blue`, `DEEP FRITZ`, `HYDRA`,
`Shredder 5.32` — dan partai konsultasi yang dua pemainnya ditulis jadi satu
nama (`Nikitin/Sakarov`, `Anand,V / FRITZ7`). Semuanya masuk sebagai baris
pemain biasa, jadi ikut terhitung di statistik pembukaan "partai GM", padahal
`CLAUDE.md` bagian 11 menetapkan fokusnya turnamen OTB dan broadcast.

**Ditandai, bukan dihapus.** Partainya nyata dan bersejarah (Kasparov melawan
Deep Blue 1997 bukan sampah), jadi yang berubah hanya apakah ia dihitung
sebagai partai antar-manusia. Menghapusnya berarti membuang data yang tidak
bisa dibuat ulang.

Aturannya BERJANGKAR, bukan substring, dan urutannya penting. Pencocokan
substring naif pada percobaan pertama menghasilkan salah-tangkap yang justru
berbahaya, semuanya dari data nyata:

  * `Saemisch, Fritz` — GM Jerman, bukan program Fritz. Begitu juga
    `Riemann, Fritz`, `Igel, Fritz`, `Roessel, Fritz`, `Fritz, Alexander`.
  * `Zagrebelny, Sergey` dan `Strebel, Beda` tertangkap pola "rebel"
    (Zag-**rebel**-ny, St-**rebel**).
  * `Grybkauskas,Pijus` tertangkap pola "rybka" (G-**rybka**-uskas).
  * `Albuquerque Junior`, `Ferreira Junior`, `Almeida Junior` — nama Brasil,
    bukan Deep Junior.

Yang memisahkan semuanya: **manusia ditulis "Belakang, Depan" dengan koma,
program tidak pernah.** Ditambah dua pagar lain — FIDE ID berarti manusia
(FIDE tidak mendaftarkan program), dan baris yang punya partai dari akun
online tidak pernah ditandai karena di situ nama adalah username yang tidak
bisa dipercaya (`komodo54`, `Juniorcatur17` adalah lawan Ray, bukan engine).

Diukur 25 Sep 2026: 24 baris mesin (93 partai) dan 6 baris konsultasi, semuanya
dari `pgnmentor`, tanpa satu pun salah-tangkap.
"""

from __future__ import annotations

import sqlite3

# Sumber yang dianggap OTB. Baris yang punya partai DI LUAR daftar ini tidak
# pernah ditandai: nama di sana adalah username akun online.
SUMBER_OTB = ("twic", "pgnmentor", "lichess-broadcast", "file")

# PGN Mentor menandai programnya sendiri dengan awalan ini. Paling bisa
# dipercaya karena berasal dari sumbernya, bukan tebakan kita.
AWALAN_MESIN = ("comp ",)

# Nama program yang muncul tanpa awalan "Comp". Dipakai HANYA pada nama yang
# tidak berkoma, jadi 'Saemisch, Fritz' tidak pernah sampai ke sini.
KATA_MESIN = (
    "fritz", "hydra", "shredder", "junior", "deep blue", "deep thought",
    "rebel", "crafty", "hiarcs", "kallisto", "genius", "leonardo", "mephisto",
    "zugzwang", "diep", "nimzo", "gandalf", "brutus", "stockfish", "komodo",
    "houdini", "rybka", "chessmaster", "pentium", "chess machine", "belle",
)

JENIS_SAH = ("mesin", "konsultasi")


def tebak_jenis(nama: str, fide_id: str | None, sumber: set | None) -> str | None:
    """
    'mesin', 'konsultasi', atau None kalau ini satu manusia (atau tidak pasti).

    None juga dipakai untuk "tidak bisa dinilai" — dan itu disengaja: aturan
    ini hanya boleh menandai yang jelas. Satu manusia yang salah ditandai
    mesin menghilangkan partainya dari statistik tanpa suara, kerusakan yang
    jauh lebih mahal daripada satu program yang lolos.
    """
    if fide_id:
        return None                      # FIDE tidak mendaftarkan program
    if sumber and not set(sumber) <= set(SUMBER_OTB):
        return None                      # ada partai akun online: nama = username
    n = (nama or "").strip().lower()
    if not n:
        return None
    # Awalan "Comp " lebih dulu daripada pemeriksaan "/": 'Comp Genius/Pentium'
    # adalah satu program yang namanya memuat garis miring, bukan konsultasi.
    if n.startswith(AWALAN_MESIN):
        return "mesin"
    if "/" in n:
        return "konsultasi"
    if "," in n:
        return None                      # 'Belakang, Depan' = manusia
    return "mesin" if any(k in n for k in KATA_MESIN) else None


def _sumber_per_pemain(conn: sqlite3.Connection) -> dict:
    """{pemain_id: {sumber, ...}} untuk pemain yang punya partai."""
    keluar: dict = {}
    for kolom in ("putih_id", "hitam_id"):
        for r in conn.execute(
                f"SELECT {kolom} AS pid, sumber FROM partai "
                f"WHERE {kolom} IS NOT NULL GROUP BY {kolom}, sumber"):
            keluar.setdefault(r["pid"], set()).add(r["sumber"])
    return keluar


def usulkan(conn: sqlite3.Connection) -> list[tuple[int, str, str, int]]:
    """
    (id, nama, jenis, jumlah_partai) untuk baris yang perlu ditandai.

    Hanya baris yang punya partai: baris tanpa partai tidak mengganggu
    statistik apa pun, dan menandainya cuma menambah keramaian.
    """
    sumber = _sumber_per_pemain(conn)
    jumlah: dict = {}
    for kolom in ("putih_id", "hitam_id"):
        for r in conn.execute(f"SELECT {kolom} AS pid, COUNT(*) n FROM partai "
                              f"WHERE {kolom} IS NOT NULL GROUP BY {kolom}"):
            jumlah[r["pid"]] = jumlah.get(r["pid"], 0) + r["n"]

    hasil = []
    for r in conn.execute("SELECT id, nama, fide_id, jenis FROM pemain"):
        if not jumlah.get(r["id"]):
            continue
        jenis = tebak_jenis(r["nama"], r["fide_id"], sumber.get(r["id"]))
        if jenis and jenis != r["jenis"]:
            hasil.append((r["id"], r["nama"], jenis, jumlah[r["id"]]))
    hasil.sort(key=lambda x: -x[3])
    return hasil


def tandai(conn: sqlite3.Connection, jalan: bool = False) -> list:
    """Terapkan usulan `usulkan()`. Tanpa `jalan=True` tidak menulis apa pun."""
    usul = usulkan(conn)
    if jalan and usul:
        conn.executemany("UPDATE pemain SET jenis = ? WHERE id = ?",
                         [(j, i) for i, _, j, _ in usul])
        conn.commit()
    return usul


def main() -> int:
    """CLI: `python -m app.jenis_pemain` (lihat saja) atau `--jalan`."""
    import argparse

    from .db import koneksi
    from .kunci_penulis import KunciPenulis, KunciTerpakai

    p = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    p.add_argument("--jalan", action="store_true",
                   help="benar-benar tandai (bawaan: lihat saja)")
    p.add_argument("--db")
    a = p.parse_args()

    # Menulis kolom pemain = penulis besar (CLAUDE.md bagian 7).
    penjaga = None
    if a.jalan:
        try:
            penjaga = KunciPenulis("tandai jenis pemain").ambil()
        except KunciTerpakai as e:
            print(e)
            return 1
    try:
        conn = koneksi(a.db)
        usul = tandai(conn, jalan=a.jalan)
        if not usul:
            print("Tidak ada baris yang perlu ditandai.")
            return 0
        lebar = max(len(n) for _, n, _, _ in usul)
        for pid, nama, jenis, n in usul:
            print(f"  {n:5} partai  {nama:{lebar}}  -> {jenis}  (id {pid})")
        per_jenis: dict = {}
        for _, _, jenis, n in usul:
            a_, b_ = per_jenis.get(jenis, (0, 0))
            per_jenis[jenis] = (a_ + 1, b_ + n)
        ringkas = ", ".join(f"{j}: {baris} baris / {partai} partai"
                            for j, (baris, partai) in sorted(per_jenis.items()))
        print(f"\n{ringkas}"
              + ("" if a.jalan else " (lihat saja; tambahkan --jalan untuk menandai)"))
    finally:
        if penjaga:
            penjaga.lepas()
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
