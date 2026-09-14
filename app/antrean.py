"""
Antrean pekerjaan analisis: daftar batch yang menunggu giliran.

Kenapa ada: satu batch berhenti, slot engine menganggur sampai ada orang yang
menekan Mulai lagi. Ray, 13 Sep 2026: "bisa buatkan fitur untuk aktif terus?"
Antrean ini disimpan di berkas, jadi isinya selamat kalau server dimuat ulang
ATAU komputernya dimatikan — begitu `jaga_antrean.py` jalan lagi, sisa antrean
dilanjutkan.

Yang TIDAK bisa dilakukan, supaya jelas: tidak ada yang berjalan saat komputer
mati. Windows mematikan semua proses. Yang bisa dijamin hanyalah pekerjaannya
tidak hilang dan dilanjutkan otomatis setelah dinyalakan lagi.

Berkasnya sengaja JSON biasa, bukan tabel database: isinya niat kerja (bukan
data catur), sering ditulis-ulang, dan harus bisa dibaca/disunting tangan
kalau ada yang macet.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

AKAR = Path(__file__).resolve().parents[1]
PATH_ANTREAN = AKAR / "data" / "antrean-analisa.json"

# Status satu pekerjaan:
#   menunggu  belum dijalankan
#   jalan     prosesnya sedang berjalan (pid tercatat)
#   selesai   batch mencapai baris penutup
#   gagal     prosesnya hilang sebelum selesai
#   batal     dibuang Ray sebelum sempat jalan
STATUS = ("menunggu", "jalan", "selesai", "gagal", "batal")


def baca(path: Path | None = None) -> list[dict]:
    p = path or PATH_ANTREAN
    try:
        isi = json.loads(p.read_text(encoding="utf-8"))
        return isi if isinstance(isi, list) else []
    except (OSError, ValueError):
        return []


def tulis(daftar: list[dict], path: Path | None = None) -> None:
    p = path or PATH_ANTREAN
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(daftar, indent=1, ensure_ascii=False), encoding="utf-8",
                 newline="\n")


def _nomor_baru(daftar: list[dict]) -> int:
    return max((d.get("id", 0) for d in daftar), default=0) + 1


def tambah(saringan: dict, keterangan: str = "", jumlah: int | None = None,
           perkiraan_menit: int | None = None, path: Path | None = None) -> dict:
    """Taruh satu pekerjaan di belakang antrean."""
    daftar = baca(path)
    kerja = {
        "id": _nomor_baru(daftar),
        "saringan": saringan,
        "keterangan": keterangan,
        "jumlah": jumlah,
        "perkiraan_menit": perkiraan_menit,
        "status": "menunggu",
        "ditambah": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }
    daftar.append(kerja)
    tulis(daftar, path)
    return kerja


def hapus(id_: int, path: Path | None = None) -> bool:
    """
    Buang satu pekerjaan dari antrean.

    Yang sedang JALAN tidak dibuang di sini — menghentikan prosesnya urusan
    /api/analisa/hentikan, supaya tidak ada baris yang hilang sementara
    prosesnya masih menulis ke database.
    """
    daftar = baca(path)
    sisa = [d for d in daftar if not (d.get("id") == id_ and d.get("status") != "jalan")]
    if len(sisa) == len(daftar):
        return False
    tulis(sisa, path)
    return True


def berikutnya(path: Path | None = None) -> dict | None:
    for d in baca(path):
        if d.get("status") == "menunggu":
            return d
    return None


def perbarui(id_: int, path: Path | None = None, **ubah) -> dict | None:
    daftar = baca(path)
    hasil = None
    for d in daftar:
        if d.get("id") == id_:
            d.update(ubah)
            hasil = d
    if hasil is not None:
        tulis(daftar, path)
    return hasil


def ringkas(path: Path | None = None) -> dict:
    daftar = baca(path)
    hitung = {s: 0 for s in STATUS}
    menit = 0
    for d in daftar:
        hitung[d.get("status", "menunggu")] = hitung.get(d.get("status", "menunggu"), 0) + 1
        if d.get("status") == "menunggu":
            menit += d.get("perkiraan_menit") or 0
    return {"antrean": daftar, "hitung": hitung, "perkiraan_menit_sisa": menit}
