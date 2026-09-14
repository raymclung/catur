"""
Pengujian pencocokan nama yang depannya disingkat (B13).

PGN Mentor menulis 'Aronian,L', database punya 'Aronian, Levon'. Tanpa
pencocokan ini, impor 32 berkas PGN Mentor melahirkan 499 pemain ganda yang
menyangkut 50.673 kemunculan partai (diukur 11 Sep 2026).

Yang dijaga tes ini ada dua sisi: yang sama harus menyatu, dan yang berbeda
harus tetap terpisah.

Jalankan dari akar proyek:
    python -m unittest discover -s tests -v
"""

import unittest

from app.db import buat_skema, koneksi
from app.impor import dapatkan_atau_buat_pemain


class PencocokanInisial(unittest.TestCase):
    def setUp(self):
        self.conn = koneksi(":memory:")
        buat_skema(self.conn)

    def tearDown(self):
        self.conn.close()

    def jumlah_pemain(self):
        return self.conn.execute("SELECT COUNT(*) FROM pemain").fetchone()[0]

    # ---------- yang sama harus menyatu ----------

    def test_singkatan_menemukan_nama_lengkap(self):
        penuh = dapatkan_atau_buat_pemain(self.conn, "Aronian, Levon")
        singkat = dapatkan_atau_buat_pemain(self.conn, "Aronian,L")
        self.assertEqual(penuh, singkat)
        self.assertEqual(self.jumlah_pemain(), 1)

    def test_nama_lengkap_menemukan_singkatan(self):
        singkat = dapatkan_atau_buat_pemain(self.conn, "Giri,A")
        penuh = dapatkan_atau_buat_pemain(self.conn, "Giri, Anish")
        self.assertEqual(singkat, penuh)
        self.assertEqual(self.jumlah_pemain(), 1)

    def test_nama_belakang_dua_kata(self):
        penuh = dapatkan_atau_buat_pemain(self.conn, "Dominguez Perez, Leinier")
        singkat = dapatkan_atau_buat_pemain(self.conn, "Dominguez Perez,L")
        self.assertEqual(penuh, singkat)
        self.assertEqual(self.jumlah_pemain(), 1)

    def test_pemain_lama_ber_fide_id_tetap_dipakai(self):
        penuh = dapatkan_atau_buat_pemain(self.conn, "Nepomniachtchi, Ian",
                                          fide_id="4168119")
        singkat = dapatkan_atau_buat_pemain(self.conn, "Nepomniachtchi,I")
        self.assertEqual(penuh, singkat)
        self.assertEqual(self.jumlah_pemain(), 1)

    # ---------- yang berbeda harus tetap terpisah ----------

    def test_dua_calon_membuat_pemain_baru(self):
        """'Smith,J' bisa John atau Jane — jangan menebak."""
        dapatkan_atau_buat_pemain(self.conn, "Smith, John")
        dapatkan_atau_buat_pemain(self.conn, "Smith, Jane")
        ragu = dapatkan_atau_buat_pemain(self.conn, "Smith,J")
        self.assertEqual(self.jumlah_pemain(), 3)
        self.assertNotIn(ragu, (1, 2))

    def test_inisial_berbeda_tidak_cocok(self):
        levon = dapatkan_atau_buat_pemain(self.conn, "Aronian, Levon")
        sara = dapatkan_atau_buat_pemain(self.conn, "Aronian,S")
        self.assertNotEqual(levon, sara)
        self.assertEqual(self.jumlah_pemain(), 2)

    def test_dua_nama_lengkap_berbeda_tetap_dua_orang(self):
        a = dapatkan_atau_buat_pemain(self.conn, "Aronian, Levon")
        b = dapatkan_atau_buat_pemain(self.conn, "Aronian, Leonid")
        self.assertNotEqual(a, b)
        self.assertEqual(self.jumlah_pemain(), 2)

    def test_fide_id_berbeda_tetap_terpisah(self):
        """Carlsen, Magnus (1503014) bukan Carlsen,M (6015549)."""
        magnus = dapatkan_atau_buat_pemain(self.conn, "Carlsen, Magnus",
                                           fide_id="1503014")
        lain = dapatkan_atau_buat_pemain(self.conn, "Carlsen,M",
                                         fide_id="6015549")
        self.assertNotEqual(magnus, lain)
        self.assertEqual(self.jumlah_pemain(), 2)

    def test_username_tanpa_koma_tidak_ikut(self):
        """'magnus carlsen' urutannya terbalik; jangan disamakan dengan 'Carlsen,M'."""
        akun = dapatkan_atau_buat_pemain(self.conn, "magnus carlsen")
        gm = dapatkan_atau_buat_pemain(self.conn, "Carlsen,M")
        self.assertNotEqual(akun, gm)
        self.assertEqual(self.jumlah_pemain(), 2)

    def test_singkatan_dua_huruf_menemukan_nama_lengkap(self):
        """TWIC menulis 'Nakamura,Hi', bukan 'Nakamura,H'."""
        penuh = dapatkan_atau_buat_pemain(self.conn, "Nakamura, Hikaru")
        singkat = dapatkan_atau_buat_pemain(self.conn, "Nakamura,Hi")
        self.assertEqual(penuh, singkat)
        self.assertEqual(self.jumlah_pemain(), 1)

    def test_singkatan_tiga_huruf(self):
        penuh = dapatkan_atau_buat_pemain(self.conn, "Mamedov, Rauf")
        singkat = dapatkan_atau_buat_pemain(self.conn, "Mamedov,Rau")
        self.assertEqual(penuh, singkat)

    def test_singkatan_bukan_awalan_tidak_cocok(self):
        """'Ba' cocok untuk Baadur, tapi bukan untuk Boris."""
        boris = dapatkan_atau_buat_pemain(self.conn, "Jobava, Boris")
        ba = dapatkan_atau_buat_pemain(self.conn, "Jobava,Ba")
        self.assertNotEqual(boris, ba)

    def test_singkatan_bernomor_twic_tidak_ditebak(self):
        """'Vl3' membedakan dua orang senama; jangan dilepas angkanya."""
        penuh = dapatkan_atau_buat_pemain(self.conn, "Fedoseev, Vladimir")
        bernomor = dapatkan_atau_buat_pemain(self.conn, "Fedoseev,Vl3")
        self.assertNotEqual(penuh, bernomor)

    def test_dua_calon_dengan_awalan_sama_ditolak(self):
        """'Di' bisa Diego atau Dieter."""
        dapatkan_atau_buat_pemain(self.conn, "Flores, Diego")
        dapatkan_atau_buat_pemain(self.conn, "Flores, Dieter")
        ragu = dapatkan_atau_buat_pemain(self.conn, "Flores,Di")
        self.assertEqual(self.jumlah_pemain(), 3)

    def test_nama_satu_kata_tidak_ikut(self):
        a = dapatkan_atau_buat_pemain(self.conn, "Vaishali")
        b = dapatkan_atau_buat_pemain(self.conn, "Vaishali,R")
        self.assertNotEqual(a, b)


if __name__ == "__main__":
    unittest.main()
