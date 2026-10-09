"""
Isi FIDE ID dan gelar untuk pemain yang sumbernya tidak membawa FIDE ID.

Kenapa perlu (audit 9 Okt 2026): gelar terisi untuk 3.087 dari 20.027
pemain. Sebabnya bukan penangkapan yang rusak, tapi FIDE ID hanya datang dari
TWIC dan broadcast Lichess; PGN Mentor dan PGN lama tidak membawanya, dan
`rapikan_nama.py` hanya bisa mencari gelar lewat ID yang sudah diketahui.

Sumbernya daftar pemain resmi FIDE (`players_list.zip` dari
ratings.fide.com/download, unduhan publik, diperbarui bulanan). Nama di daftar
itu berformat "Belakang, Depan", sama seperti database ini.

Pencocokan nama BUKAN pencocokan ID, jadi pagarnya dibuat ketat. Pemain
hanya diberi FIDE ID kalau SEMUA ini terpenuhi:

  1. `nama_normal`-nya sama persis dengan tepat SATU pemain di daftar FIDE.
     Dua orang FIDE bernama sama = tidak disentuh (tidak ada tebakan).
  2. Namanya terdiri dari minimal dua kata dan nama depannya bukan singkatan
     satu huruf (`carlsen m` tidak pernah dicocokkan).
  3. Masuk akal secara umur: partai pertamanya tidak lebih awal dari tahun
     lahir FIDE + 6. Ini yang menahan pemain 1920-an yang kebetulan bernama
     sama dengan anak kelahiran 2010. Kalau FIDE tidak mencatat tahun lahir,
     pemain itu ditolak juga — tanpa pagar umur, nama saja tidak cukup.
  4. Elo-nya sejalan dengan gelar FIDE: pernah >= 2300 di partai tapi FIDE
     tidak mencatat gelar = dianggap orang lain (lihat ELO_PASTI_BERGELAR). Pemain
     tanpa satu pun Elo di partainya ditolak, karena tidak bisa diperiksa.
  5. FIDE ID itu belum dipakai pemain lain di database. Kalau sudah, itu
     tanda pemain ganda — dicatat untuk `gabung_pemain.py`, tidak diubah.
  6. Pemain hanya muncul di sumber partai GM/file, bukan akun online
     (nama akun Lichess/Chess.com bukan nama orang).

Risiko yang diterima dan ditulis terang: orang yang namanya unik di daftar
FIDE tapi partainya di database sebenarnya milik orang lain yang TIDAK
terdaftar di FIDE (misalnya pemain tua yang sudah wafat sebelum daftar FIDE
modern) akan tercocokkan salah. Pagar umur menahan sebagian besar kasus itu,
bukan semuanya. Karena itu tiap baris yang diubah dicatat di laporan
`data/laporan/cocokkan-fide-*.tsv` (bisa dibatalkan per baris), dan kolom
`terverifikasi` TIDAK dinaikkan.

    python cocokkan_fide.py data/fide/players_list.zip            # lihat saja
    python cocokkan_fide.py data/fide/players_list.zip --jalan    # tulis
"""

from __future__ import annotations

import argparse
import collections
import io
import sqlite3
import sys
import time
import zipfile
from datetime import datetime
from pathlib import Path

from app.db import PATH_DB, koneksi
from app.impor import normalisasi_nama
from app.kunci_penulis import KunciPenulis, KunciTerpakai

AKAR = Path(__file__).resolve().parent
FOLDER_LAPORAN = AKAR / "data" / "laporan"

# Gelar FIDE yang disimpan; sama dengan daftar putih di app/gelar_akun.py.
GELAR_FIDE = ("GM", "IM", "FM", "CM", "NM", "WGM", "WIM", "WFM", "WCM", "WNM")

# Sumber yang namanya nama orang. Akun online sengaja tidak ikut.
SUMBER_NAMA_ORANG = ("twic", "lichess-broadcast", "pgnmentor", "file")

UMUR_MINIMUM = 6

# Elo FIDE 2300 memenuhi syarat gelar FM, jadi pemain yang partainya pernah
# >= 2300 hampir selalu bergelar. Kalau orang FIDE bernama sama tidak bergelar,
# itu dianggap orang lain. "Hampir": gelar harus diajukan federasi, dan
# sebagian pemain (banyak dari China) tidak pernah mengurusnya — mereka ikut
# ditolak. Itu arah yang aman: kolom tetap kosong, bukan terisi salah. Kasus nyata yang memaksa pagar ini (uji kering 9 Okt 2026):
# "Larsen, Bent" (GM, 2.321 partai, Elo sampai 2660) tercocokkan dengan
# FIDE 1483455 "Larsen, Bent", lahir 1937, tanpa gelar — GM Bent Larsen
# wafat 2010 dan tidak ada lagi di daftar, namanya dipakai orang lain.
ELO_PASTI_BERGELAR = 2300


