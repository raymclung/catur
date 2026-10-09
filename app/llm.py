"""
Klien Ollama yang dipakai bersama oleh fitur-fitur LLM.

Dipisah dari `app/tanya_db.py` supaya pemanggilan HTTP tidak tersalin di tiap
modul yang butuh LLM (`tanya_db`, `peta_aplikasi`, dan apa pun nanti).

Tanpa dependency baru: Ollama punya REST API di localhost, jadi `urllib`
bawaan cukup — sesuai tabel stack tetap di CLAUDE.md.

Yang TIDAK dikerjakan di sini, dan sengaja: tidak ada usaha membuat LLM jadi
sumber kebenaran. Pemanggil bertanggung jawab memberi konteks nyata (skema
database, peta aplikasi) dan memverifikasi jawabannya. Alasannya terukur —
lihat `ARSITEKTUR.md` bagian Phase 6.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

HOST = "http://127.0.0.1:11434"

# Model khusus kode, dipilih dari banding terukur di mesin ini (12 Sep 2026):
#
#                        SQL (7 soal)        peta aplikasi (4 soal)   rata-rata
#   qwen2.5-coder:3b     5 jalan, 2 gagal    2 benar                   11 s
#   qwen2.5-coder:7b     7 jalan, 0 gagal    4 benar                   41 s
#   qwen2.5:14b          tidak dipakai: ~147 s/pertanyaan, timeout
#
# 7B dipilih walaupun 4x lebih lambat: 3B mengarang halaman yang tidak ada
# ("Pembukaan") dan menyamakan pemain.id dengan partai_id. Untuk alat belajar,
# jawaban salah lebih merugikan daripada menunggu 30 detik lebih lama.
#
# 14B ditolak bukan karena kurang pintar tapi karena 9 GB tidak muat di RAM
# bebas ~5 GB, sehingga tiap token dibaca dari SSD: 2,84 token/detik melawan
# 16,75 pada 3B.
#
# Kalau RAM sedang sempit (batch analisis engine berjalan), pindah ke
# "qwen2.5-coder:3b" lewat parameter `model=`; jangan ubah nilai ini.
MODEL = "qwen2.5-coder:7b"

TIMEOUT = 120


class LlmTidakAda(RuntimeError):
    """Ollama tidak menjawab di host yang diminta (jadi HTTP 503)."""


def panggil(prompt: str, *, model: str = MODEL, host: str = HOST,
            timeout: int = TIMEOUT, num_predict: int = 400,
            temperature: float = 0.0) -> str:
    """Kirim prompt ke Ollama, kembalikan teks jawabannya.

    `temperature=0` supaya pertanyaan yang sama memberi jawaban yang sama:
    "bukti selesai" di CLAUDE.md harus bisa diulang orang lain, dan itu
    mustahil kalau jawabannya berubah tiap dijalankan.
    """
    muatan = json.dumps({
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": temperature, "num_predict": num_predict},
    }).encode("utf-8")
    permintaan = urllib.request.Request(
        f"{host.rstrip('/')}/api/generate",
        data=muatan,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(permintaan, timeout=timeout) as jawab:
            isi = json.loads(jawab.read().decode("utf-8"))
    except urllib.error.URLError as e:
        raise LlmTidakAda(
            f"Ollama tidak menjawab di {host} ({e}). Jalankan `ollama serve`, "
            f"dan pastikan model `{model}` sudah diunduh (`ollama pull {model}`)."
        ) from e
    except TimeoutError as e:
        raise LlmTidakAda(
            f"Ollama tidak selesai dalam {timeout} detik dengan model `{model}`. "
            f"Model besar di mesin ini bisa jauh lebih lambat dari batas itu; "
            f"pakai model yang lebih kecil atau naikkan timeout."
        ) from e
    return isi.get("response", "")


def model_tersedia(*, host: str = HOST, timeout: int = 10) -> list[str]:
    """Nama model yang sudah diunduh, untuk pesan kesalahan yang menolong.

    Daftar kosong berarti Ollama menjawab tapi belum punya model apa pun —
    keadaan yang berbeda dari Ollama mati, dan pemanggil perlu bisa
    membedakannya.
    """
    try:
        with urllib.request.urlopen(f"{host.rstrip('/')}/api/tags",
                                    timeout=timeout) as jawab:
            isi = json.loads(jawab.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError) as e:
        raise LlmTidakAda(f"Ollama tidak menjawab di {host} ({e}).") from e
    return [m.get("name", "") for m in isi.get("models", [])]
