"""
Kenapa partai itu kalah: habis waktu, blunder cepat, blunder, atau dikalahkan.

Penggolongan ini sebelumnya hanya ada di `web/kekalahan.js`, jadi halaman lain
(Ringkasan, Kemajuan) tidak bisa memakai angka yang sama dan tiap halaman bisa
menjawab berbeda untuk partai yang sama. Ray, 13 Sep 2026: "perbaiki di
backendnya juga jangan hanya frontend".

Aturannya — sama persis dengan yang dipakai halaman, termasuk koreksi 13 Sep:

  waktu       jam habis (tag Termination) DAN posisi saat itu belum kalah.
              Kalau jamnya habis saat posisi SUDAH kalah telak (≤ −300 cp atau
              kena mat), itu cuma cara partainya berakhir — sebabnya dicari
              seperti partai lain, dan keadaan jam ikut disebut.
  missclick   blunder ≥ 300 cp yang dimainkan ≤ 2 detik menurut jam %clk.
  blunder     blunder ≥ 300 cp dengan waktu pikir normal.
  dikalahkan  tidak ada blunder besar; kalah perlahan atau dimatikan.
  belum       belum dianalisis engine dan bukan kalah waktu.

Yang perlu diketahui soal sudut pandang: `eval_sesudah_cp` dari sudut pemain
yang jalan (jadi negatif = buruk bagi pemain itu), sedangkan `mat_sesudah`
dari sudut Putih. Modul ini memakai `mat_dari_sudut_pemain()` supaya keduanya
tidak tercampur — kesalahan itu pernah membuat halaman menulis "mat dalam 2
untuk saya" pada partai yang justru kena mat.
"""

from __future__ import annotations

import re
import sqlite3

from fastapi import APIRouter, HTTPException, Query

from .analisis_db import mat_dari_sudut_pemain
from .db import koneksi

router = APIRouter(prefix="/api", tags=["kekalahan"])

BLUNDER_CP = 300        # ambang blunder
DETIK_CEPAT = 2.0       # blunder secepat ini kemungkinan missclick/pre-move
SUDAH_KALAH_CP = -300   # di bawah ini posisinya dianggap sudah kalah
MAT_CP = 100_000        # nilai yang dipakai analisis untuk mewakili mat
ZEITNOT_DETIK = 10      # jam sendiri di bawah ini = zeitnot
AMBANG_ELO_LAWAN = 2200

POLA_TAG = re.compile(r'\[(\w+)\s+"([^"]*)"\]')
POLA_CLK = re.compile(r"\{[^}]*%clk\s+(\d+):(\d\d):(\d\d(?:\.\d+)?)[^}]*\}")

LAWAN_VALID = ("ketentuan", "2200", "gelar", "semua")
SEBAB_URUT = ("waktu", "missclick", "blunder", "dikalahkan", "belum")


# ----------------------------------------------------------------- PGN

def tag_pgn(pgn: str) -> dict:
    return {k: v for k, v in POLA_TAG.findall(pgn or "")}


def jam_per_ply(pgn: str) -> list[float]:
    """Sisa jam (detik) sesudah tiap ply, dari komentar %clk. Kosong kalau tidak ada."""
    badan = POLA_TAG.sub("", pgn or "")
    return [int(j) * 3600 + int(m) * 60 + float(d) for j, m, d in POLA_CLK.findall(badan)]


def _angka_awal(kontrol: str | None) -> float | None:
    try:
        return float(str(kontrol or "").split("+")[0])
    except ValueError:
        return None


def detik_dipakai(jam: list[float], ply: int, kontrol: str | None) -> float | None:
    """Berapa detik dipakai untuk langkah di ply itu. None kalau tidak terhitung."""
    if not jam or ply >= len(jam) or ply < 0:
        return None
    try:
        inc = int(str(kontrol or "").split("+")[1]) if "+" in str(kontrol or "") else 0
    except ValueError:
        inc = 0
    sebelum = jam[ply - 2] if ply >= 2 else _angka_awal(kontrol)
    if sebelum is None:
        return None
    return max(0.0, round(sebelum - jam[ply] + inc, 1))


def jam_sendiri(jam: list[float], warna: str, ply: int | None = None) -> float | None:
    """Sisa jam pihak `warna` pada ply itu; ply None = jam terakhir miliknya."""
    if not jam:
        return None
    k = 0 if warna == "putih" else 1
    i = len(jam) - 1 if ply is None else min(ply, len(jam) - 1)
    while i >= 0 and i % 2 != k:
        i -= 1
    return jam[i] if i >= 0 else None


def kalah_waktu(tags: dict) -> bool:
    t = (tags.get("Termination") or "").lower()
    return "time forfeit" in t or "on time" in t or "waktu" in t


# ----------------------------------------------------------- penggolongan

