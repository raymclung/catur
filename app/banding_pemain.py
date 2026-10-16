"""
Bandingkan dua pemain berdampingan (RENCANA §10, "Compare two GM").

Halaman profil sudah menjawab "siapa pemain ini"; yang belum ada adalah
membaca dua pemain dengan ukuran yang SAMA di satu layar, plus partai di
antara keduanya. Yang dihitung di sini hanya yang belum punya endpoint:

  gaya          panjang partai, porsi remis, menang cepat, kekuatan lawan,
                langkah pertama sebagai Putih — dari kolom partai dan
                tabel posisi (ply 0), tanpa engine.
  head_to_head  partai di antara keduanya, skor dari sudut pemain A.
  pembukaan     pembukaan_pemain() apa adanya, untuk ditandai mana yang
                dimainkan keduanya.

Angka engine (akurasi, blunder per partai) TIDAK dihitung ulang di sini:
halaman memanggil /api/ringkasan per pemain secara terpisah, karena untuk
pemain dengan 10 ribu partai itu butuh ±3,7 detik dan tidak boleh menahan
tabel lain.

Batas yang diterima: "gaya" hanya dari partai yang selesai (1-0, 0-1,
remis). Elo yang kosong di PGN tidak ikut dirata-rata, dan jumlah partai
yang punya Elo ikut dikirim supaya rata-rata dari sampel kecil terlihat.
"""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, HTTPException, Query

from .db import koneksi
from .pemain_stat import pembukaan_pemain

router = APIRouter(prefix="/api", tags=["banding"])

LANGKAH_CEPAT = 30      # menang dalam <= 30 langkah = menang cepat
LANGKAH_PANJANG = 60    # partai >= 60 langkah = partai panjang
LANGKAH_PERTAMA_MAKS = 5
H2H_MAKS = 200
FORMAT_VALID = ("bullet", "blitz", "rapid", "klasik", "tanpa")
SELESAI = ("1-0", "0-1", "1/2-1/2")


def _persen(a: int | float, b: int | float) -> float | None:
    return round(100 * a / b, 1) if b else None


def _rata(jumlah: float, n: int) -> float | None:
    return round(jumlah / n, 1) if n else None


def gaya_pemain(conn: sqlite3.Connection, pemain_id: int, format_: str | None = None) -> dict:
    """Ukuran gaya dari partai selesai satu pemain; tiap angka punya penyebutnya."""
    sql = """
        SELECT CASE WHEN g.putih_id = ? THEN 'putih' ELSE 'hitam' END AS warna,
               g.hasil, g.jumlah_langkah, g.format,
               CASE WHEN g.putih_id = ? THEN g.elo_putih ELSE g.elo_hitam END AS elo_sendiri,
               CASE WHEN g.putih_id = ? THEN g.elo_hitam ELSE g.elo_putih END AS elo_lawan,
               substr(g.tanggal, 1, 4) AS tahun
        FROM partai g
        WHERE (g.putih_id = ? OR g.hitam_id = ?) AND g.hasil IN ('1-0', '0-1', '1/2-1/2')
    """
    params: list = [pemain_id] * 5
    if format_:
        sql += " AND g.format = ?"
        params.append(format_)

    n = menang = remis = kalah = 0
    menang_cepat = panjang = n_langkah = jumlah_langkah = 0
    n_elo_lawan = jumlah_elo_lawan = n_elo_sendiri = jumlah_elo_sendiri = 0
    lebih_kuat = {"partai": 0, "skor": 0.0}
    lebih_lemah = {"partai": 0, "skor": 0.0}
    per_format: dict[str, int] = {}
    tahun: list[str] = []

    for r in conn.execute(sql, params):
        n += 1
        if r["hasil"] == "1/2-1/2":
            skor = 0.5
            remis += 1
        else:
            skor = 1.0 if (r["hasil"] == "1-0") == (r["warna"] == "putih") else 0.0
            if skor:
                menang += 1
            else:
                kalah += 1
        if r["jumlah_langkah"]:
            n_langkah += 1
            jumlah_langkah += r["jumlah_langkah"]
            panjang += r["jumlah_langkah"] >= LANGKAH_PANJANG
            menang_cepat += skor == 1.0 and r["jumlah_langkah"] <= LANGKAH_CEPAT
        if r["elo_lawan"]:
            n_elo_lawan += 1
            jumlah_elo_lawan += r["elo_lawan"]
        if r["elo_sendiri"]:
            n_elo_sendiri += 1
            jumlah_elo_sendiri += r["elo_sendiri"]
        if r["elo_lawan"] and r["elo_sendiri"]:
            k = lebih_kuat if r["elo_lawan"] > r["elo_sendiri"] else lebih_lemah
            k["partai"] += 1
            k["skor"] += skor
        per_format[r["format"] or "tanpa"] = per_format.get(r["format"] or "tanpa", 0) + 1
        if r["tahun"] and r["tahun"].isdigit():
            tahun.append(r["tahun"])

    def skor_kelompok(k):
        return {"partai": k["partai"], "skor_persen": _persen(k["skor"], k["partai"])}

    return {
        "partai": n,
        "menang": menang, "remis": remis, "kalah": kalah,
        "skor_persen": _persen(menang + 0.5 * remis, n),
        "remis_persen": _persen(remis, n),
        "rata_langkah": _rata(jumlah_langkah, n_langkah),
        "menang_cepat_persen": _persen(menang_cepat, menang),
        "panjang_persen": _persen(panjang, n_langkah),
        "rata_elo_sendiri": _rata(jumlah_elo_sendiri, n_elo_sendiri),
        "rata_elo_lawan": _rata(jumlah_elo_lawan, n_elo_lawan),
        "partai_ber_elo": n_elo_lawan,
        "lawan_lebih_kuat": skor_kelompok(lebih_kuat),
        "lawan_lebih_lemah": skor_kelompok(lebih_lemah),
        "per_format": sorted(per_format.items(), key=lambda x: -x[1]),
        "tahun_dari": min(tahun) if tahun else None,
        "tahun_sampai": max(tahun) if tahun else None,
        "langkah_pertama": langkah_pertama(conn, pemain_id, format_),
    }


