"""
Berkas JSON kecil yang ditulis beberapa proses: antrean, tugas batch,
pengaturan mesin.

Kenapa perlu (audit 28 Sep 2026): semua penulisnya dulu baca-ubah-tulis tanpa
kunci dengan `write_text`, yang tidak atomik. Dua akibatnya terukur:
  * 20 klik "Antrekan" serentak dijawab 20 x 200, tapi antrean justru turun
    dari 9 baris ke 2 — tulisan yang kalah balapan menimpa yang menang;
  * pembaca yang kebetulan membaca berkas setengah tertulis mendapat JSON
    rusak, `baca()` diam-diam memberi daftar kosong, dan tulisan berikutnya
    menimpa seluruh antrean dengan daftar itu.
Penulisnya bukan hanya server: `jaga_antrean.py` proses terpisah, jadi kunci
thread saja tidak cukup.

Cara:
  * `tulis()` menulis ke berkas sementara di folder yang sama lalu
    `os.replace` — pembaca melihat isi lama atau isi baru, tidak pernah
    setengah;
  * `ubah()` memegang kunci berkas (`<nama>.lock`, msvcrt/fcntl seperti
    `kunci_penulis.py`) selama baca-ubah-tulis, dan MENUNGGU (bukan menolak),
    karena pemegangnya hanya butuh milidetik;
  * isi yang tidak bisa dibaca tidak ditimpa diam-diam: disimpan dulu sebagai
    `<nama>.rusak-<waktu>` supaya bisa diperiksa tangan.
"""

from __future__ import annotations

import contextlib
import json
import os
import threading
import time
from datetime import datetime
from pathlib import Path

# Kunci berkas OS tidak menahan thread lain di proses yang sama pada semua
# sistem, jadi tiap path juga punya kunci thread.
_kunci_thread: dict[str, threading.Lock] = {}
_kunci_daftar = threading.Lock()


def _kunci_untuk(path: Path) -> threading.Lock:
    kunci = str(Path(path).resolve()).lower()
    with _kunci_daftar:
        return _kunci_thread.setdefault(kunci, threading.Lock())


def _kunci_os(fh, tunggu: float) -> None:
    batas = time.monotonic() + tunggu
    while True:
        try:
            if os.name == "nt":
                import msvcrt
                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return
        except OSError:
            if time.monotonic() > batas:
                raise TimeoutError(f"kunci {fh.name} dipegang terlalu lama")
            time.sleep(0.02)


def _lepas_os(fh) -> None:
    with contextlib.suppress(OSError):
        if os.name == "nt":
            import msvcrt
            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


@contextlib.contextmanager
def terkunci(path: Path, tunggu: float = 10.0):
    """Pegang kunci baca-ubah-tulis untuk `path` (lintas thread dan proses)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with _kunci_untuk(path):
        with open(str(path) + ".lock", "a+b") as fh:
            _kunci_os(fh, tunggu)
            try:
                yield
            finally:
                _lepas_os(fh)


def baca(path: Path, bawaan):
    """Isi berkas, atau `bawaan` kalau tidak ada / rusak / tipenya lain."""
    try:
        isi = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return bawaan
    return isi if isinstance(isi, type(bawaan)) else bawaan


def _baca_untuk_diubah(path: Path, bawaan):
    """Seperti `baca`, tapi isi rusak disisihkan dulu, bukan ditimpa diam-diam."""
    path = Path(path)
    try:
        teks = path.read_text(encoding="utf-8")
    except OSError:
        return bawaan
    try:
        isi = json.loads(teks)
    except ValueError:
        cadangan = path.with_name(f"{path.name}.rusak-{datetime.now():%Y%m%d-%H%M%S}")
        with contextlib.suppress(OSError):
            os.replace(path, cadangan)
        return bawaan
    return isi if isinstance(isi, type(bawaan)) else bawaan


def tulis(path: Path, isi, indent: int = 1) -> None:
    """Tulis atomik: berkas sementara di folder yang sama, lalu os.replace."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    sementara = path.with_name(f"{path.name}.{os.getpid()}-{threading.get_ident()}.tmp")
    sementara.write_text(json.dumps(isi, indent=indent, ensure_ascii=False),
                         encoding="utf-8", newline="\n")
    # Di Windows os.replace gagal (PermissionError) selama ada proses lain
    # yang sedang membuka berkas tujuan untuk dibaca; itu hanya sekejap.
    for percobaan in range(50):
        try:
            os.replace(sementara, path)
            return
        except PermissionError:
            if percobaan == 49:
                sementara.unlink(missing_ok=True)
                raise
            time.sleep(0.02)


@contextlib.contextmanager
def ubah(path: Path, bawaan):
    """
    Baca-ubah-tulis di bawah kunci:

        with ubah(PATH, []) as daftar:
            daftar.append(...)

    Objek yang diberikan diubah di tempat dan ditulis saat keluar blok (tidak
    ditulis kalau bloknya melempar galat).
    """
    with terkunci(path):
        isi = _baca_untuk_diubah(path, bawaan)
        yield isi
        tulis(path, isi)
