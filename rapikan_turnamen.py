"""
Pulihkan nama turnamen partai siaran yang tercemar label papan.

Masalahnya (ditemukan 25 Sep 2026, saat Ray bertanya "apakah memang tidak ada
turnamen lainnya?"): PGN siaran Lichess kadang menulis label papan di tag
Event, mis. "Round 1: Vrolijk, Liam - Ernst, Sipke", untuk sebagian papan
dalam ronde yang SAMA. Karena `app/impor.py` membuat satu baris `turnamen`
per Event unik, satu ronde bisa pecah jadi puluhan "turnamen" berisi satu
partai. Terukur saat ditemukan: 3.362 baris `turnamen` bernama seperti itu,
menyangkut 3.431 partai; ronde 1 Dutch Meesterklasse sendiri pecah jadi 18.

Impor baru sudah dicegah (`app.impor.EVENT_PAPAN` +
`impor_broadcast.nama_turnamen_ronde`). Skrip ini membereskan data lama.

Cara memulihkan namanya: `partai.sumber_url` tiap partai siaran memuat slug
turnamen Lichess, mis.
    https://lichess.org/broadcast/dutch-meesterklasse-2026-2027/round-1/isTlRIXU/0NhI5k8a
Diuji pada seluruh 3.431 partai saat ditemukan: 3.431 punya slug, 0 tidak.
Untuk tiap slug, nama targetnya dipilih dengan urutan:
  1. nama turnamen BERSIH yang paling sering dipakai partai lain di slug yang
     sama -- nama yang sudah lama ada di database tidak diubah, jadi Olimpiade
     tetap satu "Olymp 2026 Open", bukan pecah per bagian;
  2. kalau tidak ada, nama turnamen dari API Lichess (satu permintaan per slug).

Partai dikelompokkan per (slug, tahun) karena skema memang menganggap turnamen
yang sama di tahun berbeda sebagai dua turnamen (UNIQUE(nama, tahun)).

    python rapikan_turnamen.py --coba     # hitung saja, tidak menulis
    python rapikan_turnamen.py            # cadangkan dulu, lalu kerjakan
    python rapikan_turnamen.py --tanpa-cadangan

Pemetaan lengkap partai -> turnamen lama/baru ditulis ke
`data/laporan/rapikan-turnamen-<waktu>.json` supaya bisa ditelusuri ulang.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

from app.db import FOLDER_DATA, PATH_DB, buat_skema, koneksi
from app.impor import EVENT_PAPAN, bersih, tahun_dari_tanggal
from app.kunci_penulis import KunciPenulis, KunciTerpakai

AKAR = Path(__file__).resolve().parent
FOLDER_LAPORAN = FOLDER_DATA / "laporan"
# Nama turnamen per slug, supaya menjalankan ulang (mis. --coba lalu nyata)
# tidak menembak API Lichess dua kali dan kena rate limit.
PATH_CACHE = AKAR / "data" / "nama-turnamen-slug.json"
JEDA = 1.5


def slug_dan_ronde(url: str | None) -> tuple[str, str]:
    """
    ('slug-turnamen', 'ID-ronde') dari sumber_url siaran; ('', '') kalau tidak ada.

    Bentuk URL-nya:
      https://lichess.org/broadcast/<slug>/<slug-ronde>/<ID-ronde>/<ID-partai>
    Bentuk lama tanpa slug (`/broadcast/-/-/<ID-ronde>`) tidak bisa dipulihkan
    namanya, jadi slug-nya dikembalikan kosong dan partainya dilewati.
    """
    m = re.match(r"https://lichess\.org/broadcast/([^/]+)/([^/]+)/([^/]+)", url or "")
    if not m or m.group(1) == "-":
        return "", ""
    return m.group(1), m.group(3)


def nama_sampah(nama: str | None) -> bool:
    return bool(EVENT_PAPAN.match(nama or ""))


def kumpulkan(conn) -> tuple[dict, dict, list]:
    """
    Kembalikan (per_slug, nama_bersih_per_slug, tanpa_slug).

    per_slug[slug] = daftar dict partai yang perlu dipindahkan.
    nama_bersih_per_slug[slug] = Counter nama turnamen bersih di slug itu.
    """
    sampah_id = {r["id"] for r in conn.execute("SELECT id, nama FROM turnamen")
                 if nama_sampah(r["nama"])}

    per_slug: dict[str, list] = defaultdict(list)
    nama_bersih: dict[str, Counter] = defaultdict(Counter)
    tanpa_slug: list = []

    for r in conn.execute(
        "SELECT p.id, p.turnamen_id, p.sumber_url, p.tanggal, t.nama AS tnama "
        "FROM partai p JOIN turnamen t ON t.id = p.turnamen_id "
        "WHERE p.sumber = 'lichess-broadcast'"
    ):
        slug, rid = slug_dan_ronde(r["sumber_url"])
        if r["turnamen_id"] in sampah_id:
            if not slug:
                tanpa_slug.append(dict(r))
                continue
            per_slug[slug].append({"id": r["id"], "turnamen_id": r["turnamen_id"],
                                   "tahun": tahun_dari_tanggal(r["tanggal"]),
                                   "ronde": rid, "nama_lama": r["tnama"]})
        elif slug:
            nama_bersih[slug][r["tnama"]] += 1

    return per_slug, nama_bersih, tanpa_slug


def nama_dari_api(rid: str) -> str:
    """Nama turnamen dari API Lichess, lewat satu ronde miliknya."""
    from impor_broadcast import BroadcastError, info_ronde
    try:
        return bersih((info_ronde(rid).get("tour") or {}).get("name")) or ""
    except BroadcastError:
        return ""


def baca_cache() -> dict:
    try:
        return json.loads(PATH_CACHE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def tulis_cache(cache: dict) -> None:
    try:
        PATH_CACHE.parent.mkdir(parents=True, exist_ok=True)
        PATH_CACHE.write_text(json.dumps(cache, indent=1, ensure_ascii=False),
                              encoding="utf-8")
    except OSError:
        pass


def nama_target(slug: str, partai: list, nama_bersih: dict, pakai_api: bool,
                cache: dict | None = None) -> str:
    """Nama turnamen yang seharusnya, untuk satu slug."""
    if nama_bersih.get(slug):
        return nama_bersih[slug].most_common(1)[0][0]
    if cache is not None and cache.get(slug):
        return cache[slug]
    if not pakai_api:
        return ""
    for p in partai:
        if p["ronde"]:
            time.sleep(JEDA)
            nama = nama_dari_api(p["ronde"])
            if nama:
                if cache is not None:
                    cache[slug] = nama
                    tulis_cache(cache)
                return nama
    return ""


def turnamen_untuk(conn, nama: str, tahun: int | None, coba: bool) -> int | None:
    """id baris turnamen (nama, tahun); dibuat kalau belum ada."""
    baris = conn.execute(
        "SELECT id FROM turnamen WHERE nama = ? AND tahun IS ?", (nama, tahun)
    ).fetchone()
    if baris:
        return baris["id"]
    if coba:
        return None
    cur = conn.execute(
        "INSERT INTO turnamen(nama, tahun, dibuat_pada) VALUES (?,?,?)",
        (nama, tahun, datetime.now().isoformat(timespec="seconds")),
    )
    return cur.lastrowid


def jalankan(conn, coba: bool, pakai_api: bool = True,
             folder_laporan: Path | None = None) -> dict:
    per_slug, nama_bersih, tanpa_slug = kumpulkan(conn)
    print(f"slug siaran yang punya partai bernama label papan : {len(per_slug)}")
    print(f"partai yang perlu dipindahkan                     : "
          f"{sum(len(v) for v in per_slug.values())}")
    if tanpa_slug:
        print(f"partai tanpa slug (tidak bisa dipulihkan)         : {len(tanpa_slug)}")
    print()

    cache = baca_cache()
    pemetaan, gagal = [], []
    dipindah = 0
    for slug in sorted(per_slug, key=lambda s: -len(per_slug[s])):
        partai = per_slug[slug]
        nama = nama_target(slug, partai, nama_bersih, pakai_api, cache)
        asal = ("database" if nama_bersih.get(slug)
                else "cache" if slug in cache else "API Lichess")
        if not nama:
            gagal.append(slug)
            print(f"  ? {slug[:52]:<52} {len(partai):>4} partai  NAMA TIDAK DITEMUKAN")
            continue
        print(f"  + {slug[:52]:<52} {len(partai):>4} partai -> {nama[:34]} ({asal})")
        for tahun in sorted({p["tahun"] for p in partai}, key=lambda x: (x is None, x)):
            kelompok = [p for p in partai if p["tahun"] == tahun]
            tid = turnamen_untuk(conn, nama, tahun, coba)
            for p in kelompok:
                pemetaan.append({"partai_id": p["id"], "turnamen_lama": p["turnamen_id"],
                                 "nama_lama": p["nama_lama"], "turnamen_baru": tid,
                                 "nama_baru": nama, "tahun": tahun, "slug": slug})
            if not coba and tid is not None:
                conn.executemany("UPDATE partai SET turnamen_id = ? WHERE id = ?",
                                 [(tid, p["id"]) for p in kelompok])
            dipindah += len(kelompok)

    dihapus = 0
    if not coba:
        conn.commit()
        # Baris turnamen sampah yang sudah tidak dipakai partai mana pun.
        kosong = [r["id"] for r in conn.execute("SELECT id, nama FROM turnamen")
                  if nama_sampah(r["nama"])
                  and not conn.execute("SELECT 1 FROM partai WHERE turnamen_id = ? LIMIT 1",
                                       (r["id"],)).fetchone()]
        for i in range(0, len(kosong), 500):
            potong = kosong[i:i + 500]
            conn.execute("DELETE FROM turnamen WHERE id IN (%s)" % ",".join("?" * len(potong)),
                         potong)
        dihapus = len(kosong)
        conn.commit()

        folder = folder_laporan or FOLDER_LAPORAN
        folder.mkdir(parents=True, exist_ok=True)
        cap = datetime.now().strftime("%Y%m%d-%H%M%S")
        path = folder / f"rapikan-turnamen-{cap}.json"
        path.write_text(json.dumps({"dipindah": dipindah, "dihapus": dihapus,
                                    "gagal": gagal, "pemetaan": pemetaan},
                                   indent=1, ensure_ascii=False), encoding="utf-8")
        print(f"\n  pemetaan ditulis: {path}")

    print()
    print("=" * 60)
    print(f"  partai dipindahkan        : {dipindah}")
    print(f"  baris turnamen dihapus    : {dihapus}")
    if gagal:
        print(f"  slug tanpa nama (dibiarkan): {len(gagal)}")
    if coba:
        print("  mode --coba: tidak ada yang disimpan")
    return {"dipindah": dipindah, "dihapus": dihapus, "gagal": gagal}


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--coba", action="store_true", help="hitung saja, tidak menulis database")
    p.add_argument("--tanpa-cadangan", action="store_true",
                   help="lewati cadangan (hanya kalau kamu sudah punya cadangan sendiri)")
    p.add_argument("--tanpa-api", action="store_true",
                   help="jangan tanya API Lichess; slug tanpa nama di database dilewati")
    p.add_argument("--db", type=Path, default=PATH_DB)
    args = p.parse_args(argv)

    if args.coba:
        conn = koneksi(args.db)
        try:
            jalankan(conn, coba=True, pakai_api=not args.tanpa_api)
        finally:
            conn.close()
        return 0

    try:
        with KunciPenulis("rapikan nama turnamen siaran"):
            if not args.tanpa_cadangan:
                from cadangkan_db import cadangkan
                print("Mencadangkan database dulu...")
                cadangkan()
                print()
            conn = koneksi(args.db)
            buat_skema(conn)
            try:
                jalankan(conn, coba=False, pakai_api=not args.tanpa_api)
            finally:
                conn.close()
    except KunciTerpakai as e:
        print(f"Ditolak: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
