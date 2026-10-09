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
from collections import Counter
import urllib.error
import urllib.request
from pathlib import Path

from app.db import FOLDER_DATA, PATH_DB, buat_skema, info, koneksi
from app.impor import EVENT_GENERIK, EVENT_PAPAN, impor_teks, perbarui_elo_terakhir
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


# ---------------------------------------------------------- saringan bintang
#
# Ray, 15 Sep 2026: "database bisa diupdate setiap minggunya dengan memasukan
# game baru para gm dengan ketentuan harus terkenal/super gm".
#
# Saringan tingkat TURNAMEN (tier Lichess) saja tidak cukup: tier 5 ternyata
# juga dipakai bagian amatir — "3rd UzChess Cup | Open" dan bagian-bagian
# Biel semuanya tier 5. Jadi yang disaring tiap PARTAI, dari Elo di header:
#   * salah satu pemain >= MIN_ELO_BINTANG (pemain kuat), atau
#   * kedua pemain >= MIN_ELO_BERDUA (partai antar-GM kuat).
# Partai tanpa Elo dibuang: tidak bisa dibuktikan pemainnya terkenal.
#
# Ray, 15 Sep 2026 (sesudahnya): "gimana kalau salah 1 ada pemain diatas 2500
# maka partainya diambil?" -> ambang 2700 turun ke 2500. Diukur pada 4.321
# partai broadcast hari itu: 396 lolos aturan lama, 1.126 lolos aturan baru;
# FIDE Candidates 56 -> 108 dari 110 (Candidates Women ikut masuk).
# Dengan ambang 2500, syarat "keduanya >= N" otomatis tercakup, jadi disamakan.
#
# Ray, 25 Sep 2026: "kenapa yang lolos cuman dikit padahal sekarang ada
# turnamen dunia". Diukur dulu, pada PGN mentah 27 ronde Olimpiade Samarkand
# (2.364 partai): saringan lama TIDAK bocor -- 0 partai dibuang yang pemainnya
# bergelar GM, 0 yang database tahu GM tapi PGN-nya menulis gelar lain, dan Elo
# tertinggi di seluruh partai yang dibuang cuma 2400-2499. Bagian Women hanya
# lolos 45/1.128 (4%) karena lapangannya memang WCM/WFM/WIM/WGM di bawah 2500,
# bukan karena gelar putri tidak terbaca. Ray lalu memilih "opsi F" untuk
# MELONGGARKAN cakupan (bukan memperbaiki bug): ambang 2500 -> 2400, dan WGM
# ikut lolos. Diukur pada 2.364 partai yang sama:
#   A (lama) 2500 / GM       : Open 381 (31%), Women  45 ( 4%), total 426 (18%)
#   F (kini) 2400 / GM + WGM : Open 481 (39%), Women 179 (16%), total 660 (28%)
MIN_ELO_BINTANG = 2400
MIN_ELO_BERDUA = 2400

# Ray, 21 Sep 2026: "apakah hanya mengambil rating 2500 sudah cukup? atau
# tambahkan dengan yang bergelar GM juga?" -> setelah diukur pada 1.200 partai
# Olimpiade (GM lawan GM di bawah 2500: 0; GM lawan >=2200: 25; GM lawan <2200:
# 54), Ray memilih "opsi 3 saja": SEMUA partai yang salah satu pemainnya
# bergelar GM ikut disimpan, berapa pun ratingnya. Gelar melekat seumur hidup,
# jadi ini memang melonggarkan, bukan menyaring kekuatan.
# WGM ditambahkan 25 Sep 2026 (opsi F di atas): +112 partai putri per pekan,
# tanpa mengubah bagian Open sama sekali. Gelar putri lain (WIM/WFM/WCM) tetap
# di luar; yang terkuat di antaranya masih di bawah 2400 dan sudah tertangkap
# jalur Elo kalau memang sekuat itu.
GELAR_LOLOS = ("GM", "WGM")
PATH_STATUS_MINGGUAN = FOLDER_DATA / "pembaruan-broadcast.json"


