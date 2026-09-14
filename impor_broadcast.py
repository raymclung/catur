"""
Impor partai dari siaran (broadcast) Lichess ke database.

Lichess menyiarkan turnamen offline — termasuk turnamen super GM — dan
menyediakan PGN-nya lewat API resmi. Ini bukan scraping: endpoint-nya memang
disediakan untuk diambil.

Contoh:
    # satu ronde, tempel saja URL dari browser
    python impor_broadcast.py https://lichess.org/broadcast/tech-mahindra.../kmXN5u46

    # seluruh ronde dalam turnamen yang sama
    python impor_broadcast.py kmXN5u46 --turnamen

    # lihat isinya dulu tanpa menyimpan
    python impor_broadcast.py kmXN5u46 --turnamen --lihat-saja

Partai yang masih berjalan tetap diimpor dengan hasil '*'. Kalau perintah ini
dijalankan lagi setelah partainya selesai, versinya diperbarui — bukan
ditambahkan sebagai partai baru.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from app.db import PATH_DB, buat_skema, info, koneksi
from app.impor import impor_teks, perbarui_elo_terakhir
from app.kunci_penulis import KunciPenulis, KunciTerpakai, penulis_berjalan
from app.sumber_impor import catat, sudah_selesai

AKAR = Path(__file__).resolve().parent

USER_AGENT = "catur-analysis/0.1 (belajar catur pribadi; +https://github.com/raymclung/catur)"
JEDA = 1.5          # detik antar permintaan, supaya tidak membebani Lichess
JEDA_429 = 65.0     # kalau kena rate limit

# ID Lichess selalu 8 karakter alfanumerik.
POLA_ID = re.compile(r"^[A-Za-z0-9]{8}$")


class BroadcastError(RuntimeError):
    pass


def minta(url: str, sebagai_teks: bool = False, percobaan: int = 3):
    headers = {"User-Agent": USER_AGENT}
    if sebagai_teks:
        headers["Accept"] = "application/x-chess-pgn"

    for ke in range(percobaan):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=90) as r:
                data = r.read()
            return data.decode("utf-8", errors="replace") if sebagai_teks else json.loads(data)
        except urllib.error.HTTPError as e:
            if e.code == 429:
                print(f"    kena rate limit, tunggu {JEDA_429:.0f} detik...")
                time.sleep(JEDA_429)
                continue
            if e.code == 404:
                raise BroadcastError(f"tidak ditemukan: {url}") from e
            if ke == percobaan - 1:
                raise BroadcastError(f"HTTP {e.code}") from e
            time.sleep(3 * (ke + 1))
        except Exception as e:
            if ke == percobaan - 1:
                raise BroadcastError(f"{type(e).__name__}: {e}") from e
            time.sleep(3 * (ke + 1))
    raise BroadcastError("gagal setelah beberapa percobaan")


def id_ronde(masukan: str) -> str:
    """
    Ambil ID ronde dari URL broadcast, atau terima ID langsung.

    URL-nya berbentuk:
      https://lichess.org/broadcast/<slug-turnamen>/<slug-ronde>/<ID>#boards

    Jadi ID-nya adalah segmen terakhir, setelah tanda '#' dibuang.
    """
    teks = masukan.strip()
    if POLA_ID.match(teks):
        return teks

    tanpa_fragmen = teks.split("#")[0].split("?")[0].rstrip("/")
    segmen = [s for s in tanpa_fragmen.split("/") if s]
    for s in reversed(segmen):
        if POLA_ID.match(s):
            return s
    raise BroadcastError(
        f"Tidak menemukan ID ronde di: {masukan}\n"
        "Tempel URL lengkap dari browser, atau ID 8 karakternya saja."
    )


def info_ronde(rid: str) -> dict:
    """Metadata satu ronde, termasuk turnamen induknya."""
    return minta(f"https://lichess.org/api/broadcast/-/-/{rid}")


def info_turnamen(tour_id: str) -> dict:
    """Metadata satu turnamen broadcast, termasuk daftar rondenya."""
    return minta(f"https://lichess.org/api/broadcast/{tour_id}")


def daftar_ronde(tour_id: str) -> list[dict]:
    """Semua ronde dalam satu turnamen broadcast."""
    return info_turnamen(tour_id).get("rounds", [])


def pgn_ronde(rid: str) -> str:
    return minta(f"https://lichess.org/api/broadcast/round/{rid}.pgn", sebagai_teks=True)


def daftar_turnamen_resmi(tier_min: int) -> list[dict]:
    """
    Kumpulkan turnamen broadcast resmi dari dua endpoint Lichess:
      /api/broadcast?nb=100      100 turnamen resmi terbaru (ndjson)
      /api/broadcast/top?page=N  aktif + selesai, halaman 1..5 (batas Lichess)
    Digabung dan disaring tier >= tier_min (5 = paling elite, mis. Olimpiade).
    """
    tours: dict[str, dict] = {}

    try:
        req = urllib.request.Request("https://lichess.org/api/broadcast?nb=100",
                                     headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=90) as r:
            for baris in r.read().decode("utf-8", errors="replace").splitlines():
                if baris.strip():
                    d = json.loads(baris)
                    tours[d["tour"]["id"]] = d["tour"]
    except Exception as e:
        print(f"  peringatan: /api/broadcast gagal ({e}); lanjut dengan /top saja")

    for page in range(1, 6):
        time.sleep(JEDA)
        try:
            d = minta(f"https://lichess.org/api/broadcast/top?page={page}")
        except BroadcastError:
            break
        for kunci in ("active", "upcoming"):
            for x in d.get(kunci, []):
                tours[x["tour"]["id"]] = x["tour"]
        past = d.get("past", {})
        for x in past.get("currentPageResults", []):
            tours[x["tour"]["id"]] = x["tour"]
        if not past.get("nextPage"):
            break

    # Turnamen yang belum mulai dilewati: rondenya masih kosong, dan mengunduh
    # placeholder tiap kali cuma membuang permintaan ke Lichess.
    sekarang_ms = time.time() * 1000
    hasil = [t for t in tours.values()
             if (t.get("tier") or 0) >= tier_min
             and (t.get("dates") or [0])[0] <= sekarang_ms]
    hasil.sort(key=lambda t: (t.get("dates") or [0])[0], reverse=True)
    return hasil


def impor_ronde(conn, r: dict, lihat_saja: bool, indeks: str = "") -> dict | None:
    """
    Impor satu ronde. Mengembalikan None kalau ronde sudah final & tercatat.

    Ronde dianggap final kalau PGN-nya berisi partai dan tidak ada satu pun
    yang berhasil '*'. Ronde yang belum mulai atau masih berjalan dicatat
    selesai=0, jadi akan diambil lagi pada pemanggilan berikutnya.
    """
    r_id = r.get("id")
    nama = r.get("name", "?")
    if not lihat_saja and sudah_selesai(conn, "lichess-broadcast", r_id):
        return None

    try:
        teks = pgn_ronde(r_id)
    except BroadcastError as e:
        print(f"  {indeks}{nama[:44]:<45} GAGAL: {e}")
        return {"gagal": True}

    jumlah = teks.count("[Event ")
    final = jumlah > 0 and '[Result "*"]' not in teks
    status = "final" if final else ("berjalan/belum mulai")
    print(f"  {indeks}{nama[:44]:<45} {jumlah:>3} partai  ({status})")
    if lihat_saja:
        return {"jumlah": jumlah}

    url_ronde = f"https://lichess.org/broadcast/-/-/{r_id}"
    h = impor_teks(conn, teks, sumber="lichess-broadcast", sumber_url_bawaan=url_ronde)
    catat(conn, "lichess-broadcast", r_id, nama, url_ronde, jumlah,
          jumlah, h.baru, selesai=final)
    rincian = f"      {h.baru} baru, {h.duplikat} duplikat"
    if getattr(h, "diperbarui", 0):
        rincian += f", {h.diperbarui} diperbarui"
    if h.dilewati:
        rincian += f", {h.dilewati} dilewati"
    if h.baru or getattr(h, "diperbarui", 0):
        print(rincian)
    return {"jumlah": jumlah, "baru": h.baru, "duplikat": h.duplikat,
            "diperbarui": getattr(h, "diperbarui", 0)}


def jalankan_resmi(args) -> int:
    print(f"Mengumpulkan turnamen resmi Lichess dengan tier >= {args.tier}...")
    tours = daftar_turnamen_resmi(args.tier)
    print(f"{len(tours)} turnamen\n")

    conn = None
    if not args.lihat_saja:
        conn = koneksi(args.db)
        buat_skema(conn)
        sebelum = info(conn)["partai"]

    total = {"baru": 0, "duplikat": 0, "diperbarui": 0, "ronde": 0, "lewat": 0}
    try:
        for i, t in enumerate(tours, start=1):
            print(f"[{i}/{len(tours)}] tier {t.get('tier')}  {t.get('name', '?')[:70]}")
            time.sleep(JEDA)
            try:
                ronde = daftar_ronde(t["id"])
            except BroadcastError as e:
                print(f"  gagal ambil ronde: {e}")
                continue
            for r in ronde:
                h = impor_ronde(conn, r, args.lihat_saja)
                if h is None:
                    total["lewat"] += 1
                    continue
                time.sleep(JEDA)
                if h.get("gagal"):
                    continue
                total["ronde"] += 1
                for k in ("baru", "duplikat", "diperbarui"):
                    total[k] += h.get(k, 0)
    except KeyboardInterrupt:
        print("\nDihentikan. Ronde yang sudah final tercatat; jalankan lagi untuk melanjutkan.")

    if args.lihat_saja:
        print("\nMode lihat-saja: tidak ada yang disimpan.")
        return 0

    if total["baru"] or total["diperbarui"]:
        perbarui_elo_terakhir(conn)
    sesudah = info(conn)["partai"]
    conn.close()
    print()
    print("=" * 58)
    print(f"  ronde diproses : {total['ronde']}   dilewati (sudah final): {total['lewat']}")
    print(f"  baru {total['baru']}, duplikat {total['duplikat']}, diperbarui {total['diperbarui']}")
    print(f"  database       : {sesudah} partai (+{sesudah - sebelum})")
    return 0


def main() -> int:
    """Impor = penulis besar: ambil kunci dulu (CLAUDE.md bagian 7). Dari
    perbarui.py kuncinya diwarisi (lingkungan_anak). --lihat-saja tidak menulis."""
    if "--lihat-saja" in sys.argv[1:]:
        return _impor()
    lain = penulis_berjalan()
    if lain:
        print("Penulis besar lain sedang berjalan, impor tidak dijalankan:", file=sys.stderr)
        for b in lain:
            print("  ..." + b, file=sys.stderr)
        return 1
    try:
        with KunciPenulis("impor_broadcast.py " + " ".join(sys.argv[1:])[:80]):
            return _impor()
    except KunciTerpakai as e:
        print(e, file=sys.stderr)
        return 1


def _impor() -> int:
    p = argparse.ArgumentParser(
        description="Impor partai broadcast Lichess ke database")
    p.add_argument("sumber", nargs="?", help="URL broadcast atau ID ronde 8 karakter")
    p.add_argument("--resmi", action="store_true",
                   help="pembaruan berkala: semua turnamen resmi Lichess (tier >= --tier), "
                        "ronde yang sudah final dilewati")
    p.add_argument("--tier", type=int, default=4,
                   help="tier minimal untuk --resmi (5 = paling elite; default 4)")
    p.add_argument("--turnamen", action="store_true",
                   help="impor semua ronde dalam turnamen yang sama")
    p.add_argument("--lihat-saja", action="store_true",
                   help="tampilkan isinya tanpa menyimpan ke database")
    p.add_argument("--db", type=Path, default=PATH_DB)
    args = p.parse_args()

    if args.resmi:
        return jalankan_resmi(args)
    if not args.sumber:
        p.error("beri URL/ID ronde, atau pakai --resmi")

    try:
        rid = id_ronde(args.sumber)
        meta = info_ronde(rid)
    except BroadcastError as e:
        print(f"Gagal: {e}", file=sys.stderr)
        return 1

    tour = meta.get("tour", {})
    ronde_ini = meta.get("round", {})

    print(f"Turnamen : {tour.get('name', '?')}")
    print(f"Tour ID  : {tour.get('id', '?')}")
    print()

    if args.turnamen:
        try:
            ronde = daftar_ronde(tour.get("id", ""))
        except BroadcastError as e:
            print(f"Gagal mengambil daftar ronde: {e}", file=sys.stderr)
            return 1
        if not ronde:
            ronde = [ronde_ini]
    else:
        ronde = [ronde_ini]

    print(f"{len(ronde)} ronde akan diproses")
    print()

    conn = None
    if not args.lihat_saja:
        conn = koneksi(args.db)
        buat_skema(conn)
        sebelum = info(conn)

    total_baru = total_dup = total_perbarui = 0

    for i, r in enumerate(ronde, start=1):
        r_id = r.get("id")
        nama = r.get("name", "?")
        status = ("selesai" if r.get("finished") else
                  "SEDANG BERLANGSUNG" if r.get("ongoing") else "belum mulai")

        if i > 1:
            time.sleep(JEDA)

        try:
            teks = pgn_ronde(r_id)
        except BroadcastError as e:
            print(f"  [{i}/{len(ronde)}] {nama[:44]:<45} GAGAL: {e}")
            continue

        jumlah = teks.count("[Event ")
        print(f"  [{i}/{len(ronde)}] {nama[:44]:<45} {jumlah:>3} partai  ({status})")

        if args.lihat_saja:
            continue

        url_ronde = (f"https://lichess.org/broadcast/-/-/{r_id}")
        h = impor_teks(conn, teks, sumber="lichess-broadcast",
                       sumber_url_bawaan=url_ronde)
        catat(conn, "lichess-broadcast", r_id, nama, url_ronde, jumlah, jumlah,
              h.baru, selesai=(jumlah > 0 and '[Result "*"]' not in teks))
        total_baru += h.baru
        total_dup += h.duplikat
        total_perbarui += getattr(h, "diperbarui", 0)

        rincian = f"      {h.baru} baru, {h.duplikat} duplikat"
        if getattr(h, "diperbarui", 0):
            rincian += f", {h.diperbarui} diperbarui"
        if h.dilewati:
            rincian += f", {h.dilewati} dilewati"
        print(rincian)

    if args.lihat_saja:
        print()
        print("Mode lihat-saja: tidak ada yang disimpan.")
        return 0

    perbarui_elo_terakhir(conn)
    sesudah = info(conn)
    conn.close()

    print()
    print("=" * 58)
    print(f"  Baru       : {total_baru}")
    print(f"  Duplikat   : {total_dup}")
    if total_perbarui:
        print(f"  Diperbarui : {total_perbarui}")
    print(f"  Database   : {sesudah['partai']} partai "
          f"(+{sesudah['partai'] - sebelum['partai']}), "
          f"{sesudah['pemain']} pemain, {sesudah['turnamen']} turnamen")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
