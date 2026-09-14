"""Uji kunci satu-penulis: penolakan, keterangan pemegang, pelepasan."""

import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

AKAR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(AKAR))

from app.kunci_penulis import KunciPenulis, KunciTerpakai, pemegang, status


class UjiKunci(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = Path(self.tmp.name)
        self.kunci, self.info = d / "uji.lock", d / "uji.info"

    def tearDown(self):
        self.tmp.cleanup()

    def buat(self, keterangan="uji"):
        return KunciPenulis(keterangan, path=self.kunci, path_info=self.info)

    def test_dilepas_setelah_blok_selesai(self):
        with self.buat():
            pass
        with self.buat():        # tidak boleh menolak
            pass

    def test_keterangan_tercatat_lalu_dihapus(self):
        with self.buat("impor twic 920-1505"):
            self.assertIn("impor twic 920-1505", pemegang(self.info))
            self.assertIn("PID", pemegang(self.info))
        self.assertFalse(self.info.exists())

    def test_dilepas_walau_ada_galat(self):
        with self.assertRaises(ValueError):
            with self.buat():
                raise ValueError("pura-pura gagal")
        with self.buat():
            pass

    def test_proses_kedua_ditolak(self):
        """Proses lain yang benar-benar terpisah harus ditolak, bukan menunggu."""
        skrip = textwrap.dedent(f"""
            import sys
            sys.path.insert(0, r"{AKAR}")
            from app.kunci_penulis import KunciPenulis, KunciTerpakai
            try:
                with KunciPenulis("kedua", path=r"{self.kunci}", path_info=r"{self.info}"):
                    print("DAPAT")
            except KunciTerpakai as e:
                print("DITOLAK")
                print(e)
        """)
        with self.buat("yang pertama"):
            hasil = subprocess.run([sys.executable, "-c", skrip], capture_output=True,
                                   text=True, encoding="utf-8", errors="replace", timeout=60)
        self.assertIn("DITOLAK", hasil.stdout, hasil.stdout + hasil.stderr)
        self.assertIn("yang pertama", hasil.stdout)      # pesan menyebut pemegangnya

    def _jalankan_anak(self, env=None):
        """Proses anak yang mencoba mengambil kunci, lalu melaporkan status()."""
        skrip = textwrap.dedent(f"""
            import sys
            sys.path.insert(0, r"{AKAR}")
            from app.kunci_penulis import KunciPenulis, KunciTerpakai, status
            try:
                with KunciPenulis("anak", path=r"{self.kunci}", path_info=r"{self.info}") as k:
                    print("DAPAT diwarisi=" + str(k.diwarisi))
                    print("status bebas=" + str(status(r"{self.kunci}", r"{self.info}")[0]))
            except KunciTerpakai:
                print("DITOLAK")
        """)
        return subprocess.run([sys.executable, "-c", skrip], capture_output=True, env=env,
                              text=True, encoding="utf-8", errors="replace", timeout=60)

    def test_anak_dari_pemegang_mewarisi_kunci(self):
        """perbarui.py memegang kunci lalu menjalankan indeks_posisi.py sebagai
        proses terpisah; anak itu dulu ditolak induknya sendiri (14 Sep 2026)."""
        from app.kunci_penulis import lingkungan_anak
        with self.buat("perbarui.py") as induk:
            hasil = self._jalankan_anak(env=lingkungan_anak())
            self.assertIn("DAPAT diwarisi=True", hasil.stdout, hasil.stdout + hasil.stderr)
            self.assertIn("status bebas=False", hasil.stdout, "bagi yang bertanya, kunci tetap sibuk")
            # anak selesai TIDAK melepas kunci milik induk
            self.assertIn("DITOLAK", self._jalankan_anak().stdout)
            self.assertIn("perbarui.py", pemegang(self.info))
        self.assertIsNone(induk._fh)

    def test_proses_lain_tanpa_pewarisan_tetap_ditolak(self):
        """Batch yang diluncurkan server selagi server memegang kunci impor tidak
        boleh ikut lolos: pewarisan hanya lewat lingkungan_anak()."""
        import os
        with self.buat("impor lewat web"):
            env_palsu = {**os.environ, "CATUR_PEMEGANG_KUNCI": "99999"}
            self.assertIn("DITOLAK", self._jalankan_anak(env=env_palsu).stdout)
            self.assertIn("DITOLAK", self._jalankan_anak().stdout)

    def test_status_bebas_saat_tidak_ada_pemegang(self):
        bebas, siapa = status(self.kunci, self.info)
        self.assertTrue(bebas)
        self.assertEqual(siapa, "")

    def test_status_membersihkan_info_basi(self):
        """Proses mati mendadak: .info tertinggal padahal kunci sudah bebas.
        status() harus bilang bebas DAN membuang berkas basi itu."""
        self.info.write_text("PID 99999 | proses yang sudah mati | mulai kemarin",
                             encoding="utf-8")
        bebas, _ = status(self.kunci, self.info)
        self.assertTrue(bebas)
        self.assertFalse(self.info.exists())

    def test_status_sibuk_saat_dipegang(self):
        with self.buat("impor besar"):
            bebas, siapa = status(self.kunci, self.info)
        self.assertFalse(bebas)
        self.assertIn("impor besar", siapa)

    def test_setelah_pemegang_selesai_proses_lain_dapat(self):
        with self.buat("pertama"):
            pass
        skrip = textwrap.dedent(f"""
            import sys
            sys.path.insert(0, r"{AKAR}")
            from app.kunci_penulis import KunciPenulis
            with KunciPenulis("kedua", path=r"{self.kunci}", path_info=r"{self.info}"):
                print("DAPAT")
        """)
        hasil = subprocess.run([sys.executable, "-c", skrip], capture_output=True,
                               text=True, encoding="utf-8", errors="replace", timeout=60)
        self.assertIn("DAPAT", hasil.stdout, hasil.stdout + hasil.stderr)


if __name__ == "__main__":
    unittest.main()
