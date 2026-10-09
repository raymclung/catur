"""
Melihat dan menghentikan proses batch analisis di mesin ini.

Dipisah dari `app/analisa_web.py` 21 Sep 2026 (audit: berkas itu 1.056 baris,
melanggar aturan "file kecil, satu tanggung jawab" di CLAUDE.md bagian 6).
Isinya satu urusan saja: BERTANYA KE WINDOWS proses apa yang sedang jalan,
dan mematikannya kalau diminta. Tidak tahu apa-apa soal saringan, HTTP,
maupun database.

Dua hal yang dijaga di sini:

  * **Pemindaian ditahan JEDA_PINDAI detik.** Halaman Mesin bertanya tiap 5
    detik; tanpa penahan ini PowerShell dipanggil terus-menerus (±0,3 detik
    sekali panggil) padahal jawabannya sama.
  * **Tanpa jendela hitam.** Tiap subprocess konsol di Windows memunculkan
    jendela sekejap; dengan halaman yang menyegarkan sendiri, jendelanya
    muncul terus dan mengganggu (dilaporkan Ray, 12 Sep 2026).

`analisa_web` mengimpor ulang nama-nama ini, jadi kode dan tes lama yang
memanggil `analisa_web._pindai_batch()` tetap jalan.
"""

from __future__ import annotations

import os
import subprocess
import time

from fastapi import HTTPException

# Jeda penahan hasil pindaian (detik).
JEDA_PINDAI = 15.0

TANPA_JENDELA = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0

# Hasil pindaian terakhir: {"waktu": epoch, "hasil": [...]} per nama skrip.
_pindai: dict = {"waktu": 0.0, "hasil": []}


def _pindai_proses(nama_skrip: str) -> list[dict]:
    """
    Proses python yang baris perintahnya memuat nama skrip itu.

    Dipakai untuk batch analisis DAN untuk penjaga antrean, supaya
    keduanya dibaca dari kenyataan (daftar proses), bukan dari catatan
    yang bisa basi.
    """
    simpanan = _pindai.setdefault(nama_skrip, {"waktu": 0.0, "hasil": []})
    sekarang = time.time()
    # `_pindai["waktu"] = 0` dipakai pemanggil untuk memaksa pindai ulang
    # (mis. tepat sebelum meluncurkan batch), jadi ia ikut dilihat di sini.
    paksa = not _pindai["waktu"]
    if not paksa and sekarang - simpanan["waktu"] < JEDA_PINDAI:
        return simpanan["hasil"]

    try:
        hasil = _keluaran_proses(nama_skrip)
    except Exception:
        return simpanan["hasil"]      # tidak bisa memeriksa: jangan mengarang
    simpanan.update(waktu=sekarang, hasil=hasil)
    _pindai["waktu"] = sekarang       # paksaan sudah dipakai
    return hasil


def _pindai_batch() -> list[dict]:
    """
    Batch analisis yang benar-benar berjalan, dibaca dari daftar proses.

    Termasuk batch yang diluncurkan dari terminal oleh sesi lain — itu justru
    intinya: halaman ini tidak boleh menawarkan tombol Mulai kalau engine
    sudah dipakai.
    """
    return _pindai_proses("analisis_partai.py")


