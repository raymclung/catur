"""Uji rencana latihan mingguan (app/rencana.py), tanpa jaringan."""

import sys
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path

AKAR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(AKAR))

from app.db import buat_skema, koneksi
from app.latihan_catatan import simpan_jawaban
from app.rencana import kelemahan, kemajuan_latihan


def pada(tahun, bulan, hari):
    return datetime(tahun, bulan, hari, 12).astimezone().isoformat(timespec="seconds")


def fase(nama, langkah, blunder):
    return {"fase": nama, "langkah": langkah, "blunder": blunder, "mistake": 0}


def kelompok(nama, partai, blunder_per_partai=None, skor=None):
    return {"nama": nama, "partai": partai, "blunder_per_partai": blunder_per_partai,
            "skor_persen": skor}


class Kelemahan(unittest.TestCase):
    def test_fase_format_pembukaan_terpilih_dengan_angkanya(self):
        r = {
            "per_fase": [fase("pembukaan", 5000, 50), fase("tengah", 5000, 100),
                         fase("akhir", 1000, 80)],
            "per_format": [kelompok("bullet", 300, 1.7), kelompok("blitz", 300, 1.3)],
            "per_pembukaan": [kelompok("Sicilian", 100, skor=52.0),
                              kelompok("King's Indian", 60, skor=28.0)],
        }
        hasil = kelemahan(r)
        jenis = [k["jenis"] for k in hasil]
        self.assertEqual(sorted(jenis), ["fase", "format", "pembukaan"])
        fase_item = next(k for k in hasil if k["jenis"] == "fase")
        self.assertIn("akhir", fase_item["judul"])
        # 80 blunder / 1000 langkah = 8; angka ditulis gaya Indonesia (koma desimal)
        self.assertIn("8 blunder per 100 langkah", fase_item["angka"])
        format_item = next(k for k in hasil if k["jenis"] == "format")
        self.assertIn("1,7 blunder per partai", format_item["angka"])
        self.assertEqual(hasil, sorted(hasil, key=lambda k: -k["selisih"]), "urut selisih terbesar")

    def test_paling_banyak_tiga(self):
        r = {
            "per_fase": [fase("pembukaan", 5000, 10), fase("akhir", 1000, 200)],
            "per_format": [kelompok("bullet", 300, 3.0), kelompok("blitz", 300, 0.5)],
            "per_pembukaan": [kelompok("A", 100, skor=60.0), kelompok("B", 100, skor=10.0)],
        }
        self.assertEqual(len(kelemahan(r)), 3)

    def test_sampel_kecil_diabaikan(self):
        r = {
            "per_fase": [fase("pembukaan", 100, 1), fase("akhir", 50, 40)],
            "per_format": [kelompok("bullet", 5, 3.0), kelompok("blitz", 5, 0.1)],
            "per_pembukaan": [kelompok("A", 10, skor=60.0), kelompok("B", 10, skor=5.0)],
        }
        self.assertEqual(kelemahan(r), [], "semua kelompok di bawah batas sampel")

    def test_selisih_kecil_tidak_dilaporkan(self):
        r = {
            "per_fase": [fase("pembukaan", 5000, 100), fase("akhir", 5000, 105)],
            "per_format": [kelompok("bullet", 300, 1.40), kelompok("blitz", 300, 1.35)],
            "per_pembukaan": [kelompok("A", 100, skor=50.0), kelompok("B", 100, skor=48.0)],
        }
        self.assertEqual(kelemahan(r), [], "jangan mengarang kelemahan kalau merata")

    def test_tanpa_data_aman(self):
        self.assertEqual(kelemahan({}), [])


class KemajuanLatihan(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = koneksi(Path(self.tmp.name) / "uji.db")
        buat_skema(self.conn)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_minggu_ini_dan_minggu_lalu_dipisah(self):
        hari = date(2026, 9, 16)                      # Rabu; minggu ini mulai Senin 14 Sep
        simpan_jawaban(self.conn, "pembukaan", "buka:a", "benar", waktu=pada(2026, 9, 15))
        simpan_jawaban(self.conn, "pembukaan", "buka:b", "salah", waktu=pada(2026, 9, 16))
        simpan_jawaban(self.conn, "pembukaan", "buka:c", "benar", waktu=pada(2026, 9, 10))
        simpan_jawaban(self.conn, "kesalahan", "1:2", "benar", waktu=pada(2026, 9, 15))
        simpan_jawaban(self.conn, "pembukaan", "buka:d", "pilihan", waktu=pada(2026, 9, 15))
        simpan_jawaban(self.conn, "pembukaan", "buka:e", "benar", waktu=pada(2026, 8, 1))
        k = kemajuan_latihan(self.conn, hari)
        self.assertEqual(k["pembukaan"]["minggu_ini"], {"dijawab": 2, "benar": 1})
        self.assertEqual(k["pembukaan"]["minggu_lalu"], {"dijawab": 1, "benar": 1})
        self.assertEqual(k["kesalahan"]["minggu_ini"], {"dijawab": 1, "benar": 1})
        self.assertEqual(k["kesalahan"]["minggu_lalu"], {"dijawab": 0, "benar": 0},
                         "yang bulan lalu tidak ikut terhitung")


if __name__ == "__main__":
    unittest.main()
