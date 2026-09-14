"""
Impor file PGN ke database.

Contoh:
    python impor_pgn.py data/pgn/lichess-Mikaysr.pgn
    python impor_pgn.py data/pgn/*.pgn
    python impor_pgn.py data/pgn/uji-scholars-mate.pgn --batas 10
    python impor_pgn.py --semua
"""

from __future__ import annotations

import argparse
import sys
import time
import urllib.request
from pathlib import Path

from app.db import PATH_DB, buat_skema, info, koneksi
from app.kunci_penulis import KunciPenulis, KunciTerpakai, penulis_berjalan
from ambil_partai import AKUN
from app.impor import (HasilImpor, hitung_ulang_hash, impor_file, isi_format_kosong,
                       perbarui_elo_terakhir, periksa_jejak, tandai_akun_pribadi)

AKAR = Path(__file__).resolve().parent
FOLDER_PGN = AKAR / "data" / "pgn"
FOLDER_PGNMENTOR = AKAR / "data" / "pgnmentor"

# Koleksi karier pgnmentor diunduh per pemain sebagai zip; berkas .pgn di
# data/pgnmentor/ adalah isinya. URL zip itulah jejak asal yang sah, dan
# selalu diperiksa HEAD dulu — kalau tidak 200, sumber_url dibiarkan NULL.
URL_PGNMENTOR = "https://www.pgnmentor.com/players/{nama}.zip"
UA_PGNMENTOR = "Mozilla/5.0 (proyek belajar catur pribadi; impor_pgn.py --jejak)"

# Keputusan Ray (SESI.md bagian 6, 11 Sep): RyuMcLungG TIDAK dihitung akun
# pribadi walau partai Arena Kings-nya diunduh — akun itu sebagian dipakai
# orang lain. Akun pribadi = 5 username lain di ambil_partai.AKUN.
BUKAN_AKUN_PRIBADI = ("ryumclungg",)
AKUN_PRIBADI = [a.username for a in AKUN if a.username.lower() not in BUKAN_AKUN_PRIBADI]


def tebak_sumber(nama_berkas: str) -> str:
    """Tentukan asal partai dari nama filenya."""
    n = nama_berkas.lower()
    if n.startswith("lichess-"):
        return "lichess-user"
    if n.startswith("chesscom-"):
        return "chesscom"
    if "broadcast" in n:
        return "lichess-broadcast"
    return "file"



def url_koleksi_pgnmentor(berkas: list[Path], periksa: bool = True,
                          jeda: float = 1.0) -> dict[str, str | None]:
    """
    URL zip pgnmentor per berkas, hanya yang terbukti ada (HEAD 200).

    Jejak asal tidak boleh dikarang (CLAUDE.md bagian 7): pola URL-nya
    diketahui, tapi ejaan nama berkas belum tentu cocok, jadi tiap URL
    diperiksa dulu satu per satu dengan jeda dan User-Agent yang jujur.
    Yang gagal dikembalikan None dan barisnya tetap NULL.
    """
    hasil: dict[str, str | None] = {}
    for i, f in enumerate(berkas):
        url = URL_PGNMENTOR.format(nama=f.stem)
        if not periksa:
            hasil[f.name] = None
            continue
        if i:
            time.sleep(jeda)
        permintaan = urllib.request.Request(url, method="HEAD",
                                            headers={"User-Agent": UA_PGNMENTOR})
        try:
            with urllib.request.urlopen(permintaan, timeout=20) as jawab:
                hasil[f.name] = url if jawab.status == 200 else None
                print(f"  {f.name:28} {jawab.status} {url}")
        except Exception as e:
            hasil[f.name] = None
            print(f"  {f.name:28} GAGAL ({e}) -> sumber_url dibiarkan NULL")
    return hasil


