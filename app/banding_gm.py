"""
Kamu vs GM dari posisi yang sama (RENCANA Phase 7, "compare with GM games").

Yang sudah ada sebelum modul ini: kartu "Praktik GM" di halaman partai (di
langkah mana SATU partai keluar dari database GM) dan Latihan pembukaan
(posisi tempat langkahku jarang dimainkan GM). Keduanya soal LANGKAH. Yang
belum terjawab adalah soal HASIL: dari posisi yang sama, bagaimana aku
memainkannya dibanding GM?

Cara kerja (hanya membaca database):
  1. Partai akun sendiri dengan saringan lawan yang sama dengan halaman lain
     (bawaan: rating > 2200 atau bergelar — ketentuan Ray).
  2. Tiap partai: posisi TERDALAM (ply >= PLY_MIN) yang masih punya
     >= MIN_PARTAI_GM partai GM = titik keluar buku. Dicari dari ply terdalam
     ke atas, karena posisi dalam hanya punya sedikit baris dan berhenti di
     yang pertama cukup; dari atas ke bawah berarti membaca puluhan ribu
     baris untuk posisi awal.
  3. Partai dikelompokkan per titik. Per titik: skorku, skor GM untuk WARNA
     YANG SAMA, rata kehilangan cp di JENDELA_PLY berikutnya (aku vs GM,
     hanya partai teranalisis), langkah GM dari posisi itu, langkahku, dan
     partai GM terkuat untuk dipelajari.

Kenapa peringkat TIDAK memakai "skor GM − skorku": aku main blitz melawan
lawan setara, GM main klasik melawan GM. Skor mentah keduanya tidak setara.
Yang dibandingkan adalah skorku di posisi itu terhadap rata-rataku SENDIRI
pada partai yang sama-sama dihitung; skor GM dipakai untuk memastikan
posisinya memang sehat (bukan pembukaan yang memang buruk). Kehilangan cp
GM ditampilkan sebagai patokan, dengan catatan bahwa format waktunya beda.
"""

from __future__ import annotations

import sqlite3
import threading
import time
from collections import defaultdict

from fastapi import APIRouter, HTTPException, Query

from .db import koneksi
from .latihan_pembukaan import SEJAK, hasil_partai_gm, langkah_gm, pembukaan_repertoire
from .sebab_kalah import LAWAN_VALID, POTONGAN_ID, _syarat_lawan

router = APIRouter(prefix="/api", tags=["banding-gm"])

MIN_PARTAI_GM = 20     # sama dengan latihan_pembukaan: di bawah ini posisi dianggap di luar buku
PLY_MIN = 6            # titik keluar sebelum langkah ke-4 hampir selalu pilihan pembukaan, bukan buku
PLY_MAKS = 40          # posisi yang dibaca per partai; buku GM jarang sedalam ini untuk blitz
JENDELA_PLY = 30       # 15 langkah sendiri sesudah titik keluar
MIN_PARTAI_SAYA = 3    # titik yang dicapai lebih jarang tidak ditampilkan: satu-dua partai = kebetulan
SAMPEL_GM_ENGINE = 150  # partai GM teranalisis yang dibaca per titik
CONTOH_GM = 3
LANGKAH_GM_MAKS = 4
FORMAT_VALID = ("bullet", "blitz", "rapid", "klasik", "tanpa")


def _skor(hasil: str, warna: str) -> float | None:
    if hasil == "1/2-1/2":
        return 0.5
    if hasil in ("1-0", "0-1"):
        return 1.0 if (hasil == "1-0") == (warna == "putih") else 0.0
    return None


def _persen(total: float, n: int) -> float | None:
    return round(100 * total / n, 1) if n else None


