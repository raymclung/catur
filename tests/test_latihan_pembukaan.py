"""Uji soal latihan pembukaan (app/latihan_pembukaan.py), tanpa jaringan."""

import sys
import tempfile
import unittest
from pathlib import Path

AKAR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(AKAR))

from app.db import buat_skema, koneksi, sekarang
from app.latihan_pembukaan import (CATALAN, ENGLISH, RAGOZIN, SICILIAN,
                                   hitung_soal, pembukaan_repertoire)
from app.posisi import indeks_partai

RAY, GM1, GM2, LAWAN = 1, 2, 3, 4


def pgn(langkah, hasil):
    return (f'[Event "Uji"]\n[Site "X"]\n[Date "2026.01.01"]\n[Round "1"]\n'
            f'[White "A"]\n[Black "B"]\n[Result "{hasil}"]\n\n{langkah} {hasil}\n')


class UjiRepertoire(unittest.TestCase):
    def test_putih(self):
        self.assertEqual(pembukaan_repertoire(["c2c4", "e7e5"], "putih"), ENGLISH)
        self.assertEqual(pembukaan_repertoire(
            ["g1f3", "d7d5", "g2g3", "g8f6", "d2d4", "e7e6", "c2c4"], "putih"), CATALAN)
        self.assertIsNone(pembukaan_repertoire(["b2b4", "e7e5"], "putih"))
        self.assertIsNone(pembukaan_repertoire(["d2d4", "d7d5", "c1f4"], "putih"))

    def test_hitam(self):
        self.assertEqual(pembukaan_repertoire(["e2e4", "c7c5"], "hitam"), SICILIAN)
        self.assertEqual(pembukaan_repertoire(
            ["d2d4", "g8f6", "c2c4", "e7e6", "g1f3", "d7d5"], "hitam"), RAGOZIN)
        self.assertIsNone(pembukaan_repertoire(["e2e4", "d7d5"], "hitam"),
                          "1.e4 d5 bukan repertoire: pilihan, bukan kesalahan")
        self.assertIsNone(pembukaan_repertoire(["d2d4", "c7c5"], "hitam"))


