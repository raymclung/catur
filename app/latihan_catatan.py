"""
Catatan jawaban latihan di database (A38), menggantikan localStorage browser.

Kenapa pindah ke database: catatan di browser hilang kalau data situs
dibersihkan, dan tidak bisa dijawab "apakah latihannya ada hasilnya" karena
yang disimpan hanya status terakhir. Di sini tiap jawaban satu baris.

Status sebuah soal = baris AKTIF terbarunya, dengan aturan yang sama seperti
halaman (ketentuan Ray 13 Sep 2026, SESI.md bagian 6):
  benar            -> selesai, tidak muncul lagi
  salah / menyerah -> muncul lagi setelah JEDA_ULANG_HARI
  pilihan          -> "ini memang pilihanku", tidak ditanyakan lagi
"Ulang semua" membuat baris satu mode tidak aktif; riwayatnya tetap dihitung
di ringkasan.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from .db import koneksi, sekarang

router = APIRouter(prefix="/api/latihan", tags=["latihan"])

JEDA_ULANG_HARI = 3
MODE = ("kesalahan", "pembukaan")
HASIL = ("benar", "salah", "menyerah", "pilihan")
# Bentuk ringkas yang dipakai halaman: b = benar, s = salah (termasuk menyerah), a = pilihan
KE_HURUF = {"benar": "b", "salah": "s", "menyerah": "s", "pilihan": "a"}
DARI_HURUF = {"b": "benar", "s": "salah", "a": "pilihan"}


def mode_dari_kunci(kunci: str) -> str:
    return "pembukaan" if kunci.startswith("buka:") else "kesalahan"


def tanggal_lokal(iso: str) -> date:
    """dijawab_pada disimpan UTC; hari dihitung menurut jam laptop."""
    return datetime.fromisoformat(iso).astimezone().date()


def simpan_jawaban(conn, mode: str, kunci: str, hasil: str,
                   langkah_uci: str | None = None, kelompok: str | None = None,
                   waktu: str | None = None) -> int:
    if mode not in MODE or hasil not in HASIL:
        raise ValueError(f"mode/hasil tidak dikenal: {mode}/{hasil}")
    cur = conn.execute(
        "INSERT INTO latihan_jawaban(mode, kunci, hasil, langkah_uci, kelompok, dijawab_pada) "
        "VALUES (?,?,?,?,?,?)",
        (mode, kunci, hasil, langkah_uci, kelompok, waktu or sekarang()))
    conn.commit()
    return cur.lastrowid


def _terbaru_aktif(conn, mode: str | None = None):
    syarat, params = "aktif = 1", []
    if mode:
        syarat += " AND mode = ?"
        params.append(mode)
    return conn.execute(
        f"""
        SELECT j.mode, j.kunci, j.hasil, j.dijawab_pada
        FROM latihan_jawaban j
        JOIN (SELECT MAX(id) AS id FROM latihan_jawaban WHERE {syarat} GROUP BY mode, kunci) t
          ON t.id = j.id
        """, params).fetchall()


def catatan_aktif(conn, mode: str | None = None) -> dict:
    """{kunci: {"h": "b"|"s"|"a", "w": "YYYY-MM-DD"}} — bentuk yang sama dengan
    catatan lama di localStorage, jadi halaman tidak perlu aturan baru."""
    return {k: {"h": KE_HURUF[h], "w": tanggal_lokal(w).isoformat()}
            for _, k, h, w in _terbaru_aktif(conn, mode)}


def ulang_semua(conn, mode: str) -> int:
    n = conn.execute("UPDATE latihan_jawaban SET aktif = 0 WHERE mode = ? AND aktif = 1",
                     (mode,)).rowcount
    conn.commit()
    return n


def impor_catatan_lama(conn, catatan: dict) -> int:
    """Catatan localStorage -> baris database, sekali saja.

    Kunci yang sudah punya baris (aktif atau tidak) dilewati, jadi aman
    dikirim berulang. Nilai lama berupa teks = waktu jawaban benar."""
    ada = {(m, k) for m, k in conn.execute("SELECT DISTINCT mode, kunci FROM latihan_jawaban")}
    n = 0
    for kunci, nilai in catatan.items():
        if not isinstance(kunci, str) or not kunci:
            continue
        if isinstance(nilai, str):
            nilai = {"h": "b", "w": nilai[:10]}
        if not isinstance(nilai, dict) or nilai.get("h") not in DARI_HURUF:
            continue
        mode = mode_dari_kunci(kunci)
        if (mode, kunci) in ada:
            continue
        try:
            hari = date.fromisoformat(str(nilai.get("w", ""))[:10])
        except ValueError:
            hari = date.today()
        # tengah hari waktu lokal supaya tanggalnya tidak bergeser saat dibaca lagi
        waktu = datetime(hari.year, hari.month, hari.day, 12).astimezone().isoformat(timespec="seconds")
        conn.execute(
            "INSERT INTO latihan_jawaban(mode, kunci, hasil, dijawab_pada) VALUES (?,?,?,?)",
            (mode, kunci, DARI_HURUF[nilai["h"]], waktu))
        ada.add((mode, kunci))
        n += 1
    conn.commit()
    return n


def _persen(benar: int, total: int) -> float | None:
    return round(100 * benar / total, 1) if total else None


def ringkasan(conn, hari_ini: date | None = None, jumlah_minggu: int = 8) -> dict:
    hari_ini = hari_ini or date.today()
    senin_ini = hari_ini - timedelta(days=hari_ini.weekday())
    keluar = {}
    for mode in MODE:
        status = {"selesai": 0, "menunggu": 0, "siap_ulang": 0, "pilihan": 0}
        for _, _, hasil, w in _terbaru_aktif(conn, mode):
            if hasil == "benar":
                status["selesai"] += 1
            elif hasil == "pilihan":
                status["pilihan"] += 1
            elif (hari_ini - tanggal_lokal(w)).days < JEDA_ULANG_HARI:
                status["menunggu"] += 1
            else:
                status["siap_ulang"] += 1

        minggu = {senin_ini - timedelta(weeks=i): [0, 0] for i in range(jumlah_minggu)}
        kelompok: dict[str, list[int]] = {}
        dijawab = benar = 0
        for hasil, kel, w in conn.execute(
                "SELECT hasil, kelompok, dijawab_pada FROM latihan_jawaban "
                "WHERE mode = ? AND hasil != 'pilihan'", (mode,)):
            ok = hasil == "benar"
            dijawab += 1
            benar += ok
            d = tanggal_lokal(w)
            senin = d - timedelta(days=d.weekday())
            if senin in minggu:
                minggu[senin][0] += 1
                minggu[senin][1] += ok
            if kel:
                k = kelompok.setdefault(kel, [0, 0])
                k[0] += 1
                k[1] += ok

        keluar[mode] = {
            **status,
            "dijawab": dijawab, "benar": benar, "persen_benar": _persen(benar, dijawab),
            "per_minggu": [{"mulai": s.isoformat(), "dijawab": v[0], "benar": v[1],
                            "persen_benar": _persen(v[1], v[0])}
                           for s, v in sorted(minggu.items())],
            "per_kelompok": sorted(
                ({"kelompok": k, "dijawab": v[0], "benar": v[1], "salah": v[0] - v[1],
                  "persen_benar": _persen(v[1], v[0])} for k, v in kelompok.items()),
                key=lambda x: (-x["salah"], x["kelompok"])),
        }
    keluar["jeda_ulang_hari"] = JEDA_ULANG_HARI
    return keluar


# ---------------------------------------------------------------- RUTE

class Jawaban(BaseModel):
    kunci: str = Field(..., min_length=1, max_length=200)
    hasil: str = Field(..., pattern="^(benar|salah|menyerah|pilihan)$")
    langkah_uci: str | None = Field(None, pattern="^[a-h][1-8][a-h][1-8][qrbn]?$")
    kelompok: str | None = Field(None, max_length=60)


class CatatanLama(BaseModel):
    catatan: dict


@router.get("/catatan", summary="Status soal latihan (jawaban aktif terbaru per soal)")
def api_catatan(mode: str | None = Query(None, pattern="^(kesalahan|pembukaan)$")):
    conn = koneksi()
    try:
        return catatan_aktif(conn, mode)
    finally:
        conn.close()


@router.post("/jawaban", summary="Simpan satu jawaban latihan")
def api_jawaban(j: Jawaban):
    conn = koneksi()
    try:
        return {"id": simpan_jawaban(conn, mode_dari_kunci(j.kunci), j.kunci, j.hasil,
                                     j.langkah_uci, j.kelompok)}
    finally:
        conn.close()


@router.post("/catatan/impor", summary="Pindahkan catatan lama dari localStorage (sekali)")
def api_impor(c: CatatanLama):
    if len(c.catatan) > 50000:
        raise HTTPException(status_code=422, detail="Catatan terlalu banyak")
    conn = koneksi()
    try:
        return {"diimpor": impor_catatan_lama(conn, c.catatan)}
    finally:
        conn.close()


@router.delete("/catatan", summary="Ulang semua: lupakan status soal satu mode (riwayat tetap)")
def api_ulang_semua(mode: str = Query(..., pattern="^(kesalahan|pembukaan)$")):
    conn = koneksi()
    try:
        return {"dinonaktifkan": ulang_semua(conn, mode)}
    finally:
        conn.close()


@router.get("/ringkasan", summary="Perkembangan latihan: status soal, benar per minggu, per kelompok")
def api_ringkasan():
    conn = koneksi()
    try:
        return ringkasan(conn)
    finally:
        conn.close()