def nama_turnamen_ronde(teks: str, cadangan: str = "") -> str:
    """
    Satu nama turnamen untuk SELURUH ronde siaran.

    Satu ronde broadcast selalu satu turnamen, tapi tag Event-nya bisa berbeda
    antar papan. Ditemukan 25 Sep 2026: ronde 1 Dutch Meesterklasse memuat
    "Meesterklasse 2026-2027" (3 papan), "Round 1: <Putih> - <Hitam>" (16
    papan) dan "Meesterklasse 202627" (1 papan) sekaligus, sehingga satu ronde
    pecah jadi 18 baris `turnamen`.

    Dipilih Event yang paling sering muncul dan BUKAN label papan
    (`app.impor.EVENT_PAPAN`); kalau tidak ada yang layak, dipakai `cadangan`
    (nama turnamen dari API Lichess). Nama dari PGN didahulukan supaya nama
    yang sudah lama dipakai database tidak berubah: Olimpiade tetap satu
    "Olymp 2026 Open", bukan pecah per bagian "| Open | Matches 1-12".
    """
    hitung: Counter = Counter()
    for m in re.finditer(r'^\[Event "([^"]*)"\]', teks, re.M):
        ev = m.group(1).strip()
        if ev and not EVENT_PAPAN.match(ev) and not EVENT_GENERIK.match(ev):
            hitung[ev] += 1
    if hitung:
        return hitung.most_common(1)[0][0]
    return (cadangan or "").strip()


def jenis_catatan(bintang: tuple | None) -> str:
    """
    Kunci `sumber_impor.jenis` untuk ronde yang diimpor dengan saringan ini.

    Aturan ikut masuk ke kuncinya: kalau saringannya dilonggarkan, ronde yang
    dulu tersaring otomatis diambil ulang karena kuncinya berbeda.
    """
    if not bintang:
        return "lichess-broadcast"
    gelar = "+".join(bintang[2]) if len(bintang) > 2 and bintang[2] else "-"
    return f"lichess-broadcast>={bintang[0]}/{bintang[1]}/{gelar}"


def _elo(teks_partai: str, warna: str) -> int:
    m = re.search(rf'^\[{warna}Elo "(\d+)"\]', teks_partai, re.M)
    return int(m.group(1)) if m else 0


def partai_bintang(elo_putih: int, elo_hitam: int,
                   min_bintang: int = MIN_ELO_BINTANG, min_berdua: int = MIN_ELO_BERDUA,
                   gelar_putih: str = "", gelar_hitam: str = "",
                   gelar_lolos: tuple[str, ...] = GELAR_LOLOS) -> bool:
    if gelar_lolos and (gelar_putih.upper() in gelar_lolos or gelar_hitam.upper() in gelar_lolos):
        return True
    return max(elo_putih, elo_hitam) >= min_bintang or min(elo_putih, elo_hitam) >= min_berdua


def _gelar(teks_partai: str, warna: str) -> str:
    m = re.search(rf'^\[{warna}Title "([A-Za-z]+)"\]', teks_partai, re.M)
    return m.group(1) if m else ""


def saring_bintang(teks: str, min_bintang: int = MIN_ELO_BINTANG,
                   min_berdua: int = MIN_ELO_BERDUA,
                   gelar_lolos: tuple[str, ...] = GELAR_LOLOS) -> tuple[str, int]:
    """(PGN yang tersisa, jumlah partai yang dibuang)."""
    simpan, dibuang = [], 0
    for p in re.split(r"(?m)^(?=\[Event )", teks):
        if not p.strip():
            continue
        if partai_bintang(_elo(p, "White"), _elo(p, "Black"), min_bintang, min_berdua,
                          _gelar(p, "White"), _gelar(p, "Black"), gelar_lolos):
            simpan.append(p)
        else:
            dibuang += 1
    return "".join(simpan), dibuang


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


def gabung_turnamen(tours: dict[str, dict], tour: dict) -> None:
    """
    Simpan turnamen, pertahankan tier TERTINGGI yang pernah dilihat.

    Ray, 25 Sep 2026: "apakah memang tidak ada turnamen lainnya?" ->
    ternyata ada yang hilang. Lichess melaporkan tier berbeda untuk
    turnamen yang SAMA di dua endpoint: hari itu tiga turnamen tercatat
    tier 4 di /api/broadcast tapi tier 3 di /top, termasuk
    "Dutch Meesterklasse 2026-2027" (KLL9kgXP). Karena /top dibaca
    belakangan, penimpaan biasa (`tours[id] = tour`) menurunkan tier-nya
    dan turnamen itu lenyap dari pembaruan mingguan tanpa jejak apa pun.

    Mana yang benar tidak bisa dipastikan dari luar, jadi dipilih yang
    lebih longgar: tier tertinggi. Lebih baik satu turnamen batas ikut
    diperiksa (dan partainya tetap disaring per partai oleh saringan
    bintang) daripada dilewati diam-diam.
    """
    lama = tours.get(tour["id"])
    if lama and (lama.get("tier") or 0) > (tour.get("tier") or 0):
        tour = {**tour, "tier": lama["tier"]}
    tours[tour["id"]] = tour


