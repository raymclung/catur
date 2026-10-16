"""
Mentor: belajar dari satu GM (RENCANA §16 "GM Mentor Mode").

"Rencana minggu ini" (app/rencana.py) sudah memilih kelemahan dari angka
sendiri. Yang belum ada adalah bagian §16: pilih seorang GM, lalu kurikulum
disusun dari PARTAI GM ITU — dan RENCANA menulis "jangan membuat curriculum
secara asal, gunakan data game yang tersedia". Jadi tiap tema punya kriteria
terukur dari analisis engine, dan alasan tiap partai dipilih ikut dikirim.

Empat tema (satu per minggu):

  pembukaan   GM menang dalam pembukaan repertoire Ray, dengan warna yang
              sama dengan Ray memainkannya (English/Catalan sebagai Putih,
              Sicilian/Ragozin sebagai Hitam; pembukaan_repertoire()).
  konversi    GM unggul >= +2,00 (skor engine, sudut GM), sesudah itu tidak
              pernah turun di bawah +1,00, dan menang minimal 15 langkah
              kemudian: keunggulan dikonversi dengan sabar, bukan lawan runtuh.
  posisional  GM menang, lawan tanpa blunder dan paling banyak satu mistake,
              partai >= 35 langkah, dan keunggulan +2,00 belum ada sebelum
              langkah 25: menang dengan menumpuk tekanan kecil.
  endgame     Partai sampai akhir permainan (ply 80, batas FASE yang sama
              dengan Ringkasan) dengan skor engine -0,50..+1,50 untuk GM di
              titik itu, lalu GM menang: menang karena teknik akhir.

Urutan minggu ditentukan kelemahan Ray TERHADAP GM INI. Tiap langkah sendiri
masuk tepat satu ember: "konversi" kalau skor engine sebelum langkah itu
>= +2,00 (sedang menang), selain itu menurut fase (pembukaan / tengah =
posisional / akhir = endgame). Per ember dihitung kesalahan (blunder +
mistake) per 100 langkah oleh fungsi yang SAMA untuk Ray dan GM
(profil_kesalahan), lalu

    kesalahan berlebih = (angka Ray - angka GM) x porsi langkah Ray di ember itu

dalam satuan "kesalahan per 100 langkahmu"; keempatnya menjumlah ke selisih
total. Rasio Ray / GM sempat dipakai (16 Okt 2026) dan dibuang: kesalahan
pembukaan GM nyaris nol (Carlsen 0,22), jadi rasio pembukaan selalu raksasa
(23x) dan minggu 1 selalu pembukaan, GM siapa pun.

Risiko yang diterima: partai Ray kebanyakan blitz dan dianalisis dengan
profil berbeda dari partai GM (sebagian besar `cepat`). Rasio dipakai untuk
URUTAN, bukan untuk vonis "kamu 5x lebih buruk"; catatan itu ikut dikirim.
"""

from __future__ import annotations

import sqlite3
import threading
import time
from collections import defaultdict

from fastapi import APIRouter, HTTPException, Query

from .db import koneksi
from .latihan_pembukaan import CATALAN, ENGLISH, RAGOZIN, SICILIAN, pembukaan_repertoire
from .ringkasan import FASE
from .sebab_kalah import LAWAN_VALID, POTONGAN_ID, _syarat_lawan

router = APIRouter(prefix="/api", tags=["mentor"])

UNGGUL_CP = 200          # keunggulan "menang" menurut skor engine
JAGA_CP = 100            # sesudah unggul, tidak pernah turun di bawah ini (konversi bersih)
KONVERSI_MIN_PLY = 30    # menang minimal 15 langkah sesudah unggul
POSISIONAL_MIN_PLY = 70  # partai minimal 35 langkah
POSISIONAL_UNGGUL_PLY = 50   # +2 belum ada sebelum langkah 25
ENDGAME_PLY = 80         # = awal fase akhir di ringkasan.FASE
ENDGAME_RENTANG = (-50, 150)
PARTAI_PER_TEMA = 5
PER_PEMBUKAAN = 2        # variasi: paling banyak dua partai per pembukaan repertoire
MIN_PARTAI_GM = 50       # GM dengan partai teranalisis lebih sedikit: kurikulumnya terlalu tipis
PER_LAWAN = 2            # variasi: tanpa ini satu tema bisa berisi lima partai melawan lawan yang sama

