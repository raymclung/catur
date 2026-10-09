"""
Penjaga asal permintaan: hanya halaman aplikasi ini sendiri yang boleh
menyuruh server bekerja.

Audit 27 Sep 2026: server tidak memeriksa Host maupun Origin. Akibatnya situs
APA PUN yang dibuka Ray di browser laptop bisa diam-diam mengirim POST ke
http://127.0.0.1:8000 — memulai impor, menjeda mesin, menghapus posisi
tersimpan — karena permintaan itu memang datang dari 127.0.0.1, dan penjaga
lama (`hanya_pembantu_dari_luar`) hanya melihat alamat pengirim. Browser
tetap MENGIRIM permintaan lintas situs walau tidak mengizinkan situs itu
membaca jawabannya, jadi CORS saja tidak melindungi apa pun.

Dua lapis:

1. **Host** harus nama laptop ini sendiri. Menutup DNS rebinding: domain
   penyerang yang diarahkan ke 127.0.0.1 membawa Host domain itu, bukan
   "127.0.0.1:8000".
2. **Origin** untuk permintaan yang mengubah sesuatu (selain GET/HEAD/OPTIONS):
   kalau browser menyertakannya, harus sama dengan Host. Permintaan tanpa
   Origin (python, curl, skrip terminal Ray) tetap dilayani — penyerang lewat
   browser tidak bisa menghapus header itu.

Jalur pembantu (`JALUR_LUAR`) dikecualikan: tablet/HP membukanya lewat IP WiFi
atau nama tunnel yang tidak bisa diketahui di sini, dan jalur itu sudah dijaga
kode 6 angka di app/pembantu.py.

Fungsi di sini murni (tanpa FastAPI) supaya bisa diuji langsung.
"""

from __future__ import annotations

import socket
import time
from urllib.parse import urlsplit

METODE_AMAN = {"GET", "HEAD", "OPTIONS"}

NAMA_LOKAL = {"localhost", "127.0.0.1", "::1"}


def nama_host(nilai: str | None) -> str | None:
    """'127.0.0.1:8000' -> '127.0.0.1', '[::1]:8000' -> '::1', huruf kecil."""
    if not nilai:
        return None
    try:
        h = urlsplit("//" + nilai.strip()).hostname
    except ValueError:
        return None
    return h.lower() if h else None


def nama_laptop() -> set[str]:
    """Nama yang sah untuk laptop ini: lokal, nama komputer, dan IP WiFi-nya."""
    from .pembantu import alamat_wifi           # noqa: PLC0415 — hindari impor melingkar
    nama = set(NAMA_LOKAL)
    try:
        nama.add(socket.gethostname().lower())
    except OSError:
        pass
    nama.update(a.lower() for a in alamat_wifi())
    return nama


_simpanan = {"waktu": -1e9, "nama": set()}


def nama_laptop_terkini(detik: float = 60.0) -> set[str]:
    """nama_laptop(), dihitung ulang paling sering sekali per `detik` (IP WiFi
    bisa berganti saat laptop pindah jaringan, tapi tidak perlu dicek tiap
    permintaan)."""
    sekarang = time.monotonic()
    if sekarang - _simpanan["waktu"] > detik:
        _simpanan.update(waktu=sekarang, nama=nama_laptop())
    return _simpanan["nama"]


def asal_diizinkan(metode: str, host: str | None, origin: str | None,
                   jalur_pembantu: bool, nama_sah: set[str]) -> str | None:
    """
    None = boleh. Selain itu: alasan penolakan (untuk pesan 403).

    `host` dan `origin` adalah nilai header apa adanya.
    """
    if jalur_pembantu:
        return None
    h = nama_host(host)
    if h not in nama_sah:
        return f"Host {host!r} bukan laptop ini"
    if metode.upper() in METODE_AMAN or origin is None:
        return None
    # Origin "null" dikirim dari iframe sandbox/berkas lokal: tidak pernah sah.
    asal = urlsplit(origin.strip())
    if not asal.netloc or asal.netloc.lower() != host.strip().lower():
        return f"permintaan dari {origin!r}, bukan dari halaman aplikasi ini"
    return None
