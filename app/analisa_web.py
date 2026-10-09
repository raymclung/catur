"""
Menjalankan analisis engine dari halaman web, bukan dari terminal.

Analisis satu partai penuh butuh puluhan detik sampai beberapa menit, jadi
TIDAK dikerjakan di dalam request. Yang dilakukan modul ini: memilih partai,
menghitung perkiraan waktu, lalu MELUNCURKAN `analisis_partai.py` sebagai
proses terpisah yang lepas dari server — persis perintah yang selama ini
diketik di terminal. Servernya sendiri tidak pernah menjalankan engine batch.

    POST /api/analisa/pratinjau   berapa partai, berapa lama, perintah setara
    POST /api/analisa/mulai       luncurkan batch di latar belakang
    GET  /api/analisa/status      statistik + tugas yang berjalan/selesai
    POST /api/analisa/hentikan    hentikan satu batch

Kenapa proses terpisah, bukan thread di server:

  * Batch berjam-jam tidak boleh ikut mati kalau server di-restart. Proses
    diluncurkan DETACHED, jadi ia tetap jalan.
  * Aturan mesin ini (ARSITEKTUR.md Phase 5): maksimal dua proses engine
    bersamaan — satu batch + engine server papan analisis. Karena itu
    `/mulai` MENOLAK kalau sudah ada batch yang berjalan, termasuk batch yang
    dijalankan dari terminal oleh sesi lain.
  * Pemilihan partai memakai `pilih_partai()` milik CLI, bukan salinan SQL
    baru. Kalau saringannya berubah, halaman ini ikut berubah sendiri.

Penghentian memakai taskkill/SIGTERM. Itu aman: `analisis_partai.py`
menyimpan hasil tiap partai begitu selesai, jadi yang hilang paling banyak
satu partai yang sedang dihitung — dan partai itu akan terpilih lagi di
perintah berikutnya karena belum ada di tabel `analisis`.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import subprocess
import sys
from contextlib import contextmanager
import time
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, ValidationError, field_validator

from .db import FOLDER_DATA, koneksi

router = APIRouter(prefix="/api/analisa", tags=["analisa"])

AKAR = Path(__file__).resolve().parents[1]

# Database yang dipakai halaman ini. None = data/catur.db bawaan. Tes mengisinya
# dengan database sementara: tanpa ini dua tes diam-diam membaca database Ray
# yang sebenarnya, lolos di mesinnya tapi gagal di checkout bersih ("no such
# table: partai") — dilaporkan sesi frontend 13 Sep 00:20.
PATH_DB: Path | None = None


def _buka() -> sqlite3.Connection:
    # koneksi() tanpa argumen saat PATH_DB kosong: tes lain mengganti `koneksi`
    # dengan fungsi tanpa parameter, dan itu harus tetap bekerja.
    return koneksi() if PATH_DB is None else koneksi(PATH_DB)

# Tugas yang disimpan di catatan; yang lebih lama dibuang.

# Batas jumlah partai per batch. Bukan batas teknis melainkan rem: 2.000
# partai profil hemat sudah belasan jam. Batch raksasa tetap lewat terminal.
MAKS_PARTAI = 2000

# Hasil pemindaian daftar proses ditahan sebentar supaya halaman yang menyegar
# tiap beberapa detik tidak memanggil PowerShell terus-menerus (satu panggilan
# ~0,3 detik).
JEDA_PINDAI = 15.0

# Di Windows, setiap subprocess konsol (powershell, taskkill) memunculkan
# jendela hitam sekejap. Halaman Mesin memanggil /api/analisa/status berulang,
# jadi jendelanya muncul terus-menerus dan mengganggu (dilaporkan Ray,
# 12 Sep 2026). CREATE_NO_WINDOW menjalankannya tanpa jendela.
TANPA_JENDELA = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
_pindai: dict = {"waktu": 0.0, "hasil": []}

# WNM ikut sejak 25 Sep 2026: gelarnya ada di database (dan datang dari
# Chess.com), tapi dulu tidak bisa dipilih di --gelar-lawan maupun halaman
# Mesin, jadi partai lawan ber-WNM tidak pernah bisa disaring.
GELAR_VALID = ("GM", "IM", "FM", "CM", "WGM", "WIM", "WFM", "WCM", "NM", "WNM")
FORMAT_VALID = ("bullet", "blitz", "rapid", "klasik", "tanpa")
HASIL_VALID = ("kalah", "menang", "remis", "menang-kalah")



class Saringan(BaseModel):
    """Saringan pemilihan partai — namanya sama dengan flag CLI-nya."""

    # Batas ukuran (audit 28 Sep 2026): 39.999 id dulu 500 "too many SQL
    # variables", nama pemain 100.000 aksara 500 "LIKE pattern too complex".
    id: list[int] | None = Field(None, max_length=MAKS_PARTAI, description="id partai tertentu")
    sumber: str | None = Field(None, pattern=r"^[a-z0-9-]{2,30}$",
                               description="label sumber seperti tersimpan di database")
    pemain: list[str] | None = Field(None, max_length=20, description="potongan nama pemain")
    min_elo: int | None = Field(None, ge=1000, le=3000, description="KEDUA pemain minimal sekian")
    lawan_min_elo: int | None = Field(None, ge=1000, le=3000, description="lawan Ray minimal sekian")
    gelar_lawan: list[str] | None = Field(None, max_length=20)
    hasil: str | None = Field(None, description=" | ".join(HASIL_VALID))
    format: str | None = Field(None, description=" | ".join(FORMAT_VALID))
    terbaru: int = Field(50, ge=1, le=MAKS_PARTAI, description="jumlah partai terbaru")
    terlama: bool = Field(False, description="kerjakan dari partai terlama dulu; diisi "
                                             "otomatis kalau batch kembar sedang jalan")
    min_langkah: int = Field(10, ge=0, le=200)
    repertoire: bool = Field(False, description="hanya pembukaan repertoire Ray "
                                                "(English, Catalan, Ragozin, Sicilian)")
    ulang: bool = False
    dangkal: bool = Field(False, description="hanya partai yang analisisnya masih kasar "
                                             "(profil cepat/lichess) — untuk memperdalam")
    profil: str = Field("hemat", pattern=r"^(cepat|hemat|normal|dalam)$")
    bagian: str | None = Field(None, pattern=r"^\d{1,2}/\d{1,2}$",
                               description="K/N: hanya partai id % N == K (pengisi slot penjaga)")

    @field_validator("pemain", "gelar_lawan")
    @classmethod
    def _teks_pendek_dan_bisa_disimpan(cls, v):
        for t in v or []:
            if len(t) > 100:
                raise ValueError("teks terlalu panjang (maks 100 aksara)")
            try:
                t.encode("utf-8")
            except UnicodeEncodeError as e:
                raise ValueError("teks berisi karakter yang tidak bisa dipakai") from e
        return v

    @field_validator("bagian")
    @classmethod
    def _bagian_masuk_akal(cls, v):
        # "1/0", "0/0", "3/2" dulu lolos pola lalu diam-diam memilih 0 partai,
        # dan antrean menjawab "mungkin semuanya sudah dianalisis" — menyesatkan.
        if v is not None:
            k, n = (int(x) for x in v.split("/"))
            if n < 1 or k >= n:
                raise ValueError("bagian K/N harus 0 <= K < N")
        return v


class Penghentian(BaseModel):
    pid: int


# ------------------------------------------------------------------ bantu

def _modul():
    """
    Muat analisis_partai.py (ada di akar proyek, bukan paket app).

    Diimpor saat dipakai, bukan saat server start: kalau berkas itu sedang
    diedit sesi lain, server tidak boleh ikut gagal hidup.
    """
    akar = str(AKAR)
    if akar not in sys.path:
        sys.path.insert(0, akar)
    try:
        import analisis_partai
    except Exception as e:      # noqa: BLE001 - apa pun sebabnya, jawab jujur
        raise HTTPException(status_code=500,
                            detail=f"analisis_partai.py tidak bisa dimuat: {e}") from e
    return analisis_partai


# Sumber yang isinya partai Ray sendiri. Ditulis sekali di sini supaya
# pembagian "partai saya" vs "partai GM" tidak tersebar di beberapa kueri dan
# di JavaScript. Ray, 12 Sep: "hilangkan partai ini dari partai gm karena ini
# merupakan akun saya sudah ada di partai saya".
SUMBER_SAYA = ("lichess-user", "chesscom")


# Partai yang bisa dipilih analisis_partai.pilih_partai() tanpa --id: bawaan
# --min-langkah 10, dan hasil '*' disaring sejak 25 Sep 2026. Semua angka
# "belum dianalisis" harus memakai syarat yang SAMA, kalau tidak halaman
# menjanjikan partai yang tidak akan pernah terpilih.
SYARAT_BISA_DIPILIH = "g.jumlah_langkah >= 10 AND g.hasil <> '*'"


def daftar_sumber(conn: sqlite3.Connection) -> list[dict]:
    """
    Label sumber yang benar-benar ada di database, beserta jumlahnya.

    Sengaja tidak ditulis sebagai daftar tetap: halaman Impor membiarkan Ray
    memberi label sendiri, jadi daftar tertulis di kode pasti ketinggalan.
    """
    # "belum" = yang BISA dipilih pilih_partai(): >= 10 langkah dan hasilnya
    # bukan '*'. Tanpa itu angkanya tidak pernah nol (28 Sep 2026: "1 partai
    # GM belum", Tal-Tukmakov 1983 ber-'*', tapi Mulai menjawab 0 partai).
    #
    # Semua kolom yang dibaca ada di idx_partai_bisa_dipilih (skema v12), jadi
    # tabel partai — yang barisnya memuat PGN lengkap — tidak disentuh sama
    # sekali; analisis dicek lewat indeks uniknya. Dulu tiga kueri memindai
    # seluruh partai: 10 detik tiap cache angka habis saat disk dingin
    # (29 Sep 2026), padahal halaman Mesin bertanya tiap 5 detik.
    q = f"""
        SELECT g.sumber AS nama, COUNT(*) AS partai,
               SUM(CASE WHEN a.partai_id IS NULL AND {SYARAT_BISA_DIPILIH}
                        THEN 1 ELSE 0 END) AS belum
        FROM partai g
        LEFT JOIN analisis a ON a.partai_id = g.id
        WHERE g.sumber IS NOT NULL
        GROUP BY g.sumber ORDER BY partai DESC
    """
    return [{"nama": r["nama"], "partai": r["partai"], "belum": r["belum"] or 0,
             "pribadi": r["nama"] in SUMBER_SAYA}
            for r in conn.execute(q)]


def daftar_pemain(conn: sqlite3.Connection, sumber: str | None = None,
                  cari: str | None = None, batas: int = 40,
                  kelompok: str | None = None) -> list[dict]:
    """
    Pemain yang paling banyak punya partai BELUM dianalisis, untuk dipilih dari
    dropdown.

    Ray: "pemainnya kalau bisa pilihan sama kayak kirinya, karena kalau salah
    ketik repot" — kotak teks bebas berarti 'Carsen' menghasilkan nol partai
    tanpa penjelasan. Urutannya mengikuti jumlah yang belum dianalisis, sama
    seperti daftar sumber, supaya yang paling berguna ada di atas.

    `nama` dikembalikan apa adanya dan bisa langsung dikirim balik sebagai
    `pemain`: pencocokan `--pemain` memakai nama yang dinormalisasi, jadi
    "Carlsen, Magnus" tetap cocok.
    """
    syarat = ["a.partai_id IS NULL", SYARAT_BISA_DIPILIH]
    params: list = []
    if sumber:
        syarat.append("g.sumber = ?")
        params.append(sumber)
    # kelompok 'gm': akun Ray sendiri tidak ditawarkan di sini — partainya
    # sudah punya tab sendiri, dan memunculkannya dua kali membingungkan.
    if kelompok == "gm":
        syarat.append("p.akun_pribadi = 0")
        syarat.append(f"g.sumber NOT IN ({','.join('?' * len(SUMBER_SAYA))})")
        params += list(SUMBER_SAYA)
    elif kelompok == "saya":
        syarat.append("p.akun_pribadi = 1")
    if cari:
        from .repositori import pola_like      # noqa: PLC0415
        syarat.append("p.nama LIKE ? ESCAPE '\\'")
        params.append(pola_like(cari.strip()))
    q = f"""
        SELECT p.id, p.nama, p.gelar, COUNT(*) AS belum
        FROM partai g
        JOIN pemain p ON p.id IN (g.putih_id, g.hitam_id)
        LEFT JOIN analisis a ON a.partai_id = g.id
        WHERE {' AND '.join(syarat)}
        GROUP BY p.id
        ORDER BY belum DESC, p.nama
        LIMIT ?
    """
    return [{"id": r["id"], "nama": r["nama"], "gelar": r["gelar"], "belum": r["belum"]}
            for r in conn.execute(q, params + [max(1, min(batas, 200))])]


def _periksa_sumber(s: Saringan, conn: sqlite3.Connection | None = None) -> None:
    """
    Sumber diperiksa terhadap database, bukan daftar tertulis.

    'gm' adalah label khusus (semua sumber partai GM), jadi selalu sah.
    `conn` bisa diberikan dari luar; tanpa itu dibuka koneksi ke database
    nyata — tes memberi koneksi sementara supaya tidak bergantung pada
    data/catur.db milik Ray.
    """
    if not s.sumber or s.sumber == "gm":
        return
    if conn is not None:
        ada = [d["nama"] for d in daftar_sumber(conn)]
        if s.sumber not in ada:
            raise HTTPException(
                status_code=422,
                detail=f"Belum ada partai dengan sumber '{s.sumber}'. Yang ada: "
                       + (", ".join(ada) if ada else "(database kosong)"))
        return
    conn = _buka()
    try:
        ada = [d["nama"] for d in daftar_sumber(conn)]
    finally:
        conn.close()
    if s.sumber not in ada:
        raise HTTPException(
            status_code=422,
            detail=f"Belum ada partai dengan sumber '{s.sumber}'. Yang ada: "
                   + (", ".join(ada) if ada else "(database kosong)"))


def _periksa(s: Saringan) -> None:
    if s.hasil and s.hasil not in HASIL_VALID:
        raise HTTPException(status_code=422, detail=f"Hasil tidak dikenal: {s.hasil}")
    if s.format and s.format not in FORMAT_VALID:
        raise HTTPException(status_code=422, detail=f"Format tidak dikenal: {s.format}")
    for g in (s.gelar_lawan or []):
        if g.upper() not in GELAR_VALID:
            raise HTTPException(status_code=422, detail=f"Gelar tidak dikenal: {g}")
    if s.dangkal and s.profil == "cepat":
        # memperdalam dengan profil yang sama kasarnya = partai yang sama
        # terpilih lagi selamanya (audit 27 Sep 2026)
        raise HTTPException(status_code=422,
                            detail="Memperdalam analisis kasar butuh profil hemat atau lebih "
                                   "teliti; dengan profil cepat hasilnya tetap kasar.")
    # Tanpa saringan sama sekali BOLEH: `terbaru` selalu ada dan dibatasi
    # MAKS_PARTAI, jadi "analisis apa saja, yang terbaru dulu" tetap terbatas.
    # Dulu ini ditolak karena takut seluruh database ikut terjaring — ketakutan
    # yang sudah dijawab oleh batas `terbaru` itu sendiri. Ray, 13 Sep:
    # "ini tidak perlu ada saya ingin analisa semuanya".
    # Yang tetap dijaga di CLI: di sana `--terbaru` opsional, jadi tanpa
    # saringan artinya benar-benar 266 ribu partai.


def _ruang_nama(s: Saringan) -> SimpleNamespace:
    """Bentuk yang diminta pilih_partai(): sama persis dengan hasil argparse."""
    return SimpleNamespace(
        id=s.id, sumber=s.sumber, pemain=s.pemain, min_elo=s.min_elo,
        gelar_lawan=[g.upper() for g in s.gelar_lawan] if s.gelar_lawan else None,
        lawan_min_elo=s.lawan_min_elo, hasil=s.hasil, format_=s.format,
        terbaru=s.terbaru, min_langkah=s.min_langkah, ulang=s.ulang,
        dangkal=s.dangkal, repertoire=s.repertoire, bagian=s.bagian,
        terlama=s.terlama,
    )


def _perintah(s: Saringan) -> list[str]:
    """Perintah CLI yang setara dengan saringan ini."""
    p = ["analisis_partai.py"]
    if s.id:
        p += ["--id"] + [str(i) for i in s.id]
    if s.sumber:
        p += ["--sumber", s.sumber]
    if s.pemain:
        p += ["--pemain"] + list(s.pemain)
    if s.min_elo:
        p += ["--min-elo", str(s.min_elo)]
    if s.gelar_lawan:
        p += ["--gelar-lawan"] + [g.upper() for g in s.gelar_lawan]
    if s.lawan_min_elo:
        p += ["--lawan-min-elo", str(s.lawan_min_elo)]
    if s.hasil:
        p += ["--hasil", s.hasil]
    if s.format:
        p += ["--format", s.format]
    if s.repertoire:
        p += ["--repertoire"]
    if s.ulang:
        p += ["--ulang"]
    if s.dangkal:
        p += ["--dangkal"]
    if s.min_langkah != 10:
        p += ["--min-langkah", str(s.min_langkah)]
    if s.bagian:
        p += ["--bagian", s.bagian]
    p += ["--terbaru", str(s.terbaru), "--profil", s.profil]
    if s.terlama:
        p += ["--terlama"]
    if PATH_DB is not None:
        p += ["--db", str(PATH_DB)]
    return p


def _perintah_tampil(s: Saringan) -> str:
    """
    Perintah setara yang bisa ditempel ke terminal apa adanya.

    Argumen bertanda spasi dikutip: nama pemain memuat koma dan spasi
    ("Nakamura, Hikaru"), jadi tanpa kutip perintahnya berubah arti
    menjadi dua nama terpisah waktu ditempel.
    """
    bagian = ["python"]
    for a in _perintah(s):
        bagian.append(f'"{a}"' if " " in a else a)
    return " ".join(bagian)


def _hitung(s: Saringan) -> dict:
    """Jumlah partai + perkiraan waktu, memakai perhitungan CLI yang sama."""
    m = _modul()
    from catur_analysis.engine import PROFIL      # noqa: PLC0415 - butuh sys.path dari _modul()

    conn = _buka()
    try:
        daftar = m.pilih_partai(conn, _ruang_nama(s))
        profil = PROFIL[s.profil]
        total_ply = sum(p["jumlah_langkah"] * 2 for p in daftar)
        terukur = m.detik_per_ply_terukur(conn, s.profil)
        per_ply = terukur or m.DETIK_PER_PLY_CADANGAN[s.profil]
        contoh = [m.label(p).strip() for p in daftar[:12]]
    finally:
        conn.close()
    return {
        "jumlah": len(daftar),
        "perkiraan_menit": round(total_ply * per_ply / 60),
        "dasar_perkiraan": "dari riwayat" if terukur else "cadangan, belum ada riwayat",
        "detik_per_ply": round(per_ply, 2),
        "profil": s.profil,
        "depth": profil.depth,
        "ram_mb": profil.perkiraan_ram_mb,
        "contoh": contoh,
        "perintah": _perintah_tampil(s),
    }


# --------------------------------------------------------- daftar proses

# Berapa batch engine boleh jalan bersamaan.
#
# Diukur 12 Sep 2026 di mesin ini (10 inti fisik / 16 logis, profil hemat =
# 2 utas + ~371 MB per batch): dua batch memakai 38% CPU dan RAM bebas masih
# 4,9 GB. Jadi batas lama "satu batch saja" membuang lebih dari separuh mesin.
# Empat batch ≈ 8 utas (setengah dari yang logis), ~1,5 GB, masih menyisakan
# ruang untuk server web dan pekerjaan Ray sendiri.
#
# Ray, 12 Sep: "ini tidak bisa ditabrak" — batas ini memang aturan kita, bukan
# batas mesin, jadi angkanya boleh diubah di sini saja.
#
# Ray, 13 Sep: dinaikkan 4 -> 5 bersamaan dengan antrean otomatis.
# Lima batch ≈ 10 utas, ~1,9 GB. Penjaga antrean sengaja memakai batas
# sendiri yang lebih kecil (jaga_antrean.py --maks) supaya masih ada slot
# untuk batch yang dijalankan Ray sendiri.
#
# Ray, 13 Sep siang: 5 membuat laptop tidak bisa dipakai — "batas maks laptop
# sepertinya 3". Turun ke 3, dan batch kini berprioritas idle.
#
# Ray, 13 Sep sore: "tambahkan jadi 4 dong biar cepat". Naik ke 4 karena kini
# ada batas CPU (app/batas_cpu.py): semua engine dikurung di sebagian inti,
# jadi batch keempat berbagi inti yang sama, bukan merebut inti milik Ray.
#
# Ray, 13 Sep malam: laptop lag lagi dengan 4 batch (RAM komit 22,7/23,2 GB,
# bukan CPU). Ray: "buat di laptop ini max 2 saja yang jalan, sisanya akan
# dibantu dari komputer lain lewat rdc".
MAKS_BATCH = 2


# Melihat & mematikan proses batch pindah ke app/proses.py (audit 21 Sep 2026:
# berkas ini terlalu besar). Nama-namanya diimpor ulang di sini supaya
# pemanggil lama — termasuk jaga_antrean.py dan tes — tidak perlu diubah.
from .proses import (JEDA_PINDAI, TANPA_JENDELA, _hidup, _keluaran_proses,   # noqa: E402,F401
                     _matikan, _pindai, _pindai_batch, _pindai_proses, _proses_ada)

# ------------------------------------------------------------ catatan tugas

# Catatan tugas & pembacaan kemajuan pindah ke app/tugas_batch.py
# (audit 22 Sep 2026). Diimpor ulang supaya pemanggil lama tetap jalan.
from .tugas_batch import (FOLDER_LOG, MAKS_TUGAS, PATH_TUGAS, POLA_DIPILIH,   # noqa: E402,F401
                          POLA_MAJU, POLA_SISA, _baca_tugas, _kemajuan,
                          _rangkum_tugas, _status_akhir, _tulis_tugas,
                          bersihkan_kemajuan_yatim, kemajuan_proses, ubah_tugas)


# Angka database untuk halaman Mesin: berubah beberapa per menit, tapi
# halaman itu bertanya tiap 5 detik selagi batch jalan. Menghitungnya tiap kali
# berarti menyisir 266 ribu partai berulang-ulang — 0,6 detik sekali hitung,
# tepat saat mesin sedang sibuk dengan empat engine. Ditahan sebentar saja.
# 60 detik: angka ini berubah beberapa per menit, dan kemajuan batch di halaman
# Mesin dibaca dari log masing-masing, bukan dari sini.
JEDA_ANGKA = 60.0
_angka: dict = {"waktu": 0.0, "hasil": None}


def angka_database(conn: sqlite3.Connection) -> dict:
    """Statistik + sumber + jumlah belum dianalisis, ditahan JEDA_ANGKA detik."""
    from .analisis_db import statistik_analisis        # noqa: PLC0415

    sekarang = time.time()
    if _angka["hasil"] is not None and sekarang - _angka["waktu"] < JEDA_ANGKA:
        return _angka["hasil"]

    # "belum" per sumber sudah dihitung daftar_sumber() dengan syarat yang sama
    # (bisa dipilih DAN belum dianalisis); dulu dihitung ulang lewat dua kueri
    # lain yang masing-masing memindai seluruh tabel partai.
    sumber = daftar_sumber(conn)
    hasil = {
        "statistik": statistik_analisis(conn),
        "sumber": sumber,
        "belum_saya": sum(s["belum"] for s in sumber if s["pribadi"]),
        "belum_gm": sum(s["belum"] for s in sumber if not s["pribadi"]),
    }
    _angka.update(waktu=sekarang, hasil=hasil)
    return hasil

# ------------------------------------------------------------------ rute

@router.post("/pratinjau", summary="Berapa partai terpilih dan berapa lama")
def pratinjau(s: Saringan):
    _periksa(s)
    _periksa_sumber(s)
    # Arahnya diputar di sini juga, supaya daftar "Lihat dulu" sama dengan
    # partai yang benar-benar dikerjakan kalau Mulai ditekan.
    s, catatan = _hindari_kembar(s, batch_berjalan())
    return {**_hitung(s), "catatan": catatan}


@router.post("/mulai", summary="Jalankan batch analisis di latar belakang")
def mulai(s: Saringan):
    """Rute /mulai: periksa dulu, baru luncurkan."""
    _periksa(s)
    _periksa_sumber(s)
    return luncurkan(s)


# Proses anak yang pernah diluncurkan dari sini. Batch memang sengaja LEPAS
# (boleh hidup walau server dimatikan), tapi objek Popen-nya tetap disimpan
# sebentar: kalau dibuang begitu saja, Python meneriakkan
# "ResourceWarning: subprocess ... is still running" setiap kali objeknya
# dikumpulkan sampah — muncul di keluaran tes dan menutupi kegagalan
# sungguhan (audit 21 Sep 2026). poll() di sini juga memanen anak yang sudah
# mati supaya tidak jadi proses zombi di Linux.
_anak: list = []


def _ingat_anak(proses) -> None:
    _panen_anak()
    _anak.append(proses)


def _panen_anak() -> int:
    """Buang catatan anak yang sudah mati; kembalikan jumlah yang masih hidup."""
    _anak[:] = [p for p in _anak if p.poll() is None]
    return len(_anak)


# Batas batch pernah bocor: 25 Sep 2026 EMPAT batch jalan padahal MAKS_BATCH=2,
# dan keempatnya mengerjakan partai yang sama (#245850-245853). Sebabnya dua,
# keduanya balapan:
#   1. Pemeriksaan slot memakai pindaian daftar proses, dan proses yang baru
#      saja dianakkan belum tentu muncul di sana (pindaian lewat PowerShell,
#      ratusan milidetik). Dua permintaan yang berdekatan sama-sama melihat
#      "slot kosong".
#   2. Antara periksa dan catat tugas tidak ada penguncian, dan peluncur bukan
#      cuma satu proses: server (dua tab sekalipun) DAN jaga_antrean.py.
# Perbaikannya: hitung dari gabungan pindaian + catatan tugas, lalu kurung
# seluruh periksa-luncurkan-catat dengan kunci berkas lintas proses.
PATH_KUNCI_LUNCUR = FOLDER_DATA / ".peluncuran.lock"
TUNGGU_KUNCI_LUNCUR = 20.0


def batch_berjalan() -> list[dict]:
    """
    Batch yang sedang berjalan menurut DUA sumber sekaligus.

    Pindaian proses saja terlambat mengenali batch yang baru dianakkan;
    catatan tugas saja bisa basi kalau prosesnya mati mendadak. Gabungannya:
    pindaian, ditambah catatan berstatus `berjalan` yang PID-nya masih ada.
    """
    hasil = {b["pid"]: b for b in _pindai_batch()}
    for t in _baca_tugas():
        pid = t.get("pid")
        if t.get("status") == "berjalan" and pid and pid not in hasil and _proses_ada(pid):
            hasil[pid] = {"pid": pid, "perintah": t.get("perintah", "")}
    return list(hasil.values())


@contextmanager
def _kunci_peluncuran():
    """Satu peluncuran pada satu waktu, lintas proses (server + penjaga)."""
    from .kunci_penulis import KunciPenulis, KunciTerpakai   # noqa: PLC0415
    batas = time.monotonic() + TUNGGU_KUNCI_LUNCUR
    while True:
        k = KunciPenulis("meluncurkan batch analisis", path=PATH_KUNCI_LUNCUR,
                         path_info=PATH_KUNCI_LUNCUR.with_suffix(".info"))
        try:
            k.ambil()
        except KunciTerpakai:
            if time.monotonic() >= batas:
                raise HTTPException(
                    status_code=409,
                    detail="Peluncuran batch lain sedang diproses. Coba lagi sebentar.")
            time.sleep(0.2)
            continue
        try:
            yield
        finally:
            k.lepas()
        return


def luncurkan(s: Saringan) -> dict:
    """
    Luncurkan satu batch analisis sebagai proses lepas.

    Dipisah dari rutenya supaya penjaga antrean (jaga_antrean.py) memakai
    jalur peluncuran yang SAMA — termasuk batas jumlah batch, bendera
    proses, dan pencatatan tugas.
    """
    from . import jeda_mesin                 # noqa: PLC0415
    if jeda_mesin.sedang_jeda():
        raise HTTPException(status_code=409,
                            detail="Mesin sedang dijeda. Tekan Lanjutkan dulu kalau mau menjalankan batch.")
    with _kunci_peluncuran():
        return _luncurkan_terkunci(s)


def _bentrok(s: Saringan, lain: list[tuple], conn, m) -> list[tuple[dict, int]]:
    """
    Batch berjalan yang akan mengerjakan partai yang sama dengan saringan ini:
    arahnya sama (terbaru/terlama) dan partai pilihannya beririsan. Batch
    dari arah berlawanan tidak dihitung — keduanya bertemu di tengah dan
    jalankan() melewati partai yang sudah dianalisis di tempat lain.
    """
    ids = {p["id"] for p in m.pilih_partai(conn, _ruang_nama(s))}
    teks = " ".join(_perintah(s))
    hasil = []
    for b, args, ids_b in lain:
        if args is None:
            # Perintah tak terbaca: jatuh ke pembanding lama, teks yang sama persis.
            if (b.get("perintah") or "").endswith(teks):
                hasil.append((b, len(ids)))
        elif bool(getattr(args, "terlama", False)) == s.terlama and ids & ids_b:
            hasil.append((b, len(ids & ids_b)))
    return hasil


def _hindari_kembar(s: Saringan, berjalan: list[dict]) -> tuple[Saringan, str | None]:
    """
    Dua batch yang memilih partai yang sama = kerja percuma. 25 Sep 2026 empat
    batch `--terbaru 2000 --profil cepat` berjalan bersamaan dan keempatnya
    menganalisis #245850-245853.

    Dulu kembaran ditolak dengan saran "--bagian 0/2 dan 1/2", yang tidak ada
    di halaman Mesin (Ray, 27 Sep 2026: "saya ingin membantu lebih cepat tapi
    malah error"). Sekarang batch baru diputar ke ujung lain (terlama dulu
    kalau yang jalan terbaru dulu, dan sebaliknya); baru ditolak kalau kedua
    ujung sudah dikerjakan.

    Yang dibandingkan PARTAINYA, bukan teks perintah: `--sumber gm --terbaru
    2000` lolos di samping `--terbaru 2000` padahal isinya sama (partai Ray
    yang belum dianalisis = 0), dan keduanya mengerjakan partai yang sama.
    """
    if not berjalan:
        return s, None
    m = _modul()
    conn = _buka()
    try:
        lain = []
        for b in berjalan:
            args = m.argumen_dari_perintah(b.get("perintah") or "")
            ids_b = {p["id"] for p in m.pilih_partai(conn, args)} if args else None
            lain.append((b, args, ids_b))
        kena = _bentrok(s, lain, conn, m)
        if not kena:
            return s, None
        balik = s.model_copy(update={"terlama": not s.terlama})
        kena_balik = _bentrok(balik, lain, conn, m)
    finally:
        conn.close()
    if kena_balik:
        raise HTTPException(
            status_code=409,
            detail="Partai pilihan ini sudah dikerjakan dari dua arah sekaligus (PID "
                   + ", ".join(str(b["pid"]) for b, _ in kena + kena_balik)
                   + ": satu dari partai terbaru, satu dari terlama). Batch ketiga "
                   "hanya akan mengulang partai yang sama; tunggu salah satu selesai.")
    pid = ", ".join(f"{b['pid']} ({n} partai sama)" for b, n in kena)
    arah = "terlama" if balik.terlama else "terbaru"
    return balik, (f"Batch PID {pid} sedang mengerjakan partai yang sama, jadi batch ini "
                   f"mengerjakan dari partai {arah} supaya tidak mengulang.")


def buka_log_baru():
    """
    (path, berkas terbuka) untuk log batch baru, dijamin tidak dipakai batch lain.

    Namanya berstempel detik; dua batch yang mulai di detik yang sama dulu
    menulis ke SATU berkas (25 Sep 07:41, PID 13068 penjaga + 26548 server):
    open("w") kedua mengosongkan yang pertama dan kemajuannya tercampur, jadi
    batch yang dihentikan setelah 5 menit tampak 2000/2000. Mode "x" (buat
    eksklusif) dijamin sistem operasi, juga antara server dan penjaga.
    """
    cap = datetime.now().strftime("%Y%m%d-%H%M%S")
    FOLDER_LOG.mkdir(parents=True, exist_ok=True)
    for n in range(1, 100):
        path = FOLDER_LOG / (f"log-analisa-web-{cap}.txt" if n == 1
                             else f"log-analisa-web-{cap}-{n}.txt")
        try:
            return path, path.open("x", encoding="utf-8")
        except FileExistsError:
            continue
    raise HTTPException(status_code=500, detail="Tidak bisa membuat berkas log baru.")


def _luncurkan_terkunci(s: Saringan) -> dict:
    _pindai["waktu"] = 0.0                   # jangan pakai hasil pindaian lama
    berjalan = batch_berjalan()
    if len(berjalan) >= MAKS_BATCH:
        raise HTTPException(
            status_code=409,
            detail=f"Sudah ada {len(berjalan)} batch analisis berjalan (PID "
                   + ", ".join(str(b["pid"]) for b in berjalan) + f"), dan batasnya "
                   f"{MAKS_BATCH} sekaligus. Tunggu satu selesai, atau hentikan dari "
                   "daftar di bawah.")

    s, catatan = _hindari_kembar(s, berjalan)

    ringkas = _hitung(s)
    ringkas["catatan"] = catatan
    if ringkas["jumlah"] == 0:
        raise HTTPException(status_code=422,
                            detail="Tidak ada partai yang cocok (mungkin semuanya sudah "
                                   "dianalisis). Longgarkan saringannya, atau centang "
                                   "'analisis ulang'.")

    path_log, log = buka_log_baru()

    # -u: keluaran tidak ditahan buffer, jadi kemajuannya bisa dibaca hidup-hidup.
    perintah = [sys.executable, "-X", "utf8", "-u"] + _perintah(s)
    bendera = 0
    if os.name == "nt":
        # DETACHED_PROCESS, bukan CREATE_NO_WINDOW (dikembalikan 13 Sep 2026).
        #
        # CREATE_NO_WINDOW membuat batch tetap berbagi console session dengan
        # server. Waktu server dimatikan paksa untuk dimuat ulang, console itu
        # ikut dibubarkan dan Stockfish mati dengan exit code 3221225786
        # (0xC000013A = STATUS_CONTROL_C_EXIT). Terjadi nyata pada empat batch
        # Ray 12 Sep: dua mati dengan EngineTerminatedError tepat saat server
        # dimuat ulang, dua lagi berhenti di partai ke-2.
        #
        # Jendela konsol kosong yang dulu terlihat (A22) datang dari Stockfish,
        # bukan dari proses batch: induknya tidak punya console, jadi Windows
        # membuatkan satu untuk engine. Itu ditangani di sumbernya —
        # src/catur_analysis/engine.py menjalankan engine dengan CREATE_NO_WINDOW.
        #
        # IDLE_PRIORITY_CLASS (13 Sep 2026): Ray "5 terlalu banyak, komputer saya
        # tidak bisa buka apa apa". Dengan prioritas normal, engine merebut CPU
        # dari aplikasi yang sedang dipakai. Prioritas idle berarti engine hanya
        # memakai CPU yang sedang tidak dipakai siapa pun: saat Ray bekerja,
        # aplikasinya didahulukan; saat laptop ditinggal, engine tetap dapat
        # CPU penuh. Stockfish ikut mewarisi kelas ini karena dianakkan tanpa
        # bendera prioritas sendiri (lihat src/catur_analysis/engine.py).
        bendera = (subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
                   | subprocess.IDLE_PRIORITY_CLASS)
    try:
        proses = subprocess.Popen(
            perintah, cwd=str(AKAR), stdout=log, stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL, creationflags=bendera,
            start_new_session=(os.name != "nt"), close_fds=True)
    except OSError as e:
        log.close()
        raise HTTPException(status_code=500, detail=f"Gagal menjalankan batch: {e}") from e
    finally:
        log.close()
    _ingat_anak(proses)

    # Kurung di inti yang diizinkan SEBELUM batch sempat menyalakan Stockfish;
    # Stockfish mewarisi affinity induknya. Penjaga menerapkannya ulang tiap
    # putaran untuk jaga-jaga kalau balapan ini kalah.
    from . import batas_cpu                        # noqa: PLC0415
    batas_cpu.terapkan(proses.pid, batas_cpu.masker(batas_cpu.baca_persen()))

    tugas = {
        "pid": proses.pid,
        "mulai": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "status": "berjalan",
        "jumlah": ringkas["jumlah"],
        "perkiraan_menit": ringkas["perkiraan_menit"],
        "profil": s.profil,
        "perintah": ringkas["perintah"],
        "log": str(path_log),
        # Saringannya ikut disimpan supaya batch yang berhenti di tengah bisa
        # dilanjutkan tanpa Ray menyusun ulang isian formulirnya.
        "saringan": s.model_dump(),
    }
    with ubah_tugas() as daftar:
        daftar.append(tugas)
    _pindai["waktu"] = 0.0
    return {"tugas": tugas, "pratinjau": ringkas, "catatan": catatan}


@router.get("/status", summary="Statistik analisis + tugas yang berjalan")
def status():
    conn = _buka()
    try:
        angka = angka_database(conn)
    finally:
        conn.close()
    st = angka["statistik"]

    tugas = _rangkum_tugas()
    dikenal = {t.get("pid") for t in tugas}
    # Pindaian saja: yang baru dianakkan belum terlihat, jadi halaman sempat
    # menulis "slot kosong" sedetik setelah batch dijalankan.
    semua_berjalan = batch_berjalan()
    bersihkan_kemajuan_yatim({b["pid"] for b in semua_berjalan})
    lain = []
    for b in semua_berjalan:
        if b["pid"] in dikenal:
            continue
        b = dict(b)
        b.update(kemajuan_proses(b["pid"]))    # kemajuan batch dari terminal
        lain.append(b)
    return {
        "partai_dianalisis": st["partai_dianalisis"],
        "partai_total": st["partai_total"],
        "langkah_dianalisis": st["langkah_dianalisis"],
        "belum_saya": angka["belum_saya"],
        "belum_gm": angka["belum_gm"],
        "sumber": angka["sumber"],
        "tugas": tugas,
        "batch_lain": lain,
        # sibuk = slot penuh, bukan sekadar "ada yang jalan": halaman boleh
        # menawarkan tombol Mulai selama masih ada slot engine yang kosong.
        "batch_berjalan": len(semua_berjalan),
        "maks_batch": MAKS_BATCH,
        "sibuk": len(semua_berjalan) >= MAKS_BATCH,
        "jeda": _status_jeda(),
    }


def _status_jeda() -> dict:
    from . import jeda_mesin                 # noqa: PLC0415
    return jeda_mesin.status()


@router.get("/pemain", summary="Pemain yang bisa dipilih, diurutkan dari yang paling banyak belum dianalisis")
def pemain(sumber: str | None = None, cari: str | None = None, batas: int = 40,
           kelompok: str | None = None):
    """
    Isi dropdown Pemain. Ikut `sumber` dan `kelompok` yang sedang dipilih,
    supaya angkanya sesuai dengan yang akan benar-benar dianalisis.

    Rute ini sempat hilang pada B19 (13 Sep 2026) padahal `daftar_pemain()`
    tetap ada, jadi dropdown Pemain di halaman Mesin diam-diam jatuh ke mode
    "ketik sendiri". Dikembalikan dengan kontrak yang sama.
    """
    if kelompok not in (None, "", "gm", "saya", "semua"):
        raise HTTPException(status_code=422,
                            detail="kelompok harus 'gm', 'saya', atau kosong")
    conn = _buka()
    try:
        if sumber:
            ada = {x["nama"] for x in daftar_sumber(conn)}
            if sumber not in ada:
                raise HTTPException(
                    status_code=422,
                    detail=f"Sumber {sumber!r} tidak ada. Yang ada: {', '.join(sorted(ada))}")
        return {"sumber": sumber, "kelompok": kelompok,
                "pemain": daftar_pemain(conn, sumber, cari, batas, kelompok)}
    finally:
        conn.close()


class TambahAntrean(BaseModel):
    saringan: Saringan
    keterangan: str = Field("", max_length=200)


def _penjaga_jalan(segar: bool = False) -> int | None:
    """PID penjaga antrean kalau sedang jalan."""
    if segar:
        _pindai["waktu"] = 0.0
    for b in _pindai_proses("jaga_antrean.py"):
        if _proses_ada(b["pid"]):        # daftar bisa basi beberapa detik
            return b["pid"]
    return None


@router.get("/antrean", summary="Isi antrean analisis")
def lihat_antrean():
    from . import antrean as antre                 # noqa: PLC0415
    r = antre.ringkas()
    r["penjaga_pid"] = _penjaga_jalan()
    r["maks_batch"] = MAKS_BATCH
    return r


@router.post("/antrean", summary="Tambahkan pekerjaan ke antrean")
def tambah_antrean(masukan: TambahAntrean):
    """
    Pekerjaan disimpan dengan perkiraannya, bukan dijalankan sekarang.
    Penjaga (`jaga_antrean.py`) yang menjalankannya begitu ada slot kosong.
    """
    from . import antrean as antre                 # noqa: PLC0415
    s = masukan.saringan
    _periksa(s)
    _periksa_sumber(s)
    ringkas = _hitung(s)
    if ringkas["jumlah"] == 0:
        raise HTTPException(status_code=422,
                            detail="Tidak ada partai yang cocok, jadi tidak ada yang perlu diantrekan.")
    kerja = antre.tambah(s.model_dump(), masukan.keterangan or ringkas["perintah"],
                         ringkas["jumlah"], ringkas["perkiraan_menit"])
    return {"kerja": kerja, "pratinjau": ringkas, "penjaga_pid": _penjaga_jalan()}


@router.delete("/antrean/{id_}", summary="Buang satu pekerjaan dari antrean")
def hapus_antrean(id_: int):
    from . import antrean as antre                 # noqa: PLC0415
    if not antre.hapus(id_):
        raise HTTPException(status_code=404,
                            detail="Pekerjaan itu tidak ada, atau sedang berjalan "
                                   "(hentikan dulu dari daftar batch).")
    return {"id": id_, "status": "dibuang"}


@router.delete("/antrean", summary="Bersihkan riwayat antrean (selesai/gagal/batal)")
def bersihkan_antrean():
    """Yang menunggu dan yang sedang jalan TIDAK disentuh; hasil analisis di
    database juga tidak, karena berkas antrean hanya daftar pekerjaan."""
    from . import antrean as antre                 # noqa: PLC0415
    return {"dibuang": antre.bersihkan_riwayat(), "sisa": antre.ringkas()["hitung"]}


@router.post("/antrean/jaga", summary="Hidupkan/matikan penjaga antrean")
def jaga_antrean(jalan: bool = True, maks: int = MAKS_BATCH):
    """
    Penjaga dijalankan sebagai proses LEPAS, sama seperti batch: dia harus
    tetap hidup walau server dimuat ulang atau halaman ditutup.
    """
    pid = _penjaga_jalan(segar=True)
    if not jalan:
        if pid is None:
            return {"penjaga_pid": None, "status": "memang tidak jalan"}
        # hanya penjaganya; batch yang sedang jalan dibiarkan selesai
        _matikan(pid, pohon=False)
        return {"penjaga_pid": None, "status": "dihentikan"}
    if pid is not None:
        return {"penjaga_pid": pid, "status": "sudah jalan"}

    FOLDER_LOG.mkdir(parents=True, exist_ok=True)
    perintah = [sys.executable, "-X", "utf8", "-u", "jaga_antrean.py", "--jaga",
                "--maks", str(max(1, min(int(maks), MAKS_BATCH)))]
    bendera = (subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
               if os.name == "nt" else 0)
    # stdout dibuang: penjaga menulis lognya sendiri ke log-antrean.txt.
    # Kalau keduanya menulis ke berkas yang sama, tiap baris muncul dua kali.
    try:
        proses = subprocess.Popen(perintah, cwd=str(AKAR), stdout=subprocess.DEVNULL,
                                  stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
                                  creationflags=bendera,
                                  start_new_session=(os.name != "nt"), close_fds=True)
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"Gagal menjalankan penjaga: {e}") from e
    _ingat_anak(proses)
    _pindai["waktu"] = 0.0
    return {"penjaga_pid": proses.pid, "status": "dijalankan"}


@router.get("/cpu", summary="Batas pemakaian CPU untuk engine")
def lihat_batas_cpu():
    from . import batas_cpu                        # noqa: PLC0415
    persen = batas_cpu.baca_persen()
    return {"persen": persen, "pilihan": list(batas_cpu.PERSEN_VALID),
            "inti": batas_cpu.jumlah_inti(persen), "inti_total": batas_cpu.inti_total()}


@router.post("/cpu", summary="Ubah batas CPU dan langsung terapkan ke engine yang jalan")
def ubah_batas_cpu(persen: int):
    """
    Berlaku seketika untuk batch dan Stockfish yang SUDAH berjalan, tidak
    menunggu batch berikutnya — yang dikeluhkan Ray adalah laptop yang tidak
    bisa dipakai sekarang.
    """
    from . import batas_cpu                        # noqa: PLC0415
    try:
        batas_cpu.simpan_persen(persen)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    _pindai["waktu"] = 0.0
    return batas_cpu.terapkan_semua([b["pid"] for b in _pindai_batch()], persen)


@router.post("/jeda", summary="Jeda atau lanjutkan semua engine di laptop ini")
def jeda_semua(aktif: bool = True):
    """
    aktif=true: bekukan semua Stockfish (CPU kosong seketika) dan tahan batch
    baru. aktif=false: cairkan dan izinkan lagi. Lihat app/jeda_mesin.py.
    """
    from . import jeda_mesin                 # noqa: PLC0415
    _pindai["waktu"] = 0.0
    return jeda_mesin.jeda() if aktif else jeda_mesin.lanjutkan()


@router.post("/hentikan", summary="Hentikan satu batch analisis")
def hentikan(masukan: Penghentian):
    _pindai["waktu"] = 0.0
    if not _hidup(masukan.pid):
        raise HTTPException(status_code=404,
                            detail=f"Tidak ada batch analisis dengan PID {masukan.pid} "
                                   "yang sedang berjalan.")
    # Batch dari antrean ditandai batal DULU, baru prosesnya dimatikan. Dulu
    # hanya catatan tugas yang berubah: penjaga melihat prosesnya hilang tanpa
    # baris penutup, menganggapnya "terputus", lalu menjalankannya lagi sampai
    # 3 kali (22 Sep 21:05 dihentikan Ray, 21:06 diantrekan ulang).
    from . import antrean as antre                 # noqa: PLC0415
    ditandai = [k for k in antre.baca()
                if k.get("pid") == masukan.pid and k.get("status") == "jalan"]
    for k in ditandai:
        antre.perbarui(k["id"], status="batal", sampai=datetime.now().strftime("%Y-%m-%d %H:%M"),
                       alasan=f"dihentikan dari halaman Mesin (PID {masukan.pid})")
    try:
        _matikan(masukan.pid)
    except Exception:
        for k in ditandai:                        # masih hidup: kembalikan
            antre.perbarui(k["id"], status="jalan", alasan=k.get("alasan"))
        raise

    _pindai["waktu"] = 0.0
    with ubah_tugas() as daftar:
        for t in daftar:
            if t.get("pid") == masukan.pid and t.get("status") == "berjalan":
                t["status"] = "dihentikan"
                t["sampai"] = datetime.now().strftime("%Y-%m-%d %H:%M")
    return {"pid": masukan.pid, "status": "dihentikan",
            "catatan": "Partai yang sudah selesai tetap tersimpan; partai yang sedang "
                       "dihitung akan terpilih lagi di batch berikutnya. Pakai "
                       "\"Lanjutkan\" untuk meneruskan dengan saringan yang sama."}


@router.post("/lanjutkan", summary="Jalankan ulang batch yang berhenti sebelum selesai")
def lanjutkan(masukan: Penghentian):
    """
    Teruskan batch yang gagal atau dihentikan, memakai saringan yang sama.

    Ini untuk batch yang sudah BERHENTI. Untuk sekadar mengosongkan CPU
    sementara ada /jeda (app/jeda_mesin.py). Yang dipakai di sini adalah sifat
    `analisis_partai.py` sendiri — hasil disimpan tiap partai selesai, dan
    partai yang sudah ada di tabel `analisis` tidak dipilih lagi. Jadi
    menjalankan ulang saringan yang sama = melanjutkan dari tempat berhenti.

    Catatan jujur: dengan `--terbaru N`, batch lanjutan tetap mengambil N partai
    yang BELUM dianalisis, jadi jumlah totalnya bisa sedikit melebihi N yang
    semula diminta. Itu disengaja; memaksa hitungannya persis berarti menyimpan
    daftar id dan membuat batch lanjutan mengabaikan partai baru yang masuk.
    """
    for t in _baca_tugas():
        if t.get("pid") != masukan.pid:
            continue
        tersimpan = t.get("saringan")
        if not tersimpan or not isinstance(tersimpan, dict):
            raise HTTPException(
                status_code=400,
                detail="Batch ini dijalankan sebelum saringannya ikut dicatat, jadi "
                       "tidak bisa dilanjutkan otomatis. Susun saringannya lagi di atas.")
        # Saringan lama bisa tidak sah lagi menurut aturan versi sekarang (batas
        # berubah, berkas disunting tangan): itu 400 dengan alasannya, bukan 500.
        try:
            saringan = Saringan(**tersimpan)
        except ValidationError as e:
            raise HTTPException(
                status_code=400,
                detail="Saringan batch ini tidak sah lagi menurut aturan sekarang, jadi "
                       f"tidak bisa dilanjutkan otomatis: {e.errors()[0].get('msg', e)}") from e
        hasil = mulai(saringan)
        hasil["dilanjutkan_dari"] = masukan.pid
        return hasil
    raise HTTPException(status_code=404,
                        detail=f"Batch PID {masukan.pid} tidak ada di catatan.")
