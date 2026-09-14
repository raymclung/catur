"""
Uji app/engine_server.py (B5). Tes yang butuh Stockfish dilewati kalau
engine/stockfish.exe tidak ada, supaya suite tetap jalan di mesin lain.
"""

import sys
import threading
import unittest
from pathlib import Path

AKAR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(AKAR))

from app.engine_server import (EngineSibuk, EngineTidakAda, ServerEngine,
                               PATH_ENGINE_DEFAULT)

ADA_ENGINE = Path(PATH_ENGINE_DEFAULT).is_file()
MAT_1 = "r1bqkbnr/pppp1ppp/2n5/4p3/2B1P3/5Q2/PPPP1PPP/RNB1K1NR w KQkq - 4 4"   # Qxf7#


class UjiTanpaEngine(unittest.TestCase):
    def test_fen_rusak_valueerror_sebelum_engine_disentuh(self):
        s = ServerEngine(path_engine=Path("tidak/ada.exe"))
        with self.assertRaises(ValueError):
            s.evaluasi("bukan fen")
        with self.assertRaises(ValueError):
            s.evaluasi("8/8/8/8/8/8/8/8 w - - 0 1")        # tanpa raja
        self.assertFalse(s.hidup)

    def test_engine_tidak_ada_503(self):
        s = ServerEngine(path_engine=Path("tidak/ada.exe"))
        with self.assertRaises(EngineTidakAda):
            s.evaluasi("rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1")

    def test_sibuk_saat_kunci_dipegang(self):
        s = ServerEngine(path_engine=Path("tidak/ada.exe"))
        s._kunci.acquire()
        try:
            with self.assertRaises(EngineSibuk):
                s.evaluasi("rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1")
        finally:
            s._kunci.release()


@unittest.skipUnless(ADA_ENGINE, "engine/stockfish.exe tidak ada")
class UjiDenganEngine(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.s = ServerEngine(idle_detik=60)

    @classmethod
    def tearDownClass(cls):
        cls.s.matikan()

    def test_mat_dalam_satu_terdeteksi(self):
        h = self.s.evaluasi(MAT_1, multipv=1, waktu=0.5)
        self.assertEqual(h["skor"]["mate"], 1)
        # kontrak frontend #4: garis datar {multipv, cp, mate, pv, depth}
        g = h["garis"][0]
        self.assertEqual(set(g), {"multipv", "cp", "mate", "pv", "depth"})
        self.assertEqual(g["mate"], 1)
        self.assertEqual(g["pv"][0], "Qxf7#")
        self.assertTrue(self.s.hidup)
        self.assertLess(h["durasi_ms"], 3000)

    def test_multipv_dan_batas_waktu(self):
        h = self.s.evaluasi("rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
                            multipv=3, waktu=0.4)
        self.assertEqual(len(h["garis"]), 3)
        self.assertLess(h["durasi_ms"], 2500)
        self.assertEqual(h["profil"], "hemat")

    def test_posisi_selesai_tidak_memanggil_engine(self):
        # Fool's Mate: 1.f3 e5 2.g4 Qh4# — Putih (yang jalan) sudah mat.
        h = self.s.evaluasi("rnb1kbnr/pppp1ppp/8/4p3/6Pq/5P2/PPPPP2P/RNBQKBNR w KQkq - 1 3")
        self.assertTrue(h["selesai"])
        self.assertEqual(h["skor"]["mate"], 0)
        self.assertEqual(h["skor"]["pemenang"], "hitam")
        self.assertEqual(h["garis"], [])

    def test_dua_permintaan_bersamaan_satu_ditolak(self):
        hasil = []

        def kerja():
            try:
                self.s.evaluasi("rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
                                waktu=1.0)
                hasil.append("ok")
            except EngineSibuk:
                hasil.append("sibuk")

        t1 = threading.Thread(target=kerja)
        t2 = threading.Thread(target=kerja)
        t1.start(); t2.start(); t1.join(); t2.join()
        self.assertEqual(sorted(hasil), ["ok", "sibuk"])


if __name__ == "__main__":
    unittest.main()
