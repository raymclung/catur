"""
Jeda seluruh engine analisis di laptop ini, lalu lanjutkan dari posisi yang sama.

Ray, 13 Sep 2026 malam: "untuk di laptop ini pause dulu agar tidak memakan
banyak cpu, saya mau pakai untuk yang lain, buatkan fitur pause juga disini".

Caranya: yang dibekukan HANYA proses Stockfish (NtSuspendProcess). Proses
batch Python-nya sendiri dibiarkan: ia sedang menunggu jawaban engine, jadi
tidak memakai CPU dan tidak sedang memegang transaksi database. Kalau batch
Python ikut dibekukan, ia bisa beku tepat di tengah commit dan menahan kunci
SQLite — pembantu tablet dan impor ikut tertahan.

Selama dijeda, tanda `jeda` di data/pengaturan-mesin.json membuat penjaga
antrean dan tombol Mulai tidak menyalakan batch baru. Tandanya tetap tersimpan
setelah komputer dinyalakan ulang: jeda berarti jeda sampai Ray menekan
Lanjutkan.

Yang TIDAK dibebaskan: RAM. Stockfish yang beku tetap memegang memorinya
(±360 MB per batch). Kalau yang dibutuhkan RAM, hentikan batch-nya.
Pembantu tablet/HP/komputer lain tidak ikut dijeda — mereka tidak memakai CPU
laptop.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

from .batas_cpu import PATH_PENGATURAN, pid_stockfish

_HAK_JEDA = 0x0800        # PROCESS_SUSPEND_RESUME


def _baca(path: Path | None = None) -> dict:
    try:
        isi = json.loads((path or PATH_PENGATURAN).read_text(encoding="utf-8"))
        return isi if isinstance(isi, dict) else {}
    except (OSError, ValueError):
        return {}


def status(path: Path | None = None) -> dict:
    isi = _baca(path)
    return {"jeda": bool(isi.get("jeda")), "sejak": isi.get("jeda_sejak")}


def sedang_jeda(path: Path | None = None) -> bool:
    return status(path)["jeda"]


def _simpan(jeda: bool, path: Path | None = None) -> None:
    p = path or PATH_PENGATURAN
    isi = _baca(p)
    isi["jeda"] = jeda
    if jeda:
        isi["jeda_sejak"] = datetime.now().strftime("%Y-%m-%d %H:%M")
    else:
        isi.pop("jeda_sejak", None)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(isi, indent=1), encoding="utf-8", newline="\n")


def _ubah_proses(pid: int, bekukan: bool) -> bool:
    """Bekukan / cairkan satu proses. False kalau tidak bisa."""
    if os.name != "nt":
        import signal
        try:
            os.kill(pid, signal.SIGSTOP if bekukan else signal.SIGCONT)
            return True
        except OSError:
            return False
    import ctypes
    k32, ntdll = ctypes.windll.kernel32, ctypes.windll.ntdll
    h = k32.OpenProcess(_HAK_JEDA, False, pid)
    if not h:
        return False
    try:
        fungsi = ntdll.NtSuspendProcess if bekukan else ntdll.NtResumeProcess
        return fungsi(h) == 0
    finally:
        k32.CloseHandle(h)


def jeda(path: Path | None = None) -> dict:
    """
    Bekukan semua Stockfish. Kalau sudah dijeda, tidak dibekukan dua kali:
    Windows menghitung jumlah pembekuan, dan pembekuan ganda butuh dua kali
    pencairan.
    """
    if sedang_jeda(path):
        return dict(status(path), proses=0, berhasil=0, catatan="sudah dijeda")
    _simpan(True, path)
    sasaran = pid_stockfish()
    berhasil = sum(1 for pid in sasaran if _ubah_proses(pid, True))
    return dict(status(path), proses=len(sasaran), berhasil=berhasil)


def lanjutkan(path: Path | None = None) -> dict:
    """Cairkan semua Stockfish dan izinkan batch baru lagi."""
    sasaran = pid_stockfish()
    berhasil = sum(1 for pid in sasaran if _ubah_proses(pid, False))
    _simpan(False, path)
    return dict(status(path), proses=len(sasaran), berhasil=berhasil)


def bekukan_yang_lolos(path: Path | None = None) -> int:
    """
    Dipanggil penjaga tiap putaran selama dijeda: Stockfish yang menyala
    SESUDAH tombol Jeda ditekan (mis. batch dari terminal) ikut dibekukan.
    Proses yang sudah beku tidak dibekukan lagi.
    """
    if not sedang_jeda(path):
        return 0
    n = 0
    for pid in pid_stockfish():
        if not _beku(pid) and _ubah_proses(pid, True):
            n += 1
    return n


def _beku(pid: int) -> bool:
    """Semua thread proses ini sedang ditangguhkan? (hanya Windows)"""
    if os.name != "nt":
        return False
    import subprocess
    try:
        keluar = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             f"(Get-Process -Id {int(pid)}).Threads | ForEach-Object {{ $_.WaitReason }}"],
            capture_output=True, text=True, timeout=30, creationflags=0x08000000).stdout
    except Exception:            # noqa: BLE001 - tidak bisa memeriksa: anggap beku, jangan bekukan dua kali
        return True
    alasan = [b.strip() for b in keluar.splitlines() if b.strip()]
    return bool(alasan) and all(a == "Suspended" for a in alasan)
