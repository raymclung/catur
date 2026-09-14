"""Saringan pemilihan partai di analisis_partai.py (--hasil, --format, akun pribadi)."""

import argparse
import sys
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path

AKAR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(AKAR))

from analisis_partai import _rentang_eco, nama_normal_saya, pilih_partai
from app.db import buat_skema, koneksi
from app.impor import impor_teks, tandai_akun_pribadi


def pgn(putih, hitam, hasil, tc="180+0", langkah="1. e4 e5 2. Nf3 Nc6 3. Bb5 a6 4. Ba4 Nf6 5. O-O Be7"):
    tag = ['[Event "u"]', '[Site "x"]', '[Date "2026.01.01"]', '[Round "-"]',
           f'[White "{putih}"]', f'[Black "{hitam}"]', f'[Result "{hasil}"]',
           f'[TimeControl "{tc}"]', '[WhiteElo "2400"]', '[BlackElo "2400"]']
    return "\n".join(tag) + f"\n\n{langkah} {hasil}\n"


def args(**ubah):
    dasar = dict(id=None, sumber=None, format_=None, pemain=None, min_elo=None,
                 gelar_lawan=None, lawan_min_elo=None, hasil=None, ulang=False,
                 min_langkah=1, terbaru=None, rentang_eco=None, repertoire=False)
    dasar.update(ubah)
    return Namespace(**dasar)


