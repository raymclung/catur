"""
Berkas JSON bersama (antrean, tugas, pengaturan mesin): tulisan serentak tidak
boleh saling menimpa, dan isi rusak tidak boleh ditimpa diam-diam.
Audit 28 Sep 2026: 20 klik "Antrekan" serentak dijawab 200 semua, antreannya
turun dari 9 baris ke 2.
"""

import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

AKAR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(AKAR))

from app import antrean, berkas_json  # noqa: E402
from app.batas_cpu import milik_proyek  # noqa: E402


class TulisSerentak(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "antrean.json"

    def tearDown(self):
        self.tmp.cleanup()

    def test_dua_puluh_thread_tidak_ada_yang_hilang(self):
        antrean.tambah({"terbaru": 1}, "lama", path=self.path)
        mulai = threading.Barrier(20)

        def kerja(i):
            mulai.wait()
            antrean.tambah({"terbaru": 3}, f"t{i}", path=self.path)

        utas = [threading.Thread(target=kerja, args=(i,)) for i in range(20)]
        for u in utas:
            u.start()
        for u in utas:
            u.join()
        isi = antrean.baca(self.path)
        self.assertEqual(len(isi), 21, "baris lama + 20 baru, tidak ada yang tertimpa")
        self.assertEqual(sorted(d["id"] for d in isi), list(range(1, 22)), "nomor tidak kembar")

    def test_proses_terpisah_juga_terkunci(self):
        """Server dan jaga_antrean.py adalah proses berbeda: kunci thread tidak cukup."""
        skrip = ("import sys; sys.path.insert(0, sys.argv[1]); from pathlib import Path; "
                 "from app import antrean\n"
                 "for i in range(15): antrean.tambah({'terbaru': 1}, 'p', path=Path(sys.argv[2]))")
        proses = [subprocess.Popen([sys.executable, "-c", skrip, str(AKAR), str(self.path)])
                  for _ in range(4)]
        for p in proses:
            self.assertEqual(p.wait(timeout=120), 0)
        self.assertEqual(len(antrean.baca(self.path)), 60)

    def test_isi_rusak_disisihkan_bukan_ditimpa(self):
        self.path.write_text('[{"id":1,"status":"menunggu"}, {"id":2,', encoding="utf-8")
        antrean.tambah({"terbaru": 1}, "baru", path=self.path)
        rusak = list(Path(self.tmp.name).glob("antrean.json.rusak-*"))
        self.assertEqual(len(rusak), 1, "isi lama disimpan untuk diperiksa tangan")
        self.assertIn('"id":2', rusak[0].read_text(encoding="utf-8"))
        self.assertEqual(len(antrean.baca(self.path)), 1)

    def test_blok_yang_galat_tidak_menulis(self):
        berkas_json.tulis(self.path, [1, 2])
        with self.assertRaises(RuntimeError):
            with berkas_json.ubah(self.path, []) as isi:
                isi.append(3)
                raise RuntimeError("batal")
        self.assertEqual(berkas_json.baca(self.path, []), [1, 2])

    def test_tidak_ada_berkas_sementara_tertinggal(self):
        for i in range(5):
            berkas_json.tulis(self.path, list(range(i)))
        self.assertEqual(list(Path(self.tmp.name).glob("*.tmp")), [])


class HanyaStockfishProyek(unittest.TestCase):
    """Audit 28 Sep 2026: jeda & batas CPU ikut menyentuh Stockfish aplikasi puzzle."""

    def test_engine_proyek_diakui(self):
        self.assertTrue(milik_proyek(AKAR / "engine" / "stockfish.exe"))

    def test_engine_folder_lain_ditolak(self):
        self.assertFalse(milik_proyek(AKAR.parent / "puzzle" / "puzzle" / "engine" / "stockfish.exe"))

    def test_jalur_tak_terbaca_ditolak(self):
        self.assertFalse(milik_proyek(None))


if __name__ == "__main__":
    unittest.main()
