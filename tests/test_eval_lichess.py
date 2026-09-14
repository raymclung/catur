"""
Uji analisis dari evaluasi Lichess: `analisis_dari_eval()` dan `app.eval_lichess`.

Evaluasi siaran Lichess ([%eval ...] di tiap langkah) dipakai sebagai analisis
tanpa engine. Yang dijaga: sudut pandang evaluasi (Lichess memberi dari sudut
Putih), mat, langkah pertama tanpa kehilangan karangan, evaluasi yang bolong
ditolak, dan hasilnya tersimpan dengan tanda engine Lichess.
"""

import io
import sys
import tempfile
import unittest
from pathlib import Path

AKAR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(AKAR))
sys.path.insert(0, str(AKAR / "src"))

import chess.pgn

from app import eval_lichess
from app.analisis_db import ambil_analisis
from app.db import buat_skema, koneksi, sekarang
from catur_analysis.partai import akurasi_satu_langkah, analisis_dari_daftar, analisis_dari_eval

KEPALA = """[Event "Uji"]
[White "Putih"]
[Black "Hitam"]
[Result "{hasil}"]

"""


def pgn(badan, hasil="1-0"):
    return KEPALA.format(hasil=hasil) + badan + "\n"


def baca(teks):
    return chess.pgn.read_game(io.StringIO(teks))


class DariEval(unittest.TestCase):
    def test_sudut_pandang_dan_kehilangan(self):
        # Putih +0.30, Hitam blunder jadi +4.00 (dari sudut Putih)
        lap = analisis_dari_eval(baca(pgn(
            "1. e4 { [%eval 0.30] } f6 { [%eval 4.00] } 2. d4 { [%eval 4.10] } 1-0")))
        self.assertEqual(len(lap.langkah), 3)
        e4, f6, d4 = lap.langkah
        # langkah pertama: posisi awal tidak dievaluasi, jadi tanpa kehilangan
        self.assertEqual(e4.kehilangan_cp, 0)
        # f6 dari sudut Hitam: sebelum -30, sesudah -400 -> kehilangan 370
        self.assertEqual(f6.warna, "hitam")
        self.assertEqual(f6.eval_sebelum_cp, -30)
        self.assertEqual(f6.eval_sesudah_cp, -400)
        self.assertEqual(f6.kehilangan_cp, 370)
        self.assertEqual(f6.klasifikasi, "blunder")
        # d4 dari sudut Putih: 400 -> 410, tanpa kehilangan
        self.assertEqual(d4.kehilangan_cp, 0)
        self.assertEqual(lap.ringkasan_hitam.blunder, 1)

    def test_akurasi_memakai_rumus_yang_sama(self):
        lap = analisis_dari_eval(baca(pgn(
            "1. e4 { [%eval 0.30] } f6 { [%eval 4.00] } 2. d4 { [%eval 4.10] } 1-0")))
        f6 = lap.langkah[1]
        self.assertAlmostEqual(lap.ringkasan_hitam.akurasi,
                               round(akurasi_satu_langkah(f6.eval_sebelum_cp, f6.eval_sesudah_cp), 1))

    def test_mat_dari_sudut_putih_disimpan(self):
        lap = analisis_dari_eval(baca(pgn(
            "1. e4 { [%eval 0.30] } e5 { [%eval #3] } 2. Qh5 { [%eval #2] } 1-0")))
        e5 = lap.langkah[1]
        self.assertEqual(e5.mat_sesudah, 3)            # sudut Putih
        self.assertEqual(e5.eval_sesudah_cp, -100000)  # sudut Hitam: kena mat

    def test_tanpa_langkah_terbaik_tidak_dikarang(self):
        lap = analisis_dari_eval(baca(pgn("1. e4 { [%eval 0.30] } e5 { [%eval 0.25] } 1-0")))
        for l in lap.langkah:
            self.assertEqual(l.langkah_terbaik_san, "")
            self.assertNotEqual(l.klasifikasi, "terbaik")

    def test_evaluasi_bolong_ditolak(self):
        self.assertIsNone(analisis_dari_eval(baca(pgn(
            "1. e4 { [%eval 0.30] } e5 2. Nf3 { [%eval 0.20] } 1-0"))))

    def test_langkah_mat_terakhir_boleh_tanpa_evaluasi(self):
        lap = analisis_dari_eval(baca(pgn(
            "1. f3 { [%eval -0.50] } e5 { [%eval -0.60] } 2. g4 { [%eval #-1] } Qh4# 0-1",
            hasil="0-1")))
        self.assertIsNotNone(lap)
        self.assertEqual(lap.langkah[-1].mat_sesudah, 0)

    def test_tanpa_langkah_ditolak(self):
        self.assertIsNone(analisis_dari_eval(baca(pgn("1-0"))))