def _keluaran_proses(nama_skrip: str) -> list[dict]:
    """Baca daftar proses, kembalikan yang baris perintahnya cocok."""
    hasil: list[dict] = []
    if os.name == "nt":
        perintah = ("Get-CimInstance Win32_Process -Filter \"Name like 'python%'\" | "
                    "ForEach-Object { \"$($_.ProcessId)`t$($_.CreationDate)`t$($_.CommandLine)\" }")
        jalan = subprocess.run(["powershell", "-NoProfile", "-Command", perintah],
                               capture_output=True, text=True, encoding="utf-8",
                               errors="replace", timeout=30,
                               creationflags=TANPA_JENDELA)
        pemisah = "	"
    else:
        jalan = subprocess.run(["ps", "-eo", "pid=,lstart=,args="],
                               capture_output=True, text=True, timeout=30)
        pemisah = None
    keluaran = jalan.stdout or ""
    # Pemindai ini sendiri berjalan di proses python, jadi daftar yang benar
    # TIDAK PERNAH kosong. Kosong atau kode keluar != 0 = pindaiannya gagal.
    # Dulu itu dibaca "tidak ada batch": 27 Sep 17:03 batch 22436 yang masih
    # hidup dicap terputus, dan penjaga meluncurkan kembarannya (29292).
    # Melempar di sini membuat _pindai_proses memakai hasil pindaian terakhir.
    if jalan.returncode != 0 or not keluaran.strip():
        raise RuntimeError(f"pindai proses gagal (kode {jalan.returncode})")
    for baris in keluaran.splitlines():
        bagian = baris.split(pemisah, 2) if pemisah else baris.split(None, 2)
        if len(bagian) < 3 or not bagian[0].strip().isdigit():
            continue
        pid, mulai, perintah_proses = bagian[0].strip(), bagian[1].strip(), bagian[2]
        if nama_skrip not in perintah_proses or "Get-CimInstance" in perintah_proses:
            continue
        # Perintah UTUH: dulu dipotong 200 karakter, padahal awalan python.exe
        # WindowsApps saja 144 — argumen_dari_perintah() lalu salah membaca
        # arah (--terlama terpotong) atau gagal di kutip yang terbelah.
        hasil.append({"pid": int(pid), "mulai": mulai,
                      "perintah": " ".join(perintah_proses.split())})
    return hasil

def _proses_ada(pid: int) -> bool:
    """
    Apakah PID itu masih ada? Murah: tanya langsung ke Windows, tanpa
    menjalankan proses baru.
    """
    if os.name != "nt":
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False
    import ctypes
    SINKRONISASI = 0x00100000
    MASIH_HIDUP = 259
    k32 = ctypes.windll.kernel32
    h = k32.OpenProcess(SINKRONISASI | 0x0400, False, pid)   # 0x400 = QUERY_INFORMATION
    if not h:
        return False
    try:
        kode = ctypes.c_ulong()
        if k32.GetExitCodeProcess(h, ctypes.byref(kode)):
            return kode.value == MASIH_HIDUP
        return True
    finally:
        k32.CloseHandle(h)


def _matikan(pid: int, pohon: bool = True) -> None:
    """
    Hentikan satu proses beserta anaknya (Stockfish ikut mati), lalu
    TUNGGU sampai benar-benar hilang.

    Tanpa penantian itu, pemindaian berikutnya masih melihatnya dan
    jawaban jadi salah — terjadi nyata 13 Sep 2026: penjaga dimatikan,
    lalu permintaan menghidupkan dijawab "sudah jalan" dengan PID yang
    sudah mati, sehingga tidak ada penjaga yang berjalan sama sekali.
    """
    try:
        if os.name == "nt":
            # /T menyapu seluruh keturunan. Untuk BATCH itu perlu (Stockfish
            # ikut mati), tapi untuk PENJAGA justru salah: batch yang ia
            # jalankan adalah anaknya, jadi "Matikan penjaga" ikut membunuh
            # semua batch — terjadi nyata 13 Sep 2026, #1 mati tiga kali.
            argumen = ["taskkill", "/PID", str(pid)] + (["/T"] if pohon else []) + ["/F"]
            subprocess.run(argumen,
                           capture_output=True, timeout=30,
                           creationflags=TANPA_JENDELA)
        else:
            import signal
            if pohon:
                os.killpg(os.getpgid(pid), signal.SIGTERM)
            else:
                os.kill(pid, signal.SIGTERM)
    except Exception as e:      # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"Gagal menghentikan: {e}") from e

    batas = time.time() + 5
    while _proses_ada(pid) and time.time() < batas:
        time.sleep(0.2)
    _pindai["waktu"] = 0.0          # pindaian lama sudah tidak sahih