class UjiPilihPartai(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = koneksi(Path(self.tmp.name) / "uji.db")
        buat_skema(self.conn)
        # rayylung = akun Ray; ryumclungg = bukan (SESI.md bagian 6).
        impor_teks(self.conn, pgn("rayylung", "lawanA", "0-1"), "chesscom")            # kalah (putih)
        impor_teks(self.conn, pgn("lawanB", "rayylung", "1-0", langkah="1. d4 d5 2. c4 e6"), "chesscom")   # kalah (hitam)
        impor_teks(self.conn, pgn("rayylung", "lawanC", "1-0", langkah="1. c4 e5 2. Nc3 Nf6"), "chesscom")  # menang
        impor_teks(self.conn, pgn("lawanD", "rayylung", "1/2-1/2", langkah="1. e4 c5 2. Nf3 d6"), "chesscom")  # remis
        impor_teks(self.conn, pgn("ryumclungg", "lawanE", "0-1", langkah="1. g3 d5 2. Bg2 Nf6"), "chesscom")   # bukan akun Ray
        impor_teks(self.conn, pgn("rayylung", "lawanF", "0-1", tc="600", langkah="1. b3 e5 2. Bb2 Nc6"), "chesscom")  # kalah, rapid
        tandai_akun_pribadi(self.conn, ["rayylung"], kecualikan=["ryumclungg"])

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def hasil_id(self, **ubah):
        return {p["id"] for p in pilih_partai(self.conn, args(**ubah))}

    def test_akun_pribadi_jadi_sumber_kebenaran(self):
        self.assertEqual(nama_normal_saya(self.conn), ["rayylung"])

    def test_hasil_kalah_dari_sisi_ray(self):
        kalah = pilih_partai(self.conn, args(hasil="kalah"))
        # tiga partai kalah milik rayylung (dua blitz, satu rapid); partai
        # ryumclungg TIDAK ikut walau hasilnya juga kalah untuk pemain putih.
        self.assertEqual(len(kalah), 3)
        for p in kalah:
            self.assertTrue("rayylung" in (p["putih"], p["hitam"]))
            self.assertEqual(p["hasil"], "0-1" if p["putih"] == "rayylung" else "1-0")

    def test_hasil_menang_dan_remis(self):
        self.assertEqual(len(pilih_partai(self.conn, args(hasil="menang"))), 1)
        self.assertEqual(len(pilih_partai(self.conn, args(hasil="remis"))), 1)

    def test_format_menyaring(self):
        self.assertEqual(len(pilih_partai(self.conn, args(hasil="kalah", format_="blitz"))), 2)
        self.assertEqual(len(pilih_partai(self.conn, args(hasil="kalah", format_="rapid"))), 1)

    def test_hasil_digabung_lawan_min_elo(self):
        self.assertEqual(len(pilih_partai(self.conn, args(hasil="kalah", lawan_min_elo=2201))), 3)
        self.assertEqual(len(pilih_partai(self.conn, args(hasil="kalah", lawan_min_elo=2500))), 0)


class UjiSaringanEco(unittest.TestCase):
    """--eco / --repertoire: analisis partai GM di pembukaan yang Ray mainkan."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = koneksi(Path(self.tmp.name) / "uji.db")
        buat_skema(self.conn)
        from app.impor import impor_teks
        # eco ditaruh lewat tag PGN supaya jalurnya sama dengan impor sungguhan
        def tambah(eco, langkah):
            tag = ['[Event "u"]', '[Site "x"]', '[Date "2026.01.01"]', '[Round "-"]',
                   '[White "GM Satu, A"]', '[Black "GM Dua, B"]', '[Result "1-0"]',
                   f'[ECO "{eco}"]']
            impor_teks(self.conn, "\n".join(tag) + f"\n\n{langkah} 1-0\n", "twic")
        tambah("A22", "1. c4 e5 2. Nc3 Nf6")            # English
        tambah("E04", "1. d4 Nf6 2. c4 e6 3. g3 d5")     # Catalan
        tambah("B90", "1. e4 c5 2. Nf3 d6 3. d4 cxd4")   # Sicilian Najdorf
        tambah("C65", "1. e4 e5 2. Nf3 Nc6 3. Bb5 Nf6")  # Ruy Lopez, di luar repertoire

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_bentuk_kode_eco(self):
        p = argparse.ArgumentParser()
        self.assertEqual(_rentang_eco(["A22"], p), [("A22", "A22")])
        self.assertEqual(_rentang_eco(["a"], p), [("A00", "A99")])
        self.assertEqual(_rentang_eco(["A10-A39"], p), [("A10", "A39")])

    def test_kode_salah_dihentikan(self):
        p = argparse.ArgumentParser()
        with self.assertRaises(SystemExit):        # argparse.error -> SystemExit
            _rentang_eco(["ZZ9"], p)

    def test_saring_satu_rentang(self):
        n = len(pilih_partai(self.conn, args(rentang_eco=[("A10", "A39")])))
        self.assertEqual(n, 1)

    def test_repertoire_mencakup_empat_pembukaan(self):
        dipilih = pilih_partai(self.conn, args(repertoire=True))
        eco = sorted(p["eco"] for p in dipilih)
        self.assertEqual(eco, ["A22", "B90", "E04"])   # C65 di luar repertoire


class UjiSumberGm(unittest.TestCase):
    """
    `--sumber gm` = semua sumber partai GM sekaligus (permintaan frontend
    13 Sep 00:25). Tanpa ini "semua sumber" di kelompok Partai GM berarti tanpa
    saringan, dan partai akun Ray ikut terjaring.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = koneksi(Path(self.tmp.name) / "uji.db")
        buat_skema(self.conn)
        from app.impor import impor_teks
        langkah = {"twic": "1. c4 e5 2. Nc3 Nf6", "pgnmentor": "1. d4 d5 2. c4 e6",
                   "lichess-broadcast": "1. e4 c5 2. Nf3 d6", "lichess-user": "1. g3 d5 2. Bg2 Nf6",
                   "chesscom": "1. b3 e5 2. Bb2 Nc6"}
        for sumber, lg in langkah.items():
            tag = ['[Event "u"]', '[Site "x"]', '[Date "2026.01.01"]', '[Round "-"]',
                   '[White "Satu, A"]', '[Black "Dua, B"]', '[Result "1-0"]']
            impor_teks(self.conn, chr(10).join(tag) + chr(10) * 2 + lg + " 1-0" + chr(10), sumber)
        self.conn.commit()

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_gm_hanya_sumber_gm(self):
        from app.posisi import SUMBER_GM
        dipilih = pilih_partai(self.conn, args(sumber="gm"))
        sumber = sorted({self.conn.execute("SELECT sumber FROM partai WHERE id=?",
                                           (p["id"],)).fetchone()[0] for p in dipilih})
        self.assertEqual(sumber, sorted(SUMBER_GM))
        self.assertEqual(len(dipilih), 3)          # akun sendiri tidak ikut

    def test_satu_label_tetap_bekerja(self):
        self.assertEqual(len(pilih_partai(self.conn, args(sumber="pgnmentor"))), 1)


class UjiProfilLengkap(unittest.TestCase):
    def test_setiap_profil_punya_perkiraan_cadangan(self):
        """
        Profil baru tanpa entri cadangan membuat perintah pertamanya mati
        KeyError sebelum menganalisis apa pun — terjadi pada profil "cepat".
        """
        sys.path.insert(0, str(AKAR / "src"))
        from analisis_partai import DETIK_PER_PLY_CADANGAN
        from catur_analysis.engine import PROFIL
        self.assertEqual(set(PROFIL), set(DETIK_PER_PLY_CADANGAN))


if __name__ == "__main__":
    unittest.main()
