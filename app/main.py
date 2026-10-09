"""
Server web Chess GM Database.

Jalankan:
    python -m uvicorn app.main:aplikasi --reload

Lalu buka http://127.0.0.1:8000

Alamat 127.0.0.1 hanya bisa diakses dari komputer ini sendiri. Tidak ada
yang bisa membukanya dari internet maupun dari jaringan WiFi yang sama.

Supaya tablet/HP bisa ikut menganalisis (app/pembantu.py), jalankan dengan
--host 0.0.0.0. Perangkat lain di WiFi tetap hanya bisa membuka halaman
pembantu (middleware hanya_pembantu_dari_luar), dan itu pun wajib kode.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import repositori
from .analisis_db import ambil_analisis, statistik_analisis
from .db import PATH_DB, buat_skema, koneksi

AKAR = Path(__file__).resolve().parents[1]
FOLDER_WEB = AKAR / "web"


@asynccontextmanager
async def daur_hidup(app: FastAPI):
    """
    Pastikan database dan tabelnya ada sebelum melayani permintaan.

    Dulu `@aplikasi.on_event("startup")`; FastAPI menandainya usang dan tiap
    kali tes dijalankan muncul DeprecationWarning. Peringatan yang selalu ada
    membuat peringatan baru tidak terlihat, jadi diganti ke lifespan.
    """
    conn = koneksi()
    try:
        buat_skema(conn)
    finally:
        conn.close()
    # Server dari pintasan berjalan tanpa jendela, dan Windows 11 lalu
    # memperlambatnya sampai 4x (lihat app/proses.py jangan_diperlambat).
    from .proses import jangan_diperlambat
    jangan_diperlambat()
    yield


aplikasi = FastAPI(
    title="Chess GM Database",
    description="Database partai catur untuk belajar. Phase 1: daftar, cari, "
                "buka, dan mainkan ulang partai.",
    version="0.1.0",
    lifespan=daur_hidup,
)


@aplikasi.exception_handler(OverflowError)
async def angka_terlalu_besar(request, galat):
    """
    Angka di luar INTEGER 64-bit SQLite (mis. /api/partai/99999999999999999999)
    dulu lolos jadi 500 di tiap endpoint yang menerima id (audit 27 Sep 2026).
    Satu penangan di sini melindungi semua endpoint sekaligus, termasuk
    parameter query; tidak ada partai atau pemain dengan id sebesar itu.
    """
    from fastapi.responses import JSONResponse
    return JSONResponse({"detail": "Angka terlalu besar."}, status_code=422)


def db():
    """
    Satu koneksi per permintaan, ditutup setelah selesai.

    SQLite tidak mengizinkan satu koneksi dipakai lintas thread, dan server
    menangani permintaan di beberapa thread. Membuka koneksi baru tiap
    permintaan jauh lebih aman daripada berbagi satu koneksi global.
    """
    conn = koneksi()
    try:
        yield conn
    finally:
        conn.close()


# --------------------------------------------------------------------- API


@aplikasi.get("/api/statistik", summary="Ringkasan isi database")
def api_statistik(conn: sqlite3.Connection = Depends(db)):
    s = repositori.statistik(conn)
    s["analisis"] = statistik_analisis(conn)
    return s


@aplikasi.get("/api/partai", summary="Daftar partai dengan pencarian & paginasi")
def api_daftar_partai(
    cari: str | None = Query(None, description="nama pemain, turnamen, atau pembukaan"),
    sumber: str | None = Query(None, description="lichess-user, chesscom, lichess-broadcast"),
    eco: str | None = Query(None, description="kode ECO, misal A22 atau hanya A"),
    hasil: str | None = Query(None, pattern=r"^(1-0|0-1|1/2-1/2|\*|menang-kalah)$",
                              description="satu hasil, atau 'menang-kalah' = 1-0 dan 0-1 tanpa remis"),
    gelar: str | None = Query(None, pattern="^(GM|IM|FM|CM|WGM|WIM|WFM|WCM|NM)$",
                              description="salah satu pemain bergelar ini"),
    dianalisis: bool | None = Query(None, description="true = hanya yang sudah dianalisis"),
    pemain_id: int | None = Query(None, description="partai yang dimainkan pemain ini"),
    warna: str | None = Query(None, pattern="^(putih|hitam)$",
                              description="warna pemain_id"),
    eco_dari: str | None = Query(None, pattern="^[A-Ea-e][0-9]{2}$"),
    eco_sampai: str | None = Query(None, pattern="^[A-Ea-e][0-9]{2}$"),
    fen: str | None = Query(None, description="hanya partai yang pernah mencapai posisi ini"),
    format: str | None = Query(None, pattern="^(bullet|blitz|rapid|klasik|tanpa)$",
                               description="turunan kontrol waktu"),
    urut: str = Query("terbaru", pattern="^(terbaru|terlama|terpanjang|elo)$"),
    halaman: int = Query(1, ge=1),
    per_halaman: int = Query(25, ge=1, le=100),
    conn: sqlite3.Connection = Depends(db),
):
    fen_kunci = None
    if fen:
        from .posisi import kunci_dari_fen_pengguna
        try:
            fen_kunci = kunci_dari_fen_pengguna(fen)
        except ValueError as e:
            raise HTTPException(status_code=422, detail=f"FEN tidak sah: {e}")
    return repositori.cari_partai(
        conn, cari=cari, sumber=sumber, eco=eco, hasil=hasil,
        gelar=gelar, dianalisis=dianalisis, pemain_id=pemain_id, warna=warna,
        eco_dari=eco_dari, eco_sampai=eco_sampai, fen_kunci=fen_kunci,
        format_=format,
        urut=urut, halaman=halaman, per_halaman=per_halaman,
    )


@aplikasi.get("/api/partai/{partai_id}", summary="Satu partai lengkap dengan PGN")
def api_detail_partai(partai_id: int, conn: sqlite3.Connection = Depends(db)):
    partai = repositori.ambil_partai(conn, partai_id)
    if partai is None:
        raise HTTPException(status_code=404, detail=f"Partai {partai_id} tidak ada")
    return partai


@aplikasi.get("/api/partai/{partai_id}/analisis",
              summary="Hasil analisis engine satu partai (hanya membaca)")
def api_analisis_partai(partai_id: int, conn: sqlite3.Connection = Depends(db)):
    """
    Hasil analisis PARTAI PENUH hanya dibuat oleh CLI analisis_partai.py
    (terlalu lama untuk sebuah request). Kalau belum ada, jawabannya 404
    beserta perintah CLI-nya. Evaluasi satu posisi secara langsung ada di
    /api/evaluasi (ARSITEKTUR.md, Phase 5).
    """
    if repositori.ambil_partai(conn, partai_id) is None:
        raise HTTPException(status_code=404, detail=f"Partai {partai_id} tidak ada")
    hasil = ambil_analisis(conn, partai_id)
    if hasil is None:
        raise HTTPException(
            status_code=404,
            detail={
                "pesan": "Partai ini belum dianalisis.",
                "perintah": f"python analisis_partai.py --id {partai_id}",
            },
        )
    return hasil


@aplikasi.get("/api/ringkasan", summary="Ringkasan lintas partai satu/beberapa pemain")
def api_ringkasan(
    pemain_id: list[int] = Query(..., description="boleh diulang untuk beberapa akun"),
    format: str | None = Query(None, pattern="^(bullet|blitz|rapid|klasik|tanpa)$"),
    conn: sqlite3.Connection = Depends(db),
):
    from .ringkasan import ringkasan_pemain
    if len(pemain_id) > 20:
        raise HTTPException(status_code=422, detail="Maksimal 20 pemain sekaligus")
    return ringkasan_pemain(conn, pemain_id, format_=format)


@aplikasi.get("/api/pemain/{pemain_id}", summary="Profil satu pemain: rating, W/D/L, sumber")
def api_profil_pemain(pemain_id: int, conn: sqlite3.Connection = Depends(db)):
    from .pemain_stat import profil_pemain
    p = profil_pemain(conn, pemain_id)
    if p is None:
        raise HTTPException(status_code=404, detail=f"Pemain {pemain_id} tidak ada")
    return p


@aplikasi.get("/api/pemain/{pemain_id}/pembukaan", summary="Pembukaan yang dipakai pemain")
def api_pembukaan_pemain(pemain_id: int, minimal: int = Query(1, ge=1),
                         conn: sqlite3.Connection = Depends(db)):
    from .pemain_stat import pembukaan_pemain
    # Pemeriksaan "pemainnya ada" memakai repositori, bukan SQL di berkas ini:
    # app/main.py sengaja bebas SQL (CLAUDE.md bagian 4, dijaga tests/test_arsitektur.py).
    if not repositori.pemain_ada(conn, pemain_id):
        raise HTTPException(status_code=404, detail=f"Pemain {pemain_id} tidak ada")
    return pembukaan_pemain(conn, pemain_id, minimal=minimal)


@aplikasi.get("/api/posisi", summary="Penjelajah pembukaan: langkah dari sebuah posisi")
def api_posisi(
    fen: str = Query(..., description="FEN posisi"),
    sumber: str = Query("gm", pattern="^(gm|semua|twic|lichess-broadcast|pgnmentor|lichess-user|chesscom)$"),
    min_elo: int | None = Query(None, ge=1000, le=3000),
    pemain_id: int | None = Query(None, description="hanya partai pemain ini"),
    warna: str | None = Query(None, pattern="^(putih|hitam)$",
                              description="warna pemain_id; tanpa ini = kedua warna"),
    conn: sqlite3.Connection = Depends(db),
):
    from .posisi import cari_langkah, kunci_dari_fen_pengguna
    try:
        kunci = kunci_dari_fen_pengguna(fen)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=f"FEN tidak sah: {e}")
    return cari_langkah(conn, kunci, sumber=sumber, min_elo=min_elo,
                        pemain_id=pemain_id, warna=warna)


@aplikasi.get("/api/evaluasi", summary="Evaluasi posisi langsung oleh Stockfish (papan analisis)")
def api_evaluasi(
    fen: str = Query(..., description="FEN posisi yang dianalisis"),
    multipv: int = Query(3, ge=1, le=5, description="jumlah garis terbaik"),
    depth: int | None = Query(None, ge=1, le=30, description="batas atas kedalaman"),
    waktu: float = Query(1.5, ge=0.1, le=10.0, description="batas waktu detik"),
):
    """
    Satu proses Stockfish profil HEMAT hidup di server, dinyalakan saat
    permintaan pertama, dimatikan setelah 5 menit menganggur. Kalau sedang
    dipakai → 409 (tidak antre). Tidak menulis database.
    """
    from .engine_server import EngineSibuk, EngineTidakAda, server_engine
    try:
        return server_engine.evaluasi(fen, multipv=multipv, depth=depth, waktu=waktu)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=f"FEN tidak sah: {e}")
    except EngineSibuk as e:
        raise HTTPException(status_code=409, detail=str(e))
    except EngineTidakAda as e:
        raise HTTPException(status_code=503, detail=str(e))


# ------------------------------------------------ posisi tersimpan (Phase 6)

from pydantic import BaseModel, Field  # noqa: E402


class PosisiBaru(BaseModel):
    fen: str = Field(..., min_length=10, max_length=120)
    # Batas sama dengan posisi_tersimpan.PANJANG_*_MAKS. Dulu tanpa batas:
    # judul 5 MB diterima lalu dipotong diam-diam (audit 28 Sep 2026).
    judul: str = Field("", max_length=200)
    catatan: str = Field("", max_length=2000)
    asal: str = Field("", max_length=40)
    partai_id: int | None = None


@aplikasi.get("/api/posisi-tersimpan", summary="Daftar posisi tersimpan, terbaru dulu")
def api_daftar_posisi_tersimpan(conn: sqlite3.Connection = Depends(db)):
    from .posisi_tersimpan import daftar
    baris = daftar(conn)
    return {"posisi": baris, "total": len(baris)}


@aplikasi.post("/api/posisi-tersimpan", status_code=201, summary="Simpan satu posisi dari papan")
def api_simpan_posisi(badan: PosisiBaru, conn: sqlite3.Connection = Depends(db)):
    from .posisi_tersimpan import simpan
    try:
        return simpan(conn, badan.fen, badan.judul, badan.catatan, badan.asal, badan.partai_id)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=f"Tidak bisa disimpan: {e}")


@aplikasi.delete("/api/posisi-tersimpan/{id_}", status_code=204, summary="Hapus posisi tersimpan")
def api_hapus_posisi(id_: int, conn: sqlite3.Connection = Depends(db)):
    from .posisi_tersimpan import hapus
    if not hapus(conn, id_):
        raise HTTPException(status_code=404, detail=f"Posisi tersimpan {id_} tidak ada")
    from fastapi import Response
    return Response(status_code=204)


@aplikasi.get("/api/repertoire", summary="Rentang ECO repertoire untuk saringan cepat")
def api_repertoire():
    from .eco import REPERTOIRE
    return {"repertoire": [{"nama": n, "eco_dari": a, "eco_sampai": b} for n, a, b in REPERTOIRE]}


@aplikasi.get("/api/pemain", summary="Daftar pemain")
def api_pemain(
    cari: str | None = None,
    limit: int = Query(30, ge=1, le=200),
    pribadi: bool | None = Query(None, description="true = hanya akun pemilik aplikasi"),
    conn: sqlite3.Connection = Depends(db),
):
    return {"pemain": repositori.cari_pemain(conn, cari=cari, limit=limit, pribadi=pribadi)}


# Impor lewat web (unggah PGN / tautan broadcast Lichess) dan analisis batch
# lewat web ada di modulnya sendiri supaya berkas ini tetap ringkas.
from .impor_web import router as router_impor      # noqa: E402
from .analisa_web import router as router_analisa  # noqa: E402

aplikasi.include_router(router_impor)
aplikasi.include_router(router_analisa)

# Sebab kekalahan (habis waktu / blunder / dikalahkan) dipindah ke server
# supaya semua halaman memakai angka yang sama.
from .sebab_kalah import router as router_kekalahan   # noqa: E402

aplikasi.include_router(router_kekalahan)

# Soal latihan pembukaan: posisi repertoire tempat langkah sendiri jarang
# dimainkan GM. Dihitung di server karena butuh ribuan kueri posisi.
from .latihan_pembukaan import router as router_latihan_pembukaan   # noqa: E402

aplikasi.include_router(router_latihan_pembukaan)

# Catatan jawaban latihan + ringkasan perkembangan (A38).
from .latihan_catatan import router as router_latihan_catatan   # noqa: E402

aplikasi.include_router(router_latihan_catatan)

# Rencana latihan minggu ini (A40): membaca ringkasan + catatan latihan.
from .rencana import router as router_rencana   # noqa: E402

aplikasi.include_router(router_rencana)

# Unduh repertoire yang dimainkan sebagai berkas PGN (A41).
from .repertoire_pgn import router as router_repertoire_pgn   # noqa: E402

aplikasi.include_router(router_repertoire_pgn)

# Tanya database/aplikasi pakai bahasa Indonesia lewat Ollama (Phase 6).
from .tanya_web import router as router_tanya   # noqa: E402

aplikasi.include_router(router_tanya)

# Pembantu analisis: tablet/HP di WiFi rumah menghitung dengan Stockfish browser.
from .pembantu import HEADER_PROXY, boleh_dari_luar, router as router_pembantu   # noqa: E402

aplikasi.include_router(router_pembantu)


# ---------------------------------------------------------------- HALAMAN

# File statis (CSS, JS, library papan) disajikan dari /static.
if FOLDER_WEB.is_dir():
    aplikasi.mount("/static", StaticFiles(directory=FOLDER_WEB), name="static")


@aplikasi.middleware("http")
async def jangan_simpan_cache_statis(request, panggil_berikutnya):
    """
    Paksa browser memeriksa ulang berkas statis setiap kali.

    Aplikasi ini berubah berkali-kali dalam satu sesi, dan berkas JS/CSS lama
    yang tersimpan di cache membuat Ray melihat perilaku versi kemarin lalu
    melaporkannya sebagai bug — sudah terjadi 12-13 Sep 2026 (halaman Latihan
    dianggap belum punya engine padahal sudah). ETag tetap dikirim, jadi kalau
    isinya memang belum berubah jawabannya 304, bukan kiriman ulang.
    """
    tanggapan = await panggil_berikutnya(request)
    if request.url.path.startswith("/static/"):
        tanggapan.headers["Cache-Control"] = "no-cache, must-revalidate"
    return tanggapan


@aplikasi.middleware("http")
async def hanya_dari_halaman_sendiri(request, panggil_berikutnya):
    """
    Tolak permintaan yang Host-nya bukan laptop ini, atau yang mengubah
    sesuatu atas suruhan situs lain (audit 27 Sep 2026). Alasannya dan
    batasnya di app/penjaga_asal.py.
    """
    from .penjaga_asal import asal_diizinkan, nama_laptop_terkini
    from .pembantu import _jalur_pembantu
    alasan = asal_diizinkan(request.method, request.headers.get("host"),
                            request.headers.get("origin"),
                            _jalur_pembantu(request.url.path), nama_laptop_terkini())
    if alasan:
        from fastapi.responses import JSONResponse
        return JSONResponse({"detail": f"Ditolak: {alasan}."}, status_code=403)
    return await panggil_berikutnya(request)


@aplikasi.middleware("http")
async def hanya_pembantu_dari_luar(request, panggil_berikutnya):
    """
    Perangkat lain di WiFi hanya boleh membuka halaman pembantu dan API-nya.

    Server dijalankan dengan --host 0.0.0.0 supaya tablet/HP bisa ikut
    menganalisis (app/pembantu.py). Tanpa penjaga ini, siapa pun di WiFi yang
    sama bisa mengimpor, menghentikan batch, atau menghapus data. Dipasang
    paling akhir supaya menjadi lapisan terluar.
    """
    host = request.client.host if request.client else None
    # Lewat tunnel/proxy, permintaan internet tampak datang dari 127.0.0.1;
    # header proxy-nya yang menandai bahwa ini bukan laptop sendiri.
    lewat_proxy = any(h in request.headers for h in HEADER_PROXY)
    if not boleh_dari_luar(host, request.url.path, lewat_proxy):
        from fastapi.responses import JSONResponse
        return JSONResponse({"detail": "Dari perangkat lain hanya halaman pembantu yang bisa dibuka."},
                            status_code=403)
    return await panggil_berikutnya(request)


def halaman(nama: str) -> FileResponse:
    """
    Sajikan satu berkas HTML dari web/, atau 404 yang menyebut berkasnya.

    Keenam rute halaman di bawah dulunya enam fungsi yang isinya sama persis
    kecuali nama berkas. Sekarang satu helper, jadi menambah halaman baru
    cukup satu baris dan pesan 404-nya tidak pernah berbeda-beda.
    """
    berkas = FOLDER_WEB / nama
    if not berkas.is_file():
        raise HTTPException(status_code=404, detail=f"web/{nama} belum dibuat")
    return FileResponse(berkas)


# Alamat rapi -> berkas di web/. Bagian {..} pada alamat dibaca JavaScript
# halamannya sendiri, jadi berkas yang disajikan sama untuk semua nomor.
@aplikasi.get("/", include_in_schema=False)
def halaman_daftar():
    return halaman("index.html")


@aplikasi.get("/analisis", include_in_schema=False)
def halaman_analisis():
    return halaman("analisis.html")


@aplikasi.get("/jelajah", include_in_schema=False)
def halaman_jelajah():
    return halaman("jelajah.html")


@aplikasi.get("/pemain/{pemain_id}", include_in_schema=False)
def halaman_pemain(pemain_id: int):
    return halaman("pemain.html")


@aplikasi.get("/ringkasan", include_in_schema=False)
def halaman_ringkasan():
    return halaman("ringkasan.html")

@aplikasi.get("/tanya", include_in_schema=False)
def halaman_tanya():
    return halaman("tanya.html")



@aplikasi.get("/partai/{partai_id}", include_in_schema=False)
def halaman_partai(partai_id: int):
    return halaman("partai.html")
