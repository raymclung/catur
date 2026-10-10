"""
Jejak tindakan yang mengendalikan mesin analisis: hentikan batch, jeda,
matikan penjaga.

Kenapa ada: 10 Okt 2026 15:54 dua batch analisis teliti (PID 9516 dan 14208)
dihentikan lewat `/api/analisa/hentikan` di tengah jalan, dan tidak ada yang
bisa menjawab siapa atau kenapa. Statusnya cuma "dihentikan", dan log server
yang mungkin memuat permintaannya sudah ditimpa saat server di-restart 15:57.

Berkasnya HANYA ditambah, tidak pernah ditulis ulang, dan terpisah dari log
server supaya restart tidak menghapusnya. Yang dicatat hanya yang bisa
diketahui dengan jujur dari permintaannya: alamat, halaman asal (Referer),
User-Agent, dan alasan kalau pengirimnya menyebutkan. Server lokal tidak bisa
tahu SESI mana yang mengirim; User-Agent setidaknya membedakan halaman Mesin
di browser dari curl/Python/PowerShell milik sesi Claude.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

from .db import FOLDER_DATA

PATH_JEJAK = FOLDER_DATA / "laporan" / "log-kendali-mesin.txt"


def asal_permintaan(request, alasan: str = "") -> dict:
    """
    Ringkas asal satu permintaan HTTP. `request` boleh None (pemanggilan
    langsung dari kode atau tes) — hasilnya tetap dicatat sebagai "tanpa HTTP".
    """
    alasan = " ".join((alasan or "").split())[:200]
    if request is None:
        return {"jenis": "tanpa HTTP", "alamat": None, "halaman": None,
                "agen": None, "alasan": alasan or None}
    h = request.headers
    agen = (h.get("user-agent") or "").strip()
    rujukan = h.get("referer") or ""
    halaman = (urlsplit(rujukan).path or None) if rujukan else None
    # PowerShell Invoke-RestMethod juga mengaku "Mozilla/5.0", jadi diperiksa dulu.
    if "PowerShell" in agen:
        jenis = "PowerShell"
    elif agen.startswith("Mozilla/"):
        jenis = "browser"
    elif agen:
        jenis = agen.split("/")[0][:40]
    else:
        jenis = "tanpa User-Agent"
    klien = getattr(request, "client", None)
    return {"jenis": jenis, "alamat": getattr(klien, "host", None),
            "halaman": halaman, "agen": agen[:160] or None, "alasan": alasan or None}


def teks_asal(asal: dict) -> str:
    """Satu frasa untuk dibaca orang, mis. 'browser /static/mesin.html'."""
    bagian = [asal.get("jenis") or "?"]
    if asal.get("halaman"):
        bagian.append(asal["halaman"])
    if asal.get("alamat") and asal["alamat"] not in ("127.0.0.1", "::1"):
        bagian.append(f"dari {asal['alamat']}")
    if asal.get("alasan"):
        bagian.append(f"alasan: {asal['alasan']}")
    return " ".join(bagian)


def catat(aksi: str, asal: dict, rincian: str = "", path: Path | None = None) -> None:
    """
    Tambahkan satu baris ke jejak. Gagal menulis TIDAK menggagalkan tindakannya:
    batch yang diminta berhenti tetap harus berhenti walau disk penuh.
    """
    p = path or PATH_JEJAK
    baris = (f"{datetime.now():%Y-%m-%d %H:%M:%S}  {aksi:<16} {rincian}  |  "
             f"{teks_asal(asal)}  |  agen: {asal.get('agen') or '-'}\n")
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "a", encoding="utf-8", newline="\n") as f:
            f.write(baris)
    except OSError:
        pass
