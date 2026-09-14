"""Profil dan statistik pembukaan satu pemain."""

from __future__ import annotations

import sqlite3
from collections import defaultdict

from .eco import keluarga_eco


def profil_pemain(conn: sqlite3.Connection, pemain_id: int) -> dict | None:
    p = conn.execute("SELECT * FROM pemain WHERE id = ?", (pemain_id,)).fetchone()
    if p is None:
        return None

    baris = conn.execute(
        """
        SELECT CASE WHEN g.putih_id = ? THEN 'putih' ELSE 'hitam' END AS warna,
               g.hasil, substr(g.tanggal, 1, 4) AS tahun, g.sumber,
               (a.partai_id IS NOT NULL) AS dianalisis
        FROM partai g LEFT JOIN analisis a ON a.partai_id = g.id
        WHERE g.putih_id = ? OR g.hitam_id = ?
        """,
        (pemain_id, pemain_id, pemain_id),
    ).fetchall()

    def kosong():
        return {"partai": 0, "menang": 0, "remis": 0, "kalah": 0, "belum_selesai": 0}

    per_warna = {"putih": kosong(), "hitam": kosong()}
    per_tahun = defaultdict(int)
    per_sumber = defaultdict(int)
    dianalisis = 0
    for r in baris:
        d = per_warna[r["warna"]]
        d["partai"] += 1
        if r["hasil"] == "1/2-1/2":
            d["remis"] += 1
        elif r["hasil"] in ("1-0", "0-1"):
            menang = (r["hasil"] == "1-0") == (r["warna"] == "putih")
            d["menang" if menang else "kalah"] += 1
        else:
            d["belum_selesai"] += 1
        if r["tahun"] and r["tahun"].isdigit():
            per_tahun[r["tahun"]] += 1
        per_sumber[r["sumber"]] += 1
        dianalisis += r["dianalisis"]

    total = kosong()
    for d in per_warna.values():
        for k in total:
            total[k] += d[k]

    def dengan_skor(d):
        n = d["menang"] + d["remis"] + d["kalah"]
        d["skor_persen"] = round(100 * (d["menang"] + 0.5 * d["remis"]) / n, 1) if n else None
        return d

    return {
        **dict(p),
        "total": dengan_skor(total),
        "putih": dengan_skor(per_warna["putih"]),
        "hitam": dengan_skor(per_warna["hitam"]),
        "per_tahun": sorted(per_tahun.items()),
        "per_sumber": sorted(per_sumber.items(), key=lambda x: -x[1]),
        "partai_dianalisis": dianalisis,
    }


def pembukaan_pemain(conn: sqlite3.Connection, pemain_id: int,
                     minimal: int = 1) -> dict:
    """
    Pembukaan yang dipakai pemain, dipisah sebagai Putih dan Hitam.

    Dikelompokkan per keluarga ECO (lihat eco.py) karena TWIC tidak punya nama
    pembukaan. Nama pembukaan spesifik (kalau ada dari Lichess) ditampilkan
    sebagai contoh yang paling sering, bukan sebagai kunci pengelompokan.
    """
    baris = conn.execute(
        """
        SELECT CASE WHEN g.putih_id = ? THEN 'putih' ELSE 'hitam' END AS warna,
               g.eco, g.pembukaan, g.hasil
        FROM partai g
        WHERE (g.putih_id = ? OR g.hitam_id = ?) AND g.eco IS NOT NULL
        """,
        (pemain_id, pemain_id, pemain_id),
    ).fetchall()

    kel = {"putih": defaultdict(lambda: {"partai": 0, "skor": 0.0, "n_skor": 0,
                                         "eco": defaultdict(int), "nama": defaultdict(int)}),
           "hitam": defaultdict(lambda: {"partai": 0, "skor": 0.0, "n_skor": 0,
                                         "eco": defaultdict(int), "nama": defaultdict(int)})}
    for r in baris:
        keluarga = keluarga_eco(r["eco"]) or r["eco"][:1] + "xx"
        d = kel[r["warna"]][keluarga]
        d["partai"] += 1
        d["eco"][r["eco"][:3]] += 1
        if r["pembukaan"]:
            d["nama"][r["pembukaan"]] += 1
        if r["hasil"] == "1/2-1/2":
            d["skor"] += 0.5
            d["n_skor"] += 1
        elif r["hasil"] in ("1-0", "0-1"):
            d["skor"] += 1.0 if (r["hasil"] == "1-0") == (r["warna"] == "putih") else 0.0
            d["n_skor"] += 1

    def rapikan(warna):
        total = sum(d["partai"] for d in kel[warna].values()) or 1
        keluar = []
        for nama, d in kel[warna].items():
            if d["partai"] < minimal:
                continue
            keluar.append({
                "keluarga": nama,
                "partai": d["partai"],
                "persen": round(100 * d["partai"] / total, 1),
                "skor_persen": round(100 * d["skor"] / d["n_skor"], 1) if d["n_skor"] else None,
                "eco_terbanyak": sorted(d["eco"].items(), key=lambda x: -x[1])[:4],
                "contoh_nama": sorted(d["nama"].items(), key=lambda x: -x[1])[:2],
            })
        return sorted(keluar, key=lambda x: -x["partai"])

    return {"pemain_id": pemain_id, "putih": rapikan("putih"), "hitam": rapikan("hitam"),
            "catatan": "Dikelompokkan per keluarga ECO. Skor dari sudut pandang pemain ini."}
