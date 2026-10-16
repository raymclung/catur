"""
Tes `app.db.migrasi_saat_start`: migrasi skema dari start server.

16 Okt 2026: server dulu menjalankan buat_skema() tiap start, jadi restart
oleh siapa pun memasang perubahan skema tanpa kunci penulis (trigger v14
terpasang 26 detik sesudah app/db.py disimpan, belum di-commit). Sekarang:
tidak disentuh kalau sudah terkini, dimigrasi di bawah kunci kalau perlu,
ditunda (server tetap jalan) kalau kunci dipegang penulis lain.
"""

import sys
import tempfile
import unittest
from pathlib import Path

AKAR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(AKAR))

from app.db import VERSI_SKEMA, buat_skema, koneksi, migrasi_saat_start, versi_skema
from app.kunci_penulis import KunciPenulis


class MigrasiSaatStart(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = Path(self.tmp.name)
        self.conn = koneksi(d / "uji.db")
        # Kunci di folder sementara, bukan data/.penulis.lock yang asli.
        self.berkas_kunci = d / ".penulis.lock"
        self.berkas_info = d / ".penulis.info"

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def kunci(self):
        return KunciPenulis("uji", path=self.berkas_kunci, path_info=self.berkas_info)

    def test_database_baru_dimigrasi_di_bawah_kunci(self):
        hasil = migrasi_saat_start(self.conn, kunci=self.kunci())
        self.assertIn("dimigrasi", hasil)
        self.assertEqual(versi_skema(self.conn), VERSI_SKEMA)
        self.assertFalse(self.berkas_info.exists(), "kunci harus dilepas sesudahnya")

    def test_skema_terkini_tidak_disentuh(self):
        buat_skema(self.conn)
        self.conn.execute("DROP INDEX idx_partai_tanggal_urut")   # penanda: tidak dibuat ulang
        hasil = migrasi_saat_start(self.conn, kunci=self.kunci())
        self.assertIn("tidak disentuh", hasil)
        ada = self.conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE name = "
                                "'idx_partai_tanggal_urut'").fetchone()[0]
        self.assertEqual(ada, 0)

    def test_kunci_dipegang_migrasi_ditunda_server_tetap_jalan(self):
        buat_skema(self.conn)
        self.conn.execute(f"PRAGMA user_version = {VERSI_SKEMA - 1}")
        pemegang = self.kunci().ambil()            # penulis besar lain sedang jalan
        try:
            hasil = migrasi_saat_start(self.conn, kunci=self.kunci())
        finally:
            pemegang.lepas()
        self.assertIn("DITUNDA", hasil)
        self.assertEqual(versi_skema(self.conn), VERSI_SKEMA - 1, "skema tidak boleh disentuh")
        # Start berikutnya, kunci bebas: migrasi berjalan.
        self.assertIn("dimigrasi", migrasi_saat_start(self.conn, kunci=self.kunci()))
        self.assertEqual(versi_skema(self.conn), VERSI_SKEMA)


class ServerMemakainya(unittest.TestCase):
    def test_lifespan_tidak_lagi_memanggil_buat_skema_langsung(self):
        import inspect
        from app import main
        sumber = inspect.getsource(main.daur_hidup)
        self.assertIn("migrasi_saat_start", sumber)
        self.assertNotIn("buat_skema(", sumber)


if __name__ == "__main__":
    unittest.main()
