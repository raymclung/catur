"""
Tarik partai akun sendiri (Lichess & Chess.com) — bagian yang tidak menyentuh
jaringan: menentukan "sejak kapan", dan memasukkan hasilnya ke database.

Pengunduh aslinya diganti fungsi palsu, jadi tes ini tidak pernah memanggil
Lichess maupun Chess.com.
"""

import sys
import tempfile
import unittest
from pathlib import Path

AKAR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(AKAR))

import app.tarik_akun as tarik
from ambil_partai import Akun
from app.db import buat_skema, koneksi


def pgn(putih, hitam, tanggal, langkah="1. e4 e5 2. Nf3 Nc6"):
    return (f'[Event "Rated blitz game"]\n[Site "-"]\n[Date "{tanggal}"]\n[Round "-"]\n'
            f'[White "{putih}"]\n[Black "{hitam}"]\n[Result "1-0"]\n'
            f'[TimeControl "180+0"]\n\n{langkah} 1-0\n')


class UjiSejakKapan(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.conn = koneksi(Path(self.tmp) / "uji.db")
        buat_skema(self.conn)

    def tearDown(self):
        import shutil
        self.conn.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_tanpa_partai_berarti_seluruh_riwayat(self):
        self.assertIsNone(tarik.tanggal_partai_terakhir(self.conn, "McLung_07"))
        self.assertEqual(tarik._sejak(None), (None, None))

    def test_tanggal_terakhir_dibaca_per_akun(self):
        from app.impor import impor_teks
        impor_teks(self.conn, pgn("McLung_07", "lawan", "2026.09.01"), "lichess-user")
        impor_teks(self.conn, pgn("lawan", "McLung_07", "2026.09.10", "1. d4 d5"), "lichess-user")
        impor_teks(self.conn, pgn("Mikaysr", "lawan", "2026.01.05", "1. c4 e5"), "lichess-user")
        self.assertEqual(tarik.tanggal_partai_terakhir(self.conn, "McLung_07"), "2026.09.10")
        self.assertEqual(tarik.tanggal_partai_terakhir(self.conn, "Mikaysr"), "2026.01.05")

    def test_sejak_mundur_satu_hari(self):
        ms, bulan = tarik._sejak("2026.09.10")
        self.assertEqual(bulan, "2026-09")
        import datetime as dt
        t = dt.datetime.fromtimestamp(ms / 1000, dt.timezone.utc)
        self.assertEqual((t.year, t.month, t.day), (2026, 9, 9))

    def test_sejak_lintas_awal_bulan(self):
        # 1 September mundur sehari = 31 Agustus, jadi arsip Agustus ikut ditarik.
        _, bulan = tarik._sejak("2026.09.01")
        self.assertEqual(bulan, "2026-08")

    def test_tanggal_rusak_tidak_bikin_gagal(self):
        self.assertEqual(tarik._sejak("????.??.??"), (None, None))


class UjiTarik(UjiSejakKapan):
    """tarik_satu/tarik_semua dengan pengunduh palsu."""

    def setUp(self):
        super().setUp()
        self.asli = (tarik.ambil_lichess, tarik.ambil_chesscom, tarik.AKUN)
        self.dipanggil = []

    def tearDown(self):
        tarik.ambil_lichess, tarik.ambil_chesscom, tarik.AKUN = self.asli
        super().tearDown()

    def pasang(self, teks_lichess="", teks_chesscom="", galat=None):
        def palsu_lichess(username, kecepatan, maks, hanya_rated, sejak_ms=None):
            self.dipanggil.append(("lichess", username, sejak_ms))
            if galat:
                raise RuntimeError(galat)
            return teks_lichess

        def palsu_chesscom(username, kecepatan, maks, hanya_rated,
                           saring_turnamen="", sejak_bulan=None):
            self.dipanggil.append(("chesscom", username, sejak_bulan, saring_turnamen))
            return teks_chesscom

        tarik.ambil_lichess = palsu_lichess
        tarik.ambil_chesscom = palsu_chesscom

    def test_partai_baru_masuk_database(self):
        self.pasang(teks_lichess=pgn("McLung_07", "lawanA", "2026.09.11"))
        tarik.AKUN = [Akun("lichess", "McLung_07")]
        h = tarik.tarik_semua(self.conn)
        self.assertEqual(h["total_baru"], 1)
        self.assertEqual(h["akun"][0]["diunduh"], 1)
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM partai WHERE sumber='lichess-user'").fetchone()[0], 1)

    def test_penarikan_kedua_memakai_sejak_dan_menolak_duplikat(self):
        teks = pgn("McLung_07", "lawanA", "2026.09.11")
        self.pasang(teks_lichess=teks)
        tarik.AKUN = [Akun("lichess", "McLung_07")]
        tarik.tarik_semua(self.conn)
        self.dipanggil.clear()
        h = tarik.tarik_semua(self.conn)           # sekali lagi, isi sama
        self.assertEqual(h["total_baru"], 0)
        self.assertEqual(h["total_duplikat"], 1)
        self.assertIsNotNone(self.dipanggil[0][2], "penarikan kedua harus memakai sejak")

    def test_saringan_turnamen_akun_ikut_terbawa(self):
        # RyuMcLungG: hanya Arena Kings yang miliknya (SESI.md bagian 6).
        self.pasang(teks_chesscom="")
        tarik.AKUN = [Akun("chesscom", "ryumclungg", saring_turnamen="arena-kings")]
        tarik.tarik_semua(self.conn)
        self.assertEqual(self.dipanggil[0][3], "arena-kings")

    def test_jaringan_gagal_tidak_membatalkan_akun_lain(self):
        self.pasang(galat="429 Too Many Requests")
        tarik.AKUN = [Akun("lichess", "McLung_07"), Akun("chesscom", "rayylung")]
        h = tarik.tarik_semua(self.conn)
        self.assertEqual(h["gagal"], ["McLung_07"])
        self.assertIn("429", h["akun"][0]["galat"])
        self.assertIsNone(h["akun"][1]["galat"])

    def test_username_tak_dikenal_ditolak(self):
        self.pasang()
        tarik.AKUN = [Akun("lichess", "McLung_07")]
        with self.assertRaises(ValueError):
            tarik.tarik_semua(self.conn, username=["bukan_akun_saya"])


if __name__ == "__main__":
    unittest.main()
