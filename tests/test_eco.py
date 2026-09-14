import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.eco import KELUARGA, REPERTOIRE, keluarga_eco


class UjiEco(unittest.TestCase):
    def test_repertoire_ray(self):
        self.assertEqual(keluarga_eco("A22"), "English Opening")
        self.assertEqual(keluarga_eco("E04"), "Catalan Opening")
        self.assertEqual(keluarga_eco("D38"), "Ragozin Defense")
        self.assertEqual(keluarga_eco("B90"), "Sicilian Defense")

    def test_ragozin_didahulukan_dari_qgd(self):
        self.assertEqual(keluarga_eco("D39"), "Ragozin Defense")
        self.assertEqual(keluarga_eco("D37"), "Queen's Gambit Declined")

    def test_kode_kosong_atau_aneh(self):
        self.assertIsNone(keluarga_eco(None))
        self.assertIsNone(keluarga_eco(""))
        self.assertIsNone(keluarga_eco("Z99"))
        self.assertEqual(keluarga_eco("b90"), "Sicilian Defense")

    def test_semua_kode_a00_e99_terpetakan(self):
        for huruf in "ABCDE":
            for n in range(100):
                self.assertIsNotNone(keluarga_eco(f"{huruf}{n:02d}"), f"{huruf}{n:02d}")

    def test_repertoire_memakai_rentang_yang_ada(self):
        for nama, a, b in REPERTOIRE:
            self.assertLessEqual(a, b)
            self.assertIsNotNone(keluarga_eco(a))


if __name__ == "__main__":
    unittest.main()