def _partai_saya(conn, pemain_id: list[int], lawan: str, format_: str | None) -> dict[int, dict]:
    tanda = ",".join("?" * len(pemain_id))
    sql = f"""
        SELECT g.id, g.hasil, g.tanggal, g.format,
               CASE WHEN g.putih_id IN ({tanda}) THEN 'putih' ELSE 'hitam' END AS warna,
               CASE WHEN g.putih_id IN ({tanda}) THEN pb.nama ELSE pw.nama END AS lawan
        FROM partai g
        JOIN pemain pw ON pw.id = g.putih_id
        JOIN pemain pb ON pb.id = g.hitam_id
        WHERE (g.putih_id IN ({tanda}) OR g.hitam_id IN ({tanda}))
          AND NOT (g.putih_id IN ({tanda}) AND g.hitam_id IN ({tanda}))
    """
    params: list = list(pemain_id) * 6
    if format_:
        sql += " AND g.format = ?"
        params.append(format_)
    syarat = _syarat_lawan(lawan, tanda)
    if syarat:
        sql += f" AND {syarat}"
        params += list(pemain_id) * syarat.count(f"IN ({tanda})")
    return {r["id"]: dict(r) for r in conn.execute(sql, params)}


def _posisi_partai(conn, ids: list[int]) -> dict[int, list[tuple]]:
    """partai_id -> [(ply, fen, uci, san)] untuk ply <= PLY_MAKS, hanya kalau tidak bolong."""
    per: dict[int, list[tuple]] = defaultdict(list)
    for i in range(0, len(ids), POTONGAN_ID):
        potong = ids[i:i + POTONGAN_ID]
        for pid, ply, fen, uci, san in conn.execute(
                f"SELECT partai_id, ply, fen_kunci, langkah_uci, langkah_san FROM posisi "
                f"WHERE partai_id IN ({','.join('?' * len(potong))}) AND ply <= ? "
                f"ORDER BY partai_id, ply", [*potong, PLY_MAKS]):
            per[pid].append((ply, fen, uci, san))
    # Indeks bolong = jangan menebak jalurnya (sama dengan latihan_pembukaan).
    return {pid: b for pid, b in per.items() if [x[0] for x in b] == list(range(len(b)))}


def _kehilangan_jendela(conn, awal_per_partai: dict[int, tuple[int, str]]) -> dict[int, tuple[int, int, int]]:
    """
    partai_id -> (jumlah langkah, total kehilangan cp, blunder+mistake) untuk
    langkah warna itu di [ply_awal, ply_awal + JENDELA_PLY). Partai yang belum
    dianalisis tidak muncul.
    """
    keluar: dict[int, list[int]] = {}
    ids = list(awal_per_partai)
    for i in range(0, len(ids), POTONGAN_ID):
        potong = ids[i:i + POTONGAN_ID]
        for pid, ply, warna, hilang, klas in conn.execute(
                f"SELECT partai_id, ply, warna, kehilangan_cp, klasifikasi FROM analisis_langkah "
                f"WHERE partai_id IN ({','.join('?' * len(potong))})", potong):
            awal, w = awal_per_partai[pid]
            if warna != w or not (awal <= ply < awal + JENDELA_PLY):
                continue
            k = keluar.setdefault(pid, [0, 0, 0])
            k[0] += 1
            k[1] += hilang
            k[2] += klas in ("blunder", "mistake")
    return {pid: tuple(v) for pid, v in keluar.items()}


def _ringkas_engine(per_partai: list[tuple[int, int, int]]) -> dict:
    langkah = sum(x[0] for x in per_partai)
    return {"partai": len(per_partai), "langkah": langkah,
            "rata_kehilangan": round(sum(x[1] for x in per_partai) / langkah, 1) if langkah else None,
            "kesalahan_per_100": round(100 * sum(x[2] for x in per_partai) / langkah, 1) if langkah else None}


