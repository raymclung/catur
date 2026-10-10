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

import math
import re
import sqlite3

from fastapi import APIRouter, HTTPException, Query

from .analisis_db import mat_dari_sudut_pemain
from .db import koneksi
from .eco import KELUARGA, keluarga_eco
from .ringkasan import FASE
from .latihan_pembukaan import CATALAN, ENGLISH, RAGOZIN, SICILIAN, pembukaan_repertoire

router = APIRouter(prefix="/api", tags=["kekalahan"])

DETIK_CEPAT = 2.0       # blunder secepat ini kemungkinan missclick/pre-move
SUDAH_KALAH_CP = -300   # di bawah ini posisinya dianggap sudah kalah
MAT_CP = 100_000        # nilai yang dipakai analisis untuk mewakili mat
ZEITNOT_DETIK = 10      # jam sendiri di bawah ini = zeitnot
AMBANG_ELO_LAWAN = 2200

POLA_TAG = re.compile(r'\[(\w+)\s+"([^"]*)"\]')
POLA_CLK = re.compile(r"\{[^}]*%clk\s+(\d+):(\d\d):(\d\d(?:\.\d+)?)[^}]*\}")

LAWAN_VALID = ("ketentuan", "2200", "gelar", "gmim", "semua")
GELAR_GMIM = ("GM", "IM")
SAMPEL_KECIL = 5        # di bawah ini satu-dua partai sudah membalik persentasenya

# Baris kartu pembukaan -> nama repertoire di soal latihan pembukaan, supaya
# halaman Kekalahan bisa menaut "latih ->" (Ray, 10 Okt 2026).
#
# Sejak 10 Okt malam tautannya ditentukan dari LANGKAH tiap partai, dengan
# fungsi yang sama dengan Latihan (`pembukaan_repertoire`), bukan dari ECO:
# diukur pada 12.427 partai akun, kartu Hitam "Queen's Pawn (London, Colle)"
# 73% Ragozin/QGD dan Putih "Queen's Gambit Declined" 58% English
# (transposisi 1.c4), padahal pemetaan ECO memberi keduanya tanpa tautan.
# Tabel ini tinggal CADANGAN untuk partai yang belum punya indeks posisi.
LATIHAN_REPERTOIRE = {
    ("putih", "English Opening"): ENGLISH,
    ("putih", "Catalan Opening"): CATALAN,
    ("hitam", "Sicilian Defense"): SICILIAN,
    ("hitam", "Ragozin Defense"): RAGOZIN,
    ("hitam", "Queen's Gambit Declined"): RAGOZIN,
}
SEBAB_URUT = ("waktu", "missclick", "blunder", "dikalahkan", "belum")
PLY_REPERTOIRE = 12     # pembukaan_repertoire hanya melihat enam langkah pertama tiap pihak
PORSI_TAUTAN = 0.5      # kartu bertaut kalau repertoire ini menguasai setidaknya separuh partainya


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
        # float, bukan int: Chess.com menulis "10+0.1" (223 partai Ray).
        inc = float(str(kontrol or "").split("+")[1]) if "+" in str(kontrol or "") else 0
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


def _peluang(cp: int) -> float:
    """Peluang menang 0..1, rumus Lichess — sama dengan `peluang_menang` di
    catur_analysis/partai.py (disalin supaya modul ini tidak mengimpor engine)."""
    cp = max(-1000, min(1000, cp))
    return 1 / (1 + math.exp(-0.00368208 * cp))