def posisi_akhir(langkah: dict | None) -> dict | None:
    """
    Keadaan posisi sesudah langkah TERAKHIR yang dimainkan pemain itu, dari
    sudut pandangnya sendiri.
    """
    if not langkah or langkah.get("eval_sesudah_cp") is None:
        return None
    cp = langkah["eval_sesudah_cp"]
    mat = mat_dari_sudut_pemain(langkah.get("mat_sesudah"), langkah["warna"])
    langkah_mat = abs(mat) if mat else None
    if abs(cp) >= MAT_CP or langkah_mat:
        sudah_kalah = mat < 0 if mat else cp < 0
        nilai = ("kena mat" if sudah_kalah else "mat untuk saya") + \
                (f" dalam {langkah_mat}" if langkah_mat else "")
    else:
        nilai = f"{'+' if cp >= 0 else ''}{cp / 100:.1f}"
        sudah_kalah = cp <= SUDAH_KALAH_CP
    keadaan = "sudah kalah" if sudah_kalah else ("masih unggul" if cp >= 150 else "masih seimbang")
    return {"nilai": nilai, "sudah_kalah": sudah_kalah, "keadaan": keadaan}


def _teks_blunder(b: dict, detik: float | None) -> str:
    nomor = f"{b['nomor_langkah']}." if b["warna"] == "putih" else f"{b['nomor_langkah']}…"
    # kehilangan_cp dibatasi 1000 (BATAS_KEHILANGAN_CP); angka mentahnya
    # terbaca seperti nilai sungguhan, padahal artinya "sebesar mungkin".
    hilang = ("−1000 cp (batas: jadi kalah/mat)" if b["kehilangan_cp"] >= 1000
              else f"−{b['kehilangan_cp']} cp")
    ekor = f", {detik:g} s" if detik is not None else ""
    terbaik = f" · seharusnya {b['langkah_terbaik_san']}" if b.get("langkah_terbaik_san") else ""
    return f"{nomor}{b['san']} ({hilang}{ekor}){terbaik}"


def golongkan(pgn: str, kontrol_waktu: str | None, warna: str,
              langkah_saya: list[dict], ada_analisis: bool) -> dict:
    """
    Satu partai kalah -> {sebab, keterangan, ply, zeitnot, habis_waktu}.

    `langkah_saya` = baris analisis_langkah milik warna itu saja, urut ply.
    """
    tags = tag_pgn(pgn)
    jam = jam_per_ply(pgn)
    akhir = jam_sendiri(jam, warna, None)
    hasil = {"sebab": "belum", "keterangan": "", "ply": None,
             "zeitnot": akhir is not None and akhir < ZEITNOT_DETIK,
             "habis_waktu": False}

    blunder = sorted((l for l in langkah_saya if (l.get("kehilangan_cp") or 0) >= BLUNDER_CP),
                     key=lambda l: -l["kehilangan_cp"])

    awalan = ""
    if kalah_waktu(tags):
        hasil["habis_waktu"] = True
        pos = posisi_akhir(langkah_saya[-1] if langkah_saya else None)
        if pos is None:
            hasil["sebab"] = "waktu"
            hasil["keterangan"] = "" if ada_analisis else "belum dianalisis"
            return hasil
        if not pos["sudah_kalah"]:
            hasil["sebab"] = "waktu"
            hasil["keterangan"] = f"posisi saat jatuh: {pos['keadaan']} ({pos['nilai']})"
            return hasil
        awalan = f"jam habis, tapi posisi memang sudah kalah ({pos['nilai']}) — "

    if not ada_analisis:
        hasil["keterangan"] = "jalankan analisis engine untuk mengetahui sebabnya"
        return hasil

    if blunder:
        cepat = [l for l in blunder
                 if (d := detik_dipakai(jam, l["ply"], kontrol_waktu)) is not None and d <= DETIK_CEPAT]
        b = cepat[0] if cepat else blunder[0]
        detik = detik_dipakai(jam, b["ply"], kontrol_waktu)
        saat = jam_sendiri(jam, warna, b["ply"])
        hasil["sebab"] = "missclick" if cepat else "blunder"
        hasil["ply"] = b["ply"]
        hasil["zeitnot"] = saat is not None and saat < ZEITNOT_DETIK
        ket = _teks_blunder(b, detik)
        if len(blunder) > 1:
            ket += f" · {len(blunder)} blunder"
        hasil["keterangan"] = awalan + ket
        return hasil

    t = (tags.get("Termination") or "").lower()
    dimatikan = "checkmate" in t or "mat" in t
    n_imprecise = sum(1 for l in langkah_saya if l.get("klasifikasi") == "inaccuracy")
    n_mistake = sum(1 for l in langkah_saya if l.get("klasifikasi") == "mistake")
    hasil["sebab"] = "dikalahkan"
    hasil["keterangan"] = (awalan + ("dimatikan" if dimatikan else "tanpa blunder besar") +
                           f" · ?! {n_imprecise} · ? {n_mistake}")
    return hasil


# ------------------------------------------------------------- kumpulan

