"""
Catatan batch analisis: apa yang pernah dijalankan, dan sejauh mana majunya.

Dipisah dari `app/analisa_web.py` 22 Sep 2026 (audit: berkas itu terlalu
besar, CLAUDE.md bagian 6). Satu urusan saja: BERKAS — catatan tugas
(`data/tugas-analisa.json`) dan berkas log batch. Tidak tahu soal HTTP,
saringan, maupun database.

Dua hal yang dijaga di sini:

  * **Proses hilang bukan berarti pekerjaan beres.** `_status_akhir()` membaca
    ekor log dan hanya menyebut "selesai" kalau baris penutupnya ada; batch
    yang mati di tengah harus terlihat gagal, bukan sukses.
  * **Batch dari terminal ikut terlihat.** `analisis_partai.py` menulis catatan
    kecil bernama PID-nya, jadi kemajuan batch yang tidak diluncurkan halaman
    Mesin tetap bisa dibaca (Ray, 12 Sep: "saya tidak melihat persentasenya").

`analisa_web` mengimpor ulang nama-nama ini supaya pemanggil lama tetap jalan;
yang menambalnya di tes menambal modul INI (jalur berkasnya ada di sini).
"""

from __future__ import annotations

import json
import re
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from . import berkas_json
from .db import FOLDER_DATA
from .proses import _hidup

AKAR = Path(__file__).resolve().parents[1]
FOLDER_LOG = FOLDER_DATA / "laporan"

# Catatan batch yang pernah diluncurkan halaman Mesin.
PATH_TUGAS = FOLDER_DATA / "tugas-analisa.json"
MAKS_TUGAS = 20

POLA_MAJU = re.compile(r"\[(\d+)/(\d+)\]")
POLA_SISA = re.compile(r"sisa ~(\d+)m")
POLA_DIPILIH = re.compile(r"^(\d+) partai dipilih")


def _baca_tugas() -> list[dict]:
    # Entri tanpa dict (berkas disunting tangan) dibuang di sini supaya semua
    # pemanggil boleh memakai t.get(...) tanpa KeyError/AttributeError.
    return [t for t in berkas_json.baca(PATH_TUGAS, []) if isinstance(t, dict)]


def _tulis_tugas(daftar: list[dict]) -> None:
    berkas_json.tulis(PATH_TUGAS, daftar[-MAKS_TUGAS:])


@contextmanager
def ubah_tugas():
    """
    Baca-ubah-tulis catatan tugas di bawah kunci (server dan penjaga antrean
    sama-sama menulisnya; audit 28 Sep 2026, lihat app/berkas_json.py).
    """
    with berkas_json.ubah(PATH_TUGAS, []) as daftar:
        daftar[:] = [t for t in daftar if isinstance(t, dict)]
        yield daftar
        del daftar[:-MAKS_TUGAS]


def _status_akhir(path_log: Path) -> tuple[str, str]:
    """
    ("selesai" | "gagal", alasan) untuk batch yang prosesnya sudah tidak ada.

    Kenapa tidak cukup "prosesnya hilang berarti selesai": pada 12 Sep 2026 dua
    batch mati dengan `EngineTerminatedError ... exit code 3221225786`
    (0xC000013A = STATUS_CONTROL_C_EXIT — jendela konsolnya ditutup) tapi tetap
    dilabeli "selesai", padahal baru 2 dari 25 partai. Label yang bohong lebih
    berbahaya daripada tidak ada label: Ray mengira pekerjaannya beres.

    Penanda selesai yang benar = baris penutup `analisis_partai.py`:
    "selesai N, gagal M, ... menit  |  database: ...". Kalau baris itu tidak
    ada, batch berhenti sebelum waktunya.
    """
    try:
        baris = [b for b in path_log.read_text(encoding="utf-8", errors="replace").splitlines()
                 if b.strip()]
    except OSError:
        return "gagal", "berkas log tidak terbaca"
    if any(b.startswith("selesai ") and " menit" in b for b in baris[-6:]):
        return "selesai", ""
    for b in reversed(baris):
        # Baris galat Python terakhir paling menjelaskan sebabnya.
        if "Error" in b or "error" in b:
            return "gagal", b.strip()[:160]
    if any("Dihentikan" in b for b in baris[-4:]):
        return "dihentikan", "dihentikan di tengah jalan"
    return "gagal", "berhenti sebelum baris penutup muncul"


