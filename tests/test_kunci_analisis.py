"""simpan_laporan harus bertahan saat database sedang dikunci penulis lain."""

import shutil
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

AKAR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(AKAR))

import app.analisis_db as analisis_db
from app.analisis_db import ambil_analisis, simpan_laporan
from app.db import buat_skema, koneksi, sekarang


class LangkahPalsu:
    ply = 0; nomor_langkah = 1; warna = "putih"; san = "e4"; uci = "e2e4"
    eval_sebelum_cp = 20; eval_sesudah_cp = 25; eval_putih_cp = 25; mat_sesudah = None
    kehilangan_cp = 0; klasifikasi = "baik"
    langkah_terbaik_san = "e4"; langkah_terbaik_uci = "e2e4"; variasi_terbaik = ["e7e5"]


class RingkasanPalsu:
    akurasi = 90.0; blunder = 0; mistake = 0; inaccuracy = 1


class LaporanPalsu:
    depth = 16
    turning_point_ply = None
    ringkasan_putih = RingkasanPalsu()
    ringkasan_hitam = RingkasanPalsu()
    langkah = [LangkahPalsu()]


class UjiKunci(unittest.TestCase):
    def setUp(self):
        # mkdtemp + rmtree(ignore_errors): di Windows berkas WAL kadang masih
        # dipegang saat tes selesai, dan TemporaryDirectory akan melempar.
        self.tmp = tempfile.mkdtemp()
        self.path = Path(self.tmp) / "uji.db"
        self.conn = koneksi(self.path)
        buat_skema(self.conn)
        self.conn.execute("INSERT INTO pemain(id,nama,nama_normal,dibuat_pada) VALUES(1,'A','a',?)", (sekarang(),))
        self.conn.execute("INSERT INTO pemain(id,nama,nama_normal,dibuat_pada) VALUES(2,'B','b',?)", (sekarang(),))
        self.conn.execute(
            "INSERT INTO partai(id,putih_id,hitam_id,hasil,jumlah_langkah,pgn,sumber,hash_unik,diimpor_pada) "
            "VALUES(1,1,2,'1-0',1,'x','file','h',?)", (sekarang(),))
        self.conn.commit()
        self.asli_jeda = analisis_db.JEDA_AWAL
        analisis_db.JEDA_AWAL = 0.3          # tes tidak perlu menunggu 2 detik

    def tearDown(self):
        analisis_db.JEDA_AWAL = self.asli_jeda
        self.conn.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_menunggu_penulis_lain_lalu_berhasil(self):
        """Penulis lain memegang kunci 1,2 detik; simpan_laporan harus sabar."""
        lain = sqlite3.connect(self.path, timeout=0.1, check_same_thread=False)
        lain.execute("BEGIN EXCLUSIVE")
        lain.execute("UPDATE partai SET hasil = '0-1' WHERE id = 1")

        def lepas():
            time.sleep(1.2)
            lain.rollback()
            lain.close()
        threading.Thread(target=lepas, daemon=True).start()

        pesan = []
        self.conn.execute("PRAGMA busy_timeout = 200")   # paksa jalur coba-ulang
        simpan_laporan(self.conn, 1, LaporanPalsu(), "sf-uji", "hemat", 1.0,
                       lapor=pesan.append)
        self.assertIsNotNone(ambil_analisis(self.conn, 1))
        self.assertTrue(pesan, "seharusnya melaporkan percobaan ulang")
        self.assertIn("terkunci", pesan[0])

    def test_menyerah_dengan_error_jelas_kalau_tidak_pernah_lepas(self):
        lain = sqlite3.connect(self.path, timeout=0.1)
        lain.execute("BEGIN EXCLUSIVE")
        lain.execute("UPDATE partai SET hasil = '0-1' WHERE id = 1")
        self.conn.execute("PRAGMA busy_timeout = 100")
        analisis_db.ULANG_MAKS = 2
        try:
            with self.assertRaises(sqlite3.OperationalError):
                simpan_laporan(self.conn, 1, LaporanPalsu(), "sf-uji", "hemat", 1.0)
        finally:
            analisis_db.ULANG_MAKS = 6
            lain.rollback(); lain.close()
        self.assertIsNone(ambil_analisis(self.conn, 1))


class UjiTungguPenulisBesar(unittest.TestCase):
    """
    Batch analisis tidak boleh MEMEGANG kunci penulis (tiga batch sekaligus
    diizinkan), tapi juga tidak boleh mulai di tengah impor besar — itu yang
    membunuh batch B11 di partai ke-70 pada 11 Sep 2026.
    """

    def setUp(self):
        import app.kunci_penulis as kp
        self.kp = kp
        self.asli = (kp.status, kp.penulis_berjalan)

    def tearDown(self):
        self.kp.status, self.kp.penulis_berjalan = self.asli

    def test_langsung_jalan_kalau_bebas(self):
        self.kp.status = lambda *a, **k: (True, "")
        self.kp.penulis_berjalan = lambda: []
        tidur = []
        self.assertTrue(self.kp.tunggu_sampai_bebas(lapor=lambda *_: None,
                                                    _tidur=tidur.append))
        self.assertEqual(tidur, [], "tidak boleh tidur kalau sudah bebas")

    def test_menunggu_sampai_impor_selesai(self):
        sisa = [2]                       # dua putaran masih sibuk, lalu bebas
        def status_palsu(*a, **k):
            return (sisa[0] <= 0), ("impor_pgn.py pgnmentor" if sisa[0] > 0 else "")
        self.kp.status = status_palsu
        self.kp.penulis_berjalan = lambda: []
        pesan = []
        def tidur(_):
            sisa[0] -= 1
        self.assertTrue(self.kp.tunggu_sampai_bebas(lapor=pesan.append, _tidur=tidur))
        self.assertEqual(sisa[0], 0)
        self.assertTrue(any("Menunggu penulis besar" in m for m in pesan))
        self.assertTrue(any("dilanjutkan" in m for m in pesan))

    def test_proses_tanpa_kunci_tetap_terdeteksi(self):
        # Skrip lama yang belum mengambil kunci hanya terlihat di daftar proses.
        self.kp.status = lambda *a, **k: (True, "")
        sisa = [1]
        self.kp.penulis_berjalan = lambda: (["impor_twic.py 920-1505"] if sisa[0] > 0 else [])
        pesan = []
        self.assertTrue(self.kp.tunggu_sampai_bebas(
            lapor=pesan.append, _tidur=lambda _: sisa.__setitem__(0, 0)))
        self.assertTrue(any("impor_twic" in m for m in pesan))

    def test_batas_waktu_menyerah(self):
        self.kp.status = lambda *a, **k: (False, "impor")
        self.kp.penulis_berjalan = lambda: []
        self.assertFalse(self.kp.tunggu_sampai_bebas(
            lapor=lambda *_: None, jeda=0, batas=0, _tidur=lambda _: None))


if __name__ == "__main__":
    unittest.main()
