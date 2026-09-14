"""Uji cadangkan_db.py pada database kecil sementara (bukan catur.db asli)."""

import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

AKAR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(AKAR))

from app.db import buat_skema, koneksi, sekarang
from cadangkan_db import cadangkan, daftar_cadangan


class UjiCadangan(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.akar = Path(self.tmp.name)
        self.db = self.akar / "uji.db"
        self.folder = self.akar / "backup"
        conn = koneksi(self.db)
        buat_skema(conn)
        conn.execute("INSERT INTO pemain(nama, nama_normal, dibuat_pada) VALUES (?,?,?)",
                     ("Carlsen, Magnus", "carlsen magnus", sekarang()))
        conn.commit()
        self.conn = conn      # dibiarkan terbuka: meniru server yang sedang hidup

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_cadangan_berisi_data_yang_sama(self):
        tujuan = cadangkan(self.db, self.folder, simpan=5)
        self.assertTrue(tujuan.is_file())
        c = sqlite3.connect(tujuan)
        self.assertEqual(c.execute("SELECT nama FROM pemain").fetchone()[0], "Carlsen, Magnus")
        self.assertEqual(c.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        c.close()

    def test_tidak_ada_berkas_part_tersisa(self):
        cadangkan(self.db, self.folder)
        self.assertEqual(list(self.folder.glob("*.part")), [])

    def test_rotasi_menyisakan_n_terbaru(self):
        for i in range(4):
            b = self.folder / f"catur-2026010{i + 1}-000000.db"
            self.folder.mkdir(exist_ok=True)
            sqlite3.connect(b).close()       # berkas lama tiruan
        cadangkan(self.db, self.folder, simpan=3)
        sisa = [b.name for b in daftar_cadangan(self.folder)]
        self.assertEqual(len(sisa), 3)
        self.assertNotIn("catur-20260101-000000.db", sisa)
        self.assertNotIn("catur-20260102-000000.db", sisa)

    def test_database_tidak_ada_ditolak(self):
        with self.assertRaises(FileNotFoundError):
            cadangkan(self.akar / "tidak-ada.db", self.folder)


if __name__ == "__main__":
    unittest.main()