def _kemajuan(path_log: Path) -> dict:
    """Baca kemajuan dari ekor berkas log batch."""
    try:
        teks = path_log.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}
    baris = [b for b in teks.splitlines() if b.strip()]
    maju = {}
    for b in reversed(baris):
        m = POLA_MAJU.search(b)
        if m:
            maju["selesai"], maju["dari"] = int(m.group(1)), int(m.group(2))
            sisa = POLA_SISA.search(b)
            if sisa:
                maju["sisa_menit"] = int(sisa.group(1))
            break
    for b in baris:
        m = POLA_DIPILIH.match(b)
        if m:
            maju.setdefault("dari", int(m.group(1)))
            break
    maju["ekor"] = baris[-6:]
    return maju



def kemajuan_proses(pid: int) -> dict:
    """
    Kemajuan batch yang dijalankan dari TERMINAL.

    Halaman ini hanya tahu berkas log batch yang diluncurkannya sendiri. Batch
    dari terminal mengarahkan keluarannya ke mana saja, jadi `analisis_partai.py`
    menulis catatan kecil bernama PID-nya (lihat tulis_kemajuan di sana) dan di
    sini tinggal dibaca. Ray, 12 Sep: "saya tidak melihat persentasenya".
    """
    berkas = FOLDER_LOG / f".kemajuan-{pid}.json"
    try:
        d = json.loads(berkas.read_text(encoding="utf-8"))
    except Exception:
        return {}
    keluar = {k: d[k] for k in ("selesai", "dari", "sisa_menit", "mulai") if k in d}
    if keluar.get("dari"):
        keluar["persen"] = round(100 * keluar.get("selesai", 0) / keluar["dari"], 1)
    return keluar


def bersihkan_kemajuan_yatim(hidup: set[int]) -> None:
    """Buang catatan kemajuan milik proses yang sudah tidak ada."""
    folder = FOLDER_LOG
    try:
        berkas = list(folder.glob(".kemajuan-*.json"))
    except OSError:
        return
    for b in berkas:
        try:
            pid = int(b.stem.split("-")[-1])
        except ValueError:
            continue
        if pid not in hidup:
            try:
                b.unlink()
            except OSError:
                pass


def _rangkum_tugas() -> list[dict]:
    daftar = _baca_tugas()
    # Pindaian proses (lambat) di luar kunci; hanya perubahan status yang
    # ditulis, di bawah kunci, ke isi berkas yang TERBARU — bukan menimpa
    # dengan salinan yang dibaca sebelum pindaian.
    mati: dict[int, dict] = {}
    for t in daftar:
        hidup = bool(t.get("pid")) and _hidup(t["pid"], t.get("mulai"))
        log = Path(t["log"]) if t.get("log") else None
        if t.get("status") == "berjalan" and not hidup:
            # Prosesnya hilang BUKAN berarti pekerjaannya beres — lihat
            # _status_akhir(). Batch yang mati di tengah harus terlihat gagal.
            status, alasan = _status_akhir(log) if log else ("gagal", "log tidak tercatat")
            ubah = {"status": status, "sampai": datetime.now().strftime("%Y-%m-%d %H:%M")}
            if alasan:
                ubah["alasan"] = alasan
            t.update(ubah)
            mati[t.get("pid")] = ubah
        t["berjalan"] = hidup
        if log:
            t.update(_kemajuan(log))
    if mati:
        with ubah_tugas() as terbaru:
            for t in terbaru:
                if t.get("status") == "berjalan" and t.get("pid") in mati:
                    t.update(mati[t["pid"]])
    return list(reversed(daftar))
