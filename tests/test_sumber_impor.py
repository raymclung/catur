"""
Uji catatan sumber impor (skema v3).

Sengaja TIDAK mengimpor impor_twic.py: file itu sedang dikerjakan sesi lain
dan tanda tangannya berubah-ubah. Tes untuk saringan TWIC ikut file itu.
"""

import sys
import tempfile
import unittest
from pathlib import Path

AKAR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(AKAR))

from app.db import buat_skema, koneksi
from app.sumber_impor import catat, ringkasan, sudah_selesai


class UjiSumberImpor(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = koneksi(Path(self.tmp.name) / "uji.db")
        buat_skema(self.conn)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_belum_tercatat(self):
        self.assertFalse(sudah_selesai(self.conn, "twic", "1661"))

    def test_catat_lalu_selesai(self):
        catat(self.conn, "twic", "1661", "TWIC 1661", "u", 7670, 89, 30)
        self.assertTrue(sudah_selesai(self.conn, "twic", "1661"))
        self.assertFalse(sudah_selesai(self.conn, "lichess-broadcast", "1661"))

    def test_ronde_belum_final_tidak_dianggap_selesai(self):
        catat(self.conn, "lichess-broadcast", "abc", None, None, 6, 6, 6, selesai=False)
        self.assertFalse(sudah_selesai(self.conn, "lichess-broadcast", "abc"))
        catat(self.conn, "lichess-broadcast", "abc", None, None, 6, 6, 0, selesai=True)
        self.assertTrue(sudah_selesai(self.conn, "lichess-broadcast", "abc"))

    def test_catat_ulang_menumpuk_baru_bukan_baris(self):
        catat(self.conn, "twic", "1", None, None, 10, 5, 5)
        catat(self.conn, "twic", "1", None, None, 10, 5, 2)
        r = ringkasan(self.conn)
        self.assertEqual(len(r), 1)
        self.assertEqual(r[0]["sumber"], 1)
        self.assertEqual(r[0]["baru"], 7)

    def test_jenis_dan_kunci_dipisah(self):
        catat(self.conn, "twic", "x", None, None, 1, 1, 1)
        catat(self.conn, "lichess-broadcast", "x", None, None, 1, 1, 1)
        self.assertEqual(len(ringkasan(self.conn)), 2)


if __name__ == "__main__":
    unittest.main()
