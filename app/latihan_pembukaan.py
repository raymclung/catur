"""
Latihan pembukaan: posisi repertoire tempat Ray biasa keluar dari jalur GM.

Cara kerja (hanya membaca database):
  1. Ambil partai akun pribadi yang pembukaannya termasuk repertoire Ray —
     Putih: English atau Catalan; Hitam: Sicilian atau Ragozin/QGD. Partai di
     luar itu (1.b4, 1.e4 d5, ...) sengaja TIDAK dipakai: itu pilihan
     pembukaan, bukan kesalahan, dan keputusannya milik Ray.
  2. Telusuri langkah Ray di tiap partai. Di setiap posisi, lihat langkah apa
     yang dimainkan GM (tabel `posisi`, sumber GM, sejak tahun SEJAK).
       - posisi dengan partai GM < MIN_PARTAI_GM  -> sudah di luar buku, berhenti
       - langkah Ray punya porsi GM < PORSI_MIN    -> MENYIMPANG, dicatat, berhenti
       - selain itu langkahnya lazim, lanjut ke langkah Ray berikutnya
     Langkah pertama Ray tidak dinilai: itu yang menentukan repertoire.
  3. Soal = posisi tempat Ray menyimpang minimal MIN_MENYIMPANG kali. Kalau
     cuma sekali, itu kebetulan, bukan kebiasaan.

Jawaban benar = langkah GM mana pun dengan porsi >= PORSI_MIN. Ini statistik
partai GM, bukan penilaian engine: langkah Ray bisa saja tetap sehat, hanya
jarang dimainkan di level itu. Halaman latihan menampilkan engine untuk
menilainya sendiri.
"""

from __future__ import annotations

import threading
import time
from collections import Counter, defaultdict

from fastapi import APIRouter, Query

from .db import koneksi
from .posisi import SUMBER_GM

router = APIRouter(prefix="/api", tags=["latihan"])

MIN_PARTAI_GM = 20      # posisi dengan partai GM lebih sedikit dianggap sudah di luar buku
PORSI_MIN = 0.05        # langkah dengan porsi GM di bawah ini = menyimpang
MIN_MENYIMPANG = 2      # menyimpang sekali belum kebiasaan
SEJAK = "2000"          # partai GM sebelum ini (koleksi pgnmentor sejak 1879) tidak dipakai

ENGLISH, CATALAN, SICILIAN, RAGOZIN = "English", "Catalan", "Sicilian", "Ragozin / QGD"


def pembukaan_repertoire(uci: list[str], warna: str) -> str | None:
    """Nama pembukaan repertoire kalau partai ini termasuk, selain itu None.

    `uci` = langkah partai berurutan dari posisi awal."""
    if not uci:
        return None
    if warna == "putih":
        if uci[0] == "c2c4":
            return ENGLISH
        # Catalan bisa lewat 1.d4 atau 1.Nf3: cukup d4, c4, dan g3 Putih
        # dalam enam langkah pertamanya.
        if {"d2d4", "c2c4", "g2g3"} <= set(uci[0:12:2]):
            return CATALAN
        return None
    if len(uci) >= 2 and uci[0] == "e2e4" and uci[1] == "c7c5":
        return SICILIAN
    if uci[0] == "d2d4" and {"d7d5", "e7e6"} <= set(uci[1:12:2]):
        return RAGOZIN
    return None


def _skor_yang_jalan(menang_putih: int, remis: int, menang_hitam: int, giliran: str) -> float | None:
    selesai = menang_putih + remis + menang_hitam
    if not selesai:
        return None
    menang = menang_putih if giliran == "w" else menang_hitam
    return round(100 * (menang + 0.5 * remis) / selesai, 1)


