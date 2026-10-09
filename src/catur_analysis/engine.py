"""
Pembungkus Stockfish dengan batas pemakaian resource.

Kenapa pakai pembungkus, bukan langsung chess.engine?

1. RAM. Pemakaian RAM Stockfish hampir seluruhnya ditentukan opsi UCI "Hash"
   (ukuran transposition table). Kalau tidak diset, gampang kelupaan dan
   engine dibiarkan pakai nilai yang tidak kita kehendaki. Di sini Hash SELALU
   diset eksplisit sebelum analisis pertama.

2. Proses yang bocor. Stockfish jalan sebagai proses terpisah. Kalau program
   Python berhenti tanpa memanggil quit(), proses stockfish.exe bisa tetap
   hidup di background dan terus memakan RAM. Class ini dibuat context manager
   supaya engine dijamin ditutup, termasuk saat terjadi error.

3. Satu instance saja. Dua Stockfish jalan bersamaan artinya dua kali RAM dan
   rebutan CPU. Modul ini menjaga hanya ada satu proses aktif.
"""

from __future__ import annotations

import atexit
import os
import threading
from dataclasses import dataclass
from pathlib import Path

import chess
import chess.engine

# Stockfish adalah aplikasi konsol: tanpa bendera ini Windows memberinya
# jendela konsol sendiri, dan jendela itu muncul di layar tiap kali engine
# dijalankan — termasuk waktu batch diluncurkan dari halaman web, yang
# justru tidak punya konsol untuk menampungnya (A22, 12 Sep 2026).
# CREATE_NO_WINDOW = 0x08000000.
TANPA_JENDELA = 0x08000000 if os.name == "nt" else 0

# Lokasi engine relatif terhadap akar proyek (Catur/).
AKAR_PROYEK = Path(__file__).resolve().parents[2]
PATH_ENGINE_DEFAULT = AKAR_PROYEK / "engine" / "stockfish.exe"


# Model pemakaian RAM Stockfish 18, DIUKUR di laptop ini (bukan tebakan).
#
#   RAM total = BIAYA_DASAR + hash_mb + RAM_PER_THREAD x (threads - 2)
#
# Dua pengukuran yang jadi dasarnya:
#   Hash  64 MB, 2 thread -> 371,4 MB
#   Hash 128 MB, 4 thread -> 496,9 MB
#
# Stockfish 18 memuat jaringan NNUE berukuran besar ke memori begitu proses
# dijalankan, terlepas dari setelan Hash. Itu sebabnya biaya dasarnya tinggi.
#
# Konsekuensi praktis: mengecilkan Hash TIDAK banyak menolong. Turun dari
# Hash 128 ke 64 cuma menghemat 64 MB dari total ~500 MB. Yang benar-benar
# membebaskan RAM cuma satu: menutup engine kalau sedang tidak dipakai.
BIAYA_DASAR_RAM_MB = 307
RAM_PER_THREAD_MB = 31
THREAD_DASAR = 2


@dataclass(frozen=True)
class ProfilResource:
    """
    Satu set batasan pemakaian resource.

    hash_mb  : ukuran transposition table dalam MB. Ini satu-satunya bagian
               RAM yang bisa kita atur.
    threads  : jumlah thread CPU. Nyaris tidak menambah RAM, tapi menentukan
               seberapa berat laptop terasa saat analisis jalan.
    depth    : kedalaman pencarian default.
    """

    nama: str
    hash_mb: int
    threads: int
    depth: int

    @property
    def perkiraan_ram_mb(self) -> int:
        """Perkiraan total RAM proses engine, berdasarkan pengukuran nyata."""
        thread_ekstra = max(0, self.threads - THREAD_DASAR)
        return BIAYA_DASAR_RAM_MB + self.hash_mb + RAM_PER_THREAD_MB * thread_ekstra


