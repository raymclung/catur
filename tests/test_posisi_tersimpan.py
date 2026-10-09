"""Uji posisi tersimpan (skema v5, permintaan frontend #6)."""

import sys
import tempfile
import unittest
from pathlib import Path

AKAR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(AKAR))

from app.db import buat_skema, koneksi, sekarang
from app.posisi_tersimpan import ambil, daftar, hapus, simpan

AWAL = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"


class UjiPosisiTersimpan(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = koneksi(Path(self.tmp.name) / "uji.db")
        buat_skema(self.conn)
        t = sekarang()
        self.conn.execute("INSERT INTO pemain(id,nama,nama_normal,dibuat_pada) VALUES(1,'A','a',?)", (t,))
        self.conn.execute("INSERT INTO pemain(id,nama,nama_normal,dibuat_pada) VALUES(2,'B','b',?)", (t,))
        self.conn.execute(
            "INSERT INTO partai(id,putih_id,hitam_id,hasil,pgn,sumber,hash_unik,diimpor_pada) "
            "VALUES(10,1,2,'1-0','x','uji','h10',?)", (t,))
        self.conn.commit()

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_simpan_lalu_daftar_terbaru_dulu(self):
        a = simpan(self.conn, AWAL, judul="awal", asal="jelajah")
        b = simpan(self.conn, AWAL, judul="kedua")
        self.assertEqual([p["id"] for p in daftar(self.conn)], [b["id"], a["id"]])
        self.assertEqual(a["judul"], "awal")
        self.assertEqual(a["asal"], "jelajah")
        self.assertIsNone(a["partai_id"])
        self.assertTrue(a["dibuat_pada"])

    def test_fen_dinormalisasi_dan_divalidasi(self):
        p = simpan(self.conn, "  " + AWAL.replace(" 0 1", " 3 7") + " ")
        self.assertEqual(p["fen"], AWAL.replace(" 0 1", " 3 7"))
        with self.assertRaises(ValueError):
            simpan(self.conn, "bukan fen")
        with self.assertRaises(ValueError):
            simpan(self.conn, "8/8/8/8/8/8/8/8 w - - 0 1")     # tanpa raja
        self.assertEqual(len(daftar(self.conn)), 1)

    def test_partai_id_diperiksa(self):
        p = simpan(self.conn, AWAL, partai_id=10)
        self.assertEqual(p["partai_id"], 10)
        with self.assertRaises(ValueError):
            simpan(self.conn, AWAL, partai_id=999)

    def test_hapus_partai_melepas_tautan_bukan_menghapus_posisi(self):
        p = simpan(self.conn, AWAL, partai_id=10, judul="dari partai")
        self.conn.execute("DELETE FROM partai WHERE id = 10")
        self.conn.commit()
        lagi = ambil(self.conn, p["id"])
        self.assertIsNotNone(lagi)
        self.assertIsNone(lagi["partai_id"])

    def test_hapus(self):
        p = simpan(self.conn, AWAL)
        self.assertTrue(hapus(self.conn, p["id"]))
        self.assertFalse(hapus(self.conn, p["id"]))
        self.assertIsNone(ambil(self.conn, p["id"]))

    def test_judul_dipotong_dan_dirapikan(self):
        p = simpan(self.conn, AWAL, judul="  " + "x" * 300 + "  ", catatan="  c  ")
        self.assertEqual(len(p["judul"]), 200)
        self.assertEqual(p["catatan"], "c")


if __name__ == "__main__":
    unittest.main()


class UjiBatasMasukan(unittest.TestCase):
    """Audit 28 Sep 2026: judul 5 MB diterima, halfmove 20 digit tersimpan."""

    def test_hitungan_langkah_mustahil_ditolak(self):
        from app.posisi_tersimpan import _fen_sah
        with self.assertRaises(ValueError):
            _fen_sah("4k3/8/8/8/8/8/8/4K3 w - - 99999999999999999999 1")
        with self.assertRaises(ValueError):
            _fen_sah("4k3/8/8/8/8/8/8/4K3 w - - 0 5000")
        self.assertTrue(_fen_sah("4k3/8/8/8/8/8/8/4K3 w - - 100 80"))

    def test_judul_terlalu_panjang_422(self):
        from pydantic import ValidationError
        from app.main import PosisiBaru
        with self.assertRaises(ValidationError):
            PosisiBaru(fen="4k3/8/8/8/8/8/8/4K3 w - - 0 1", judul="x" * 201)