def blunder_penentu(blunder: list[dict]) -> dict | None:
    """
    Blunder yang benar-benar membuang partai: turun PELUANG MENANG terbesar,
    hanya dari posisi yang BELUM kalah.

    Dulu diurutkan menurut `kehilangan_cp`, yang dibatasi 1000 — membiarkan
    mat saat sudah -5 pun bernilai 1000 — lalu blunder cepat mana pun
    diutamakan. Hasilnya 87 dari 500 kekalahan menunjuk langkah di posisi yang
    sudah kalah, padahal blunder penentunya lebih awal (audit 28 Sep 2026).
    Kalau semua blunder terjadi di posisi yang sudah kalah, partainya hilang
    lewat hal lain dulu: tidak ada penentu (sebabnya "dikalahkan").
    """
    kandidat = []
    for l in blunder:
        sebelum = l.get("eval_sebelum_cp")
        if sebelum is None:            # baris lama tanpa eval_sebelum: perkirakan
            sebelum = (l.get("eval_sesudah_cp") or 0) + (l.get("kehilangan_cp") or 0)
        if sebelum <= SUDAH_KALAH_CP:
            continue
        turun = _peluang(sebelum) - _peluang(l.get("eval_sesudah_cp") or 0)
        kandidat.append((-turun, l["ply"], l))
    return min(kandidat, key=lambda k: k[:2])[2] if kandidat else None


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

    blunder = [l for l in langkah_saya if l.get("klasifikasi") == "blunder"]
    penentu = blunder_penentu(blunder)

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

    if penentu:
        b = penentu
        detik = detik_dipakai(jam, b["ply"], kontrol_waktu)
        saat = jam_sendiri(jam, warna, b["ply"])
        # Missclick hanya kalau blunder YANG MEMBUANG PARTAI itu sendiri yang
        # dimainkan secepat itu; dulu blunder cepat mana pun didahulukan,
        # termasuk yang terjadi saat posisi sudah hancur.
        hasil["sebab"] = "missclick" if detik is not None and detik <= DETIK_CEPAT else "blunder"
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

def _syarat_lawan(mode: str, tanda: str) -> str:
    """
    Saringan lawan sebagai SQL, supaya berlaku SEBELUM LIMIT.

    Audit 27 Sep 2026: dulu SQL mengambil `batas * 3` kekalahan terbaru lalu
    Python menyaringnya; lawan bergelar yang jarang tidak pernah terambil
    (akun Ray: 147 dari 263 kekalahan melawan pemain bergelar, bahkan dengan
    batas 500). Placeholder `tanda` dipakai dua kali: urutan parameternya
    diatur pemanggil.
    """
    gelar = (f"COALESCE(CASE WHEN g.putih_id IN ({tanda}) THEN pb.gelar"
             f" ELSE pw.gelar END, '') <> ''")
    kuat = (f"COALESCE(CASE WHEN g.putih_id IN ({tanda}) THEN g.elo_hitam"
            f" ELSE g.elo_putih END, 0) > {int(AMBANG_ELO_LAWAN)}")
    # Daftar gelar konstanta kode, bukan masukan pengguna; disambung sebagai
    # literal supaya hitungan placeholder `tanda` di pemanggil tetap benar.
    daftar_gmim = ",".join(f"'{g}'" for g in GELAR_GMIM)
    gmim = (f"(CASE WHEN g.putih_id IN ({tanda}) THEN pb.gelar"
            f" ELSE pw.gelar END) IN ({daftar_gmim})")
    return {"semua": "", "gelar": gelar, "2200": kuat, "gmim": gmim}.get(mode, f"({gelar} OR {kuat})")


POTONGAN_ID = 900      # id per kueri IN (...): di bawah batas variabel SQLite lama (999)


def _langkah_per_partai(conn: sqlite3.Connection, ids: list[int]) -> dict[int, list[dict]]:
    """Baris analisis_langkah per partai, urut ply. Dipotong supaya ribuan id aman."""
    per_partai: dict[int, list[dict]] = {}
    for i in range(0, len(ids), POTONGAN_ID):
        potong = ids[i:i + POTONGAN_ID]
        q = f"""SELECT partai_id, ply, nomor_langkah, warna, san, kehilangan_cp, klasifikasi,
                       eval_sebelum_cp, eval_sesudah_cp, mat_sesudah, langkah_terbaik_san
                FROM analisis_langkah WHERE partai_id IN ({",".join("?" * len(potong))})
                ORDER BY partai_id, ply"""
        for l in conn.execute(q, potong):
            per_partai.setdefault(l["partai_id"], []).append(dict(l))
    return per_partai


