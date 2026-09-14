"""
Sidik jari partai harus sama walau sumbernya menulis nama berbeda.

Kasus nyata 9 Sep 2026: 14 partai GCL 2026 masuk dua kali karena Lichess
menulis 'Anand, Viswanathan' dan TWIC menulis 'Anand,V'.
"""

import io
import sys
import tempfile
import unittest
from pathlib import Path

AKAR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(AKAR))

import chess.pgn

from app.db import buat_skema, koneksi
from app.impor import (hash_partai, hitung_ulang_hash, impor_satu,
                       nama_belakang)

LANGKAH = "e2e4 e7e5 g1f3 b8c6"


def pgn(putih, hitam, hasil="1-0", langkah="1. e4 e5 2. Nf3 Nc6", fide=True,
        event="Uji", babak="1"):
    tag = [f'[Event "{event}"]', '[Site "Jakarta INA"]', '[Date "2026.09.05"]',
           f'[Round "{babak}"]', f'[White "{putih}"]', f'[Black "{hitam}"]',
           f'[Result "{hasil}"]']
    if fide:
        tag += ['[WhiteFideId "5000017"]', '[BlackFideId "1503014"]']
    return "\n".join(tag) + f"\n\n{langkah} {hasil}\n"


def game(teks):
    return chess.pgn.read_game(io.StringIO(teks))


class UjiNamaBelakang(unittest.TestCase):
    def test_singkatan_dan_lengkap_sama(self):
        self.assertEqual(nama_belakang("Anand,V"), nama_belakang("Anand, Viswanathan"))
        self.assertEqual(nama_belakang("Dominguez Perez,L"), "dominguez")

    def test_tanpa_koma_pakai_kata_pertama(self):
        self.assertEqual(nama_belakang("Pranesh M"), "pranesh")
        self.assertEqual(nama_belakang("McLung_07"), "mclung_07")

    def test_gelar_dibuang(self):
        self.assertEqual(nama_belakang("GM Carlsen, Magnus"), "carlsen")

    def test_hash_sama_lintas_penulisan(self):
        a = hash_partai("Anand, Viswanathan", "Carlsen, Magnus", "2026.09.05", "1-0", LANGKAH)
        b = hash_partai("Anand,V", "Carlsen,M", "2026.09.05", "1-0", LANGKAH)
        self.assertEqual(a, b)

    def test_hash_beda_kalau_langkah_beda(self):
        a = hash_partai("Anand,V", "Carlsen,M", "2026.09.05", "1-0", LANGKAH)
        b = hash_partai("Anand,V", "Carlsen,M", "2026.09.05", "1-0", LANGKAH + " f1c4")
        self.assertNotEqual(a, b)


class UjiImporLintasSumber(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = koneksi(Path(self.tmp.name) / "uji.db")
        buat_skema(self.conn)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def jumlah(self):
        return self.conn.execute("SELECT COUNT(*) FROM partai").fetchone()[0]

    def test_versi_twic_dikenali_duplikat(self):
        self.assertEqual(impor_satu(self.conn, game(pgn("Anand, Viswanathan", "Carlsen, Magnus")), "lichess-broadcast"), "baru")
        self.assertEqual(impor_satu(self.conn, game(pgn("Anand,V", "Carlsen,M", event="TWIC Ev")), "twic"), "duplikat")
        self.assertEqual(self.jumlah(), 1)

    def test_partai_berjalan_diperbarui_oleh_sumber_lain(self):
        # Lichess: masih berjalan, 2 langkah. TWIC: selesai, 4 langkah, turnamen beda.
        berjalan = pgn("Anand, Viswanathan", "Carlsen, Magnus", hasil="*",
                       langkah="1. e4 e5", event="Global Chess League", babak="20.1")
        selesai = pgn("Anand,V", "Carlsen,M", hasil="1-0",
                      langkah="1. e4 e5 2. Nf3 Nc6", event="TechM GCL 2026", babak="20.1")
        self.assertEqual(impor_satu(self.conn, game(berjalan), "lichess-broadcast"), "baru")
        self.assertEqual(impor_satu(self.conn, game(selesai), "twic"), "diperbarui")
        self.assertEqual(self.jumlah(), 1)
        r = self.conn.execute("SELECT hasil, jumlah_langkah FROM partai").fetchone()
        self.assertEqual((r["hasil"], r["jumlah_langkah"]), ("1-0", 2))

    def test_tanpa_fide_id_tidak_pernah_diperbarui_lintas_sumber(self):
        # Pasangan tanpa FIDE ID = partai online; versi lebih panjang di hari
        # yang sama harus jadi partai baru, sesuai jaminan tests/test_impor.py.
        a = pgn("McLung_07", "lawan", hasil="*", langkah="1. e4 e5", fide=False, babak="-")
        b = pgn("McLung_07", "lawan", hasil="*", langkah="1. e4 e5 2. Nf3", fide=False, babak="-")
        self.assertEqual(impor_satu(self.conn, game(a), "lichess-user"), "baru")
        self.assertEqual(impor_satu(self.conn, game(b), "lichess-user"), "baru")
        self.assertEqual(self.jumlah(), 2)

    def test_partai_lain_di_hari_sama_tidak_menimpa(self):
        # Langkah berbeda sejak awal -> bukan awalan -> partai baru, bukan pembaruan.
        berjalan = pgn("Anand, Viswanathan", "Carlsen, Magnus", hasil="*", langkah="1. d4 d5")
        lain = pgn("Anand,V", "Carlsen,M", hasil="1-0", langkah="1. e4 e5 2. Nf3 Nc6", event="Lain")
        impor_satu(self.conn, game(berjalan), "lichess-broadcast")
        self.assertEqual(impor_satu(self.conn, game(lain), "twic"), "baru")
        self.assertEqual(self.jumlah(), 2)

    def test_hitung_ulang_hash_membersihkan_duplikat_lama(self):
        # Simulasikan hash lama yang berbeda: masukkan dua baris dengan hash palsu.
        for i, (w, b) in enumerate([("Anand, Viswanathan", "Carlsen, Magnus"), ("Anand,V", "Carlsen,M")]):
            g = game(pgn(w, b))
            impor_satu(self.conn, g, "uji")
            self.conn.execute("UPDATE partai SET hash_unik = ? WHERE id = (SELECT MAX(id) FROM partai)", (f"lama-{i}",))
            if i == 0:
                # paksa baris kedua bisa masuk walau sebenarnya duplikat
                pass
        self.conn.commit()
        # Kalau baris kedua ditolak sebagai duplikat oleh hash baru, buat manual.
        if self.jumlah() == 1:
            r = self.conn.execute("SELECT * FROM partai").fetchone()
            self.conn.execute(
                "INSERT INTO partai(putih_id,hitam_id,tanggal,hasil,pgn,sumber,hash_unik,diimpor_pada,jumlah_langkah) "
                "VALUES(?,?,?,?,?,?,?,?,?)",
                (r["putih_id"], r["hitam_id"], r["tanggal"], r["hasil"], r["pgn"], "twic", "lama-1", r["diimpor_pada"], r["jumlah_langkah"]))
            self.conn.commit()
        self.assertEqual(self.jumlah(), 2)
        h = hitung_ulang_hash(self.conn)
        self.assertEqual(h["dihapus"], 1)
        self.assertEqual(self.jumlah(), 1)


if __name__ == "__main__":
    unittest.main()