def hitung(conn: sqlite3.Connection, pemain_id: list[int], lawan: str = "ketentuan",
           format_: str | None = None, min_partai_gm: int = MIN_PARTAI_GM,
           min_partai_saya: int = MIN_PARTAI_SAYA, sejak: str = SEJAK) -> dict:
    mulai = time.monotonic()
    kosong = {"posisi": [], "ringkasan": {"partai": 0}}
    if not pemain_id:
        return kosong
    partai = _partai_saya(conn, pemain_id, lawan, format_)
    jalur = _posisi_partai(conn, list(partai))
    if not jalur:
        return kosong
    hasil_gm = hasil_partai_gm(conn, sejak)

    cache_gm: dict[str, list[int]] = {}
    cache_cukup: dict[str, bool] = {}

    def partai_gm(fen: str) -> list[int]:
        if fen not in cache_gm:
            cache_gm[fen] = [p for (p,) in conn.execute(
                "SELECT DISTINCT partai_id FROM posisi WHERE fen_kunci = ?", (fen,)) if p in hasil_gm]
        return cache_gm[fen]

    def cukup_gm(fen: str) -> bool:
        """
        Ada >= min_partai_gm partai GM di posisi ini? Kursor dibaca bertahap
        dan berhenti begitu cukup — tanpa DISTINCT, yang memaksa SQLite membaca
        semua baris dulu. Posisi awal punya puluhan ribu baris; diukur 16 Okt
        2026: membaca semuanya membuat permintaan pertama 20,7 s.
        """
        if fen not in cache_cukup:
            terlihat: set[int] = set()
            cache_cukup[fen] = False
            for (p,) in conn.execute("SELECT partai_id FROM posisi WHERE fen_kunci = ?", (fen,)):
                if p in hasil_gm:
                    terlihat.add(p)
                    if len(terlihat) >= min_partai_gm:
                        cache_cukup[fen] = True
                        break
        return cache_cukup[fen]

    # 2. titik keluar buku per partai
    titik: dict[str, list[tuple[int, int]]] = defaultdict(list)   # fen -> [(partai_id, ply)]
    tanpa_buku = 0
    # Jalur yang sudah keluar buku sebelum PLY_MIN: bukan kesalahan, tapi
    # pilihan pembukaan yang jarang dimainkan GM. Ditampilkan supaya angka
    # "partai dihitung" yang jauh di bawah total bisa dijelaskan.
    jalur_awal: dict[str, int] = defaultdict(int)
    for pid, baris in jalur.items():
        for ply, fen, _uci, _san in reversed(baris):
            if ply < PLY_MIN:
                tanpa_buku += 1
                jalur_awal[" ".join(b[3] for b in baris[:PLY_MIN])] += 1
                break
            if cukup_gm(fen):
                titik[fen].append((pid, ply))
                break

    # rata-rata skor sendiri pada partai yang punya titik (penyebut yang sama dengan tiap baris)
    dihitung = [pid for daftar in titik.values() for pid, _ in daftar]
    skor_semua = [s for s in (_skor(partai[p]["hasil"], partai[p]["warna"]) for p in dihitung) if s is not None]
    rata_saya = _persen(sum(skor_semua), len(skor_semua))

    # engine sendiri: satu kueri untuk semua partai yang dihitung
    awal_saya = {pid: (ply, partai[pid]["warna"]) for daftar in titik.values() for pid, ply in daftar}
    engine_saya = _kehilangan_jendela(conn, awal_saya)
    rata_engine_saya = _ringkas_engine(list(engine_saya.values()))

    # 3. ringkas per titik yang cukup sering dicapai
    keluar = []
    for fen, daftar in titik.items():
        if len(daftar) < min_partai_saya:
            continue
        contoh_pid, contoh_ply = max(daftar, key=lambda x: (partai[x[0]]["tanggal"] or "", x[0]))
        warna = partai[contoh_pid]["warna"]
        skor = [s for s in (_skor(partai[p]["hasil"], warna) for p, _ in daftar) if s is not None]
        menang = sum(1 for s in skor if s == 1.0)
        remis = sum(1 for s in skor if s == 0.5)

        gm_ids = partai_gm(fen)
        skor_gm = [s for s in (_skor(hasil_gm[p], warna) for p in gm_ids) if s is not None]

        # langkah berikut di partaiku dari posisi ini (bisa langkahku atau lawan)
        langkah_saya: dict[str, dict] = {}
        for p, ply in daftar:
            _, _, uci, san = jalur[p][ply]
            r = langkah_saya.setdefault(uci, {"uci": uci, "san": san, "n": 0, "skor_total": 0.0, "skor_n": 0})
            r["n"] += 1
            s = _skor(partai[p]["hasil"], warna)
            if s is not None:
                r["skor_total"] += s
                r["skor_n"] += 1
        for r in langkah_saya.values():
            r["skor"] = _persen(r.pop("skor_total"), r.pop("skor_n"))

        keluar.append({
            "fen": fen, "warna": warna, "ply": contoh_ply,
            "giliran_saya": (contoh_ply % 2 == 0) == (warna == "putih"),
            "jalur": [b[3] for b in jalur[contoh_pid][:contoh_ply]],
            "pembukaan": pembukaan_repertoire([b[2] for b in jalur[contoh_pid]], warna),
            "saya": {"partai": len(daftar), "menang": menang, "remis": remis,
                     "kalah": len(skor) - menang - remis, "skor": _persen(sum(skor), len(skor)),
                     "engine": _ringkas_engine([engine_saya[p] for p, _ in daftar if p in engine_saya]),
                     "langkah": sorted(langkah_saya.values(), key=lambda x: -x["n"])},
            "gm": {"partai": len(gm_ids), "skor": _persen(sum(skor_gm), len(skor_gm))},
            "contoh_saya": {"partai_id": contoh_pid, "tanggal": partai[contoh_pid]["tanggal"],
                            "lawan": partai[contoh_pid]["lawan"]},
        })

    # Peringkat: poin yang hilang dibanding rata-rata sendiri. Posisi yang
    # memang buruk menurut GM (skor < 40% untuk warna ini) tetap ditampilkan
    # tapi tidak dinaikkan — masalahnya pembukaan, bukan cara memainkannya.
    for t in keluar:
        selisih = (t["saya"]["skor"] - rata_saya) if t["saya"]["skor"] is not None and rata_saya is not None else 0.0
        t["selisih_dari_rata"] = round(selisih, 1)
        t["poin_hilang"] = round(-selisih * t["saya"]["partai"] / 100, 1)
        t["posisi_sehat"] = t["gm"]["skor"] is not None and t["gm"]["skor"] >= 40.0
    keluar.sort(key=lambda t: (not t["posisi_sehat"], -t["poin_hilang"]))

    return {
        "posisi": keluar,
        "ringkasan": {
            "partai": len(partai), "partai_berjalur": len(jalur),
            "partai_dihitung": len(dihitung), "keluar_sebelum_ply_min": tanpa_buku,
            "keluar_awal_teratas": sorted(jalur_awal.items(), key=lambda x: -x[1])[:8],
            "titik": len(titik), "titik_ditampilkan": len(keluar),
            "skor_rata_saya": rata_saya, "engine_saya": rata_engine_saya,
            "ambang": {"min_partai_gm": min_partai_gm, "min_partai_saya": min_partai_saya,
                       "ply_min": PLY_MIN, "jendela_ply": JENDELA_PLY, "gm_sejak": sejak},
            "lawan": lawan, "format": format_ or "semua",
            "detik": round(time.monotonic() - mulai, 1),
        },
    }