def fase_ply(ply: int) -> str:
    """Nama fase untuk satu ply, dengan batas yang sama dengan halaman Ringkasan."""
    for nama, awal, akhir in FASE:
        if awal <= ply < akhir:
            return nama
    return FASE[-1][0]


def langkah_awal(conn: sqlite3.Connection, ids: list[int]) -> dict[int, list[str]]:
    """
    UCI ply 0..PLY_REPERTOIRE-1 per partai, dari indeks posisi (tanpa mem-parse
    PGN: 12.427 partai dalam ±0,9 s). Partai yang indeksnya bolong tidak
    dikembalikan — jangan menggolongkan dari langkah yang tidak lengkap.
    """
    per_partai: dict[int, list[tuple[int, str]]] = {}
    for i in range(0, len(ids), POTONGAN_ID):
        potong = ids[i:i + POTONGAN_ID]
        q = f"""SELECT partai_id, ply, langkah_uci FROM posisi
                WHERE partai_id IN ({",".join("?" * len(potong))}) AND ply < ?
                ORDER BY partai_id, ply"""
        for pid, ply, uci in conn.execute(q, [*potong, PLY_REPERTOIRE]):
            per_partai.setdefault(pid, []).append((ply, uci))
    return {pid: [u for _, u in b] for pid, b in per_partai.items()
            if [p for p, _ in b] == list(range(len(b))) and all(u for _, u in b)}


def repertoire_kartu(anggota: list[tuple[int, str]], keluarga: str,
                     awal: dict[int, list[str]]) -> tuple[str | None, int]:
    """
    (nama repertoire, porsi %) untuk satu kartu. `anggota` = [(id, warna Ray)].
    Partai ber-indeks digolongkan dari langkahnya; yang tidak, dari tabel ECO
    cadangan. Tautan hanya kalau satu repertoire menguasai >= PORSI_TAUTAN.
    """
    hitung: dict[str, int] = {}
    for pid, warna in anggota:
        nama = (pembukaan_repertoire(awal[pid], warna) if pid in awal
                else LATIHAN_REPERTOIRE.get((warna, keluarga)))
        if nama:
            hitung[nama] = hitung.get(nama, 0) + 1
    if not anggota or not hitung:
        return None, 0
    nama, n = max(hitung.items(), key=lambda x: (x[1], x[0]))
    if n < PORSI_TAUTAN * len(anggota):
        return None, round(100 * n / len(anggota))
    return nama, round(100 * n / len(anggota))


# Nama keluarga ECO -> [kode_awal, kode_akhir] (eco.KELUARGA).
RENTANG_KELUARGA = {nama: [awal, akhir] for awal, akhir, nama in KELUARGA}

# Urutan tampil kolom "Kalah di": fase dulu (yang bisa dilatih), lalu sisanya.
KALAH_DI = ("pembukaan", "tengah", "akhir", "waktu", "dikalahkan", "belum")


def kalah_di(gol: dict) -> str:
    """
    Satu hasil golongkan() -> tempat partai itu hilang.

    Blunder dan missclick punya ply penentu, jadi dipetakan ke fasenya; kalah
    waktu, dikalahkan perlahan, dan belum dianalisis tidak punya satu momen.
    """
    if gol["sebab"] in ("blunder", "missclick") and gol.get("ply") is not None:
        return fase_ply(gol["ply"])
    return {"waktu": "waktu", "dikalahkan": "dikalahkan"}.get(gol["sebab"], "belum")


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
    syarat_lawan = _syarat_lawan(lawan, tanda)
    if syarat_lawan:
        sql += f" AND {syarat_lawan}"
        params += list(pemain_ids) * syarat_lawan.count(f"IN ({tanda})")
    sql += " ORDER BY g.tanggal DESC, g.id DESC LIMIT ?"
    params.append(max(1, min(int(batas), 500)))

    dipakai = conn.execute(sql, params).fetchall()

    per_partai = _langkah_per_partai(conn, [r["id"] for r in dipakai])

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


