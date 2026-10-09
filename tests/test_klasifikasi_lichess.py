"""
Klasifikasi dan akurasi mengikuti Lichess (lila Advice.scala dan
AccuracyPercent.scala), audit 28 Sep 2026.

Dulu: ambang centipawn mentah 300/100/50 dan akurasi = rata-rata biasa.
Akibatnya +16 -> +7 saat sudah menang telak disebut blunder, dan partai
dengan satu blunder tetap tampak ~90%.
"""

import io
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

import chess.pgn

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from catur_analysis.partai import (SKOR_MAT, _akurasi_dari_persen,       # noqa: E402
                                   akurasi_partai, analisis_dari_daftar,
                                   klasifikasikan)

MAT = SKOR_MAT


class Klasifikasi(unittest.TestCase):
    def test_turunnya_peluang_menang_bukan_centipawn(self):
        self.assertEqual(klasifikasikan(0, -300, False), "blunder")
        self.assertEqual(klasifikasikan(0, -100, False), "inaccuracy")   # dulu "mistake"
        self.assertEqual(klasifikasikan(1600, 700, False), "baik")       # dulu "blunder"
        self.assertEqual(klasifikasikan(50, 40, False), "baik")

    def test_langkah_terbaik_tetap_terbaik(self):
        self.assertEqual(klasifikasikan(0, -900, True), "terbaik")

    def test_mat_muncul(self):
        self.assertEqual(klasifikasikan(0, -MAT, False), "blunder")
        self.assertEqual(klasifikasikan(-800, -MAT, False), "mistake")
        self.assertEqual(klasifikasikan(-1200, -MAT, False), "inaccuracy")

    def test_mat_hilang(self):
        self.assertEqual(klasifikasikan(MAT, 300, False), "blunder")
        self.assertEqual(klasifikasikan(MAT, 800, False), "mistake")
        self.assertEqual(klasifikasikan(MAT, 1200, False), "inaccuracy")
        self.assertEqual(klasifikasikan(MAT, -MAT, False), "blunder")

    def test_mat_ke_mat_tidak_dinilai(self):
        self.assertEqual(klasifikasikan(MAT, MAT, False), "baik")         # mat tertunda
        self.assertEqual(klasifikasikan(-MAT, -MAT, False), "baik")


def langkah(warna, eval_putih, klasifikasi="baik"):
    return SimpleNamespace(warna=warna, eval_putih_cp=eval_putih, klasifikasi=klasifikasi)


class Akurasi(unittest.TestCase):
    def test_tidak_turun_seratus(self):
        self.assertEqual(_akurasi_dari_persen(50, 50), 100.0)
        self.assertEqual(_akurasi_dari_persen(50, 60), 100.0)

    def test_partai_tanpa_kesalahan_seratus(self):
        daftar = [langkah("putih" if i % 2 == 0 else "hitam", 15) for i in range(20)]
        self.assertEqual(akurasi_partai(daftar, 15), {"putih": 100.0, "hitam": 100.0})

    def test_satu_blunder_menarik_turun_lebih_dari_rata_rata_biasa(self):
        """Inti rumus Lichess: rata-rata harmonik + bobot volatilitas."""
        # indeks ganjil = langkah Hitam; di indeks 9 Hitam blunder (+0,15 -> +6
        # untuk Putih) dan posisinya tetap begitu sampai akhir
        evals = [15] * 9 + [600] * 11
        daftar = [langkah("putih" if i % 2 == 0 else "hitam", e) for i, e in enumerate(evals)]
        akurasi = akurasi_partai(daftar, 15)
        per_langkah = []
        semua = [15] + evals
        from catur_analysis.partai import peluang_menang
        for i in range(1, len(semua)):
            if i % 2 == 0:                   # langkah Hitam (ply 1, 3, ...)
                sebelum, sesudah = 100 - peluang_menang(semua[i - 1]) * 100, 100 - peluang_menang(semua[i]) * 100
                per_langkah.append(_akurasi_dari_persen(sebelum, sesudah))
        rata_biasa = sum(per_langkah) / len(per_langkah)
        self.assertLess(akurasi["hitam"], rata_biasa)
        self.assertLess(akurasi["hitam"], 95)

    def test_pihak_yang_belum_melangkah_tanpa_akurasi(self):
        self.assertEqual(akurasi_partai([langkah("putih", 20)], 15)["hitam"], None)
        self.assertEqual(akurasi_partai([], 15), {"putih": None, "hitam": None})


