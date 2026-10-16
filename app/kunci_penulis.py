"""
Kunci "satu penulis besar pada satu waktu" (CLAUDE.md bagian 7).

Kenapa perlu: memeriksa daftar proses lalu meluncurkan perintah BUKAN operasi
atomik. Pada 12 Sep 2026 dua sesi meluncurkan impor TWIC dengan selisih satu
detik — pemeriksaan keduanya bersih, lalu yang kedua mati di tengah dengan
`sqlite3.OperationalError: database is locked`. Yang menolak harus sistem
operasi, bukan kesopanan antar-sesi.

Cara: kunci berkas eksklusif non-blocking (msvcrt di Windows, fcntl di POSIX).
Kalau prosesnya mati — termasuk mati mendadak atau laptop dimatikan — sistem
operasi melepas kuncinya sendiri, jadi tidak ada kunci basi yang harus
dibersihkan manual.

Keterangan pemegang kunci ditulis ke berkas TERPISAH supaya proses yang
tertolak tetap bisa membacanya (berkas kunci itu sendiri sedang terkunci).

Pakai:
    from app.kunci_penulis import KunciPenulis, KunciTerpakai
    try:
        with KunciPenulis("impor twic 920-1505"):
            ...
    except KunciTerpakai as e:
        print(e)
"""

from __future__ import annotations

import os
import re
from datetime import datetime
from pathlib import Path

from .db import FOLDER_DATA

AKAR = Path(__file__).resolve().parents[1]
PATH_KUNCI = FOLDER_DATA / ".penulis.lock"
PATH_INFO = FOLDER_DATA / ".penulis.info"


class KunciTerpakai(RuntimeError):
    """Penulis besar lain sedang berjalan."""


def _tanpa_jendela() -> int:
    """
    Bendera supaya subprocess konsol di Windows tidak memunculkan jendela hitam.

    Tanpa ini, tiap pemindaian daftar proses membuka jendela sekejap. Terasa
    sekali kalau dipanggil berulang, dan Ray melaporkannya 12 Sep 2026.
    """
    import subprocess
    return getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0


def _kunci(fh) -> None:
    """Kunci eksklusif non-blocking pada 1 byte pertama. OSError kalau gagal."""
    fh.seek(0)
    if os.name == "nt":
        import msvcrt
        msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
    else:
        import fcntl
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def _lepas(fh) -> None:
    fh.seek(0)
    try:
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
    except OSError:
        pass          # sudah dilepas saat berkas ditutup


def pemegang(path_info: Path = PATH_INFO) -> str:
    """
    Keterangan pemegang kunci menurut berkas .info.

    HATI-HATI: ini bisa BASI. Berkas .info hanya terhapus saat proses keluar
    dengan rapi; kalau proses mati mendadak, sistem operasi melepas kuncinya
    tapi berkas keterangan tertinggal. Untuk tahu keadaan sebenarnya, pakai
    status() yang benar-benar mencoba mengambil kunci.
    """
    try:
        isi = Path(path_info).read_text(encoding="utf-8").strip()
        return isi or "(tidak diketahui)"
    except OSError:
        return "(tidak diketahui)"


def status(path: Path = PATH_KUNCI, path_info: Path = PATH_INFO) -> tuple[bool, str]:
    """
    (bebas, keterangan) — ditentukan dengan benar-benar mencoba mengambil
    kunci, bukan dengan membaca berkas .info yang bisa basi. Kalau ternyata
    bebas, berkas .info yang tertinggal ikut dibersihkan.
    """
    k = KunciPenulis("pemeriksaan status", path=path, path_info=path_info)
    try:
        k.ambil()
    except KunciTerpakai:
        return False, pemegang(path_info)
    if k.diwarisi:      # dipegang induk: tetap SIBUK bagi siapa pun yang bertanya
        k.lepas()
        return False, pemegang(path_info)
    k.lepas()
    return True, ""


# Pewarisan kunci ke proses anak. Perlu karena perbarui.py memegang kunci lalu
# menjalankan indeks_posisi.py / impor_broadcast.py sebagai PROSES TERPISAH:
# kunci berkas milik proses, jadi tanpa pewarisan anak itu ditolak oleh
# induknya sendiri (dibuktikan 14 Sep 2026 — tahap indeks pembaruan mingguan
# selalu "GAGAL").
#
# SENGAJA EKSPLISIT, tidak dipasang di os.environ pemegang: server juga bisa
# memegang kunci (impor lewat web) sambil meluncurkan batch analisis, dan batch
# itu justru HARUS tetap menunggu impor selesai. Jadi hanya anak yang diberi
# lingkungan_anak() yang mewarisi.
ENV_PEMEGANG = "CATUR_PEMEGANG_KUNCI"