REPERTOIRE_PER_WARNA = {"putih": (ENGLISH, CATALAN), "hitam": (SICILIAN, RAGOZIN)}
TEMA = {
    "pembukaan": ("Pembukaan repertoire-mu", "langkah di fase pembukaan"),
    "konversi": ("Mengubah keunggulan jadi kemenangan", "langkah saat skor engine >= +2,00"),
    "posisional": ("Tekanan posisional", "langkah di fase tengah, belum unggul +2,00"),
    "endgame": ("Teknik endgame", "langkah di fase akhir, belum unggul +2,00"),
}
TEMA_FASE = {"pembukaan": "pembukaan", "tengah": "posisional", "akhir": "endgame"}
# Satu partai hanya di satu minggu, jadi urutan pengambilan menentukan siapa
# yang dapat partai yang cocok untuk dua tema. "posisional" kriteria paling
# umum (partai endgame yang menang tanpa blunder lawan juga lolos), jadi
# mengambil terakhir; dulu ia mengambil lebih dulu dan tema endgame kosong.
URUTAN_ALOKASI = ("pembukaan", "konversi", "endgame", "posisional")


def _fase(ply: int) -> str:
    for nama, awal, akhir in FASE:
        if awal <= ply < akhir:
            return nama
    return FASE[-1][0]


def _menang(hasil: str, warna: str) -> bool:
    return (hasil == "1-0" and warna == "putih") or (hasil == "0-1" and warna == "hitam")


# ---------------------------------------------------------------- profil kesalahan

def profil_kesalahan(conn: sqlite3.Connection, partai: dict[int, dict]) -> tuple[dict, dict[int, dict]]:
    """
    Ukuran kesalahan untuk sekumpulan partai dari sudut `warna` tiap partai,
    plus fitur per partai (dipakai memilih partai GM).

    `partai` = {partai_id: {"warna": "putih"/"hitam", "hasil": ...}}. Partai
    yang belum dianalisis tidak punya baris dan tidak ikut dihitung.
    """
    ember = {tema: {"langkah": 0, "kesalahan": 0} for tema in TEMA}
    fitur: dict[int, dict] = {}
    ids = list(partai)
    for i in range(0, len(ids), POTONGAN_ID):
        potong = ids[i:i + POTONGAN_ID]
        baris = defaultdict(list)
        for pid, ply, warna, ev_putih, ev_sebelum, klas in conn.execute(
                f"SELECT partai_id, ply, warna, eval_putih_cp, eval_sebelum_cp, klasifikasi FROM analisis_langkah "
                f"WHERE partai_id IN ({','.join('?' * len(potong))}) ORDER BY partai_id, ply", potong):
            baris[pid].append((ply, warna, ev_putih, ev_sebelum, klas))
        for pid, rows in baris.items():
            w = partai[pid]["warna"]
            tanda = 1 if w == "putih" else -1
            f = {"ply": rows[-1][0] + 1, "unggul_ply": None, "min_sesudah_unggul": None,
                 "eval_ply_akhir": None, "lawan_blunder": 0, "lawan_mistake": 0}
            for ply, warna, ev_putih, ev_sebelum, klas in rows:
                ev = tanda * ev_putih                       # sudut pemain ini, sesudah langkah ply
                if warna == w:
                    # eval_sebelum_cp sudah dari sudut yang jalan (= pemain ini)
                    d = ember["konversi" if ev_sebelum >= UNGGUL_CP else TEMA_FASE[_fase(ply)]]
                    d["langkah"] += 1
                    d["kesalahan"] += klas in ("blunder", "mistake")
                else:
                    f["lawan_blunder"] += klas == "blunder"
                    f["lawan_mistake"] += klas == "mistake"
                if f["unggul_ply"] is None:
                    if ev >= UNGGUL_CP:
                        f["unggul_ply"] = ply
                        f["min_sesudah_unggul"] = ev
                else:
                    f["min_sesudah_unggul"] = min(f["min_sesudah_unggul"], ev)
                if ply == ENDGAME_PLY:
                    f["eval_ply_akhir"] = ev
            fitur[pid] = f

    pernah_unggul = [p for p, f in fitur.items() if f["unggul_ply"] is not None]
    gagal = [p for p in pernah_unggul if not _menang(partai[p]["hasil"], partai[p]["warna"])]
    ukuran = {
        "partai": len(fitur),
        "langkah": sum(v["langkah"] for v in ember.values()),
        "ember": {k: {**v, "per_100": round(100 * v["kesalahan"] / v["langkah"], 2) if v["langkah"] else None}
                  for k, v in ember.items()},
        "konversi": {"pernah_unggul": len(pernah_unggul), "gagal": len(gagal),
                     "persen_gagal": round(100 * len(gagal) / len(pernah_unggul), 1) if pernah_unggul else None},
    }
    return ukuran, fitur


