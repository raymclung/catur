"""
Analisis tanpa henti untuk papan analisis: depth naik terus, garis dikirim
tiap kali satu depth selesai (Server-Sent Events).

Dulu dicatat "ditunda ke versi 2" (ARSITEKTUR.md Phase 5): papan hanya bisa
meminta evaluasi ~1,5 detik. Untuk posisi yang sulit itu terlalu dangkal, dan
menekan Analisa berulang kali tidak membuat engine berpikir lebih dalam.

    GET /api/evaluasi/aliran?fen=<FEN>&multipv=3&batas=120

Aturan engine yang TIDAK berubah, dan cara aliran ini menghormatinya:

  satu engine   memakai `server_engine` yang sama dengan /api/evaluasi,
                dengan kunci yang sama. Selama aliran berjalan, /api/evaluasi
                menjawab 409 — jujur, bukan antre. Tidak ada proses baru,
                jadi batas dua batch engine (CLAUDE.md bagian 7) tetap.
  ada ujungnya  berhenti pada yang lebih dulu: tab ditutup / tombol Henti
                (koneksi putus), depth 30, atau `batas` detik (bawaan 120,
                maksimal 600). "Tanpa henti" tanpa batas waktu berarti kunci
                engine bisa tertahan selamanya oleh tab yang terlupa.
  kunci aman    kalau koneksi putus di tengah satu langkah, pencarian
                dihentikan lewat `berhenti` (~0,1 detik, `stop()` python-chess)
                dan kunci baru dilepas SESUDAH engine benar-benar berhenti,
                bukan saat koneksi putus — kalau tidak, permintaan berikutnya
                memakai engine yang masih sibuk. Tanpa penghentian itu,
                menggeser bidak sesudah Henti menunggu sampai 20 detik.

Kenapa depth naik satu per satu, bukan satu `analysis()` tak terbatas:
tiap kiriman jadi hasil multipv yang LENGKAP pada satu depth (semua garis
sama dalamnya), memakai pembungkus `Stockfish.evaluasi()` yang sudah teruji.
Pencarian ulang dari depth sebelumnya murah karena tabel transposisi
(Hash) engine masih berisi hasil langkah sebelumnya.

Bentuk kiriman (`event: garis`, lalu satu `event: selesai`):
    data: <JSON sama persis dengan /api/evaluasi> + "detik" (sejak mulai)
    data: {"alasan": "depth-maks" | "batas-waktu" | "posisi-selesai"}
atau satu `event: galat` {"detail": ...} kalau engine direbut permintaan lain
di sela pemeriksaan rute dan awal aliran.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import AsyncIterator, Awaitable, Callable

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from . import engine_server

router = APIRouter(tags=["evaluasi"])

DEPTH_AWAL = 8
BATAS_BAWAAN = 120
BATAS_MAKS = 600
WAKTU_LANGKAH_MAKS = 20.0      # detik untuk satu depth; menahan kunci paling lama ini

# Satu utas cukup: hanya satu aliran yang bisa memegang engine.
_utas = ThreadPoolExecutor(max_workers=1, thread_name_prefix="aliran-engine")


def _kiriman(jenis: str, isi: dict) -> str:
    return f"event: {jenis}\ndata: {json.dumps(isi, ensure_ascii=False)}\n\n"


async def aliran_analisis(board, multipv: int, batas: float,
                          terputus: Callable[[], Awaitable[bool]],
                          mesin=None) -> AsyncIterator[str]:
    """Inti aliran: ambil engine, deepening, lepas engine.

    Kunci diambil DI SINI, bukan di rute: blok `finally` sebuah generator
    hanya berjalan kalau generatornya sudah mulai. Kalau kunci diambil di
    rute lalu koneksi putus sebelum generator sempat jalan, engine terkunci
    selamanya. Rute hanya memeriksa lebih dulu supaya bisa menjawab 409/503.

    Dipisah dari rute supaya bisa diuji dengan engine tiruan dan tanpa HTTP
    (httpx tidak terpasang, lihat tests/test_impor_web.py).
    """
    mesin = mesin or engine_server.server_engine
    loop = asyncio.get_running_loop()
    try:
        await loop.run_in_executor(_utas, mesin.pegang)
    except (engine_server.EngineSibuk, engine_server.EngineTidakAda) as e:
        # Kalah balapan dengan permintaan lain di sela pemeriksaan rute.
        yield _kiriman("galat", {"detail": str(e)})
        return
    langkah = None
    berhenti = threading.Event()
    mulai = time.monotonic()
    alasan = "depth-maks"
    terdalam = 0
    try:
        if board.is_game_over():
            hasil = mesin.analisis_dipegang(board, multipv=multipv, depth=1, waktu=0.1)
            yield _kiriman("garis", {**hasil, "detik": 0.0})
            alasan = "posisi-selesai"
        else:
            depth = DEPTH_AWAL
            while depth <= engine_server.DEPTH_MAKS:
                sisa = batas - (time.monotonic() - mulai)
                if sisa <= 0:
                    alasan = "batas-waktu"
                    break
                if await terputus():
                    return
                langkah = _utas.submit(mesin.analisis_dipegang, board, multipv,
                                       depth, min(sisa, WAKTU_LANGKAH_MAKS), berhenti)
                hasil = await asyncio.wrap_future(langkah)
                langkah = None
                dicapai = hasil.get("depth") or 0
                # Waktu habis di tengah langkah: Stockfish melaporkan depth
                # terakhir yang tuntas, yang bisa LEBIH DANGKAL dari kiriman
                # sebelumnya (terbukti dengan Stockfish sungguhan: 19 lalu
                # 18). Kiriman itu dibuang supaya papan tidak mundur.
                if dicapai >= terdalam:
                    terdalam = dicapai
                    yield _kiriman("garis", {**hasil,
                                             "detik": round(time.monotonic() - mulai, 1)})
                # Waktu langkah habis sebelum depth tercapai: depth berikutnya
                # tetap satu di atas yang BENAR-BENAR dicapai, bukan diminta.
                depth = max(depth, dicapai) + 1
        yield _kiriman("selesai", {"alasan": alasan})
    finally:
        if langkah is not None and not langkah.done():
            # Koneksi putus di tengah langkah: suruh engine berhenti (~0,1 s),
            # lalu lepas kunci SESUDAH ia benar-benar berhenti.
            berhenti.set()
            langkah.add_done_callback(lambda _f: mesin.lepas())
        else:
            mesin.lepas()


@router.get("/api/evaluasi/aliran",
            summary="Analisis tanpa henti: depth naik, garis dikirim tiap depth (SSE)")
async def api_evaluasi_aliran(
    request: Request,
    fen: str = Query(..., description="FEN posisi yang dianalisis"),
    multipv: int = Query(3, ge=1, le=engine_server.MULTIPV_MAKS, description="jumlah garis terbaik"),
    batas: float = Query(BATAS_BAWAAN, ge=1, le=BATAS_MAKS, description="berhenti sesudah sekian detik"),
):
    """422 FEN rusak, 409 engine dipakai, 503 engine tidak ada — sebelum aliran dimulai."""
    try:
        board = engine_server.papan_dari_fen(fen)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=f"FEN tidak sah: {e}")
    mesin = engine_server.server_engine
    if not mesin.path_engine.is_file():
        raise HTTPException(status_code=503, detail=f"engine tidak ditemukan di {mesin.path_engine}")
    if mesin.sedang_dipakai:
        raise HTTPException(status_code=409, detail="engine sedang dipakai permintaan lain")
    return StreamingResponse(
        aliran_analisis(board, multipv, batas, request.is_disconnected, mesin),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