class RokadeLichess(unittest.TestCase):
    def test_e1h1_disimpan_sebagai_e1g1(self):
        """Lichess menulis rokade raja-makan-benteng; soal latihan membandingkan e1g1."""
        game = chess.pgn.read_game(io.StringIO(
            "1. e4 e5 2. Nf3 Nc6 3. Bc4 Bc5 4. d3 *"))
        daftar = [{"eval": 30}, {"eval": 30}, {"eval": 30}, {"eval": 30},
                  {"eval": 30}, {"eval": 30}, {"eval": -40, "best": "e1h1"}]
        lap = analisis_dari_daftar(game, daftar)
        self.assertEqual(lap.langkah[6].langkah_terbaik_uci, "e1g1")
        self.assertEqual(lap.langkah[6].langkah_terbaik_san, "O-O")


class KlasifikasiUlangDataTersimpan(unittest.TestCase):
    """
    app.analisis_db.klasifikasi_ulang_satu: analisis LAMA yang dihitung ulang
    harus sama persis dengan analisis BARU dari eval yang sama.
    """

    def setUp(self):
        import tempfile
        from app.db import buat_skema, koneksi
        from app.impor import impor_teks
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = koneksi(Path(self.tmp.name) / "uji.db")
        buat_skema(self.conn)
        teks = ('[Event "u"]\n[Date "2026.01.01"]\n[White "A, B"]\n[Black "C, D"]\n'
                '[Result "*"]\n\n1. e4 e5 2. Nf3 Nc6 3. Bc4 Bc5 4. d3 Nf6 5. Nc3 d6 *\n')
        impor_teks(self.conn, teks, "twic")
        self.pid = self.conn.execute("SELECT id FROM partai").fetchone()[0]
        game = chess.pgn.read_game(io.StringIO(teks))
        # eval sudut Putih sesudah tiap ply; ply 6 (4. d3) gagal rokade -> blunder,
        # ply 7 kesalahan kecil Hitam
        daftar = [{"eval": 30}, {"eval": 25}, {"eval": 30}, {"eval": 30}, {"eval": 35},
                  {"eval": 30}, {"eval": -250, "best": "e1h1"}, {"eval": -120},
                  {"eval": -130}, {"eval": -60}]
        from app.analisis_db import simpan_laporan
        simpan_laporan(self.conn, self.pid, analisis_dari_daftar(game, daftar), "lichess", "lichess")
        self.benar = self.baca()

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def baca(self):
        langkah = self.conn.execute(
            "SELECT ply, klasifikasi, kehilangan_cp, langkah_terbaik_uci FROM analisis_langkah "
            "WHERE partai_id = ? ORDER BY ply", (self.pid,)).fetchall()
        ringkas = self.conn.execute(
            "SELECT akurasi_putih, akurasi_hitam, blunder_putih, mistake_putih, inaccuracy_putih, "
            "blunder_hitam, mistake_hitam, inaccuracy_hitam FROM analisis WHERE partai_id = ?",
            (self.pid,)).fetchone()
        return [tuple(r) for r in langkah], tuple(ringkas)

    def test_data_lama_kembali_sama_dengan_analisis_baru(self):
        from app.analisis_db import klasifikasi_ulang_satu
        self.assertIn("e1g1", [r[3] for r in self.benar[0]])
        # bentuk LAMA: ambang centipawn, akurasi rata-rata biasa, rokade Lichess
        self.conn.execute("UPDATE analisis_langkah SET klasifikasi = 'mistake' WHERE klasifikasi <> 'buku'")
        self.conn.execute("UPDATE analisis_langkah SET langkah_terbaik_uci = 'e1h1' "
                          "WHERE langkah_terbaik_uci = 'e1g1'")
        self.conn.execute("UPDATE analisis SET akurasi_putih = 1, akurasi_hitam = 1, blunder_putih = 9")
        h = klasifikasi_ulang_satu(self.conn, self.pid)
        self.assertEqual(h["rokade"], 1)
        self.assertEqual(self.baca(), self.benar)

    def test_idempoten(self):
        from app.analisis_db import klasifikasi_ulang_satu
        self.assertEqual(klasifikasi_ulang_satu(self.conn, self.pid)["langkah_berubah"], 0)
        self.assertEqual(self.baca(), self.benar)


if __name__ == "__main__":
    unittest.main()