class UjiSoal(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = koneksi(Path(self.tmp.name) / "uji.db")
        buat_skema(self.conn)
        t = sekarang()
        for pid, nama in [(RAY, "Ray"), (GM1, "G1"), (GM2, "G2"), (LAWAN, "L")]:
            self.conn.execute("INSERT INTO pemain(id,nama,nama_normal,dibuat_pada,akun_pribadi) "
                              "VALUES(?,?,?,?,?)", (pid, nama, nama.lower(), t, int(pid == RAY)))
        self.id = 0

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def partai(self, langkah, putih, hitam, sumber, hasil="1-0", tanggal="2024.01.01"):
        self.id += 1
        teks = pgn(langkah, hasil)
        self.conn.execute(
            "INSERT INTO partai(id,putih_id,hitam_id,hasil,pgn,sumber,hash_unik,diimpor_pada,tanggal) "
            "VALUES(?,?,?,?,?,?,?,?,?)",
            (self.id, putih, hitam, hasil, teks, sumber, f"h{self.id}", sekarang(), tanggal))
        indeks_partai(self.conn, self.id, teks)

    def isi_gm(self, langkah, n, tanggal="2024.01.01"):
        for _ in range(n):
            self.partai(langkah, GM1, GM2, "twic", tanggal=tanggal)

    def test_kebiasaan_menyimpang_jadi_soal(self):
        # GM: 1.c4 e5 2.Nc3 Nf6 3.Nf3 (19x) dan 3.g3 (6x); tidak pernah 3.d3
        self.isi_gm("1. c4 e5 2. Nc3 Nf6 3. Nf3", 19)
        self.isi_gm("1. c4 e5 2. Nc3 Nf6 3. g3", 6)
        for _ in range(3):
            self.partai("1. c4 e5 2. Nc3 Nf6 3. d3", RAY, LAWAN, "lichess-user", "0-1")
        self.partai("1. c4 e5 2. Nc3 Nf6 3. g3", RAY, LAWAN, "lichess-user", "1-0")
        self.conn.commit()

        h = hitung_soal(self.conn, [RAY])
        self.assertEqual(len(h["soal"]), 1)
        s = h["soal"][0]
        self.assertEqual(s["pembukaan"], ENGLISH)
        self.assertEqual((s["dicapai"], s["menyimpang"]), (4, 3))
        self.assertEqual(s["langkah_ray"][0]["san"], "d3")
        self.assertEqual(s["langkah_ray"][0]["skor"], 0.0)
        self.assertEqual(set(s["diterima"]), {"g1f3", "g2g3"})
        self.assertEqual(s["contoh"]["jalur"], ["c4", "e5", "Nc3", "Nf6"])
        self.assertEqual(h["ringkasan"]["partai_repertoire"], 4)

    def test_menyimpang_sekali_bukan_soal(self):
        self.isi_gm("1. c4 e5 2. Nc3 Nf6 3. Nf3", 25)
        self.partai("1. c4 e5 2. Nc3 Nf6 3. d3", RAY, LAWAN, "lichess-user")
        self.partai("1. c4 e5 2. Nc3 Nf6 3. Nf3", RAY, LAWAN, "lichess-user")
        self.conn.commit()
        self.assertEqual(hitung_soal(self.conn, [RAY])["soal"], [])

    def test_posisi_dengan_sedikit_partai_gm_dilewati(self):
        self.isi_gm("1. c4 e5 2. Nc3 Nf6 3. Nf3", 10)
        for _ in range(3):
            self.partai("1. c4 e5 2. Nc3 Nf6 3. d3", RAY, LAWAN, "lichess-user")
        self.conn.commit()
        self.assertEqual(hitung_soal(self.conn, [RAY])["soal"], [])

    def test_partai_gm_lama_tidak_dihitung(self):
        self.isi_gm("1. c4 e5 2. Nc3 Nf6 3. Nf3", 25, tanggal="1950.01.01")
        for _ in range(3):
            self.partai("1. c4 e5 2. Nc3 Nf6 3. d3", RAY, LAWAN, "lichess-user")
        self.conn.commit()
        self.assertEqual(hitung_soal(self.conn, [RAY])["soal"], [])

    def test_di_luar_repertoire_tidak_jadi_soal(self):
        self.isi_gm("1. e4 e5 2. Nf3", 30)
        for _ in range(3):
            self.partai("1. e4 e5 2. Bc4", RAY, LAWAN, "lichess-user")
        self.conn.commit()
        h = hitung_soal(self.conn, [RAY])
        self.assertEqual(h["soal"], [])
        self.assertEqual(h["ringkasan"]["partai_repertoire"], 0)

    def test_langkah_pertama_tidak_dinilai_dan_hitam_ikut(self):
        # 1.e4 c5: c5 dimainkan GM hanya 1 dari 30 — tetap bukan soal karena
        # itu pilihan repertoire. Kesalahan sesudahnya (2...Nf6?) jadi soal.
        self.isi_gm("1. e4 e5", 29)
        self.isi_gm("1. e4 c5 2. Nf3 d6", 1)
        self.isi_gm("1. e4 c5 2. Nf3 d6", 24)
        for _ in range(2):
            self.partai("1. e4 c5 2. Nf3 Nf6", LAWAN, RAY, "lichess-user")
        self.conn.commit()
        soal = hitung_soal(self.conn, [RAY])["soal"]
        self.assertEqual([(s["warna"], s["langkah_ray"][0]["san"]) for s in soal], [("hitam", "Nf6")])
        self.assertEqual(soal[0]["langkah_ray"][0]["skor"], 0.0, "1-0 saat Ray Hitam = kalah")


if __name__ == "__main__":
    unittest.main()