def pembukaan_kalah(conn: sqlite3.Connection, pemain_ids: list[int], format_: str | None = None,
                    lawan: str = "gmim") -> dict:
    """
    Pembukaan mana yang paling sering kalah melawan lawan kuat, per warna.

    Ray (ide 10 Sep, dikerjakan 9 Okt 2026): "pembukaan mana yang paling sering
    kalah melawan GM/IM". Sengaja dari SEMUA partai selesai, bukan hanya yang
    dianalisis: pertanyaannya soal hasil, dan syarat analisis hanya membuang
    partai. Dikelompokkan per keluarga ECO (eco.py) seperti profil pemain,
    karena nama pembukaan Chess.com/Lichess terlalu terpecah untuk dihitung.
    Risiko yang diterima: partai vs GM/IM Ray hanya ±100 dan semuanya
    bullet/blitz, jadi tiap baris ditandai `sampel_kecil` di bawah
    SAMPEL_KECIL partai, bukan disembunyikan.
    """
    if not pemain_ids:
        return {"pembukaan": [], "total": {"partai": 0, "menang": 0, "remis": 0, "kalah": 0}}
    tanda = ",".join("?" * len(pemain_ids))
    params: list = list(pemain_ids) * 3
    sql = f"""
        SELECT g.id, g.tanggal, g.eco, g.pembukaan, g.hasil,
               CASE WHEN g.putih_id IN ({tanda}) THEN 'putih' ELSE 'hitam' END AS warna_saya
        FROM partai g
        JOIN pemain pw ON pw.id = g.putih_id
        JOIN pemain pb ON pb.id = g.hitam_id
        WHERE (g.putih_id IN ({tanda}) OR g.hitam_id IN ({tanda}))
          AND g.hasil IN ('1-0', '0-1', '1/2-1/2')
    """
    if format_ and format_ != "semua":
        sql += " AND g.format = ?"
        params.append(format_)
    syarat_lawan = _syarat_lawan(lawan, tanda)
    if syarat_lawan:
        sql += f" AND {syarat_lawan}"
        params += list(pemain_ids) * syarat_lawan.count(f"IN ({tanda})")
    sql += " ORDER BY g.tanggal DESC, g.id DESC"

    kel: dict[tuple, dict] = {}
    kalah_ke: dict[int, tuple] = {}     # id partai kalah -> (warna Ray, kunci baris)
    anggota: dict[tuple, list] = {}     # kunci baris -> [(id partai, warna Ray)]
    total = {"partai": 0, "menang": 0, "remis": 0, "kalah": 0}
    for r in conn.execute(sql, params):
        warna = r["warna_saya"]
        eco = (r["eco"] or "").strip()
        keluarga = keluarga_eco(eco) or (eco[:1] + "xx" if eco else "Tanpa ECO")
        d = kel.setdefault((warna, keluarga), {"warna": warna, "keluarga": keluarga, "partai": 0,
                                               "menang": 0, "remis": 0, "kalah": 0,
                                               "nama": {}, "kalah_terakhir": None})
        if r["hasil"] == "1/2-1/2":
            hasil = "remis"
        else:
            hasil = "menang" if (r["hasil"] == "1-0") == (warna == "putih") else "kalah"
        d["partai"] += 1
        d[hasil] += 1
        anggota.setdefault((warna, keluarga), []).append((r["id"], warna))
        total["partai"] += 1
        total[hasil] += 1
        if r["pembukaan"]:
            d["nama"][r["pembukaan"]] = d["nama"].get(r["pembukaan"], 0) + 1
        # urutan SQL terbaru dulu, jadi kekalahan pertama yang ditemui = terakhir
        if hasil == "kalah" and d["kalah_terakhir"] is None:
            d["kalah_terakhir"] = {"id": r["id"], "tanggal": r["tanggal"]}
        if hasil == "kalah":
            kalah_ke[r["id"]] = (warna, (warna, keluarga))

    # Di mana tiap kekalahan hilang (Ray, 10 Okt 2026: "di fase mana kalah
    # dalam Sicilian/English") — penggolong yang SAMA dengan daftar kalah,
    # supaya angka kartu ini dan tabel di bawahnya tidak pernah berselisih.
    for d in kel.values():
        d["kalah_di"] = {k: 0 for k in KALAH_DI}
    ids = list(kalah_ke)
    langkah = _langkah_per_partai(conn, ids)
    for i in range(0, len(ids), POTONGAN_ID):
        potong = ids[i:i + POTONGAN_ID]
        q = f"""SELECT g.id, g.pgn, g.kontrol_waktu, (a.partai_id IS NOT NULL) AS dianalisis
                FROM partai g LEFT JOIN analisis a ON a.partai_id = g.id
                WHERE g.id IN ({",".join("?" * len(potong))})"""
        for r in conn.execute(q, potong):
            warna, kunci = kalah_ke[r["id"]]
            milik = [l for l in langkah.get(r["id"], []) if l["warna"] == warna]
            gol = golongkan(r["pgn"] or "", r["kontrol_waktu"], warna, milik, bool(r["dianalisis"]))
            kel[kunci]["kalah_di"][kalah_di(gol)] += 1

    awal = langkah_awal(conn, [pid for a in anggota.values() for pid, _ in a])
    keluar = []
    for kunci, d in kel.items():
        nama = sorted(d.pop("nama").items(), key=lambda x: -x[1])
        latihan, porsi = repertoire_kartu(anggota.get(kunci, []), d["keluarga"], awal)
        keluar.append({
            **d,
            "skor_persen": round(100 * (d["menang"] + 0.5 * d["remis"]) / d["partai"], 1),
            "kalah_persen": round(100 * d["kalah"] / d["partai"], 1),
            "sampel_kecil": d["partai"] < SAMPEL_KECIL,
            "contoh_nama": [n for n, _ in nama[:2]],
            "latihan": latihan,
            # Bagian partai kartu ini yang tergolong repertoire teratas menurut
            # langkahnya; < 50 berarti tidak bertaut.
            "latihan_porsi": porsi,
            # Rentang ECO keluarga ini, untuk menaut ke Latihan mode kesalahan
            # (/api/partai?eco_dari=&eco_sampai=). Kelompok cadangan "Bxx" /
            # "Tanpa ECO" tidak punya rentang, jadi tidak bertaut.
            "eco_rentang": RENTANG_KELUARGA.get(d["keluarga"]),
        })
    # Paling sering kalah dulu; seri jumlah kalah -> persentase kalah lebih tinggi.
    keluar.sort(key=lambda x: (-x["kalah"], -x["kalah_persen"], x["keluarga"]))
    return {"pembukaan": keluar, "total": total, "lawan": lawan, "format": format_ or "semua",
            "sampel_kecil_di_bawah": SAMPEL_KECIL}


@router.get("/kekalahan/pembukaan", summary="Pembukaan yang paling sering kalah melawan lawan kuat")
def api_pembukaan_kalah(pemain_id: list[int] = Query(..., description="id akun sendiri, boleh beberapa"),
                        format: str | None = None,
                        lawan: str = "gmim"):
    if lawan not in LAWAN_VALID:
        raise HTTPException(status_code=422,
                            detail=f"lawan harus salah satu dari: {', '.join(LAWAN_VALID)}")
    conn = koneksi()
    try:
        return pembukaan_kalah(conn, pemain_id, format, lawan)
    finally:
        conn.close()


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
