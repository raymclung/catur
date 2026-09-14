"""
Uji app/analisis_db.py tanpa Stockfish: LaporanPartai dibuat manual.

Yang dipastikan: simpan -> baca kembali sama persis, simpan ulang mengganti
(bukan menambah), hapus bersih, dan foreign key ke partai ditegakkan.
"""

import sys
import tempfile
import unittest
from pathlib import Path

AKAR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(AKAR))
sys.path.insert(0, str(AKAR / "src"))

from app.db import buat_skema, koneksi, sekarang
from app.analisis_db import (ambil_analisis, hapus_analisis, mat_dari_sudut_pemain,
                             simpan_laporan, statistik_analisis, sudah_dianalisis,
                             teks_skor)
from catur_analysis.partai import LangkahAnalisis, LaporanPartai, RingkasanPemain


def laporan_contoh(depth=12):
    lap = LaporanPartai(
        event="uji", site="-", tanggal="2026.01.01", babak="-",
        putih="A", hitam="B", hasil="1-0", eco="C20", pembukaan="uji",
        elo_putih="2000", elo_hitam="2000", kontrol_waktu="600", depth=depth,
    )
    lap.langkah = [
        LangkahAnalisis(ply=0, nomor_langkah=1, warna="putih", san="e4", uci="e2e4",
                        fen_sebelum="fen0", eval_sebelum_cp=30, eval_sesudah_cp=35,
                        kehilangan_cp=0, langkah_terbaik_san="e4",
                        langkah_terbaik_uci="e2e4", variasi_terbaik=["e4", "e5"],
                        klasifikasi="terbaik"),
        LangkahAnalisis(ply=1, nomor_langkah=1, warna="hitam", san="f6", uci="f7f6",
                        fen_sebelum="fen1", eval_sebelum_cp=-35, eval_sesudah_cp=-400,
                        kehilangan_cp=365, langkah_terbaik_san="e5",
                        langkah_terbaik_uci="e7e5", variasi_terbaik=[],
                        klasifikasi="blunder", mat_sesudah=None),
    ]
    lap.ringkasan_putih = RingkasanPemain("A", "putih", 95.0, 0.0, 1)
    lap.ringkasan_hitam = RingkasanPemain("B", "hitam", 40.0, 365.0, 1, blunder=1)
    lap.turning_point_ply = 1
    return lap


class UjiSudutPandangMat(unittest.TestCase):
    """
    mat_sesudah bertanda dari sudut pandang PUTIH, sedangkan eval_sesudah_cp
    dari sudut pemain yang jalan. Mencampurnya sudah pernah membuat halaman
    Kekalahan menulis "mat dalam 2 untuk saya" pada partai yang justru kena
    mat (13 Sep 2026), jadi turunannya diuji.
    """

    def test_putih_tetap_apa_adanya(self):
        self.assertEqual(mat_dari_sudut_pemain(3, "putih"), 3)
        self.assertEqual(mat_dari_sudut_pemain(-3, "putih"), -3)

    def test_hitam_dibalik(self):
        # mat 2 untuk Putih, dilihat dari Hitam yang baru jalan = kena mat 2
        self.assertEqual(mat_dari_sudut_pemain(2, "hitam"), -2)
        self.assertEqual(mat_dari_sudut_pemain(-2, "hitam"), 2)

    def test_kosong_dan_nol(self):
        self.assertIsNone(mat_dari_sudut_pemain(None, "putih"))
        self.assertIsNone(mat_dari_sudut_pemain(None, "hitam"))
        # 0 = mat sudah terjadi; tandanya tidak berarti apa-apa
        self.assertEqual(mat_dari_sudut_pemain(0, "hitam"), 0)


class UjiTeksSkor(unittest.TestCase):
    """
    Ray, 13 Sep: "kalau mate pake mate in berapa jangan +1000", lalu "ya pake format M4". Engine
    menyimpan mat sebagai +/-100000 cp, yang tampil apa adanya jadi "+1000,00".
    """

    def test_mat_ditulis_sebagai_mat(self):
        self.assertEqual(teks_skor(100_000, 3), "M3")
        self.assertEqual(teks_skor(-100_000, -2), "-M2")
        self.assertEqual(teks_skor(100_000, 0), "#")

    def test_skor_biasa_koma_desimal(self):
        self.assertEqual(teks_skor(451, None), "+4,51")
        self.assertEqual(teks_skor(-451, None), "-4,51")
        self.assertEqual(teks_skor(0, None), "0,00")

    def test_skor_mat_tanpa_jarak_tetap_bukan_seribu(self):
        self.assertEqual(teks_skor(100_000, None), "M")
        self.assertEqual(teks_skor(-100_000, None), "-M")
        self.assertNotIn("1000", teks_skor(100_000, None))

    def test_kosong(self):
        self.assertEqual(teks_skor(None, None), "?")