def baca_daftar_fide(path_zip: Path) -> dict[str, list[dict]]:
    """Daftar FIDE dikelompokkan per `nama_normal`.

    Berkasnya teks lebar-tetap; posisi kolom dibaca dari baris judul, bukan
    ditulis mati, supaya perubahan lebar kolom FIDE tidak diam-diam menggeser
    data ke kolom yang salah.
    """
    with zipfile.ZipFile(path_zip) as z:
        nama_txt = next(n for n in z.namelist() if n.lower().endswith(".txt"))
        with z.open(nama_txt) as mentah:
            berkas = io.TextIOWrapper(mentah, encoding="latin-1", newline="")
            judul = berkas.readline().rstrip("\r\n")
            awal = {k: judul.index(k) for k in
                    ("ID Number", "Name", "Fed", "Sex", "Tit", "WTit", "OTit", "B-day", "Flag")}
            hasil: dict[str, list[dict]] = collections.defaultdict(list)
            for baris in berkas:
                nama = baris[awal["Name"]:awal["Fed"]].strip()
                if not nama:
                    continue
                gelar = baris[awal["Tit"]:awal["WTit"]].strip().upper()
                gelar_w = baris[awal["WTit"]:awal["OTit"]].strip().upper()
                lahir = baris[awal["B-day"]:awal["Flag"]].strip()
                hasil[normalisasi_nama(nama)].append({
                    "fide_id": baris[awal["ID Number"]:awal["Name"]].strip(),
                    "nama": nama,
                    "negara": baris[awal["Fed"]:awal["Sex"]].strip() or None,
                    "gelar": next((g for g in (gelar, gelar_w) if g in GELAR_FIDE), None),
                    "lahir": int(lahir) if lahir.isdigit() and int(lahir) > 1800 else None,
                })
    return hasil


