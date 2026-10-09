"""
Satu proses Stockfish yang hidup di dalam server web, untuk papan analisis.

Ini membalik keputusan lama "server tidak pernah menjalankan engine" — atas
permintaan Ray (SESI.md bagian 6, 10 Sep 2026): analisis harus bisa dilakukan
dari papan tanpa perintah terminal. Batasan yang membuat keputusan lama itu
dibuat tetap dihormati dengan cara lain:

  RAM      hanya SATU proses, profil HEMAT (terukur ~371 MB); tidak pernah
           dua engine per server. Batch (analisis_partai.py) tetap proses
           terpisah, jadi maksimal dua engine di mesin ini.
  waktu    tiap permintaan dibatasi ~1,5 detik (Limit(time=...)), bukan depth,
           supaya papan tetap responsif; depth cuma batas atas. Analisis
           tanpa henti (app/evaluasi_aliran.py) memegang engine yang SAMA
           lewat pegang()/lepas(), dengan batas waktu sendiri.
  antrean  tidak ada. Kalau engine sedang dipakai, permintaan berikutnya
           langsung ditolak (409) — lebih jujur daripada menumpuk permintaan
           yang jawabannya sudah basi saat tiba.
  idle     dimatikan otomatis setelah 5 menit tidak dipakai, supaya RAM
           kembali saat papan analisis tidak dibuka.
"""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

import chess

AKAR = Path(__file__).resolve().parents[1]
if str(AKAR / "src") not in sys.path:
    sys.path.insert(0, str(AKAR / "src"))

from catur_analysis.engine import HEMAT, EngineError, PATH_ENGINE_DEFAULT, Stockfish  # noqa: E402

WAKTU_DEFAULT = 1.5        # detik per permintaan
DEPTH_MAKS = 30
MULTIPV_MAKS = 5
IDLE_DETIK = 300           # 5 menit


class EngineSibuk(RuntimeError):
    """Engine sedang melayani permintaan lain (→ HTTP 409)."""


class EngineTidakAda(RuntimeError):
    """Berkas engine tidak ditemukan (→ HTTP 503)."""


