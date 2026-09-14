"""Skema v6 (permintaan frontend #7 & #8): kolom format dan tanda akun pribadi."""

import sys
import tempfile
import unittest
from pathlib import Path

AKAR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(AKAR))

from app.db import VERSI_SKEMA, buat_skema, koneksi, sekarang, versi_skema
from app.format_partai import FORMAT_VALID, format_dari_kontrol_waktu, kontrol_normal
from app.impor import impor_teks, isi_format_kosong, tandai_akun_pribadi
from app.repositori import cari_partai, cari_pemain
from app.ringkasan import ringkasan_pemain


class UjiFormat(unittest.TestCase):
    def test_tabel_konversi(self):
        kasus = {
            None: "tanpa", "": "tanpa", "-": "tanpa", "?": "tanpa", "abc": "tanpa",
            "60": "bullet", "120+1": "bullet",            # 60, 160
            "180": "blitz", "180+2": "blitz", "300": "blitz",  # 180, 260, 300
            "600": "rapid", "900+10": "rapid",            # 600, 1300
            "1800": "klasik", "5400+30": "klasik", "1/259200": "klasik",
            "10+0.1": "bullet", "0+1": "bullet", "15+0": "bullet",   # Chess.com pecahan, Lichess ultrabullet
            "3 min + 2 s": "blitz", "90 min + 30 s": "klasik",
        }
        for tc, harapan in kasus.items():
            with self.subTest(tc=tc):
                self.assertEqual(format_dari_kontrol_waktu(tc), harapan)
                self.assertIn(harapan, FORMAT_VALID)

    def test_sumber_otb_membaca_angka_kecil_sebagai_menit(self):
        # "3+2" dari broadcast = 3 menit (blitz); dari Lichess pengguna tetap detik (bullet).
        self.assertEqual(format_dari_kontrol_waktu("3+2", "lichess-broadcast"), "blitz")
        self.assertEqual(format_dari_kontrol_waktu("90+30", "twic"), "klasik")
        self.assertEqual(format_dari_kontrol_waktu("3+2", "lichess-user"), "bullet")
        self.assertEqual(format_dari_kontrol_waktu("15+0", "lichess-user"), "bullet")
        self.assertEqual(format_dari_kontrol_waktu("2700+30", "lichess-broadcast"), "klasik")


class UjiKontrolNormal(unittest.TestCase):
    """Bentuk tampilan kontrol waktu (Ray, 12 Sep: "180+0 ini ganti jadi 3+0").

    Aturan menit/detiknya harus sama dengan format_dari_kontrol_waktu, dan
    sama dengan kontrolNormal() di web/alat-catur.js — dua salinan yang
    menyimpang lebih buruk daripada tidak ada sama sekali.
    """

    def test_tabel_tampilan(self):
        kasus = {
            ("180+0", "lichess-user"): "3+0",       # detik: dibagi 60
            ("180", "chesscom"): "3+0",
            ("60+0", "lichess-user"): "1+0",
            ("600", "chesscom"): "10+0",
            ("300+3", "lichess-user"): "5+3",
            ("90+0", "lichess-user"): "1.5+0",
            ("10800+0", "lichess-user"): "180+0",   # 3 jam
            ("15+0", "lichess-user"): "15s+0",      # ultrabullet: satuan detik
            ("30", "chesscom"): "30s+0",
            ("10+0.1", "chesscom"): "10s+0.1",      # 10 DETIK, bukan 10 menit
            ("0+1", "lichess-user"): "0s+1",
            ("1/259200", "chesscom"): "1/259200",   # harian: apa adanya
            ("abc", "twic"): "abc",                 # tidak dikenal: apa adanya
            (None, "twic"): "",
            ("", "twic"): "",
            ("-", "twic"): "",
            ("?", "twic"): "",
        }
        for (tc, sumber), harapan in kasus.items():
            with self.subTest(tc=tc, sumber=sumber):
                self.assertEqual(kontrol_normal(tc, sumber), harapan)

    def test_sumber_otb_angka_kecil_sudah_menit(self):
        # Sama seperti penggolongan format: di broadcast/TWIC "3+2" = 3 menit.
        self.assertEqual(kontrol_normal("3+2", "lichess-broadcast"), "3+2")
        self.assertEqual(kontrol_normal("3 min + 2 s", "lichess-broadcast"), "3+2")
        self.assertEqual(kontrol_normal("90 min + 30 s", "twic"), "90+30")
        self.assertEqual(kontrol_normal("2700+30", "lichess-broadcast"), "45+30")
        # Dari Lichess pengguna angka yang sama berarti detik.
        self.assertEqual(kontrol_normal("3+2", "lichess-user"), "3s+2")

    def test_tanpa_sumber_angka_kecil_tidak_ditebak_jadi_menit(self):
        self.assertEqual(kontrol_normal("3+2"), "3s+2")


def pgn(putih, hitam, tc=None, langkah="1. e4 e5"):
    tag = ['[Event "u"]', '[Site "x"]', '[Date "2026.01.01"]', '[Round "-"]',
           f'[White "{putih}"]', f'[Black "{hitam}"]', '[Result "1-0"]']
    if tc:
        tag.append(f'[TimeControl "{tc}"]')
    return "\n".join(tag) + f"\n\n{langkah} 1-0\n"