def calon_database(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Pemain tanpa FIDE ID yang hanya muncul di sumber bernama orang."""
    tanda = ",".join("?" * len(SUMBER_NAMA_ORANG))
    return conn.execute(f"""
        WITH main AS (
            SELECT putih_id AS pid, tanggal, sumber, elo_putih AS elo FROM partai
            UNION ALL
            SELECT hitam_id, tanggal, sumber, elo_hitam FROM partai
        )
        SELECT p.id, p.nama, p.nama_normal, p.gelar, p.negara,
               COUNT(*) AS partai,
               MIN(CAST(substr(m.tanggal, 1, 4) AS INTEGER)) AS tahun_awal,
               MAX(m.elo) AS elo_maks,
               SUM(m.sumber NOT IN ({tanda})) AS di_akun_online
        FROM pemain p JOIN main m ON m.pid = p.id
        WHERE p.fide_id IS NULL AND COALESCE(p.akun_pribadi, 0) = 0
        GROUP BY p.id
    """, SUMBER_NAMA_ORANG).fetchall()


def putuskan(baris, daftar: dict[str, list[dict]], id_terpakai: set[str]) -> tuple[str, dict | None]:
    """(alasan, data FIDE) — alasan 'cocok' berarti boleh ditulis."""
    if baris["di_akun_online"]:
        return "akun-online", None
    kata = baris["nama_normal"].split()
    if len(kata) < 2 or all(len(k) == 1 for k in kata[1:]):
        return "nama-singkat", None
    calon = daftar.get(baris["nama_normal"], [])
    if not calon:
        return "tidak-ada-di-fide", None
    if len(calon) > 1:
        return "nama-ganda-di-fide", None
    data = calon[0]
    if data["lahir"] is None:
        return "tanpa-tahun-lahir", data
    awal = baris["tahun_awal"]
    if not awal or awal < data["lahir"] + UMUR_MINIMUM:
        return "umur-tidak-masuk-akal", data
    if baris["elo_maks"] is None:
        return "tanpa-elo", data
    if baris["elo_maks"] >= ELO_PASTI_BERGELAR and not data["gelar"]:
        return "elo-tidak-sejalan-gelar", data
    if data["fide_id"] in id_terpakai:
        return "id-sudah-dipakai", data
    return "cocok", data


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("daftar", type=Path, help="players_list.zip dari ratings.fide.com/download")
    p.add_argument("--jalan", action="store_true", help="benar-benar menulis (bawaan: lihat saja)")
    p.add_argument("--contoh", type=int, default=15, help="berapa contoh per alasan ditampilkan")
    args = p.parse_args()

    t = time.monotonic()
    daftar = baca_daftar_fide(args.daftar)
    print(f"daftar FIDE: {sum(map(len, daftar.values())):,} pemain, "
          f"{len(daftar):,} nama berbeda ({time.monotonic() - t:.0f} s)")

    ro = sqlite3.connect(f"file:{PATH_DB}?mode=ro", uri=True)
    ro.row_factory = sqlite3.Row
    id_terpakai = {r[0] for r in ro.execute("SELECT fide_id FROM pemain WHERE fide_id IS NOT NULL")}
    semua = calon_database(ro)
    ro.close()

    per_alasan: dict[str, list] = collections.defaultdict(list)
    dipakai_di_sini: set[str] = set()
    for b in semua:
        alasan, data = putuskan(b, daftar, id_terpakai | dipakai_di_sini)
        if alasan == "cocok":
            dipakai_di_sini.add(data["fide_id"])
        per_alasan[alasan].append((b, data))

    print(f"pemain tanpa FIDE ID: {len(semua):,}")
    for alasan, isi in sorted(per_alasan.items(), key=lambda x: -len(x[1])):
        print(f"  {alasan:<24} {len(isi):>6} pemain  {sum(b['partai'] for b, _ in isi):>8,} partai")
    cocok = per_alasan.get("cocok", [])
    bergelar = [x for x in cocok if x[1]["gelar"] and not x[0]["gelar"]]
    print(f"cocok: {len(cocok):,} pemain dapat FIDE ID, {len(bergelar):,} dapat gelar baru")

    for alasan in ("cocok", "elo-tidak-sejalan-gelar", "id-sudah-dipakai"):
        isi = sorted(per_alasan.get(alasan, []), key=lambda x: -x[0]["partai"])[:args.contoh]
        if isi:
            print(f"\ncontoh {alasan}:")
        for b, d in isi:
            print(f"  #{b['id']:<6} {b['nama'][:30]:<30} {b['partai']:>5} partai sejak {b['tahun_awal']} "
                  f"-> {d['fide_id']} {d['nama'][:30]} {d['gelar'] or ''} {d['negara'] or ''} lahir {d['lahir']}")

    FOLDER_LAPORAN.mkdir(parents=True, exist_ok=True)
    cap = datetime.now().strftime("%Y%m%d-%H%M%S")
    laporan = FOLDER_LAPORAN / f"cocokkan-fide-{cap}{'' if args.jalan else '-lihat'}.tsv"
    with laporan.open("w", encoding="utf-8", newline="\n") as f:
        f.write("pemain_id\tnama\tpartai\ttahun_awal\telo_maks\talasan\tfide_id\tnama_fide\tgelar\tnegara\tlahir\n")
        for alasan, isi in per_alasan.items():
            for b, d in isi:
                d = d or {}
                f.write("\t".join(str(x if x is not None else "") for x in (
                    b["id"], b["nama"], b["partai"], b["tahun_awal"], b["elo_maks"], alasan, d.get("fide_id"),
                    d.get("nama"), d.get("gelar"), d.get("negara"), d.get("lahir"))) + "\n")
    print(f"\nlaporan: {laporan}")

    if not args.jalan:
        print("lihat saja; tambahkan --jalan untuk menulis")
        return 0

    try:
        with KunciPenulis("cocokkan_fide.py --jalan"):
            with koneksi() as conn:
                n = 0
                for b, d in cocok:
                    # fide_id IS NULL di WHERE: kalau impor lain sudah mengisinya
                    # sejak pembacaan di atas, baris itu dibiarkan.
                    n += conn.execute(
                        "UPDATE pemain SET fide_id = ?, gelar = COALESCE(gelar, ?), "
                        "negara = COALESCE(negara, ?) WHERE id = ? AND fide_id IS NULL",
                        (d["fide_id"], d["gelar"], d["negara"], b["id"]),
                    ).rowcount
                conn.commit()
    except KunciTerpakai as e:
        print(f"database sedang dipakai penulis lain: {e}", file=sys.stderr)
        return 2
    print(f"ditulis: {n:,} pemain")
    return 0


if __name__ == "__main__":
    sys.exit(main())
