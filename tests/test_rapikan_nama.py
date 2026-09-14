"""
Pengujian perapian nama pemain dari FIDE ID (tanpa menyentuh jaringan).

Yang diuji hanya pengambilan keputusannya: kapan sebuah nama boleh diganti
dan kapan harus ditolak. Pengambilan data dari API Lichess tidak diuji di
sini — yang penting, keputusan di sekitarnya tidak pernah menukar identitas
orang.

Jalankan dari akar proyek:
    python -m unittest discover -s tests -v
"""

import unittest

from rapikan_nama import nama_belakang_dari, perlu_dirapikan, putuskan


class PerluDirapikan(unittest.TestCase):
    def test_nama_singkat_dikenali(self):
        for n in ("Caruana,F", "Nakamura,Hi", "Fedoseev,Vl3", "Carlsen,M.", "So,W"):
            self.assertTrue(perlu_dirapikan(n), n)

    def test_nama_lengkap_dilewati(self):
        for n in ("Carlsen, Magnus", "Anand, Viswanathan", "Dominguez Perez, Leinier"):
            self.assertFalse(perlu_dirapikan(n), n)

    def test_username_tanpa_koma_dilewati(self):
        for n in ("McLung_07", "rayylung", "Wei Yi", "Ding Liren"):
            self.assertFalse(perlu_dirapikan(n), n)


class NamaBelakang(unittest.TestCase):
    def test_diambil_sebelum_koma(self):
        self.assertEqual(nama_belakang_dari("Caruana,F"), "caruana")
        self.assertEqual(nama_belakang_dari("Caruana, Fabiano"), "caruana")

    def test_nama_belakang_dua_kata(self):
        self.assertEqual(nama_belakang_dari("Dominguez Perez, Leinier"), "dominguez")


class Putuskan(unittest.TestCase):
    def test_nama_lengkap_diterima(self):
        baru, alasan = putuskan("Caruana,F", {"name": "Caruana, Fabiano"})
        self.assertEqual(baru, "Caruana, Fabiano")
        self.assertIsNone(alasan)

    def test_nama_belakang_berbeda_ditolak(self):
        """FIDE ID keliru di sumber: jangan sampai pemain berganti identitas."""
        baru, alasan = putuskan("Carlsen,M", {"name": "Hansen, Torbjorn"})
        self.assertIsNone(baru)
        self.assertIn("nama belakang berbeda", alasan)

    def test_bentrok_dengan_pemain_lain_ditolak(self):
        baru, alasan = putuskan("Aronian,L", {"name": "Aronian, Levon"},
                                cari_bentrok=lambda n: "id 7 ('Aronian, Levon')")
        self.assertIsNone(baru)
        self.assertIn("bentrok", alasan)

    def test_tanpa_bentrok_diterima(self):
        baru, alasan = putuskan("Aronian,L", {"name": "Aronian, Levon"},
                                cari_bentrok=lambda n: None)
        self.assertEqual(baru, "Aronian, Levon")
        self.assertIsNone(alasan)

    def test_sudah_sama_bukan_perubahan(self):
        baru, alasan = putuskan("Carlsen, Magnus", {"name": "Carlsen, Magnus"})
        self.assertIsNone(baru)
        self.assertEqual(alasan, "sudah sama")

    def test_tidak_ada_di_fide(self):
        self.assertEqual(putuskan("Caruana,F", None)[1], "tidak ada di FIDE")
        self.assertEqual(putuskan("Caruana,F", {})[1], "tidak ada di FIDE")

    def test_nama_kosong_ditolak(self):
        self.assertIsNone(putuskan("Caruana,F", {"name": "   "})[0])


if __name__ == "__main__":
    unittest.main()