def urutan_tema(saya: dict, gm: dict) -> list[dict]:
    """Tema diurut dari kesalahan berlebih terbesar (lihat docstring modul)."""
    total = saya.get("langkah") or 0
    keluar = []
    for tema in TEMA:
        a, b = saya["ember"][tema]["per_100"], gm["ember"][tema]["per_100"]
        porsi = saya["ember"][tema]["langkah"] / total if total else 0.0
        berlebih = round((a - b) * porsi, 2) if a is not None and b is not None else None
        keluar.append({"tema": tema, "nama": TEMA[tema][0], "ember": TEMA[tema][1],
                       "saya": a, "gm": b, "porsi_langkah_saya": round(100 * porsi, 1),
                       "berlebih": berlebih})
    keluar.sort(key=lambda t: (t["berlebih"] is None, -(t["berlebih"] or 0)))
    return keluar


# ---------------------------------------------------------------- partai GM

def _info_partai(conn, ids: list[int]) -> dict[int, dict]:
    info = {}
    for i in range(0, len(ids), POTONGAN_ID):
        potong = ids[i:i + POTONGAN_ID]
        for r in conn.execute(
                f"""SELECT g.id, g.tanggal, g.hasil, g.putih_id, g.hitam_id, g.elo_putih, g.elo_hitam,
                           g.jumlah_langkah, pw.nama AS putih, pb.nama AS hitam, t.nama AS turnamen
                    FROM partai g JOIN pemain pw ON pw.id = g.putih_id JOIN pemain pb ON pb.id = g.hitam_id
                    LEFT JOIN turnamen t ON t.id = g.turnamen_id
                    WHERE g.id IN ({','.join('?' * len(potong))})""", potong):
            info[r["id"]] = dict(r)
    return info


def _langkah_awal(conn, ids: list[int]) -> dict[int, list[str]]:
    awal: dict[int, list[str]] = defaultdict(list)
    for i in range(0, len(ids), POTONGAN_ID):
        potong = ids[i:i + POTONGAN_ID]
        for pid, uci in conn.execute(
                f"SELECT partai_id, langkah_uci FROM posisi WHERE partai_id IN ({','.join('?' * len(potong))}) "
                f"AND ply < 12 ORDER BY partai_id, ply", potong):
            awal[pid].append(uci)
    return awal


def _cp(cp: int | None) -> str:
    if cp is None:
        return "?"
    return f"{cp / 100:+.2f}".replace(".", ",")