def _jejak(args) -> int:
    """Audit jejak asal koleksi PGN; menulis hanya kalau --tulis."""
    berkas = sorted(args.berkas) if args.berkas else sorted(FOLDER_PGNMENTOR.glob("*.pgn"))
    if not berkas:
        print(f"Tidak ada .pgn di {FOLDER_PGNMENTOR}", file=sys.stderr)
        return 1

    print(f"{len(berkas)} berkas, sumber '{args.sumber or 'pgnmentor'}'"
          f"{' - MENULIS' if args.tulis else ' - lihat saja'}")
    print()
    print("Memeriksa URL koleksi (HEAD, jeda 1 detik):")
    url_per_berkas = url_koleksi_pgnmentor(berkas, periksa=not args.tanpa_jaringan)
    sah = sum(1 for v in url_per_berkas.values() if v)
    print(f"  {sah} dari {len(berkas)} URL terbukti ada")
    print()

    conn = koneksi(args.db)
    buat_skema(conn)
    lebar = max(len(f.name) for f in berkas)
    print(f"{'berkas'.ljust(lebar)}  {'partai':>7} {'cocok':>7} {'koleks.lain':>12} "
          f"{'dup.lain':>9} {'TIDAK ADA':>10} {'url baru':>9}")

    def lapor(s):
        print(f"{s['berkas'].ljust(lebar)}  {s['partai']:>7} {s['cocok']:>7} "
              f"{s['juga_koleksi_lain']:>12} {s['duplikat_sumber_lain']:>9} "
              f"{s['tidak_ada']:>10} {s['url_ditulis']:>9}")

    hasil = periksa_jejak(conn, berkas, url_per_berkas,
                          sumber=args.sumber or "pgnmentor",
                          tulis=args.tulis, lapor=lapor)

    jml = lambda k: sum(s[k] for s in hasil)
    print()
    print(f"TOTAL: {jml('partai')} partai di berkas - {jml('cocok')} baris sumber ini "
          f"({jml('juga_koleksi_lain')} juga ada di koleksi lain), "
          f"{jml('duplikat_sumber_lain')} duplikat sumber lain, "
          f"{jml('tidak_ada')} TIDAK ADA di database, "
          f"{jml('tanpa_langkah')} tanpa langkah")
    print(f"sumber_url ditulis: {jml('url_ditulis')}")
    if not args.tulis:
        print()
        print("Belum ada yang ditulis. Ulangi dengan --tulis.")
    conn.close()
    return 0


def main() -> int:
    """Impor = penulis besar: ambil kunci dulu (CLAUDE.md bagian 7)."""
    lain = penulis_berjalan()
    if lain:
        print("Penulis besar lain sedang berjalan, impor tidak dijalankan:", file=sys.stderr)
        for b in lain:
            print("  ..." + b, file=sys.stderr)
        return 1
    try:
        with KunciPenulis("impor_pgn.py " + " ".join(sys.argv[1:])[:80]):
            return _impor(sys.argv)
    except KunciTerpakai as e:
        print(e, file=sys.stderr)
        return 1


