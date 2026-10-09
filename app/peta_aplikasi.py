"""
Tanya LLM tentang aplikasi ini sendiri: halaman apa saja, endpoint apa saja.

Permintaan Ray: *"tolong latih ollamanya supaya mahir tentang semua web ini"*.
Melatih (mengubah bobot model) tidak mungkin di mesin ini — alasannya terukur
di `ARSITEKTUR.md` bagian Phase 6. Yang dikerjakan sebagai gantinya: peta
aplikasi dibangun **saat dijalankan** dari kodenya sendiri, lalu dikirim ke
LLM sebagai konteks.

Kenapa dibangun saat dijalankan, bukan ditulis sebagai teks tetap: aplikasi
ini dikerjakan beberapa sesi paralel yang terus menambah halaman dan endpoint
(pada 12 Sep 2026 saja bertambah halaman Impor dan Mesin dalam satu hari).
Peta yang ditulis tangan akan usang dalam hitungan jam, dan LLM yang memakai
peta usang akan menjawab dengan yakin tentang halaman yang sudah berubah —
persis jenis karangan yang dilarang CLAUDE.md. Peta yang dibaca dari
`aplikasi.openapi()` dan dari berkas `web/*.html` tidak bisa usang.

Batas yang diterima dan disebut terus-terang: berbeda dari `app/tanya_db.py`
yang jawabannya berupa baris database, di sini **LLM yang menyusun kalimat**,
jadi karangan tetap mungkin. Yang bisa dijamin hanya bahwa fakta yang
*tersedia* untuknya benar dan mutakhir. Karena itu peta yang dipakai ikut
dikembalikan, supaya jawaban bisa diperiksa terhadap sumbernya.
"""

from __future__ import annotations

import re
from pathlib import Path

from . import llm

AKAR = Path(__file__).resolve().parent.parent
FOLDER_WEB = AKAR / "web"

# Halaman bantuan FastAPI, bukan bagian aplikasi yang Ray pakai.
_RUTE_DILEWATI = ("/openapi.json", "/docs", "/docs/oauth2-redirect", "/redoc")

# Maksimal bagian per halaman yang dimasukkan ke peta. Peta yang panjang
# memperlambat prefill: pada model 3B, tiap ~50 token prompt menambah 1 detik.
_MAKS_BAGIAN = 4


def daftar_endpoint() -> list[tuple[str, str, str]]:
    """(metode, path, ringkasan) dari spesifikasi OpenAPI aplikasi.

    Diambil lewat `aplikasi.openapi()`, bukan dengan menelusuri
    `aplikasi.routes`: router yang disertakan lewat `include_router()` muncul
    di daftar rute sebagai pembungkus tanpa path, jadi endpoint dari
    `app/impor_web.py` dan `app/analisa_web.py` akan terlewat. Diperiksa
    12 Sep 2026: cara rute menemukan 14 endpoint, `openapi()` menemukan 23.
    """
    from .main import aplikasi  # lazy: main.py boleh memakai modul ini

    hasil: list[tuple[str, str, str]] = []
    for path, per_metode in (aplikasi.openapi().get("paths") or {}).items():
        if path in _RUTE_DILEWATI:
            continue
        for metode, info in per_metode.items():
            if metode.upper() == "HEAD":
                continue
            ringkas = (info.get("summary") or info.get("description") or "").strip()
            hasil.append((metode.upper(), path, ringkas.split("\n")[0]))
    return sorted(hasil, key=lambda x: (x[1], x[0]))


def _teks_tag(html: str, tag: str, batas: int) -> list[str]:
    pola = re.compile(rf"<{tag}\b[^>]*>(.*?)</{tag}>", re.I | re.S)
    keluar = []
    for m in pola.findall(html)[:batas]:
        bersih = re.sub(r"<[^>]+>", " ", m)
        bersih = re.sub(r"\s+", " ", bersih).strip()
        if bersih:
            keluar.append(bersih)
    return keluar