def pilih_partai(conn, gm_id: int, partai: dict[int, dict], fitur: dict[int, dict]) -> dict[str, list[dict]]:
    """Partai GM per tema, dengan alasan berangka. Satu partai hanya di satu tema."""
    menang = [p for p, f in fitur.items() if _menang(partai[p]["hasil"], partai[p]["warna"])]
    info = _info_partai(conn, menang)
    awal = _langkah_awal(conn, menang)

    def elo_lawan(p):
        g = info[p]
        return (g["elo_hitam"] if g["putih_id"] == gm_id else g["elo_putih"]) or 0

    def urut(ps):
        return sorted(ps, key=lambda p: (-elo_lawan(p), -(int(info[p]["tanggal"][:4]) if (info[p]["tanggal"] or "")[:4].isdigit() else 0), -p))

    kandidat: dict[str, list[tuple[int, str]]] = {t: [] for t in TEMA}
    per_pembukaan: dict[str, list[int]] = defaultdict(list)
    for p in menang:
        warna = partai[p]["warna"]
        nama = pembukaan_repertoire(awal.get(p, []), warna)
        if nama in REPERTOIRE_PER_WARNA[warna]:
            per_pembukaan[nama].append(p)
    jumlah_pembukaan = sum(len(ps) for ps in per_pembukaan.values())
    for nama, ps in per_pembukaan.items():
        for p in urut(ps)[:PER_PEMBUKAAN]:
            warna = "Putih" if partai[p]["warna"] == "putih" else "Hitam"
            kandidat["pembukaan"].append((p, f"{nama}, dimainkan GM sebagai {warna} — sama dengan repertoire-mu"))

    for p in urut(menang):
        f = fitur[p]
        u = f["unggul_ply"]
        if u is not None and f["min_sesudah_unggul"] >= JAGA_CP and f["ply"] - u >= KONVERSI_MIN_PLY:
            kandidat["konversi"].append((p, f"unggul {_cp(UNGGUL_CP)} sejak langkah {u // 2 + 1}, tidak pernah turun "
                                            f"di bawah {_cp(JAGA_CP)}, menang di langkah {(f['ply'] + 1) // 2}"))
        if f["lawan_blunder"] == 0 and f["lawan_mistake"] <= 1 and f["ply"] >= POSISIONAL_MIN_PLY \
                and (u is None or u >= POSISIONAL_UNGGUL_PLY):
            kapan = f"keunggulan {_cp(UNGGUL_CP)} baru di langkah {u // 2 + 1}" if u is not None else "tanpa keunggulan besar"
            kandidat["posisional"].append((p, f"lawan tanpa blunder, {f['lawan_mistake']} mistake; {kapan}; "
                                              f"menang di langkah {(f['ply'] + 1) // 2}"))
        e = f["eval_ply_akhir"]
        if e is not None and ENDGAME_RENTANG[0] <= e <= ENDGAME_RENTANG[1]:
            kandidat["endgame"].append((p, f"skor engine di langkah {ENDGAME_PLY // 2 + 1}: {_cp(e)}; "
                                           f"menang di langkah {(f['ply'] + 1) // 2}"))

    dipakai: set[int] = set()
    keluar: dict[str, list[dict]] = {}
    for tema in URUTAN_ALOKASI:
        daftar = []
        per_lawan: dict[int, int] = defaultdict(int)
        for p, alasan in kandidat[tema]:
            g = info[p]
            gm_putih = g["putih_id"] == gm_id
            id_lawan = g["hitam_id"] if gm_putih else g["putih_id"]
            if p in dipakai or len(daftar) >= PARTAI_PER_TEMA or per_lawan[id_lawan] >= PER_LAWAN:
                continue
            dipakai.add(p)
            per_lawan[id_lawan] += 1
            daftar.append({"partai_id": p, "tanggal": g["tanggal"], "turnamen": g["turnamen"],
                           "warna_gm": "putih" if gm_putih else "hitam",
                           "lawan": g["hitam"] if gm_putih else g["putih"], "elo_lawan": elo_lawan(p) or None,
                           "hasil": g["hasil"], "jumlah_langkah": g["jumlah_langkah"], "alasan": alasan})
        keluar[tema] = {"partai": daftar,
                        "kandidat": jumlah_pembukaan if tema == "pembukaan" else len(kandidat[tema])}
    return keluar


# ---------------------------------------------------------------- kurikulum

def _partai_gm(conn, gm_id: int) -> dict[int, dict]:
    return {r[0]: {"warna": "putih" if r[1] == gm_id else "hitam", "hasil": r[2]} for r in conn.execute(
        """SELECT g.id, g.putih_id, g.hasil FROM partai g JOIN analisis a ON a.partai_id = g.id
           WHERE (g.putih_id = ? OR g.hitam_id = ?) AND g.hasil IN ('1-0', '0-1', '1/2-1/2')
             AND g.putih_id <> g.hitam_id""", (gm_id, gm_id))}


def _partai_saya(conn, pemain_id: list[int], lawan: str) -> dict[int, dict]:
    tanda = ",".join("?" * len(pemain_id))
    sql = f"""
        SELECT g.id, g.hasil, CASE WHEN g.putih_id IN ({tanda}) THEN 'putih' ELSE 'hitam' END AS warna
        FROM partai g JOIN analisis a ON a.partai_id = g.id
        JOIN pemain pw ON pw.id = g.putih_id JOIN pemain pb ON pb.id = g.hitam_id
        WHERE (g.putih_id IN ({tanda}) OR g.hitam_id IN ({tanda}))
          AND NOT (g.putih_id IN ({tanda}) AND g.hitam_id IN ({tanda}))
          AND g.hasil IN ('1-0', '0-1', '1/2-1/2')
    """
    params: list = list(pemain_id) * 5
    syarat = _syarat_lawan(lawan, tanda)
    if syarat:
        sql += f" AND {syarat}"
        params += list(pemain_id) * syarat.count(f"IN ({tanda})")
    return {r["id"]: {"warna": r["warna"], "hasil": r["hasil"]} for r in conn.execute(sql, params)}


