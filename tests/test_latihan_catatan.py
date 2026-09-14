"""Uji catatan jawaban latihan (app/latihan_catatan.py), tanpa jaringan."""

import sys
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path

AKAR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(AKAR))

from app.db import buat_skema, koneksi
from app.latihan_catatan import (catatan_aktif, impor_catatan_lama, ringkasan,
                                 simpan_jawaban, ulang_semua)

FEN = "buka:rnbqkbnr/pppppppp/8/8/2P5/8/PP1PPPPP/RNBQKBNR b KQkq -"


def pada(tahun, bulan, hari):
    return datetime(tahun, bulan, hari, 12).astimezone().isoformat(timespec="seconds")


class UjiCatatan(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = koneksi(Path(self.tmp.name) / "uji.db")
        buat_skema(self.conn)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_status_dari_jawaban_terbaru(self):
        simpan_jawaban(self.conn, "kesalahan", "10:5", "salah", "e2e4", waktu=pada(2026, 9, 1))
        simpan_jawaban(self.conn, "kesalahan", "10:5", "benar", "d2d4", waktu=pada(2026, 9, 5))
        simpan_jawaban(self.conn, "pembukaan", FEN, "menyerah", waktu=pada(2026, 9, 6))
        c = catatan_aktif(self.conn)
        self.assertEqual(c["10:5"], {"h": "b", "w": "2026-09-05"})
        self.assertEqual(c[FEN], {"h": "s", "w": "2026-09-06"}, "menyerah dihitung salah")
        self.assertEqual(list(catatan_aktif(self.conn, "pembukaan")), [FEN])

    def test_ulang_semua_hanya_satu_mode_dan_riwayat_tetap(self):
        simpan_jawaban(self.conn, "kesalahan", "10:5", "benar")
        simpan_jawaban(self.conn, "pembukaan", FEN, "benar")
        self.assertEqual(ulang_semua(self.conn, "pembukaan"), 1)
        self.assertEqual(list(catatan_aktif(self.conn)), ["10:5"])
        self.assertEqual(ringkasan(self.conn)["pembukaan"]["dijawab"], 1)

    def test_impor_lama_sekali_saja(self):
        lama = {"10:5": "2026-09-01T10:00:00.000Z", "11:7": {"h": "s", "w": "2026-09-12"},
                FEN: {"h": "a", "w": "2026-09-13"}, "rusak": {"h": "x"}}
        self.assertEqual(impor_catatan_lama(self.conn, lama), 3)
        self.assertEqual(impor_catatan_lama(self.conn, lama), 0)
        c = catatan_aktif(self.conn)
        self.assertEqual(c["10:5"], {"h": "b", "w": "2026-09-01"})
        self.assertEqual(c["11:7"], {"h": "s", "w": "2026-09-12"})
        self.assertEqual(c[FEN]["h"], "a")

    def test_impor_tidak_menimpa_jawaban_baru(self):
        simpan_jawaban(self.conn, "kesalahan", "10:5", "salah")
        self.assertEqual(impor_catatan_lama(self.conn, {"10:5": {"h": "b", "w": "2026-01-01"}}), 0)
        self.assertEqual(catatan_aktif(self.conn)["10:5"]["h"], "s")

    def test_ringkasan(self):
        hari = date(2026, 9, 13)   # Minggu; minggu ini mulai Senin 7 Sep
        simpan_jawaban(self.conn, "pembukaan", "buka:a", "benar", kelompok="English", waktu=pada(2026, 9, 8))
        simpan_jawaban(self.conn, "pembukaan", "buka:b", "salah", kelompok="English", waktu=pada(2026, 9, 12))
        simpan_jawaban(self.conn, "pembukaan", "buka:c", "salah", kelompok="Sicilian", waktu=pada(2026, 9, 1))
        simpan_jawaban(self.conn, "pembukaan", "buka:d", "pilihan", kelompok="Sicilian", waktu=pada(2026, 9, 12))
        simpan_jawaban(self.conn, "pembukaan", "buka:e", "menyerah", kelompok="English", waktu=pada(2026, 9, 13))
        r = ringkasan(self.conn, hari_ini=hari)["pembukaan"]
        self.assertEqual((r["selesai"], r["menunggu"], r["siap_ulang"], r["pilihan"]), (1, 2, 1, 1))
        self.assertEqual((r["dijawab"], r["benar"], r["persen_benar"]), (4, 1, 25.0))
        self.assertEqual(r["per_minggu"][-1], {"mulai": "2026-09-07", "dijawab": 3, "benar": 1, "persen_benar": 33.3})
        self.assertEqual(r["per_minggu"][-2]["dijawab"], 1)
        self.assertEqual([(k["kelompok"], k["salah"]) for k in r["per_kelompok"]],
                         [("English", 2), ("Sicilian", 1)])
        self.assertEqual(ringkasan(self.conn, hari_ini=hari)["kesalahan"]["dijawab"], 0)

    def test_nilai_tidak_dikenal_ditolak_database(self):
        with self.assertRaises(ValueError):
            simpan_jawaban(self.conn, "kesalahan", "1:1", "mungkin")


if __name__ == "__main__":
    unittest.main()