def daftar_turnamen_resmi(tier_min: int, hari: int = 0) -> list[dict]:
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
                    gabung_turnamen(tours, d["tour"])
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
                gabung_turnamen(tours, x["tour"])
        past = d.get("past", {})
        for x in past.get("currentPageResults", []):
            gabung_turnamen(tours, x["tour"])
        if not past.get("nextPage"):
            break

    # Turnamen yang belum mulai dilewati: rondenya masih kosong, dan mengunduh
    # placeholder tiap kali cuma membuang permintaan ke Lichess.
    sekarang_ms = time.time() * 1000
    # hari > 0: hanya turnamen yang masih berlangsung dalam `hari` terakhir.
    # Pembaruan mingguan tidak perlu memeriksa ulang ratusan turnamen lama
    # yang rondenya sudah final semua.
    batas_ms = sekarang_ms - hari * 86_400_000 if hari else 0
    hasil = [t for t in tours.values()
             if (t.get("tier") or 0) >= tier_min
             and (t.get("dates") or [0])[0] <= sekarang_ms
             and (t.get("dates") or [0])[-1] >= batas_ms]
    hasil.sort(key=lambda t: (t.get("dates") or [0])[0], reverse=True)
    return hasil


def impor_ronde(conn, r: dict, lihat_saja: bool, indeks: str = "",
                bintang: tuple[int, int] | None = None,
                nama_turnamen: str = "") -> dict | None:
    """
    Impor satu ronde. Mengembalikan None kalau ronde sudah final & tercatat.

    Ronde dianggap final kalau PGN-nya berisi partai dan tidak ada satu pun
    yang berhasil '*'. Ronde yang belum mulai atau masih berjalan dicatat
    selesai=0, jadi akan diambil lagi pada pemanggilan berikutnya.
    """
    r_id = r.get("id")
    nama = r.get("name", "?")
    # Ronde yang diimpor LENGKAP dicatat sebagai "lichess-broadcast"; ronde yang
    # diimpor TERSARING dicatat per aturan saringannya. Kalau aturannya
    # dilonggarkan (15 Sep 2026: 2700 -> 2500), ronde yang dulu tersaring
    # diambil ulang supaya partai yang kini lolos ikut masuk.
    jenis = jenis_catatan(bintang)
    if not lihat_saja and (sudah_selesai(conn, "lichess-broadcast", r_id)
                           or sudah_selesai(conn, jenis, r_id)):
        return None

    try:
        teks = pgn_ronde(r_id)
    except BroadcastError as e:
        print(f"  {indeks}{nama[:44]:<45} GAGAL: {e}")
        return {"gagal": True}

    jumlah = teks.count("[Event ")
    final = jumlah > 0 and '[Result "*"]' not in teks
    status = "final" if final else ("berjalan/belum mulai")
    dibuang = 0
    if bintang:
        teks, dibuang = saring_bintang(teks, *bintang)
    print(f"  {indeks}{nama[:44]:<45} {jumlah:>3} partai  ({status})"
          + (f", {jumlah - dibuang} lolos saringan bintang" if bintang else ""))
    if lihat_saja:
        return {"jumlah": jumlah, "dibuang": dibuang}

    url_ronde = f"https://lichess.org/broadcast/-/-/{r_id}"
    h = impor_teks(conn, teks, sumber="lichess-broadcast", sumber_url_bawaan=url_ronde,
                   nama_turnamen_paksa=nama_turnamen_ronde(teks, nama_turnamen))
    catat(conn, jenis, r_id, nama, url_ronde, jumlah,
          jumlah - dibuang, h.baru, selesai=final)
    rincian = f"      {h.baru} baru, {h.duplikat} duplikat"
    if getattr(h, "diperbarui", 0):
        rincian += f", {h.diperbarui} diperbarui"
    if h.dilewati:
        rincian += f", {h.dilewati} dilewati"
    if h.baru or getattr(h, "diperbarui", 0):
        print(rincian)
    return {"jumlah": jumlah, "baru": h.baru, "duplikat": h.duplikat,
            "diperbarui": getattr(h, "diperbarui", 0), "dibuang": dibuang}


