"""
Tanya database dan aplikasi lewat halaman web (Phase 6).

`app/tanya_db.py` dan `app/peta_aplikasi.py` sudah selesai 12 Sep 2026 tapi
tidak pernah tersambung: waktu itu tiga sesi lain sedang menulis `app/main.py`
dan nav `web/`, jadi penyambungannya ditunda — dan tertunda 13 hari. Akibatnya
fitur yang utuh, berikut 48 tesnya, tidak bisa dipakai Ray sama sekali.

    GET  /api/tanya/status   Ollama hidup? model apa yang ada?
    POST /api/tanya          { pertanyaan, mode, model? }

Dua mode, karena pertanyaannya dua jenis yang berbeda sifatnya:

  * `database` — LLM hanya mengarang SQL, **database yang menjawab**. Jawaban
    selalu memuat SQL-nya supaya tiap angka bisa ditelusuri.
  * `aplikasi` — pertanyaan tentang halaman/endpoint aplikasi ini sendiri.
    Jawabannya tidak bisa diverifikasi otomatis, jadi peta yang dipakai ikut
    dikembalikan; itu satu-satunya pengaman yang jujur di sini.

Router terpisah supaya `app/main.py` tetap bebas SQL (CLAUDE.md bagian 4,
dijaga `tests/test_arsitektur.py`); main.py hanya menyertakannya satu baris.

Kenapa `/status` ada terpisah: satu pertanyaan bisa memakan 20–60 detik di
mesin ini. Tanpa `/status`, halaman yang Ollama-nya mati baru tahu setelah
menunggu semenit. Dengan ini, keadaannya terbaca sebelum Ray mengetik.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from . import llm, peta_aplikasi, tanya_db

router = APIRouter(prefix="/api/tanya", tags=["tanya"])


class Pertanyaan(BaseModel):
    pertanyaan: str = Field(min_length=1, max_length=500)
    mode: str = Field(default="database", pattern="^(database|aplikasi)$")
    # Model bisa diturunkan ke 3b saat RAM sempit (batch engine berjalan),
    # tanpa mengubah bawaan di app/llm.py. Lihat catatan di berkas itu.
    model: str | None = Field(default=None, max_length=80)


@router.get("/status")
def status() -> dict:
    """Keadaan Ollama, tanpa memanggil model (jadi selalu cepat)."""
    try:
        model = llm.model_tersedia()
    except llm.LlmTidakAda as e:
        return {"hidup": False, "alasan": str(e), "model_bawaan": llm.MODEL,
                "model": [], "siap": False}
    return {
        "hidup": True,
        "alasan": None,
        "model_bawaan": llm.MODEL,
        "model": model,
        # Ollama hidup tapi tanpa model adalah keadaan yang berbeda dari mati,
        # dan halaman perlu bisa membedakannya untuk memberi saran yang benar.
        "siap": llm.MODEL in model,
    }


@router.post("")
def bertanya(minta: Pertanyaan) -> dict:
    """Jawab satu pertanyaan; 503 kalau Ollama mati, 422 kalau SQL-nya ditolak."""
    tambahan = {"model": minta.model} if minta.model else {}
    try:
        if minta.mode == "aplikasi":
            hasil = peta_aplikasi.tanya_aplikasi(minta.pertanyaan, **tambahan)
        else:
            hasil = tanya_db.tanya(minta.pertanyaan, **tambahan)
    except tanya_db.SqlDitolak as e:
        raise HTTPException(422, str(e)) from e
    except tanya_db.DatabaseSibuk as e:
        raise HTTPException(503, str(e)) from e
    except llm.LlmTidakAda as e:
        raise HTTPException(503, str(e)) from e
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    hasil["mode"] = minta.mode
    return hasil