def langkah_gm(conn, fen: str, sejak: str = SEJAK) -> list[dict]:
    """Langkah GM dari satu posisi, urut terbanyak, dengan porsi dan skor
    DARI SUDUT YANG JALAN (bukan Putih seperti penjelajah pembukaan)."""
    baris = conn.execute(
        f"""
        SELECT p.langkah_uci, p.langkah_san, COUNT(*),
               SUM(g.hasil = '1-0'), SUM(g.hasil = '1/2-1/2'), SUM(g.hasil = '0-1')
        FROM posisi p JOIN partai g ON g.id = p.partai_id
        WHERE p.fen_kunci = ? AND g.sumber IN ({','.join('?' * len(SUMBER_GM))})
              AND g.tanggal >= ?
        GROUP BY p.langkah_uci
        """,
        [fen, *SUMBER_GM, sejak],
    ).fetchall()
    total = sum(r[2] for r in baris)
    giliran = fen.split()[1]
    keluar = [{"uci": r[0], "san": r[1], "n": r[2],
               "porsi": round(100 * r[2] / total, 1) if total else 0.0,
               "skor": _skor_yang_jalan(r[3] or 0, r[4] or 0, r[5] or 0, giliran)}
              for r in baris]
    keluar.sort(key=lambda x: -x["n"])
    return keluar


def _skor_ray(hasil: str, warna: str) -> float | None:
    if hasil == "1/2-1/2":
        return 0.5
    if hasil in ("1-0", "0-1"):
        return 1.0 if (hasil == "1-0") == (warna == "putih") else 0.0
    return None


def hitung_soal(conn, pemain_id: list[int], min_partai_gm: int = MIN_PARTAI_GM,
                porsi_min: float = PORSI_MIN, min_menyimpang: int = MIN_MENYIMPANG,
                sejak: str = SEJAK) -> dict:
    mulai = time.monotonic()
    if not pemain_id:
        return {"soal": [], "ringkasan": {"partai_repertoire": 0}}
    tanda = ",".join("?" * len(pemain_id))

    partai = {}
    for pid, putih, hitam, hasil, tanggal, fmt in conn.execute(
            f"SELECT id, putih_id, hitam_id, hasil, tanggal, format FROM partai "
            f"WHERE putih_id IN ({tanda}) OR hitam_id IN ({tanda})", pemain_id * 2):
        p, h = putih in pemain_id, hitam in pemain_id
        if p == h:      # akun sendiri melawan akun sendiri
            continue
        partai[pid] = {"warna": "putih" if p else "hitam", "hasil": hasil,
                       "tanggal": tanggal or "", "format": fmt}

    langkah = defaultdict(list)     # partai_id -> [(ply, fen, uci, san)]
    for pid, ply, fen, uci, san in conn.execute(
            f"SELECT p.partai_id, p.ply, p.fen_kunci, p.langkah_uci, p.langkah_san "
            f"FROM posisi p JOIN partai g ON g.id = p.partai_id "
            f"WHERE g.putih_id IN ({tanda}) OR g.hitam_id IN ({tanda}) "
            f"ORDER BY p.partai_id, p.ply", pemain_id * 2):
        if pid in partai:
            langkah[pid].append((ply, fen, uci, san))

    # 1. partai repertoire + berapa kali tiap posisi (giliran Ray) dicapai
    dipakai = []
    dicapai = Counter()
    per_pembukaan = Counter()
    for pid, baris in langkah.items():
        if [b[0] for b in baris] != list(range(len(baris))):
            continue    # indeks bolong, jangan menebak
        info = partai[pid]
        nama = pembukaan_repertoire([b[2] for b in baris], info["warna"])
        if not nama:
            continue
        info["pembukaan"] = nama
        per_pembukaan[nama] += 1
        sisa = 0 if info["warna"] == "putih" else 1
        giliran_ray = [b for b in baris if b[0] % 2 == sisa][1:]   # langkah pertama = pilihan repertoire
        dipakai.append((pid, giliran_ray, baris))
        for b in giliran_ray:
            dicapai[b[1]] += 1

    # 2. telusuri sampai menyimpang atau keluar buku
    cache_gm: dict[str, list[dict]] = {}
    temuan: dict[str, dict] = {}
    for pid, giliran_ray, baris in dipakai:
        info = partai[pid]
        for ply, fen, uci, san in giliran_ray:
            if dicapai[fen] < min_menyimpang:
                break   # posisi yang hanya dicapai sekali tidak bisa jadi kebiasaan
            if fen not in cache_gm:
                cache_gm[fen] = langkah_gm(conn, fen, sejak)
            gm = cache_gm[fen]
            total = sum(x["n"] for x in gm)
            if total < min_partai_gm:
                break
            porsi = next((x["n"] for x in gm if x["uci"] == uci), 0) / total
            if porsi >= porsi_min:
                continue
            t = temuan.setdefault(fen, {
                "fen": fen, "warna": info["warna"], "pembukaan": Counter(),
                "menyimpang": 0, "ray": {}, "contoh": None})
            t["menyimpang"] += 1
            t["pembukaan"][info["pembukaan"]] += 1
            r = t["ray"].setdefault(uci, {"uci": uci, "san": san, "n": 0, "skor_total": 0.0, "skor_n": 0})
            r["n"] += 1
            s = _skor_ray(info["hasil"], info["warna"])
            if s is not None:
                r["skor_total"] += s
                r["skor_n"] += 1
            if t["contoh"] is None or info["tanggal"] > t["contoh"]["tanggal"]:
                t["contoh"] = {"partai_id": pid, "tanggal": info["tanggal"], "ply": ply,
                               "jalur": [b[3] for b in baris[:ply]]}
            break

    soal = []
    for fen, t in temuan.items():
        if t["menyimpang"] < min_menyimpang:
            continue
        gm = cache_gm[fen]
        total = sum(x["n"] for x in gm)
        ray = sorted(t["ray"].values(), key=lambda x: -x["n"])
        for r in ray:
            r["skor"] = round(100 * r.pop("skor_total") / r["skor_n"], 1) if r["skor_n"] else None
            r.pop("skor_n")
        soal.append({
            "fen": fen, "warna": t["warna"],
            "pembukaan": t["pembukaan"].most_common(1)[0][0],
            "dicapai": dicapai[fen], "menyimpang": t["menyimpang"],
            "langkah_ray": ray,
            "partai_gm": total,
            "diterima": [x["uci"] for x in gm if x["n"] / total >= porsi_min],
            "gm": gm[:8],
            "contoh": t["contoh"],
        })
    soal.sort(key=lambda s: (-s["menyimpang"], -s["dicapai"]))

    return {
        "soal": soal,
        "ringkasan": {
            "partai_repertoire": len(dipakai),
            "per_pembukaan": dict(per_pembukaan),
            "posisi_diperiksa": len(cache_gm),
            "ambang": {"min_partai_gm": min_partai_gm, "porsi_min_persen": round(100 * porsi_min, 1),
                       "min_menyimpang": min_menyimpang, "gm_sejak": sejak},
            "detik": round(time.monotonic() - mulai, 1),
        },
    }