def rincian(conn: sqlite3.Connection, fen: str, warna: str, sejak: str = SEJAK) -> dict:
    """
    Bagian GM satu titik yang terlalu mahal untuk dihitung di semua titik:
    langkah GM dari posisi ini, kehilangan cp GM di jendela yang sama, dan
    partai GM terkuat untuk dipelajari.
    """
    hasil_gm = hasil_partai_gm(conn, sejak)
    ply_per_partai: dict[int, int] = {}
    for pid, ply in conn.execute("SELECT partai_id, MIN(ply) FROM posisi WHERE fen_kunci = ? GROUP BY partai_id",
                                 (fen,)):
        if pid in hasil_gm:
            ply_per_partai[pid] = ply

    teranalisis = []
    ids = list(ply_per_partai)
    for i in range(0, len(ids), POTONGAN_ID):
        potong = ids[i:i + POTONGAN_ID]
        teranalisis += [r[0] for r in conn.execute(
            f"SELECT partai_id FROM analisis WHERE partai_id IN ({','.join('?' * len(potong))})", potong)]
    teranalisis = sorted(teranalisis, reverse=True)[:SAMPEL_GM_ENGINE]   # terbaru dulu (id naik seiring impor)
    engine_gm = _kehilangan_jendela(conn, {p: (ply_per_partai[p], warna) for p in teranalisis})

    contoh = []
    if ids:
        tanda_elo = "CASE WHEN g.elo_putih IS NULL OR g.elo_hitam IS NULL THEN 0 ELSE g.elo_putih + g.elo_hitam END"
        kandidat = []
        for i in range(0, len(ids), POTONGAN_ID):
            potong = ids[i:i + POTONGAN_ID]
            kandidat += [dict(r) for r in conn.execute(
                f"""SELECT g.id, g.tanggal, g.hasil, g.elo_putih, g.elo_hitam, g.jumlah_langkah,
                           pw.nama AS putih, pb.nama AS hitam, t.nama AS turnamen, {tanda_elo} AS kuat
                    FROM partai g JOIN pemain pw ON pw.id = g.putih_id JOIN pemain pb ON pb.id = g.hitam_id
                    LEFT JOIN turnamen t ON t.id = g.turnamen_id
                    WHERE g.id IN ({','.join('?' * len(potong))})""", potong)]
        kandidat.sort(key=lambda r: (-r["kuat"], -(int((r["tanggal"] or "0")[:4]) if (r["tanggal"] or "")[:4].isdigit() else 0)))
        for r in kandidat[:CONTOH_GM]:
            r.pop("kuat")
            r["ply"] = ply_per_partai[r["id"]]
            contoh.append(r)

    return {"fen": fen, "warna": warna,
            "langkah_gm": langkah_gm(conn, fen, sejak, hasil_gm)[:LANGKAH_GM_MAKS],
            "engine_gm": _ringkas_engine(list(engine_gm.values())),
            "contoh_gm": contoh}