def lingkungan_anak() -> dict:
    """Lingkungan untuk proses anak yang boleh menulis di bawah kunci proses ini.
    Tidak memberi apa-apa kalau proses ini ternyata tidak memegang kunci:
    _diwarisi() mencocokkan PID dengan berkas keterangan pemegang."""
    return {**os.environ, ENV_PEMEGANG: str(os.getpid())}


# Job Object Windows milik proses ini: anak yang mewarisi kunci dimasukkan ke
# sini dan ikut mati saat proses ini mati. Handle-nya sengaja tidak pernah
# ditutup — Windows menutupnya ketika proses ini berakhir, dengan cara apa pun.
_job_anak = None


def _job_matikan_saat_tutup():
    """Job dengan JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE, dibuat sekali."""
    global _job_anak
    if _job_anak is not None:
        return _job_anak
    import ctypes
    from ctypes import wintypes

    class Dasar(ctypes.Structure):
        _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64),
                    ("PerJobUserTimeLimit", ctypes.c_int64),
                    ("LimitFlags", wintypes.DWORD),
                    ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t),
                    ("ActiveProcessLimit", wintypes.DWORD),
                    ("Affinity", ctypes.c_size_t),
                    ("PriorityClass", wintypes.DWORD),
                    ("SchedulingClass", wintypes.DWORD)]

    class Io(ctypes.Structure):
        _fields_ = [(n, ctypes.c_uint64) for n in (
            "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
            "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

    class Luas(ctypes.Structure):
        _fields_ = [("BasicLimitInformation", Dasar), ("IoInfo", Io),
                    ("ProcessMemoryLimit", ctypes.c_size_t),
                    ("JobMemoryLimit", ctypes.c_size_t),
                    ("PeakProcessMemoryUsed", ctypes.c_size_t),
                    ("PeakJobMemoryUsed", ctypes.c_size_t)]

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateJobObjectW.restype = wintypes.HANDLE
    job = k32.CreateJobObjectW(None, None)
    if not job:
        return None
    info = Luas()
    info.BasicLimitInformation.LimitFlags = 0x2000     # KILL_ON_JOB_CLOSE
    if not k32.SetInformationJobObject(wintypes.HANDLE(job), 9,  # ExtendedLimitInformation
                                       ctypes.byref(info), ctypes.sizeof(info)):
        return None
    _job_anak = job
    return job


def jalankan_anak(perintah: list[str], **kwargs):
    """
    `subprocess.run` untuk anak yang MEWARISI kunci proses ini (lihat
    lingkungan_anak): anaknya diikat ke umur proses ini.

    Tanpa ikatan ini, induk yang dimatikan dengan `taskkill /PID` tanpa `/T`
    melepas kuncinya (OS menutup berkas kunci) sementara anaknya — mis.
    indeks_posisi.py dari perbarui.py — terus menulis, dan penulis besar lain
    lolos mengambil kunci (audit 28 Sep 2026). Di Windows anak dimasukkan ke
    Job Object ber-KILL_ON_JOB_CLOSE; di sistem lain perilakunya tetap
    subprocess.run biasa.
    """
    import subprocess

    kwargs.setdefault("env", lingkungan_anak())
    # Tanpa jendela: perbarui.py dari tombol "Perbarui sekarang" berjalan
    # DETACHED (tanpa konsol), dan anak konsol dari proses tanpa konsol diberi
    # konsol BARU oleh Windows — jendela Windows Terminal hitam kosong muncul
    # tiap tahap (Ray, 16 Okt 2026). Menutupnya mematikan tahap itu. Keluaran
    # anak sudah lewat pipa, jadi konsol memang tidak dibutuhkan.
    kwargs.setdefault("creationflags", _tanpa_jendela())
    masukan = kwargs.pop("input", None)
    timeout = kwargs.pop("timeout", None)
    with subprocess.Popen(perintah, **kwargs) as proses:
        if os.name == "nt":
            import ctypes
            job = _job_matikan_saat_tutup()
            if job is not None:
                ctypes.windll.kernel32.AssignProcessToJobObject(
                    ctypes.c_void_p(job), ctypes.c_void_p(int(proses._handle)))
        try:
            keluar, galat = proses.communicate(masukan, timeout=timeout)
        except BaseException:
            proses.kill()
            raise
        return subprocess.CompletedProcess(perintah, proses.returncode, keluar, galat)


def _diwarisi(path_info: Path) -> bool:
    """Kunci sedang dipegang oleh proses yang menurunkan proses ini?

    Syarat keduanya: variabel lingkungan dari induk ADA, dan berkas keterangan
    menyebut PID yang sama. Variabel sisa dari pemegang yang sudah selesai
    tidak cukup, karena berkas keterangannya sudah terhapus atau berganti."""
    pid = os.environ.get(ENV_PEMEGANG, "")
    return bool(pid) and pid != str(os.getpid()) and pemegang(path_info).startswith(f"PID {pid} |")


class KunciPenulis:
    def __init__(self, keterangan: str = "", path: Path = PATH_KUNCI,
                 path_info: Path = PATH_INFO):
        self.keterangan = keterangan
        self.path = Path(path)
        self.path_info = Path(path_info)
        self._fh = None
        self.diwarisi = False

    def ambil(self) -> "KunciPenulis":
        """Ambil kunci. Melempar KunciTerpakai kalau penulis lain sedang jalan."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # "a+b": jangan pernah memotong isi; berkas kunci hanya wadah, bukan data.
        self._fh = open(self.path, "a+b")
        try:
            _kunci(self._fh)
        except OSError:
            self._fh.close()
            self._fh = None
            if _diwarisi(self.path_info):
                # Induk yang memegang kunci; ia yang melepasnya nanti.
                self.diwarisi = True
                return self
            raise KunciTerpakai(
                "Penulis besar lain sedang berjalan, jadi perintah ini tidak "
                "dijalankan.\n  Pemegang kunci: " + pemegang(self.path_info) +
                "\n  Tunggu sampai selesai, lalu ulangi perintah yang sama "
                "(yang sudah masuk akan dilewati)."
            )
        try:
            self.path_info.write_text(
                f"PID {os.getpid()} | {self.keterangan} | mulai "
                f"{datetime.now():%Y-%m-%d %H:%M:%S}",
                encoding="utf-8",
            )
        except OSError:
            pass          # keterangan bersifat bantuan, bukan syarat
        return self

    def lepas(self) -> None:
        """Lepas kunci. Aman dipanggil walau kunci tidak pernah diambil."""
        if self.diwarisi:
            self.diwarisi = False
            return
        if self._fh is None:
            return
        _lepas(self._fh)
        self._fh.close()
        self._fh = None
        try:
            self.path_info.unlink()
        except OSError:
            pass

    # Bisa dipakai sebagai context manager maupun lewat ambil()/lepas().
    def __enter__(self) -> "KunciPenulis":
        return self.ambil()

    def __exit__(self, *exc) -> None:
        self.lepas()


# Skrip yang menulis database dalam jumlah besar. Sejak 14 Sep 2026 semuanya
# mengambil kunci, tapi pemindaian daftar proses tetap dipakai sebagai lapis
# kedua: salinan skrip versi lama yang masih berjalan, atau skrip yang sedang
# memuat modul sebelum sempat mengambil kunci, tidak kelihatan dari kunci saja.
POLA_PENULIS = re.compile(
    r"(?:^|[\/ \"'])(perbarui|impor_pgn|impor_twic|impor_broadcast|"
    r"indeks_posisi|rapikan_nama)\.py(?:\s|$)"
)


def _leluhur() -> set[str]:
    """
    PID diri sendiri beserta seluruh induknya, sebagai teks.

    Perlu karena baris perintah shell yang MELUNCURKAN skrip ini ikut memuat
    nama skripnya ("python impor_pgn.py ..."), sehingga penulis_berjalan()
    mengira ada impor lain dan skrip menolak dirinya sendiri. Diuji 12 Sep 2026:
    tanpa saringan ini `python impor_pgn.py --rapikan` selalu ditolak.
    """
    hasil = {str(os.getpid())}
    if os.name != "nt":
        return hasil
    import subprocess
    try:
        keluaran = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-CimInstance Win32_Process | ForEach-Object "
             "{ \"$($_.ProcessId) $($_.ParentProcessId)\" }"],
            capture_output=True, text=True, timeout=60,
            creationflags=_tanpa_jendela()).stdout
    except Exception:
        return hasil
    induk = {}
    for baris in keluaran.splitlines():
        bagian = baris.split()
        if len(bagian) == 2:
            induk[bagian[0]] = bagian[1]
    kini = str(os.getpid())
    for _ in range(20):                  # cukup dalam; sekaligus jaga siklus
        naik = induk.get(kini)
        if not naik or naik in hasil:
            break
        hasil.add(naik)
        kini = naik
    return hasil


def penulis_berjalan() -> list[str]:
    """
    Perintah penulis besar yang sedang berjalan, dibaca dari daftar proses.

    Perlu karena sebagian skrip belum mengambil kunci (dijalankan langsung,
    bukan lewat perbarui.py). Tanpa ini status() bisa menjawab "bebas" padahal
    ada impor yang sedang menulis.

    Baris perintah yang memuat baris baru DIRATAKAN dulu supaya satu proses
    tetap satu baris; nama skrip harus berdiri sebagai argumen (diikuti spasi
    atau akhir baris) supaya teks yang kebetulan menyebutnya — misalnya berkas
    ini sendiri — tidak ikut terhitung.
    """
    import subprocess
    PENANDA = "<<<BATAS-PROSES>>>"
    try:
        if os.name == "nt":
            perintah = ("Get-CimInstance Win32_Process | ForEach-Object "
                        "{ \"$($_.ProcessId) $($_.Name) $($_.CommandLine)\"; '"
                        + PENANDA + "' }")
            keluaran = subprocess.run(
                ["powershell", "-NoProfile", "-Command", perintah],
                capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=60,
                creationflags=_tanpa_jendela()).stdout
        else:
            mentah = subprocess.run(["ps", "-eo", "pid=,comm=,args="], capture_output=True,
                                    text=True, timeout=60).stdout
            keluaran = PENANDA.join(mentah.splitlines())
    except Exception:
        return []          # tidak bisa memeriksa; jangan mengarang
    # Tiap baris kini berbentuk "<pid> <nama proses> <baris perintah>".
    #   * PID membuang diri sendiri BESERTA induknya — shell yang meluncurkan
    #     skrip ini memuat nama skripnya juga, tanpa saringan itu skrip menolak
    #     dirinya sendiri.
    #   * NAMA PROSES harus python: shell lain yang baris perintahnya kebetulan
    #     menyebut skrip (mis. bekas perintah `python impor_pgn.py ...` di
    #     terminal yang masih terbuka) bukan penulis yang sedang menulis.
    abaikan = _leluhur()
    hasil = []
    for baris in keluaran.split(PENANDA):
        b = " ".join(baris.split())      # ratakan baris perintah multi-baris
        pid, _, sisa = b.partition(" ")
        nama, _, perintah = sisa.partition(" ")
        if pid.isdigit():
            if pid in abaikan or not nama.lower().startswith("python"):
                continue
            b = perintah
        if not b or "Get-CimInstance" in b:
            continue
        if POLA_PENULIS.search(b):
            hasil.append(b[-70:] if len(b) > 70 else b)
    return hasil


def tunggu_sampai_bebas(lapor=print, jeda: float = 60.0, batas: float | None = None,
                        _tidur=None) -> bool:
    """
    Tunggu sampai tidak ada penulis besar. Untuk perintah yang MENULIS SEDIKIT
    tapi berjalan lama — batch analisis — sehingga tidak boleh memegang kunci
    (tiga batch sekaligus diizinkan) tapi juga tidak boleh mulai di tengah
    impor: 11 Sep 2026 batch B11 mati di partai ke-70 karena impor TWIC mulai.

    Kembalikan True kalau sudah bebas, False kalau batas waktu (detik)
    terlampaui. `_tidur` hanya untuk tes.
    """
    import time as _t
    tidur = _tidur or _t.sleep
    mulai = _t.monotonic()
    sudah_lapor = False
    while True:
        bebas, siapa = status()
        lain = penulis_berjalan()
        if bebas and not lain:
            if sudah_lapor:
                lapor("Penulis besar selesai, batch dilanjutkan.")
            return True
        if batas is not None and _t.monotonic() - mulai >= batas:
            return False
        if not sudah_lapor:
            keterangan = siapa if not bebas else "; ".join(lain)
            lapor(f"Menunggu penulis besar selesai dulu: {keterangan}")
            lapor("  (batch analisis menulis sedikit tapi lama; mulai di tengah "
                  "impor membuatnya mati terkunci)")
            sudah_lapor = True
        tidur(jeda)


def _main() -> int:
    bebas, siapa = status()
    proses = penulis_berjalan()
    if bebas and not proses:
        print("bebas, boleh mulai")
        return 0
    print("SEDANG DIPAKAI penulis besar, jangan mulai dulu.")
    if not bebas:
        print("  pemegang kunci : " + siapa)
    for b in proses:
        print("  proses berjalan: ..." + b)
    if bebas and proses:
        print("  (skrip ini belum mengambil kunci, jadi ketahuan dari daftar proses)")
    return 1


if __name__ == "__main__":
    raise SystemExit(_main())
