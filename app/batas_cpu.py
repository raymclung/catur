"""
Batas pemakaian CPU untuk engine analisis, sebagai persen dari seluruh inti.

Ray, 13 Sep 2026: "apakah bisa membatasi penggunaan cpu dari total? karena
tadi saya tidak bisa buka apa apa". Membatasi JUMLAH batch saja tidak cukup:
beban tiap batch berubah-ubah, dan batch dari terminal tidak ikut dihitung.

Caranya: engine dikurung di sebagian inti logis lewat CPU affinity. Inti
sisanya selalu kosong untuk aplikasi Ray, apa pun yang dilakukan engine.
Stockfish mewarisi affinity dari proses batch yang menjalankannya, jadi cukup
diterapkan ke batch begitu diluncurkan; penjaga antrean menerapkannya ulang
tiap putaran supaya batch dari terminal dan Stockfish yang sudah terlanjur
jalan ikut terkurung.

Inti yang dipakai engine diambil dari nomor TERATAS. Di i7-13620H milik Ray
(6 inti P = 12 utas, 4 inti E), inti E dinomori paling akhir, jadi engine
cenderung jatuh ke inti E dan inti P tetap untuk pekerjaan Ray.

Diukur 13 Sep 2026: masker 0xFF00 (8 dari 16 inti) diterapkan ke tiga
Stockfish yang sedang jalan dan terbaca kembali 0xFF00.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

AKAR = Path(__file__).resolve().parents[1]
PATH_PENGATURAN = AKAR / "data" / "pengaturan-mesin.json"

PERSEN_BAWAAN = 50
PERSEN_VALID = (25, 50, 75, 100)

# PROCESS_SET_INFORMATION | PROCESS_QUERY_LIMITED_INFORMATION
_HAK_UBAH = 0x0200 | 0x1000
_TANPA_JENDELA = 0x08000000 if os.name == "nt" else 0


def inti_total() -> int:
    return os.cpu_count() or 1


def baca_persen(path: Path | None = None) -> int:
    try:
        nilai = int(json.loads((path or PATH_PENGATURAN).read_text(encoding="utf-8"))
                    .get("batas_cpu_persen", PERSEN_BAWAAN))
    except (OSError, ValueError, TypeError, AttributeError):
        return PERSEN_BAWAAN
    return nilai if nilai in PERSEN_VALID else PERSEN_BAWAAN


def simpan_persen(persen: int, path: Path | None = None) -> int:
    if persen not in PERSEN_VALID:
        raise ValueError(f"batas CPU harus salah satu dari {PERSEN_VALID}")
    p = path or PATH_PENGATURAN
    p.parent.mkdir(parents=True, exist_ok=True)
    try:
        isi = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(isi, dict):
            isi = {}
    except (OSError, ValueError):
        isi = {}
    isi["batas_cpu_persen"] = persen
    p.write_text(json.dumps(isi, indent=1), encoding="utf-8", newline="\n")
    return persen


def jumlah_inti(persen: int, total: int | None = None) -> int:
    """Berapa inti logis boleh dipakai engine. Minimal 2 supaya tetap jalan."""
    total = total or inti_total()
    return max(min(2, total), min(total, round(total * persen / 100)))


def masker(persen: int, total: int | None = None) -> int:
    """Bitmask inti yang boleh dipakai engine: `n` inti dengan nomor teratas."""
    total = total or inti_total()
    n = jumlah_inti(persen, total)
    hasil = 0
    for i in range(total - n, total):
        hasil |= 1 << i
    return hasil


def terapkan(pid: int, nilai_masker: int) -> bool:
    """Kurung satu proses di inti tertentu. False kalau prosesnya tidak bisa diubah."""
    if os.name != "nt":
        try:
            os.sched_setaffinity(pid, {i for i in range(inti_total()) if nilai_masker >> i & 1})
            return True
        except (OSError, AttributeError):
            return False
    import ctypes
    k32 = ctypes.windll.kernel32
    h = k32.OpenProcess(_HAK_UBAH, False, pid)
    if not h:
        return False
    try:
        return bool(k32.SetProcessAffinityMask(h, ctypes.c_size_t(nilai_masker)))
    finally:
        k32.CloseHandle(h)


def pid_stockfish() -> list[int]:
    """PID semua Stockfish yang sedang jalan (termasuk yang dari terminal)."""
    try:
        if os.name == "nt":
            keluar = subprocess.run(
                ["tasklist", "/FI", "IMAGENAME eq stockfish.exe", "/FO", "CSV", "/NH"],
                capture_output=True, text=True, timeout=30, creationflags=_TANPA_JENDELA).stdout
            pid = []
            for baris in keluar.splitlines():
                bagian = [b.strip('"') for b in baris.split(",")]
                if len(bagian) > 1 and bagian[1].isdigit():
                    pid.append(int(bagian[1]))
            return pid
        keluar = subprocess.run(["pgrep", "-x", "stockfish"], capture_output=True,
                                text=True, timeout=30).stdout
        return [int(x) for x in keluar.split() if x.isdigit()]
    except Exception:           # noqa: BLE001 - tidak bisa memeriksa: jangan mengarang
        return []


def terapkan_semua(pid_batch: list[int], persen: int | None = None) -> dict:
    """Terapkan batas ke semua batch dan semua Stockfish yang sedang jalan."""
    persen = persen if persen is not None else baca_persen()
    m = masker(persen)
    sasaran = list(dict.fromkeys(list(pid_batch) + pid_stockfish()))
    berhasil = sum(1 for pid in sasaran if terapkan(pid, m))
    return {"persen": persen, "inti": jumlah_inti(persen), "inti_total": inti_total(),
            "masker": hex(m), "proses": len(sasaran), "berhasil": berhasil}