def waktu_lahir_proses(pid: int) -> float | None:
    """Waktu proses dibuat (epoch detik), atau None kalau tak bisa dibaca."""
    if os.name != "nt":
        return None
    import ctypes
    from ctypes import wintypes
    k32 = ctypes.windll.kernel32
    h = k32.OpenProcess(0x1000, False, pid)        # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return None
    try:
        lahir, keluar, kernel, pengguna = (wintypes.FILETIME() for _ in range(4))
        if not k32.GetProcessTimes(h, ctypes.byref(lahir), ctypes.byref(keluar),
                                   ctypes.byref(kernel), ctypes.byref(pengguna)):
            return None
        angka = (lahir.dwHighDateTime << 32) | lahir.dwLowDateTime
        return angka / 1e7 - 11644473600            # FILETIME (1601) -> epoch
    finally:
        k32.CloseHandle(h)


def masih_hidup(pid: int, sejak: str | None = None) -> bool:
    """
    Apakah batch ber-PID ini masih berjalan? Dinyatakan MATI hanya kalau dua
    bukti sepakat: tidak ada di pindaian DAN Windows bilang PID itu tidak ada.

    Pindaian saja pernah keliru (lihat _keluaran_proses). `sejak` = waktu
    batch dimulai ("YYYY-MM-DD HH:MM", dari catatan tugas/antrean): PID yang
    ada tapi prosesnya lahir SESUDAH itu adalah PID bekas pakai milik proses
    lain, bukan batch kita.
    """
    if any(b["pid"] == pid for b in _pindai_batch()):
        return True
    if not _proses_ada(pid):
        return False
    lahir = waktu_lahir_proses(pid)
    if lahir is None or not sejak:
        return True
    try:
        from datetime import datetime              # noqa: PLC0415
        mulai = datetime.strptime(sejak, "%Y-%m-%d %H:%M").timestamp()
    except ValueError:
        return True
    return lahir <= mulai + 120          # catatan beresolusi menit; beri kelonggaran


def _hidup(pid: int, sejak: str | None = None) -> bool:
    return masih_hidup(pid, sejak)


def jangan_diperlambat(pid: int | None = None) -> bool:
    """
    Keluarkan proses dari "power throttling" (EcoQoS) Windows. True kalau berhasil.

    Pintasan server menyalakan uvicorn lewat Start-Process -WindowStyle
    Hidden. Windows 11 menganggap proses tanpa jendela sebagai pekerjaan latar
    dan menjalankannya dalam mode hemat: dengan kode yang sama, unduh
    repertoire 13-14 detik di server pintasan, 3,5 detik di server yang
    dinyalakan dari terminal; setelah fungsi ini dipanggil pada server yang
    sama, 2,5 detik (diukur 29 Sep 2026). Batch analisis SENGAJA tidak ikut:
    mereka memang dirancang hemat (prioritas idle, batas CPU).
    """
    if os.name != "nt":
        return False
    import ctypes
    from ctypes import wintypes

    class Throttling(ctypes.Structure):
        _fields_ = [("Version", wintypes.ULONG), ("ControlMask", wintypes.ULONG),
                    ("StateMask", wintypes.ULONG)]

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.OpenProcess.restype = wintypes.HANDLE
    k32.GetCurrentProcess.restype = wintypes.HANDLE
    sendiri = pid is None or pid == os.getpid()
    h = k32.GetCurrentProcess() if sendiri else k32.OpenProcess(0x0200, False, pid)
    if not h:
        return False
    try:
        # Version 1; ControlMask EXECUTION_SPEED (1) dikendalikan sendiri,
        # StateMask 0 = jangan diperlambat. ProcessPowerThrottling = 4.
        info = Throttling(1, 1, 0)
        return bool(k32.SetProcessInformation(wintypes.HANDLE(h), 4, ctypes.byref(info),
                                              ctypes.sizeof(info)))
    finally:
        if not sendiri:
            k32.CloseHandle(wintypes.HANDLE(h))