class UjiAnalisisDb(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = koneksi(Path(self.tmp.name) / "uji.db")
        buat_skema(self.conn)
        t = sekarang()
        c = self.conn
        c.execute("INSERT INTO pemain(id,nama,nama_normal,dibuat_pada) VALUES(1,'A','a',?)", (t,))
        c.execute("INSERT INTO pemain(id,nama,nama_normal,dibuat_pada) VALUES(2,'B','b',?)", (t,))
        c.execute(
            "INSERT INTO partai(id,putih_id,hitam_id,hasil,pgn,sumber,hash_unik,diimpor_pada) "
            "VALUES(10,1,2,'1-0','x','uji','h10',?)", (t,))
        c.commit()

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_baca_menyertakan_mat_dari_sudut_pemain(self):
        lap = laporan_contoh()
        lap.langkah[1].mat_sesudah = 2          # Putih mat dalam 2
        simpan_laporan(self.conn, 10, lap, "Stockfish uji", "hemat", 1.0)
        langkah = ambil_analisis(self.conn, 10)["langkah"]
        self.assertEqual(langkah[1]["warna"], "hitam")
        self.assertEqual(langkah[1]["mat_sesudah"], 2)          # mentah: sudut Putih
        self.assertEqual(langkah[1]["mat_sesudah_pemain"], -2)  # Hitam yang kena mat
        self.assertIsNone(langkah[0]["mat_sesudah_pemain"])     # tanpa mat tetap None

    def test_mat_sebelum_langkah_diambil_dari_langkah_sebelumnya(self):
        """
        Kasus Ray: 'unggul (+1000,00) -> kalah (-4,51)'. Posisi SEBELUM langkah
        ke-2 adalah posisi sesudah langkah ke-1, jadi mat-nya dari baris itu.
        """
        lap = laporan_contoh()
        lap.langkah[0].mat_sesudah = -3        # sesudah langkah Putih: HITAM mat dalam 3
        lap.langkah[1].eval_sebelum_cp = 100_000
        lap.langkah[1].eval_sesudah_cp = -451
        lap.langkah[1].mat_sesudah = None      # Hitam membuang matnya
        simpan_laporan(self.conn, 10, lap, "Stockfish uji", "hemat", 1.0)
        l = ambil_analisis(self.conn, 10)["langkah"]
        self.assertEqual(l[1]["warna"], "hitam")
        self.assertEqual(l[1]["mat_sebelum_pemain"], 3)       # dari sudut Hitam: dia yang memberi mat
        self.assertEqual(l[1]["skor_sebelum_teks"], "M3")
        self.assertEqual(l[1]["skor_sesudah_teks"], "-4,51")
        self.assertIsNone(l[0]["mat_sebelum_pemain"])         # langkah pertama tidak punya sebelum

    def test_simpan_lalu_baca_sama(self):
        lap = laporan_contoh()
        self.assertFalse(sudah_dianalisis(self.conn, 10))
        simpan_laporan(self.conn, 10, lap, "Stockfish uji", "hemat", 1.5)
        self.assertTrue(sudah_dianalisis(self.conn, 10))

        h = ambil_analisis(self.conn, 10)
        self.assertEqual(h["depth"], 12)
        self.assertEqual(h["engine"], "Stockfish uji")
        self.assertEqual(h["akurasi_hitam"], 40.0)
        self.assertEqual(h["blunder_hitam"], 1)
        self.assertEqual(h["turning_point_ply"], 1)
        self.assertEqual(len(h["langkah"]), 2)
        self.assertEqual(h["langkah"][1]["klasifikasi"], "blunder")
        self.assertEqual(h["langkah"][1]["kehilangan_cp"], 365)
        self.assertEqual(h["langkah"][0]["variasi_terbaik"], ["e4", "e5"])
        self.assertEqual(h["langkah"][1]["variasi_terbaik"], [])
        # sudut pandang Putih: langkah hitam -400 dari sisi hitam = +400 putih
        self.assertEqual(h["langkah"][1]["eval_putih_cp"], 400)

    def test_simpan_ulang_mengganti_bukan_menambah(self):
        simpan_laporan(self.conn, 10, laporan_contoh(depth=12), "sf", "hemat")
        simpan_laporan(self.conn, 10, laporan_contoh(depth=20), "sf", "normal")
        n = self.conn.execute("SELECT COUNT(*) FROM analisis").fetchone()[0]
        m = self.conn.execute("SELECT COUNT(*) FROM analisis_langkah").fetchone()[0]
        self.assertEqual((n, m), (1, 2))
        self.assertEqual(ambil_analisis(self.conn, 10)["depth"], 20)

    def test_belum_dianalisis_kembalikan_none(self):
        self.assertIsNone(ambil_analisis(self.conn, 10))

    def test_hapus(self):
        simpan_laporan(self.conn, 10, laporan_contoh(), "sf", "hemat")
        self.assertTrue(hapus_analisis(self.conn, 10))
        self.assertFalse(hapus_analisis(self.conn, 10))
        self.assertEqual(statistik_analisis(self.conn)["langkah_dianalisis"], 0)

    def test_partai_tidak_ada_ditolak(self):
        import sqlite3
        with self.assertRaises(sqlite3.IntegrityError):
            simpan_laporan(self.conn, 999, laporan_contoh(), "sf", "hemat")
        # gagal = tidak ada sisa setengah jadi
        self.assertEqual(statistik_analisis(self.conn)["partai_dianalisis"], 0)


if __name__ == "__main__":
    unittest.main()