class ServerEngine:
    """Pengelola satu proses Stockfish untuk seluruh server."""

    def __init__(self, path_engine: Path | None = None, idle_detik: int = IDLE_DETIK):
        self.path_engine = Path(path_engine or PATH_ENGINE_DEFAULT)
        self.idle_detik = idle_detik
        self._kunci = threading.Lock()
        self._sf: Stockfish | None = None
        self._terakhir_dipakai = 0.0
        self._penjaga: threading.Timer | None = None

    # ---------- siklus hidup ----------

    def _pastikan_hidup(self) -> Stockfish:
        if self._sf is None:
            if not self.path_engine.is_file():
                raise EngineTidakAda(f"engine tidak ditemukan di {self.path_engine}")
            self._sf = Stockfish(profil=HEMAT, path_engine=self.path_engine).start()
        return self._sf

    def _jadwalkan_penjaga(self) -> None:
        if self._penjaga is not None:
            self._penjaga.cancel()
        self._penjaga = threading.Timer(self.idle_detik, self._matikan_jika_menganggur)
        self._penjaga.daemon = True
        self._penjaga.start()

    def _matikan_jika_menganggur(self) -> None:
        # Kalau sedang dipakai, biarkan; penjaga berikutnya dijadwalkan lagi
        # oleh permintaan itu.
        if not self._kunci.acquire(blocking=False):
            return
        try:
            if self._sf is not None and time.time() - self._terakhir_dipakai >= self.idle_detik:
                self._sf.stop()
                self._sf = None
        finally:
            self._kunci.release()

    def matikan(self) -> None:
        with self._kunci:
            if self._penjaga is not None:
                self._penjaga.cancel()
            if self._sf is not None:
                self._sf.stop()
                self._sf = None

    @property
    def hidup(self) -> bool:
        return self._sf is not None

    @property
    def sedang_dipakai(self) -> bool:
        """Petunjuk untuk menjawab 409 lebih awal; bukan jaminan (bisa berubah sesaat kemudian)."""
        return self._kunci.locked()

    # ---------- analisis ----------

    def evaluasi(self, fen: str, multipv: int = 3, depth: int | None = None,
                 waktu: float = WAKTU_DEFAULT) -> dict:
        """
        Evaluasi satu posisi. Skor selalu dari sudut pandang PUTIH.

        Melempar ValueError (FEN tidak sah), EngineSibuk, EngineTidakAda.
        """
        board = papan_dari_fen(fen)
        multipv = max(1, min(int(multipv), MULTIPV_MAKS))
        depth = min(int(depth), DEPTH_MAKS) if depth else DEPTH_MAKS
        waktu = max(0.1, min(float(waktu), 10.0))

        self.pegang()
        try:
            return self.analisis_dipegang(board, multipv=multipv, depth=depth, waktu=waktu)
        finally:
            self.lepas()

    # ---------- kunci, dipakai juga oleh aliran (app/evaluasi_aliran.py) ----------

    def pegang(self) -> None:
        """Ambil engine untuk satu pemakai; EngineSibuk kalau sedang dipakai.

        Tidak antre, sama seperti /api/evaluasi. Engine dinyalakan di sini,
        jadi EngineTidakAda juga dilempar sebelum aliran apa pun dimulai.
        """
        if not self._kunci.acquire(blocking=False):
            raise EngineSibuk("engine sedang dipakai permintaan lain")
        try:
            self._pastikan_hidup()
        except BaseException:
            self._kunci.release()
            raise

    def lepas(self) -> None:
        """Kembalikan engine. Boleh dipanggil dari utas lain (threading.Lock)."""
        self._terakhir_dipakai = time.time()
        self._jadwalkan_penjaga()
        self._kunci.release()

    def analisis_dipegang(self, board: chess.Board, multipv: int, depth: int,
                          waktu: float, berhenti: threading.Event | None = None) -> dict:
        """Satu analisis; pemanggil WAJIB sudah memegang engine lewat pegang().

        `berhenti` diteruskan ke Stockfish.evaluasi(): di-set dari utas lain =
        pencarian berhenti dalam ~0,1 detik (dipakai aliran saat koneksi putus).
        """
        sf = self._pastikan_hidup()
        mulai = time.time()
        if board.is_game_over():
            garis = []
            skor = _skor_akhir(board)
            depth_dicapai = 0
        else:
            hasil = sf.evaluasi(board, depth=depth, multipv=multipv, waktu=waktu,
                                berhenti=berhenti)
            # Bentuk garis mengikuti kontrak frontend (SESI.md bagian 3 #4):
            # keluaran Stockfish.evaluasi() apa adanya + nomor multipv.
            garis = [
                {"multipv": i + 1, "cp": h["cp"], "mate": h["mate"],
                 "pv": h["pv"], "depth": h["depth"]}
                for i, h in enumerate(hasil)
            ]
            skor = ({"cp": garis[0]["cp"], "mate": garis[0]["mate"]}
                    if garis else {"cp": 0, "mate": None})
            depth_dicapai = hasil[0]["depth"] if hasil else 0
        durasi_ms = round((time.time() - mulai) * 1000)
        return {
            "fen": board.fen(),
            "giliran": "putih" if board.turn == chess.WHITE else "hitam",
            "skor": skor,
            "garis": garis,
            "depth": depth_dicapai,
            "profil": HEMAT.nama,
            "durasi_ms": durasi_ms,
            "selesai": board.is_game_over(),
        }


def papan_dari_fen(fen: str) -> chess.Board:
    """FEN -> papan; ValueError kalau rusak atau posisinya mustahil."""
    board = chess.Board(fen)                   # ValueError kalau rusak
    if not board.is_valid():
        raise ValueError("posisi tidak sah")
    return board


def _skor_akhir(board: chess.Board) -> dict:
    """Posisi sudah selesai: mat = pihak yang jalan kalah; selain itu remis."""
    if board.is_checkmate():
        return {"cp": None, "mate": 0, "pemenang": "hitam" if board.turn == chess.WHITE else "putih"}
    return {"cp": 0, "mate": None}


# Satu untuk seluruh server. Dibuat saat modul diimpor, engine-nya sendiri
# baru dinyalakan pada permintaan pertama.
server_engine = ServerEngine()