class DariAnalisisServer(unittest.TestCase):
    """Bentuk `analysis` ekspor partai Lichess: eval + best + variation di kesalahan."""

    def test_langkah_terbaik_dari_best(self):
        game = baca(pgn("1. e4 e5 2. Nf3 f6 3. Nxe5 1-0"))
        daftar = [{"eval": 30}, {"eval": 25}, {"eval": 30},
                  {"eval": 250, "best": "b8c6", "variation": "Nc6 Bb5 a6"}, {"eval": 400}]
        lap = analisis_dari_daftar(game, daftar)
        f6 = lap.langkah[3]
        self.assertEqual(f6.san, "f6")
        self.assertEqual(f6.langkah_terbaik_uci, "b8c6")
        self.assertEqual(f6.langkah_terbaik_san, "Nc6")
        self.assertEqual(f6.variasi_terbaik, ["Nc6", "Bb5", "a6"])
        self.assertEqual(f6.kehilangan_cp, 220)          # -30 -> -250 dari sudut Hitam
        # langkah tanpa best tidak diberi langkah terbaik karangan
        self.assertEqual(lap.langkah[1].langkah_terbaik_uci, "")

    def test_best_yang_tidak_sah_diabaikan(self):
        game = baca(pgn("1. e4 e5 1-0"))
        lap = analisis_dari_daftar(game, [{"eval": 30}, {"eval": 30, "best": "a1a8"}])
        self.assertEqual(lap.langkah[1].langkah_terbaik_uci, "")

    def test_mate_dari_analisis_server(self):
        game = baca(pgn("1. e4 e5 1-0"))
        lap = analisis_dari_daftar(game, [{"eval": 30}, {"mate": -2}])
        self.assertEqual(lap.langkah[1].mat_sesudah, -2)          # sudut Putih: Hitam mat dalam 2
        self.assertEqual(lap.langkah[1].eval_sesudah_cp, 100000)  # sudut Hitam: menang

    def test_daftar_kurang_ditolak(self):
        game = baca(pgn("1. e4 e5 2. Nf3 Nc6 1-0"))
        self.assertIsNone(analisis_dari_daftar(game, [{"eval": 30}, {"eval": 25}]))