def jalankan_resmi(args) -> int:
    gelar_lolos = tuple(g.upper() for g in (args.gelar_lolos or ()))
    bintang = ((args.min_elo_bintang, args.min_elo_berdua, gelar_lolos)
               if args.min_elo_bintang or gelar_lolos else None)
    print(f"Mengumpulkan turnamen resmi Lichess dengan tier >= {args.tier}"
          + (f", {args.hari} hari terakhir" if args.hari else "") + "...")
    if bintang:
        print(f"Saringan bintang: salah satu Elo >= {bintang[0]}"
              + (f", atau bergelar {'/'.join(bintang[2])}" if bintang[2] else ""))
    tours = daftar_turnamen_resmi(args.tier, args.hari)
    print(f"{len(tours)} turnamen\n")

    conn = None
    if not args.lihat_saja:
        conn = koneksi(args.db)
        buat_skema(conn)
        sebelum = info(conn)["partai"]

    total = {"baru": 0, "duplikat": 0, "diperbarui": 0, "ronde": 0, "lewat": 0, "dibuang": 0}
    dikenal = {t["id"] for t in tours}
    try:
        i = 0
        while i < len(tours):
            t = tours[i]
            i += 1
            print(f"[{i}/{len(tours)}] tier {t.get('tier')}  {t.get('name', '?')[:70]}")
            time.sleep(JEDA)
            try:
                data = info_turnamen(t["id"])
            except BroadcastError as e:
                print(f"  gagal ambil ronde: {e}")
                continue
            ronde = data.get("rounds", [])
            # Turnamen yang dipecah (Candidates Open/Women, Olimpiade Open I-V)
            # hanya muncul sebagian di daftar Lichess; bagian lainnya ikut.
            # "Combined" dilewati: isinya gabungan bagian lain.
            grup = data.get("group") or {}
            for g in grup.get("tours", []):
                if g.get("id") and g["id"] not in dikenal and "combined" not in g.get("name", "").lower():
                    dikenal.add(g["id"])
                    tours.append({"id": g["id"], "tier": t.get("tier"),
                                  "name": f"{grup.get('name', '')} | {g.get('name', '')}"})
            for r in ronde:
                h = impor_ronde(conn, r, args.lihat_saja, bintang=bintang,
                                nama_turnamen=t.get("name", ""))
                if h is None:
                    total["lewat"] += 1
                    continue
                time.sleep(JEDA)
                if h.get("gagal"):
                    continue
                total["ronde"] += 1
                for k in ("baru", "duplikat", "diperbarui", "dibuang"):
                    total[k] += h.get(k, 0)
    except KeyboardInterrupt:
        print("\nDihentikan. Ronde yang sudah final tercatat; jalankan lagi untuk melanjutkan.")

    if args.lihat_saja:
        print("\nMode lihat-saja: tidak ada yang disimpan.")
        return 0

    if total["baru"] or total["diperbarui"]:
        perbarui_elo_terakhir(conn)
    # Evaluasi yang disertakan Lichess langsung jadi analisis (app/eval_lichess.py),
    # jadi partai siaran baru tidak ikut antre engine.
    from app.eval_lichess import isi as isi_dari_eval
    dari_eval = isi_dari_eval(conn)
    sesudah = info(conn)["partai"]
    conn.close()
    print()
    print("=" * 58)
    print(f"  ronde diproses : {total['ronde']}   dilewati (sudah final): {total['lewat']}")
    print(f"  baru {total['baru']}, duplikat {total['duplikat']}, diperbarui {total['diperbarui']}")
    if bintang:
        print(f"  dibuang saringan bintang: {total['dibuang']}")
    print(f"  analisis dari evaluasi Lichess: {dari_eval['dianalisis']}")
    print(f"  database       : {sesudah} partai (+{sesudah - sebelum})")
    try:
        from datetime import datetime
        PATH_STATUS_MINGGUAN.write_text(json.dumps({
            "selesai": datetime.now().strftime("%Y-%m-%d %H:%M"), "turnamen": len(tours),
            "baru": total["baru"], "dibuang": total["dibuang"], "diperbarui": total["diperbarui"],
            "dianalisis_lichess": dari_eval["dianalisis"], "tier": args.tier, "hari": args.hari,
            "min_elo_bintang": args.min_elo_bintang, "min_elo_berdua": args.min_elo_berdua,
            "gelar_lolos": list(gelar_lolos),
        }, indent=1), encoding="utf-8", newline="\n")
    except OSError:
        pass
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
    p.add_argument("--hari", type=int, default=0,
                   help="--resmi: hanya turnamen yang masih berlangsung dalam N hari terakhir (0 = semua)")
    p.add_argument("--min-elo-bintang", type=int, default=0,
                   help=f"--resmi: simpan hanya partai pemain terkenal, salah satu Elo >= N "
                        f"(disarankan {MIN_ELO_BINTANG}); 0 = tanpa saringan")
    p.add_argument("--min-elo-berdua", type=int, default=MIN_ELO_BERDUA,
                   help=f"--resmi: ...atau kedua pemain >= N (default {MIN_ELO_BERDUA})")
    p.add_argument("--gelar-lolos", nargs="*", default=list(GELAR_LOLOS), metavar="GELAR",
                   help=f"--resmi: ...atau salah satu pemain bergelar ini, berapa pun ratingnya "
                        f"(default {' '.join(GELAR_LOLOS)}; kosongkan untuk mematikan)")
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
                       sumber_url_bawaan=url_ronde,
                       nama_turnamen_paksa=nama_turnamen_ronde(teks, tour.get("name", "")))
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