# Profil disusun untuk laptop dengan RAM bebas terbatas.
# Angka default sengaja konservatif: lebih baik analisis sedikit lebih lambat
# daripada laptop tersendat saat dipakai kerja lain.
# "cepat" untuk memborong puluhan ribu partai GM. Diukur 13 Sep 2026 pada tiga
# partai blitz, depth 16 sebagai acuan:
#
#   depth 16  82 detik/partai   12/12 kesalahan besar ketemu
#   depth 12  10 detik/partai    9/12 ketemu, 2 palsu
#   depth 10   2,7 detik/partai  9/12 ketemu, 3 palsu   <- profil "cepat"
#   depth  8   1,7 detik/partai  6/12 ketemu, 3 palsu
#
# Depth 10 menemukan tiga perempat kesalahan besar dengan **30 kali** lebih
# cepat, dan depth 12 tidak menemukan lebih banyak daripada 10 walau 4 kali
# lebih lambat. Jadi ini pilihan untuk CAKUPAN — partai GM yang tadinya tidak
# akan pernah tersentuh. Untuk partai Ray sendiri, yang benar-benar dipelajari
# satu per satu, tetap pakai "hemat".
CEPAT = ProfilResource("cepat", hash_mb=64, threads=2, depth=10)
HEMAT = ProfilResource("hemat", hash_mb=64, threads=2, depth=16)
NORMAL = ProfilResource("normal", hash_mb=128, threads=4, depth=20)
DALAM = ProfilResource("dalam", hash_mb=256, threads=6, depth=24)

PROFIL = {p.nama: p for p in (CEPAT, HEMAT, NORMAL, DALAM)}

# Batas keras. Nilai di atas ini ditolak, bukan sekadar diperingatkan —
# supaya salah ketik tidak berujung engine menghabiskan RAM laptop.
BATAS_HASH_MB = 512
BATAS_THREADS = 8


class EngineError(RuntimeError):
    """Kesalahan yang berhubungan dengan engine."""