class TarikAkun(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = koneksi(Path(self.tmp.name) / "uji.db")
        buat_skema(self.conn)
        t = sekarang()
        self.conn.execute("INSERT INTO pemain(id,nama,nama_normal,dibuat_pada) VALUES(1,'Saya','saya',?)", (t,))
        self.conn.execute("INSERT INTO pemain(id,nama,nama_normal,dibuat_pada) VALUES(2,'Lawan','lawan',?)", (t,))
        for i, id_li in ((1, "abcd1234"), (2, "wxyz9876")):
            self.conn.execute(
                "INSERT INTO partai(id,putih_id,hitam_id,hasil,pgn,sumber,sumber_url,hash_unik,diimpor_pada) "
                "VALUES(?,1,2,'1-0',?,'lichess-user',?,?,?)",
                (i, pgn("1. e4 e5 2. Nf3 f6 3. Nxe5 1-0"), f"https://lichess.org/{id_li}", f"h{i}", t))
        self.conn.commit()
        self.asli = eval_lichess._ekspor_analisis
        self.diminta = []

        def palsu(ids):
            self.diminta.append(list(ids))
            # hanya satu partai yang pernah dimintakan analisis di Lichess
            ada = {"abcd1234": [{"eval": 30}, {"eval": 25}, {"eval": 30},
                                {"eval": 250, "best": "b8c6", "variation": "Nc6"}, {"eval": 400}]}
            return {k: v for k, v in ada.items() if k in ids}
        eval_lichess._ekspor_analisis = palsu

    def tearDown(self):
        eval_lichess._ekspor_analisis = self.asli
        self.conn.close()
        self.tmp.cleanup()

    def test_hanya_yang_punya_analisis_yang_diisi(self):
        h = eval_lichess.tarik_analisis_akun(self.conn)
        self.assertEqual(h, {"calon": 2, "punya_analisis": 1, "dianalisis": 1, "dilewati": 0})
        a = ambil_analisis(self.conn, 1)
        self.assertEqual(a["engine"], eval_lichess.NAMA_ENGINE_SERVER)
        self.assertEqual(a["langkah"][3]["langkah_terbaik_uci"], "b8c6")
        self.assertIsNone(ambil_analisis(self.conn, 2))

    def test_batas_mengambil_yang_terbaru(self):
        eval_lichess.tarik_analisis_akun(self.conn, batas=1)
        self.assertEqual(self.diminta, [["wxyz9876"]])    # id 2 = terbaru

    def test_jawaban_yang_tidak_diminta_diabaikan(self):
        eval_lichess._ekspor_analisis = lambda ids: {"bukanpunya": [{"eval": 1}]}
        h = eval_lichess.tarik_analisis_akun(self.conn)
        self.assertEqual(h["dianalisis"], 0)

    def test_jumlah_langkah_tidak_cocok_dilewati(self):
        eval_lichess._ekspor_analisis = lambda ids: {"abcd1234": [{"eval": 30}]}
        h = eval_lichess.tarik_analisis_akun(self.conn)
        self.assertEqual(h["dilewati"], 1)
        self.assertIsNone(ambil_analisis(self.conn, 1))


class IsiDatabase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = koneksi(Path(self.tmp.name) / "uji.db")
        buat_skema(self.conn)
        t = sekarang()
        self.conn.execute("INSERT INTO pemain(id,nama,nama_normal,dibuat_pada) VALUES(1,'Putih','putih',?)", (t,))
        self.conn.execute("INSERT INTO pemain(id,nama,nama_normal,dibuat_pada) VALUES(2,'Hitam','hitam',?)", (t,))
        self.conn.commit()

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def partai(self, id_, teks):
        self.conn.execute(
            "INSERT INTO partai(id,putih_id,hitam_id,hasil,pgn,sumber,hash_unik,diimpor_pada) "
            "VALUES(?,1,2,'1-0',?,'lichess-broadcast',?,?)", (id_, teks, f"h{id_}", sekarang()))
        self.conn.commit()

    def test_isi_menyimpan_dengan_tanda_lichess(self):
        self.partai(1, pgn("1. e4 { [%eval 0.30] } f6 { [%eval 4.00] } 2. d4 { [%eval 4.10] } 1-0"))
        h = eval_lichess.isi(self.conn)
        self.assertEqual(h, {"calon": 1, "dianalisis": 1, "dilewati": 0})
        a = ambil_analisis(self.conn, 1)
        self.assertEqual(a["engine"], eval_lichess.NAMA_ENGINE)
        self.assertEqual(a["profil"], "lichess")
        self.assertEqual(a["blunder_hitam"], 1)
        self.assertEqual(len(a["langkah"]), 3)

    def test_yang_bolong_dilewati_dan_tanpa_eval_bukan_calon(self):
        self.partai(1, pgn("1. e4 { [%eval 0.30] } e5 2. Nf3 { [%eval 0.20] } 1-0"))
        self.partai(2, pgn("1. e4 e5 1-0"))
        h = eval_lichess.isi(self.conn)
        self.assertEqual(h["calon"], 1)
        self.assertEqual(h["dilewati"], 1)
        self.assertIsNone(ambil_analisis(self.conn, 1))

    def test_yang_sudah_dianalisis_tidak_ditimpa(self):
        self.partai(1, pgn("1. e4 { [%eval 0.30] } e5 { [%eval 0.25] } 1-0"))
        eval_lichess.isi(self.conn)
        self.assertEqual(eval_lichess.isi(self.conn)["calon"], 0)

    def test_lihat_saja_tidak_menyimpan(self):
        self.partai(1, pgn("1. e4 { [%eval 0.30] } e5 { [%eval 0.25] } 1-0"))
        self.assertEqual(eval_lichess.isi(self.conn, lihat_saja=True)["dianalisis"], 1)
        self.assertIsNone(ambil_analisis(self.conn, 1))


if __name__ == "__main__":
    unittest.main()