def _lolos_lawan(gelar_lawan: str | None, elo_lawan: int | None, mode: str) -> bool:
    if mode == "semua":
        return True
    bergelar = bool(gelar_lawan)
    kuat = bool(elo_lawan and elo_lawan > AMBANG_ELO_LAWAN)
    if mode == "gelar":
        return bergelar
    if mode == "2200":
        return kuat
    return bergelar or kuat


def kekalahan(conn: sqlite3.Connection, pemain_ids: list[int], format_: str | None = None,
              lawan: str = "ketentuan", batas: int = 80) -> dict:
    """Partai kalah pemain-pemain itu, sudah digolongkan sebabnya."""
    if not pemain_ids:
        return {"partai": [], "ikhtisar": {s: 0 for s in SEBAB_URUT}, "zeitnot": 0}

    tanda = ",".join("?" * len(pemain_ids))
    params: list = list(pemain_ids) * 4
    sql = f"""
        SELECT g.id, g.tanggal, g.hasil, g.pgn, g.kontrol_waktu, g.format, g.sumber,
               CASE WHEN g.putih_id IN ({tanda}) THEN 'putih' ELSE 'hitam' END AS warna_saya,
               pw.nama AS putih, pb.nama AS hitam,
               pw.gelar AS putih_gelar, pb.gelar AS hitam_gelar,
               g.elo_putih, g.elo_hitam,
               CASE WHEN g.putih_id IN ({tanda}) THEN pw.nama ELSE pb.nama END AS akun,
               (a.partai_id IS NOT NULL) AS dianalisis
        FROM partai g
        JOIN pemain pw ON pw.id = g.putih_id
        JOIN pemain pb ON pb.id = g.hitam_id
        LEFT JOIN analisis a ON a.partai_id = g.id
        WHERE ((g.putih_id IN ({tanda}) AND g.hasil = '0-1')
            OR (g.hitam_id IN ({tanda}) AND g.hasil = '1-0'))
    """
    if format_ and format_ != "semua":
        sql += " AND g.format = ?"
        params.append(format_)
    sql += " ORDER BY g.tanggal DESC, g.id DESC LIMIT ?"
    params.append(max(1, min(int(batas), 500)) * 3)      # ambil lebih, saringan lawan di bawah

    baris = conn.execute(sql, params).fetchall()

    dipakai = []
    for r in baris:
        warna = r["warna_saya"]
        gelar_lawan = r["hitam_gelar"] if warna == "putih" else r["putih_gelar"]
        elo_lawan = r["elo_hitam"] if warna == "putih" else r["elo_putih"]
        if _lolos_lawan(gelar_lawan, elo_lawan, lawan):
            dipakai.append(r)
        if len(dipakai) >= batas:
            break

    # langkah analisis untuk partai-partai itu, sekali jalan
    per_partai: dict[int, list[dict]] = {}
    if dipakai:
        tanda_id = ",".join("?" * len(dipakai))
        q = f"""SELECT partai_id, ply, nomor_langkah, warna, san, kehilangan_cp, klasifikasi,
                       eval_sesudah_cp, mat_sesudah, langkah_terbaik_san
                FROM analisis_langkah WHERE partai_id IN ({tanda_id}) ORDER BY ply"""
        for l in conn.execute(q, [r["id"] for r in dipakai]):
            per_partai.setdefault(l["partai_id"], []).append(dict(l))

    keluar, ikhtisar, zeitnot = [], {s: 0 for s in SEBAB_URUT}, 0
    for r in dipakai:
        warna = r["warna_saya"]
        langkah_saya = [l for l in per_partai.get(r["id"], []) if l["warna"] == warna]
        gol = golongkan(r["pgn"] or "", r["kontrol_waktu"], warna, langkah_saya,
                        bool(r["dianalisis"]))
        ikhtisar[gol["sebab"]] += 1
        zeitnot += 1 if gol["zeitnot"] else 0
        keluar.append({
            "id": r["id"], "tanggal": r["tanggal"], "akun": r["akun"], "warna": warna,
            "lawan": r["hitam"] if warna == "putih" else r["putih"],
            "lawan_gelar": r["hitam_gelar"] if warna == "putih" else r["putih_gelar"],
            "lawan_elo": r["elo_hitam"] if warna == "putih" else r["elo_putih"],
            "format": r["format"], "kontrol_waktu": r["kontrol_waktu"],
            "dianalisis": bool(r["dianalisis"]),
            **gol,
        })

    return {"partai": keluar, "ikhtisar": ikhtisar, "zeitnot": zeitnot,
            "lawan": lawan, "format": format_ or "semua"}


@router.get("/kekalahan", summary="Partai kalah beserta sebabnya")
def api_kekalahan(pemain_id: list[int] = Query(..., description="id akun sendiri, boleh beberapa"),
                  format: str | None = None,
                  lawan: str = "ketentuan",
                  batas: int = 80):
    if lawan not in LAWAN_VALID:
        raise HTTPException(status_code=422,
                            detail=f"lawan harus salah satu dari: {', '.join(LAWAN_VALID)}")
    conn = koneksi()
    try:
        return kekalahan(conn, pemain_id, format, lawan, batas)
    finally:
        conn.close()