class Stockfish:
    """
    Satu proses Stockfish dengan resource yang dibatasi.

    Cara pakai yang dianjurkan (engine dijamin ditutup):

        with Stockfish(profil=NORMAL) as sf:
            skor = sf.evaluasi(board)
    """

    _instance_aktif: "Stockfish | None" = None

    def __init__(
        self,
        profil: ProfilResource = NORMAL,
        path_engine: Path | None = None,
    ) -> None:
        if profil.hash_mb > BATAS_HASH_MB:
            raise EngineError(
                f"Hash {profil.hash_mb} MB melebihi batas {BATAS_HASH_MB} MB"
            )
        if profil.threads > BATAS_THREADS:
            raise EngineError(
                f"Threads {profil.threads} melebihi batas {BATAS_THREADS}"
            )

        self.profil = profil
        self.path_engine = Path(path_engine or PATH_ENGINE_DEFAULT)
        self._engine: chess.engine.SimpleEngine | None = None

    # ---------- siklus hidup ----------

    def start(self) -> "Stockfish":
        if self._engine is not None:
            return self

        if Stockfish._instance_aktif is not None:
            raise EngineError(
                "Sudah ada satu proses Stockfish aktif. Tutup dulu yang lama "
                "sebelum membuka yang baru, supaya RAM tidak terpakai dobel."
            )

        if not self.path_engine.is_file():
            raise EngineError(f"Engine tidak ditemukan di: {self.path_engine}")

        try:
            self._engine = chess.engine.SimpleEngine.popen_uci(
                str(self.path_engine), creationflags=TANPA_JENDELA)
        except Exception as exc:
            raise EngineError(f"Gagal menjalankan engine: {exc}") from exc

        # Inilah baris yang menentukan pemakaian RAM. Harus dipanggil sebelum
        # analisis pertama, kalau tidak engine memakai nilai bawaannya sendiri.
        self._engine.configure(
            {
                "Hash": self.profil.hash_mb,
                "Threads": self.profil.threads,
            }
        )

        Stockfish._instance_aktif = self
        atexit.register(self.stop)
        return self

    def stop(self) -> None:
        """Tutup engine. Aman dipanggil berkali-kali."""
        if self._engine is not None:
            try:
                self._engine.quit()
            except Exception:
                # Kalau proses sudah mati duluan, tidak ada yang perlu dilakukan.
                pass
            finally:
                self._engine = None
                if Stockfish._instance_aktif is self:
                    Stockfish._instance_aktif = None

    def __enter__(self) -> "Stockfish":
        return self.start()

    def __exit__(self, exc_type, exc, tb) -> None:
        self.stop()

    # ---------- informasi ----------

    @property
    def nama_engine(self) -> str:
        self._pastikan_hidup()
        return self._engine.id.get("name", "tidak diketahui")

    def pid(self) -> int | None:
        """PID proses engine, untuk mengukur RAM dari luar."""
        if self._engine is None:
            return None
        transport = getattr(self._engine, "transport", None)
        proses = transport.get_extra_info("subprocess") if transport else None
        return proses.pid if proses else None

    # ---------- analisis ----------

    def evaluasi(
        self,
        board: chess.Board,
        depth: int | None = None,
        multipv: int = 1,
        waktu: float | None = None,
        berhenti: threading.Event | None = None,
    ) -> list[dict]:
        """
        Analisis satu posisi.

        Mengembalikan list sepanjang multipv, tiap elemen berisi:
            cp    : evaluasi dalam centipawn dari sudut pandang PUTIH,
                    atau None kalau posisinya mat paksa
            mate  : jumlah langkah menuju mat (positif = Putih menang),
                    atau None kalau bukan mat paksa
            pv    : daftar langkah terbaik dalam notasi SAN
            depth : kedalaman yang benar-benar dicapai

        Sudut pandang sengaja selalu Putih, bukan "pemain yang jalan", supaya
        grafik evaluasi nanti tidak bolak-balik tandanya tiap langkah.
        
        `berhenti`: kalau diberikan dan di-set dari utas lain, pencarian
        dihentikan dalam ~0,1 detik dan hasil sejauh itu dikembalikan.
        Dipakai analisis tanpa henti di papan (app/evaluasi_aliran.py):
        tanpa ini, menekan Henti tetap menahan engine sampai langkah yang
        sedang dihitung selesai, dan evaluasi posisi berikutnya ditolak 409.
        """
        self._pastikan_hidup()

        # waktu (detik) membuat analisis berhenti pada mana yang lebih dulu
        # tercapai: kedalaman atau waktu. Dipakai papan analisis web supaya
        # jawabannya selalu datang dalam ~1,5 detik; batch tetap pakai depth.
        limit = chess.engine.Limit(depth=depth or self.profil.depth, time=waktu)
        if berhenti is None:
            hasil = self._engine.analyse(board, limit, multipv=multipv)
        else:
            hasil = self._analisis_bisa_dihentikan(board, limit, multipv, berhenti)

        # analyse() mengembalikan dict kalau multipv=1, list kalau lebih.
        if isinstance(hasil, dict):
            hasil = [hasil]

        keluaran = []
        for info in hasil:
            skor_putih = info["score"].white()
            keluaran.append(
                {
                    "cp": skor_putih.score(),
                    "mate": skor_putih.mate(),
                    "pv": self._pv_ke_san(board, info.get("pv", [])),
                    "depth": info.get("depth"),
                }
            )
        return keluaran

    def langkah_terbaik(self, board: chess.Board, depth: int | None = None):
        """Satu langkah terbaik menurut engine."""
        self._pastikan_hidup()
        limit = chess.engine.Limit(depth=depth or self.profil.depth)
        hasil = self._engine.play(board, limit)
        return hasil.move

    # ---------- internal ----------

    @staticmethod
    def _pv_ke_san(board: chess.Board, pv: list) -> list[str]:
        """Ubah principal variation dari UCI ke SAN supaya enak dibaca."""
        papan_sementara = board.copy(stack=False)
        langkah_san = []
        for move in pv:
            if not papan_sementara.is_legal(move):
                break
            langkah_san.append(papan_sementara.san(move))
            papan_sementara.push(move)
        return langkah_san

    def _analisis_bisa_dihentikan(self, board, limit, multipv: int,
                                  berhenti: threading.Event) -> list:
        """Seperti analyse(), tapi berhenti begitu `berhenti` di-set.

        `wait()` diletakkan di utas pembantu karena tidak punya batas waktu;
        utas ini mengawasi `berhenti` dan memanggil `stop()`, yang aman
        dipanggil dari utas mana pun pada SimpleAnalysisResult.
        """
        with self._engine.analysis(board, limit, multipv=multipv) as analisis:
            penunggu = threading.Thread(target=analisis.wait, daemon=True)
            penunggu.start()
            while penunggu.is_alive():
                if berhenti.wait(0.1):
                    analisis.stop()
                    break
            penunggu.join()
            return [info for info in analisis.multipv if "score" in info]

    def _pastikan_hidup(self) -> None:
        if self._engine is None:
            raise EngineError("Engine belum dijalankan. Panggil start() dulu.")


def info_engine(path_engine: Path | None = None) -> dict:
    """Baca identitas engine tanpa menjalankan analisis apa pun."""
    path = Path(path_engine or PATH_ENGINE_DEFAULT)
    if not path.is_file():
        raise EngineError(f"Engine tidak ditemukan di: {path}")

    engine = chess.engine.SimpleEngine.popen_uci(str(path), creationflags=TANPA_JENDELA)
    try:
        return {
            "nama": engine.id.get("name", "tidak diketahui"),
            "penulis": engine.id.get("author", "tidak diketahui"),
            "path": str(path),
            "ukuran_mb": round(os.path.getsize(path) / (1024 * 1024), 1),
        }
    finally:
        engine.quit()