class UjiSkemaV6(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "uji.db"
        self.conn = koneksi(self.path)
        buat_skema(self.conn)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_versi_dan_kolom_ada(self):
        # versi skema terus naik; yang dijaga di sini kolomnya, bukan angkanya
        self.assertEqual(versi_skema(self.conn), VERSI_SKEMA)
        kolom_pemain = {r[1] for r in self.conn.execute("PRAGMA table_info(pemain)")}
        kolom_partai = {r[1] for r in self.conn.execute("PRAGMA table_info(partai)")}
        self.assertIn("akun_pribadi", kolom_pemain)
        self.assertIn("format", kolom_partai)

    def test_migrasi_tabel_lama_menambah_kolom(self):
        # Tiru database v5: tabel pemain tanpa kolom akun_pribadi.
        with tempfile.TemporaryDirectory() as d:
            import sqlite3
            p = Path(d) / "lama.db"
            c = sqlite3.connect(p)
            c.execute("CREATE TABLE pemain (id INTEGER PRIMARY KEY, nama TEXT NOT NULL, "
                      "nama_normal TEXT NOT NULL, fide_id TEXT UNIQUE, negara TEXT, gelar TEXT, "
                      "elo_terakhir INTEGER, terverifikasi INTEGER NOT NULL DEFAULT 0, "
                      "dibuat_pada TEXT NOT NULL)")
            c.execute("INSERT INTO pemain(nama,nama_normal,dibuat_pada) VALUES('A','a','t')")
            c.commit(); c.close()
            c2 = koneksi(p)
            buat_skema(c2)                      # tidak boleh gagal, data tetap
            r = c2.execute("SELECT nama, akun_pribadi FROM pemain").fetchone()
            self.assertEqual((r["nama"], r["akun_pribadi"]), ("A", 0))
            c2.close()

    def test_format_diisi_saat_impor_dan_backfill(self):
        impor_teks(self.conn, pgn("A", "B", "180+2"), "lichess-user")
        impor_teks(self.conn, pgn("A", "B", "600", "1. d4 d5"), "chesscom")
        impor_teks(self.conn, pgn("A", "B", None, "1. c4 e5"), "twic")
        f = {r["kontrol_waktu"]: r["format"] for r in self.conn.execute(
            "SELECT kontrol_waktu, format FROM partai")}
        self.assertEqual(f, {"180+2": "blitz", "600": "rapid", None: "tanpa"})
        # backfill: kosongkan lalu isi lagi
        self.conn.execute("UPDATE partai SET format = NULL"); self.conn.commit()
        self.assertEqual(isi_format_kosong(self.conn), 3)
        self.assertEqual(isi_format_kosong(self.conn), 0)
        self.assertEqual(isi_format_kosong(self.conn, ulang=True), 3)
        self.assertEqual(cari_partai(self.conn, format_="blitz")["total"], 1)
        self.assertEqual(cari_partai(self.conn, format_="tanpa")["total"], 1)
        self.assertIn("format", cari_partai(self.conn)["partai"][0])

    def test_tandai_akun_pribadi_dan_saringan(self):
        impor_teks(self.conn, pgn("McLung_07", "lawan1", "60"), "lichess-user")
        impor_teks(self.conn, pgn("lawan2", "McLung_07", "60", "1. d4 d5"), "lichess-user")
        n = tandai_akun_pribadi(self.conn, ["McLung_07", "AkunTidakAda"])
        self.assertEqual(n, 1)
        pribadi = cari_pemain(self.conn, pribadi=True)
        self.assertEqual([p["nama"] for p in pribadi], ["McLung_07"])
        self.assertEqual(pribadi[0]["per_sumber"], [["lichess-user", 2]])
        self.assertEqual(len(cari_pemain(self.conn, pribadi=False)), 2)
        # pemain ber-FIDE ID tidak ikut tertandai walau namanya sama
        self.conn.execute("INSERT INTO pemain(nama,nama_normal,fide_id,terverifikasi,dibuat_pada) "
                          "VALUES('McLung_07','mclung_07','999',1,?)", (sekarang(),))
        self.conn.commit()
        self.assertEqual(tandai_akun_pribadi(self.conn, ["McLung_07"]), 1)

    def test_kecualikan_mencabut_tanda(self):
        # RyuMcLungG (bagian 6) pernah tertandai; kecualikan harus mencabutnya.
        impor_teks(self.conn, pgn("RyuMcLungG", "lawan", "60"), "chesscom")
        self.assertEqual(tandai_akun_pribadi(self.conn, ["ryumclungg"]), 1)
        self.assertEqual(tandai_akun_pribadi(self.conn, [], kecualikan=["ryumclungg"]), 0)
        self.assertEqual(cari_pemain(self.conn, pribadi=True), [])

    def test_ringkasan_menerima_format(self):
        d = ringkasan_pemain(self.conn, [1], format_="blitz")
        self.assertEqual(d["format"], "blitz")
        self.assertEqual(d["total"]["partai"], 0)


if __name__ == "__main__":
    unittest.main()