def _impor(argv) -> int:
    p = argparse.ArgumentParser(description="Impor file PGN ke database")
    p.add_argument("berkas", nargs="*", type=Path, help="file PGN")
    p.add_argument("--semua", action="store_true",
                   help=f"impor semua .pgn di {FOLDER_PGN.name}/")
    p.add_argument("--db", type=Path, default=PATH_DB)
    p.add_argument("--sumber", default=None,
                   help="timpa penentuan sumber otomatis")
    p.add_argument("--batas", type=int, default=0,
                   help="maksimal partai per file (0 = tanpa batas)")
    p.add_argument("--hitung-ulang-hash", action="store_true",
                   help="hitung ulang hash_unik semua partai (setelah rumus hash berubah); "
                        "duplikat yang dulu lolos dihapus")
    p.add_argument("--jejak", action="store_true",
                   help="tanpa impor: cocokkan ulang berkas ke database lewat hash, "
                        f"lengkapi sumber_url + sumber_impor (bawaan: {FOLDER_PGNMENTOR.name}/*.pgn)")
    p.add_argument("--tulis", action="store_true",
                   help="dipakai bersama --jejak: benar-benar menulis (bawaannya lihat saja)")
    p.add_argument("--tanpa-jaringan", action="store_true",
                   help="dipakai bersama --jejak: jangan periksa URL lewat HEAD "
                        "(semua sumber_url dibiarkan NULL)")
    p.add_argument("--rapikan", action="store_true",
                   help="tanpa impor: hitung ulang kolom format semua partai dan tandai "
                        "akun pribadi (versi ringannya jalan otomatis setelah tiap impor)")
    args = p.parse_args()

    if args.jejak:
        return _jejak(args)

    if args.rapikan:
        conn = koneksi(args.db)
        buat_skema(conn)
        n_format = isi_format_kosong(conn, ulang=True)
        n_akun = tandai_akun_pribadi(conn, AKUN_PRIBADI, kecualikan=BUKAN_AKUN_PRIBADI)
        conn.close()
        print(f"format diisi untuk {n_format} partai; akun pribadi tertandai: {n_akun}")
        return 0

    if args.hitung_ulang_hash:
        conn = koneksi(args.db)
        buat_skema(conn)
        sebelum = info(conn)["partai"]
        print(f"Menghitung ulang hash {sebelum} partai...")
        h = hitung_ulang_hash(conn, lapor_tiap=2000)
        sesudah = info(conn)["partai"]
        conn.close()
        print(f"  diperiksa {h['diperiksa']}, hash diperbarui {h['hash_diperbarui']}, "
              f"duplikat dihapus {h['dihapus']}")
        print(f"  database: {sesudah} partai ({sesudah - sebelum:+d})")
        return 0

    berkas = list(args.berkas)
    if args.semua:
        berkas = sorted(FOLDER_PGN.glob("*.pgn"))
    if not berkas:
        print("Tidak ada file yang diberikan. Pakai --semua atau sebutkan filenya.",
              file=sys.stderr)
        return 1

    conn = koneksi(args.db)
    buat_skema(conn)

    sebelum = info(conn)
    print(f"Database: {args.db}")
    print(f"Sebelum : {sebelum['partai']} partai, {sebelum['pemain']} pemain, "
          f"{sebelum['turnamen']} turnamen")
    print()

    total = HasilImpor()
    mulai = time.time()

    try:
        for f in berkas:
            if not f.is_file():
                print(f"  {f.name}: TIDAK DITEMUKAN")
                continue

            sumber = args.sumber or tebak_sumber(f.name)
            print(f"  {f.name}  (sumber: {sumber})")

            t0 = time.time()
            h = impor_file(conn, f, sumber, batas=args.batas, lapor_tiap=2000)
            durasi = time.time() - t0

            print(f"    {h.baru} baru, {h.duplikat} duplikat, "
                  f"{h.dilewati} dilewati  [{durasi:.1f} detik]")
            for alasan, jumlah in sorted(h.alasan_dilewati.items()):
                print(f"      dilewati - {alasan}: {jumlah}")

            total.baru += h.baru
            total.duplikat += h.duplikat
            total.dilewati += h.dilewati
            for a, j in h.alasan_dilewati.items():
                total.alasan_dilewati[a] = total.alasan_dilewati.get(a, 0) + j
    finally:
        conn.commit()
        if total.baru:
            print()
            print("  Memperbarui elo_terakhir dari partai terbaru tiap pemain...")
            perbarui_elo_terakhir(conn)
        # Selalu: kolom turunan & tanda akun pribadi (skema v6), aman diulang.
        isi_format_kosong(conn)
        tandai_akun_pribadi(conn, AKUN_PRIBADI, kecualikan=BUKAN_AKUN_PRIBADI)
        sesudah = info(conn)
        conn.close()

    print()
    print("=" * 60)
    print(f"  Diproses : {total.total} partai dalam {time.time() - mulai:.1f} detik")
    print(f"  Baru     : {total.baru}")
    print(f"  Duplikat : {total.duplikat}")
    print(f"  Dilewati : {total.dilewati}")
    print()
    print(f"  Database : {sesudah['partai']} partai "
          f"(+{sesudah['partai'] - sebelum['partai']}), "
          f"{sesudah['pemain']} pemain, {sesudah['turnamen']} turnamen")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