# Penghitungan penuh 2,4–2,7 detik (diukur 16 Okt 2026, 5.883 partai, lewat
# HTTP dengan dua batch engine berjalan; sebelum cukup_gm berhenti dini:
# 20,7 detik); disimpan selama isi database tidak berubah. Sidiknya sama
# dengan latihan_pembukaan ditambah analisis terbaru, karena angka engine
# ikut berubah saat batch analisis berjalan.
_kunci_cache = threading.Lock()
_cache: dict[tuple, dict] = {}
CACHE_MAKS = 8


def _sidik_database(conn) -> tuple:
    return (conn.execute("SELECT MAX(id) FROM partai").fetchone()[0],
            conn.execute("SELECT MAX(partai_id) FROM posisi").fetchone()[0],
            conn.execute("SELECT MAX(id) FROM analisis").fetchone()[0])


def _cek(lawan: str, format_: str | None) -> None:
    if lawan not in LAWAN_VALID:
        raise HTTPException(status_code=422, detail=f"lawan harus salah satu dari: {', '.join(LAWAN_VALID)}")
    if format_ is not None and format_ not in FORMAT_VALID:
        raise HTTPException(status_code=422, detail=f"format harus salah satu dari: {', '.join(FORMAT_VALID)}")


@router.get("/banding-gm", summary="Kamu vs GM dari posisi yang sama: titik keluar buku dan hasilnya")
def api_banding_gm(pemain_id: list[int] | None = Query(None, description="id akun sendiri; kosong = semua akun pribadi"),
                   lawan: str = "ketentuan",
                   format: str | None = None):
    _cek(lawan, format)
    conn = koneksi()
    try:
        if not pemain_id:
            pemain_id = [r[0] for r in conn.execute("SELECT id FROM pemain WHERE akun_pribadi = 1")]
        kunci = (tuple(sorted(pemain_id)), lawan, format, _sidik_database(conn))
        with _kunci_cache:
            if kunci not in _cache:
                if len(_cache) >= CACHE_MAKS:
                    _cache.clear()
                _cache[kunci] = hitung(conn, pemain_id, lawan, format)
            return _cache[kunci]
    finally:
        conn.close()


@router.get("/banding-gm/rincian", summary="Langkah GM, kehilangan cp GM, dan contoh partai GM dari satu posisi")
def api_banding_gm_rincian(fen: str = Query(..., description="fen_kunci posisi (4 bagian pertama FEN)"),
                           warna: str = Query(..., pattern="^(putih|hitam)$")):
    conn = koneksi()
    try:
        return rincian(conn, fen, warna)
    finally:
        conn.close()