# Penghitungan penuh butuh puluhan detik (ribuan posisi GM); hasilnya disimpan
# selama isi database tidak berubah. Kuncinya id partai terbesar + jumlah
# baris posisi terbesar, jadi impor atau indeks baru membuatnya dihitung ulang.
_kunci_cache = threading.Lock()
_cache: dict[tuple, dict] = {}


def _sidik_database(conn) -> tuple:
    return (conn.execute("SELECT MAX(id) FROM partai").fetchone()[0],
            conn.execute("SELECT MAX(partai_id) FROM posisi").fetchone()[0])


@router.get("/latihan/pembukaan", summary="Soal pembukaan: posisi repertoire tempat langkah sendiri jarang dimainkan GM")
def api_latihan_pembukaan(pemain_id: list[int] | None = Query(None, description="id akun sendiri; kosong = semua akun pribadi")):
    conn = koneksi()
    try:
        if not pemain_id:
            pemain_id = [r[0] for r in conn.execute("SELECT id FROM pemain WHERE akun_pribadi = 1")]
        kunci = (tuple(sorted(pemain_id)), _sidik_database(conn))
        with _kunci_cache:
            if kunci not in _cache:
                _cache.clear()
                _cache[kunci] = hitung_soal(conn, pemain_id)
            return _cache[kunci]
    finally:
        conn.close()
