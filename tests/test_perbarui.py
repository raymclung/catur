"""Uji bagian murni perbarui.py: nomor edisi, saringan mingguan, edisi yang belum tercatat."""

import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

AKAR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(AKAR))

from app.db import buat_skema, koneksi
from app.sumber_impor import catat
from perbarui import EDISI_JANGKAR, TANGGAL_JANGKAR, edisi_belum, edisi_terbaru, lolos_mingguan

HEADER_OTB = {"Site": "Stavanger NOR", "WhiteElo": "2803", "BlackElo": "2745"}


class UjiEdisi(unittest.TestCase):
    def test_hari_jangkar(self):
        self.assertEqual(edisi_terbaru(TANGGAL_JANGKAR), EDISI_JANGKAR)

    def test_enam_hari_kemudian_masih_edisi_sama(self):
        self.assertEqual(edisi_terbaru(date(2026, 9, 13)), EDISI_JANGKAR)

    def test_senin_berikutnya_naik_satu(self):
        self.assertEqual(edisi_terbaru(date(2026, 9, 14)), EDISI_JANGKAR + 1)
        self.assertEqual(edisi_terbaru(date(2026, 12, 7)), EDISI_JANGKAR + 13)


class UjiSaringan(unittest.TestCase):
    def test_otb_kuat_lolos(self):
        self.assertTrue(lolos_mingguan(HEADER_OTB, 2500))

    def test_online_dibuang(self):
        self.assertFalse(lolos_mingguan({**HEADER_OTB, "Site": "Chess.com INT"}, 2500))

    def test_satu_pemain_di_bawah_ambang_dibuang(self):
        self.assertFalse(lolos_mingguan({**HEADER_OTB, "BlackElo": "2450"}, 2500))
        self.assertTrue(lolos_mingguan({**HEADER_OTB, "BlackElo": "2450"}, 2400))

    def test_tanpa_elo_dibuang_bukan_ditebak(self):
        h = dict(HEADER_OTB); del h["BlackElo"]
        self.assertFalse(lolos_mingguan(h, 2500))


class UjiEdisiBelum(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = koneksi(Path(self.tmp.name) / "uji.db")
        buat_skema(self.conn)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_tanpa_catatan_mulai_setelah_jangkar(self):
        self.assertEqual(edisi_belum(self.conn, EDISI_JANGKAR + 2), [EDISI_JANGKAR + 1, EDISI_JANGKAR + 2])

    def test_edisi_tercatat_dilewati(self):
        catat(self.conn, "twic", str(EDISI_JANGKAR + 1), "TWIC", "u", 7000, 90, 90)
        self.assertEqual(edisi_belum(self.conn, EDISI_JANGKAR + 3), [EDISI_JANGKAR + 2, EDISI_JANGKAR + 3])

    def test_sudah_terkini_kosong(self):
        catat(self.conn, "twic", str(EDISI_JANGKAR + 1), "TWIC", "u", 7000, 90, 90)
        self.assertEqual(edisi_belum(self.conn, EDISI_JANGKAR + 1), [])


if __name__ == "__main__":
    unittest.main()