def kurikulum(conn: sqlite3.Connection, gm_id: int, pemain_id: list[int], lawan: str = "ketentuan") -> dict | None:
    mulai = time.monotonic()
    gm = conn.execute("SELECT id, nama, gelar, elo_terakhir FROM pemain WHERE id = ?", (gm_id,)).fetchone()
    if gm is None:
        return None
    partai_gm = _partai_gm(conn, gm_id)
    ukuran_gm, fitur_gm = profil_kesalahan(conn, partai_gm)
    partai_saya = _partai_saya(conn, pemain_id, lawan) if pemain_id else {}
    ukuran_saya, _ = profil_kesalahan(conn, partai_saya)

    dasar = {"gm": dict(gm), "ukuran_gm": ukuran_gm, "ukuran_saya": ukuran_saya, "lawan": lawan,
             "ambang": {"min_partai_gm": MIN_PARTAI_GM, "unggul_cp": UNGGUL_CP, "jaga_cp": JAGA_CP,
                        "endgame_ply": ENDGAME_PLY, "endgame_rentang": list(ENDGAME_RENTANG)}}
    if ukuran_gm["partai"] < MIN_PARTAI_GM:
        return {**dasar, "minggu": [], "cukup": False,
                "detik": round(time.monotonic() - mulai, 1)}

    urut = urutan_tema(ukuran_saya, ukuran_gm)
    partai = pilih_partai(conn, gm_id, partai_gm, fitur_gm)
    minggu = [{**t, "minggu": i + 1, **partai[t["tema"]]} for i, t in enumerate(urut)]
    return {**dasar, "minggu": minggu, "cukup": True,
            "catatan": [
                "Urutan minggu = kesalahan berlebih: (kesalahanmu per 100 langkah - milik GM) x porsi "
                "langkahmu di kelompok itu, terbesar dulu. Tiap langkahmu masuk satu kelompok: sedang unggul "
                ">= +2,00, atau menurut fase. Dipakai untuk urutan, bukan vonis: partaimu kebanyakan blitz dan "
                "dianalisis dengan profil berbeda dari partai GM.",
                "Partai dipilih dari kemenangan GM yang sudah dianalisis, lawan terkuat dulu; tiap partai "
                "hanya muncul di satu minggu.",
            ],
            "detik": round(time.monotonic() - mulai, 1)}


# Satu GM ±2–4 detik (Carlsen: 743 ribu baris analisis); disimpan per GM selama
# isi database tidak berubah, sama seperti latihan_pembukaan dan banding_gm.
_kunci_cache = threading.Lock()
_cache: dict[tuple, dict] = {}
CACHE_MAKS = 12


def _sidik_database(conn) -> tuple:
    return (conn.execute("SELECT MAX(id) FROM partai").fetchone()[0],
            conn.execute("SELECT MAX(id) FROM analisis").fetchone()[0])


@router.get("/mentor", summary="Kurikulum 4 minggu dari partai satu GM, diurutkan menurut kelemahan sendiri")
def api_mentor(gm: int = Query(..., description="id pemain GM yang ingin dipelajari"),
               pemain_id: list[int] | None = Query(None, description="id akun sendiri; kosong = semua akun pribadi"),
               lawan: str = "ketentuan"):
    if lawan not in LAWAN_VALID:
        raise HTTPException(status_code=422, detail=f"lawan harus salah satu dari: {', '.join(LAWAN_VALID)}")
    conn = koneksi()
    try:
        if not pemain_id:
            pemain_id = [r[0] for r in conn.execute("SELECT id FROM pemain WHERE akun_pribadi = 1")]
        if gm in pemain_id:
            raise HTTPException(status_code=422, detail="Pilih GM, bukan akun sendiri")
        kunci = (gm, tuple(sorted(pemain_id)), lawan, _sidik_database(conn))
        with _kunci_cache:
            if kunci not in _cache:
                if len(_cache) >= CACHE_MAKS:
                    _cache.clear()
                hasil = kurikulum(conn, gm, pemain_id, lawan)
                if hasil is None:
                    raise HTTPException(status_code=404, detail=f"Pemain {gm} tidak ada")
                _cache[kunci] = hasil
            return _cache[kunci]
    finally:
        conn.close()