def daftar_halaman(folder: Path = FOLDER_WEB) -> list[tuple[str, str, list[str]]]:
    """(berkas, judul, bagian) untuk tiap halaman di `web/`.

    Judul dan heading dipakai, bukan isi halaman: yang perlu diketahui LLM
    adalah halaman mana yang menjawab kebutuhan apa, bukan tata letaknya.
    """
    hasil = []
    for berkas in sorted(folder.glob("*.html")):
        html = berkas.read_text(encoding="utf-8", errors="replace")
        judul = (_teks_tag(html, "title", 1) or [berkas.stem])[0]
        # "Kelemahan — McLung ChessBase" -> "Kelemahan"
        judul = re.split(r"\s+[—–-]\s+", judul)[0].strip()
        bagian = _teks_tag(html, "h1", 1) + _teks_tag(html, "h2", _MAKS_BAGIAN)
        # buang yang mengulang judul
        bagian = [b for b in dict.fromkeys(bagian) if b.lower() != judul.lower()]
        hasil.append((berkas.name, judul, bagian[:_MAKS_BAGIAN]))
    return hasil


def peta(folder: Path = FOLDER_WEB) -> str:
    """Peta aplikasi sebagai teks, siap ditempel ke prompt."""
    baris = ["HALAMAN WEB (dilayani di /static/<berkas>):"]
    for berkas, judul, bagian in daftar_halaman(folder):
        isi = f" — bagian: {', '.join(bagian)}" if bagian else ""
        baris.append(f"- {judul} ({berkas}){isi}")
    baris.append("")
    baris.append("ENDPOINT API:")
    for metode, path, ringkas in daftar_endpoint():
        baris.append(f"- {metode} {path}" + (f" — {ringkas}" if ringkas else ""))
    return "\n".join(baris)


def bangun_prompt(pertanyaan: str, peta_teks: str) -> str:
    """Prompt yang mengurung jawaban pada isi peta.

    Instruksi "kalau tidak ada, katakan tidak ada" tidak menghilangkan
    karangan, tapi menurunkannya jauh — dan membuat jawaban yang salah lebih
    mudah dikenali Ray karena bentuknya jadi menyimpang dari daftar.
    """
    return f"""Kamu pemandu untuk aplikasi catur pribadi bernama McLung ChessBase.
Jawab pertanyaan tentang aplikasi ini HANYA berdasarkan daftar di bawah.

{peta_teks}

Aturan jawaban:
- Jawab dalam bahasa Indonesia, singkat, langsung ke inti.
- Sebut nama halaman atau path endpoint yang relevan, apa adanya.
- Kalau yang ditanyakan TIDAK ada di daftar, katakan terus-terang bahwa
  fitur itu belum ada. Jangan mengarang halaman atau endpoint.
- Jangan menebak isi database atau angka apa pun; daftar ini hanya memuat
  struktur aplikasi, bukan datanya.

Pertanyaan: {pertanyaan}

Jawaban:"""


def tanya_aplikasi(pertanyaan: str, *, model: str = llm.MODEL,
                   host: str = llm.HOST, folder: Path = FOLDER_WEB,
                   timeout: int = llm.TIMEOUT) -> dict:
    """Jawab pertanyaan tentang aplikasi, berdasarkan peta yang dibaca sekarang.

    `peta` ikut dikembalikan, bukan disembunyikan: jawaban LLM di sini tidak
    bisa diverifikasi otomatis, jadi satu-satunya pengaman yang jujur adalah
    membuat sumbernya bisa dilihat.
    """
    if not (pertanyaan or "").strip():
        raise ValueError("Pertanyaan kosong.")
    peta_teks = peta(folder)
    jawaban = llm.panggil(
        bangun_prompt(pertanyaan, peta_teks),
        model=model, host=host, timeout=timeout, num_predict=300,
    ).strip()
    return {
        "pertanyaan": pertanyaan,
        "jawaban": jawaban,
        "peta": peta_teks,
        "model": model,
    }
