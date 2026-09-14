"""
Format partai (bullet / blitz / rapid / klasik / tanpa) dari tag TimeControl.

Bentuk TimeControl yang benar-benar ada di database ini (diperiksa 11 Sep 2026):
  Lichess pengguna  "180+2", "0+1", "15+0"      detik dasar + inkremen (ultrabullet
                                                 memang 15 detik, jadi angka kecil
                                                 dari sumber daring TIDAK boleh
                                                 dibaca sebagai menit)
  Chess.com         "600", "10+0.1", "1/259200"  inkremen bisa pecahan; "1/N" = harian
  Broadcast Lichess "3+2", "3 min + 2 s", "2700+30"
                                                 turnamen OTB kadang menulis MENIT.
                                                 Untuk sumber OTB (TWIC/broadcast)
                                                 angka dasar < 180 dibaca menit:
                                                 tidak ada partai OTB di bawah
                                                 3 menit, dan tidak ada yang di
                                                 atas 120 menit dasar.
  TWIC              tidak ada tag                -> 'tanpa' (hampir pasti klasik OTB,
                                                 tapi kita tidak mengarang)

Batasnya mengikuti konvensi Lichess: perkiraan durasi = dasar + 40 x inkremen;
< 3 menit bullet, < 8 menit blitz, < 25 menit rapid, selebihnya klasik.
Harian/korespondensi masuk 'klasik' karena tidak ada tekanan waktu per langkah.
"""

from __future__ import annotations

import re

from .posisi import SUMBER_GM

FORMAT_VALID = ("bullet", "blitz", "rapid", "klasik", "tanpa")

_ANGKA = r"(\d+(?:[.,]\d+)?)"
_POLA_DETIK = re.compile(rf"^\s*{_ANGKA}\s*(?:\+\s*{_ANGKA})?\s*$")
_POLA_MENIT = re.compile(rf"^\s*{_ANGKA}\s*min(?:ute)?s?\s*(?:\+\s*{_ANGKA}\s*s(?:ec)?(?:ond)?s?)?\s*$",
                         re.IGNORECASE)


def _angka(s: str | None) -> float:
    return float((s or "0").replace(",", "."))


def format_dari_kontrol_waktu(kontrol_waktu: str | None, sumber: str | None = None) -> str:
    if not kontrol_waktu:
        return "tanpa"
    tc = kontrol_waktu.strip()
    if tc in ("", "-", "?"):
        return "tanpa"
    if "/" in tc:                       # "1/259200": harian
        return "klasik"

    m = _POLA_MENIT.match(tc)
    if m:
        dasar, inkremen = 60 * _angka(m.group(1)), _angka(m.group(2))
    else:
        m = _POLA_DETIK.match(tc)
        if not m:
            return "tanpa"
        dasar, inkremen = _angka(m.group(1)), _angka(m.group(2))
        if sumber in SUMBER_GM and dasar < 180:
            dasar *= 60                 # "3+2" / "90+30" di broadcast OTB = menit

    perkiraan = dasar + 40 * inkremen
    if perkiraan < 180:
        return "bullet"
    if perkiraan < 480:
        return "blitz"
    if perkiraan < 1500:
        return "rapid"
    return "klasik"


def _rapi(x: float) -> str:
    """Angka tanpa nol yang tidak perlu: 3.0 -> "3", 1.5 -> "1.5"."""
    return f"{round(x, 2):g}"


def kontrol_normal(kontrol_waktu: str | None, sumber: str | None = None) -> str:
    """Kontrol waktu dalam bentuk yang biasa dibaca pemain: "180+0" -> "3+0".

    Dipisah dari format_dari_kontrol_waktu karena jawabannya beda jenis
    (tampilan, bukan penggolongan), tapi aturan menit/detiknya **sama** dan
    diambil dari tabel di docstring berkas ini. Dikirim ke frontend sebagai
    field `kontrol_normal` (app/repositori.py) supaya tiap halaman tidak
    menghitung sendiri dan menyimpang.

    Yang tidak diubah, supaya tidak ada angka yang dikarang:
      - harian "1/259200" dan bentuk yang tidak dikenal: apa adanya
      - dasar di bawah satu menit diberi satuan detik ("15+0" -> "15s+0"):
        ultrabullet tidak punya bentuk menit yang lazim, dan tanpa satuan
        "10+2" dari Chess.com terbaca 10 menit padahal 10 detik
    Inkremen selalu detik, di bentuk mana pun. "" kalau tidak ada kontrol waktu.
    """
    if not kontrol_waktu:
        return ""
    tc = kontrol_waktu.strip()
    if tc in ("", "-", "?"):
        return ""
    if "/" in tc:
        return tc

    m = _POLA_MENIT.match(tc)
    if m:                               # "3 min + 2 s" sudah menit
        return f"{_rapi(_angka(m.group(1)))}+{_rapi(_angka(m.group(2)))}"

    m = _POLA_DETIK.match(tc)
    if not m:
        return tc
    dasar, inkremen = _angka(m.group(1)), _angka(m.group(2))
    if sumber in SUMBER_GM and dasar < 180:
        return f"{_rapi(dasar)}+{_rapi(inkremen)}"      # "3+2" broadcast = menit
    if dasar < 60:
        return f"{_rapi(dasar)}s+{_rapi(inkremen)}"
    return f"{_rapi(dasar / 60)}+{_rapi(inkremen)}"
