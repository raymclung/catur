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

from datetime import datetime
from pathlib import Path

from . import berkas_json
from .db import FOLDER_DATA

AKAR = Path(__file__).resolve().parents[1]
PATH_ANTREAN = FOLDER_DATA / "antrean-analisa.json"

# Status satu pekerjaan:
#   menunggu  belum dijalankan
#   jalan     prosesnya sedang berjalan (pid tercatat)
#   selesai   batch mencapai baris penutup
#   gagal     prosesnya hilang sebelum selesai
#   batal     dibuang Ray sebelum sempat jalan
STATUS = ("menunggu", "jalan", "selesai", "gagal", "batal")


def baca(path: Path | None = None) -> list[dict]:
    return berkas_json.baca(path or PATH_ANTREAN, [])


def tulis(daftar: list[dict], path: Path | None = None) -> None:
    """Tulis atomik. Untuk baca-ubah-tulis pakai `_ubah()`, bukan baca()+tulis()."""
    berkas_json.tulis(path or PATH_ANTREAN, daftar)


def _ubah(path: Path | None):
    # Semua perubahan lewat sini: server dan jaga_antrean.py menulis berkas
    # yang sama, dan baca-ubah-tulis tanpa kunci kehilangan pekerjaan
    # (audit 28 Sep 2026, lihat app/berkas_json.py).
    return berkas_json.ubah(path or PATH_ANTREAN, [])


def _nomor_baru(daftar: list[dict]) -> int:
    return max((d.get("id", 0) for d in daftar), default=0) + 1


def tambah(saringan: dict, keterangan: str = "", jumlah: int | None = None,
           perkiraan_menit: int | None = None, path: Path | None = None) -> dict:
    """Taruh satu pekerjaan di belakang antrean."""
    with _ubah(path) as daftar:
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
    return kerja


def hapus(id_: int, path: Path | None = None) -> bool:
    """
    Buang satu pekerjaan dari antrean.

    Yang sedang JALAN tidak dibuang di sini — menghentikan prosesnya urusan
    /api/analisa/hentikan, supaya tidak ada baris yang hilang sementara
    prosesnya masih menulis ke database.
    """
    with _ubah(path) as daftar:
        n = len(daftar)
        daftar[:] = [d for d in daftar if not (d.get("id") == id_ and d.get("status") != "jalan")]
    return len(daftar) != n


# Status yang sudah tidak akan berjalan lagi: isinya riwayat, bukan antrean.
SELESAI = ("selesai", "gagal", "batal")


def bersihkan_riwayat(path: Path | None = None) -> int:
    """
    Buang semua baris riwayat sekaligus, sisakan yang menunggu dan jalan.

    Hanya menyentuh berkas antrean. Hasil analisisnya ada di tabel `analisis`
    dan tidak ikut terhapus — Ray sempat mengira sebaliknya waktu melihat
    tombol "Buang" di baris yang sudah selesai (25 Sep 2026).
    """
    with _ubah(path) as daftar:
        n = len(daftar)
        daftar[:] = [d for d in daftar if d.get("status") not in SELESAI]
    return n - len(daftar)


def berikutnya(path: Path | None = None) -> dict | None:
    for d in baca(path):
        if d.get("status") == "menunggu":
            return d
    return None


def perbarui(id_: int, path: Path | None = None, **ubah) -> dict | None:
    hasil = None
    with _ubah(path) as daftar:
        for d in daftar:
            if d.get("id") == id_:
                d.update(ubah)
                hasil = d
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