def langkah_pertama(conn: sqlite3.Connection, pemain_id: int, format_: str | None = None) -> dict:
    """
    Langkah pertama sebagai Putih, dari tabel posisi (ply 0).

    Partai yang belum diindeks posisinya tidak terhitung; jumlah yang
    terhitung ikut dikirim (`terindeks`) supaya persentasenya jujur.
    """
    sql = """
        SELECT p.langkah_san AS san, COUNT(*) AS n
        FROM partai g JOIN posisi p ON p.partai_id = g.id AND p.ply = 0
        WHERE g.putih_id = ?
    """
    params: list = [pemain_id]
    if format_:
        sql += " AND g.format = ?"
        params.append(format_)
    sql += " GROUP BY p.langkah_san ORDER BY n DESC"
    baris = conn.execute(sql, params).fetchall()
    total = sum(r["n"] for r in baris)
    return {"terindeks": total,
            "langkah": [{"san": r["san"], "partai": r["n"], "persen": _persen(r["n"], total)}
                        for r in baris[:LANGKAH_PERTAMA_MAKS]]}


def head_to_head(conn: sqlite3.Connection, a: int, b: int, format_: str | None = None) -> dict:
    """Partai di antara A dan B, terbaru dulu; skor dari sudut A."""
    sql = """
        SELECT g.id, g.tanggal, g.hasil, g.eco, g.pembukaan, g.jumlah_langkah, g.format,
               g.putih_id, t.nama AS turnamen
        FROM partai g LEFT JOIN turnamen t ON t.id = g.turnamen_id
        WHERE ((g.putih_id = ? AND g.hitam_id = ?) OR (g.putih_id = ? AND g.hitam_id = ?))
    """
    params: list = [a, b, b, a]
    if format_:
        sql += " AND g.format = ?"
        params.append(format_)
    sql += " ORDER BY REPLACE(g.tanggal, '?', '0') DESC, g.id DESC"
    menang_a = remis = menang_b = total = 0
    partai = []
    for r in conn.execute(sql, params):
        total += 1
        warna_a = "putih" if r["putih_id"] == a else "hitam"
        if r["hasil"] == "1/2-1/2":
            remis += 1
        elif r["hasil"] in ("1-0", "0-1"):
            if (r["hasil"] == "1-0") == (warna_a == "putih"):
                menang_a += 1
            else:
                menang_b += 1
        if len(partai) < H2H_MAKS:
            partai.append({k: r[k] for k in ("id", "tanggal", "hasil", "eco", "pembukaan",
                                             "jumlah_langkah", "format", "turnamen")}
                          | {"warna_a": warna_a})
    selesai = menang_a + remis + menang_b
    return {"menang_a": menang_a, "remis": remis, "menang_b": menang_b,
            "skor_a": menang_a + 0.5 * remis, "selesai": selesai,
            "skor_a_persen": _persen(menang_a + 0.5 * remis, selesai),
            "total": total, "dipotong": total > len(partai),
            "partai": partai}


def _ringkas_pemain(conn: sqlite3.Connection, pemain_id: int) -> dict | None:
    p = conn.execute(
        "SELECT id, nama, gelar, negara, fide_id, elo_terakhir, akun_pribadi FROM pemain WHERE id = ?",
        (pemain_id,)).fetchone()
    return dict(p) if p else None


def banding(conn: sqlite3.Connection, a: int, b: int, format_: str | None = None) -> dict | None:
    """Semua bahan halaman banding kecuali angka engine; None kalau salah satu tidak ada."""
    pa, pb = _ringkas_pemain(conn, a), _ringkas_pemain(conn, b)
    if pa is None or pb is None:
        return None
    return {
        "format": format_ or "semua",
        "a": {**pa, "gaya": gaya_pemain(conn, a, format_), "pembukaan": pembukaan_pemain(conn, a)},
        "b": {**pb, "gaya": gaya_pemain(conn, b, format_), "pembukaan": pembukaan_pemain(conn, b)},
        "head_to_head": head_to_head(conn, a, b, format_),
        "catatan": [
            "Gaya dihitung dari partai yang selesai (menang, kalah, remis).",
            f"Menang cepat = menang dalam {LANGKAH_CEPAT} langkah atau kurang; "
            f"partai panjang = {LANGKAH_PANJANG} langkah atau lebih.",
            "Pembukaan dari seluruh partai (tanpa saringan format), dikelompokkan per keluarga ECO.",
        ],
    }


@router.get("/banding", summary="Bandingkan dua pemain: gaya, pembukaan, dan partai di antara keduanya")
def api_banding(a: int = Query(..., description="id pemain A"),
                b: int = Query(..., description="id pemain B"),
                format: str | None = Query(None, description="bullet / blitz / rapid / klasik / tanpa")):
    if a == b:
        raise HTTPException(status_code=422, detail="Pilih dua pemain yang berbeda")
    if format is not None and format not in FORMAT_VALID:
        raise HTTPException(status_code=422,
                            detail=f"format harus salah satu dari: {', '.join(FORMAT_VALID)}")
    conn = koneksi()
    try:
        hasil = banding(conn, a, b, format)
    finally:
        conn.close()
    if hasil is None:
        raise HTTPException(status_code=404, detail="Salah satu pemain tidak ada")
    return hasil
