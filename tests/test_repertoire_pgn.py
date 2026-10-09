"""Uji ekspor repertoire ke PGN (app/repertoire_pgn.py), tanpa jaringan."""

import io
import sys
import tempfile
import unittest
from pathlib import Path

AKAR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(AKAR))

import chess.pgn

from app.db import buat_skema, koneksi, sekarang
from app.posisi import indeks_partai
from app.repertoire_pgn import pgn_repertoire

SAYA, LAWAN, GM1, GM2 = 1, 2, 3, 4


def pgn(langkah, hasil):
    return (f'[Event "Uji"]\n[Site "X"]\n[Date "2026.01.01"]\n[Round "1"]\n'
            f'[White "A"]\n[Black "B"]\n[Result "{hasil}"]\n\n{langkah} {hasil}\n')


class Ekspor(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = koneksi(Path(self.tmp.name) / "uji.db")
        buat_skema(self.conn)
        t = sekarang()
        for pid, nama in [(SAYA, "Saya"), (LAWAN, "Lawan"), (GM1, "G1"), (GM2, "G2")]:
            self.conn.execute("INSERT INTO pemain(id,nama,nama_normal,dibuat_pada,akun_pribadi) "
                              "VALUES(?,?,?,?,?)", (pid, nama, nama.lower(), t, int(pid == SAYA)))
        self.id = 0

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def partai(self, langkah, putih, hitam, sumber, hasil="1-0", n=1):
        for _ in range(n):
            self.id += 1
            teks = pgn(langkah, hasil)
            self.conn.execute(
                "INSERT INTO partai(id,putih_id,hitam_id,hasil,pgn,sumber,hash_unik,diimpor_pada,tanggal) "
                "VALUES(?,?,?,?,?,?,?,?,'2024.01.01')",
                (self.id, putih, hitam, hasil, teks, sumber, f"h{self.id}", sekarang()))
            indeks_partai(self.conn, self.id, teks)
        self.conn.commit()

    def baca(self, teks):
        return chess.pgn.read_game(io.StringIO(teks))

    def test_garis_utama_dan_variasi_dari_partai_sendiri(self):
        self.partai("1. c4 e5 2. Nc3", SAYA, LAWAN, "lichess-user", "1-0", n=12)
        self.partai("1. c4 c5 2. Nf3", SAYA, LAWAN, "lichess-user", "0-1", n=11)
        self.partai("1. e4 e5", SAYA, LAWAN, "lichess-user", "1-0", n=3)   # di bawah minimal
        teks = pgn_repertoire(self.conn, [SAYA], "putih", kedalaman=6, cabang=2, minimal=10,
                              nama="Saya")
        game = self.baca(teks)
        self.assertEqual(game.headers["White"], "Saya")
        self.assertEqual([v.move.uci() for v in game.variations], ["c2c4"],
                         "1.e4 cuma 3 partai, di bawah minimal")
        c4 = game.variations[0]
        self.assertIn("kamu 23x", c4.comment)
        jawaban = sorted(v.move.uci() for v in c4.variations)
        self.assertEqual(jawaban, ["c7c5", "e7e5"])

    def test_catatan_menyebut_praktik_gm(self):
        self.partai("1. c4 e5 2. Nc3", SAYA, LAWAN, "lichess-user", "1-0", n=12)
        self.partai("1. Nf3", GM1, GM2, "twic", "1/2-1/2", n=25)     # GM hampir selalu 1.Nf3
        self.partai("1. c4", GM1, GM2, "twic", "1/2-1/2", n=1)
        teks = pgn_repertoire(self.conn, [SAYA], "putih", kedalaman=2, cabang=1, minimal=10)
        catatan = self.baca(teks).variations[0].comment
        self.assertIn("jarang di GM", catatan)
        self.assertIn("GM paling sering Nf3", catatan)

    def test_langkah_yang_tak_pernah_dimainkan_gm_ditulis_apa_adanya(self):
        self.partai("1. h4", SAYA, LAWAN, "lichess-user", "1-0", n=12)
        self.partai("1. e4", GM1, GM2, "twic", "1/2-1/2", n=30)
        teks = pgn_repertoire(self.conn, [SAYA], "putih", kedalaman=2, cabang=1, minimal=10)
        self.assertIn("tidak pernah dimainkan GM", self.baca(teks).variations[0].comment)

    def test_skor_dari_sudut_sendiri_saat_hitam(self):
        # Saya Hitam dan menang semua (0-1) -> skor 100%, bukan 0%
        self.partai("1. e4 c5 2. Nf3", LAWAN, SAYA, "lichess-user", "0-1", n=12)
        teks = pgn_repertoire(self.conn, [SAYA], "hitam", kedalaman=4, cabang=1, minimal=10)
        game = self.baca(teks)
        c5 = game.variations[0].variations[0]
        self.assertEqual(c5.move.uci(), "c7c5")
        self.assertIn("skor 100,0%", c5.comment)

    def test_nama_berkutip_tidak_merusak_tag(self):
        """Dulu: [White "a"b"] — tag rusak (audit 27 Sep 2026)."""
        teks = pgn_repertoire(self.conn, [SAYA], "putih", nama='a"b\\c')
        self.assertIn('[White "a\'b/c"]', teks)
        self.assertEqual(self.baca(teks).headers["White"], "a'b/c")

    def test_pemain_tak_ada_404(self):
        from fastapi import HTTPException
        import app.repertoire_pgn as rp
        asli = rp.koneksi
        rp.koneksi = lambda: koneksi(Path(self.tmp.name) / "uji.db")
        try:
            with self.assertRaises(HTTPException) as c:
                rp.api_repertoire_pgn(pemain_id=[SAYA, 987654], warna="putih", kedalaman=8,
                                      cabang=2, minimal=10, nama="Saya")
        finally:
            rp.koneksi = asli
        self.assertEqual(c.exception.status_code, 404)
        self.assertIn("987654", c.exception.detail)

    def test_tanpa_partai_menghasilkan_pgn_kosong_yang_sah(self):
        teks = pgn_repertoire(self.conn, [SAYA], "putih", kedalaman=4, cabang=2, minimal=10)
        game = self.baca(teks)
        self.assertIsNotNone(game)
        self.assertEqual(list(game.variations), [])



class UjiHeaderUnduhan(unittest.TestCase):
    """Audit 27 Sep: nama dengan apostrof HP (’) membuat /api/repertoire/pgn 500."""

    def test_nama_non_latin_tidak_membuat_respons_gagal(self):
        from fastapi.responses import PlainTextResponse
        from app.repertoire_pgn import header_unduhan
        for nama in ("Ray’s", "Раймонд", 'a"b', "Saya"):
            with self.subTest(nama=nama):
                berkas = f"repertoire-{nama}-putih.pgn"
                r = PlainTextResponse("x", headers={"Content-Disposition": header_unduhan(berkas)})
                h = r.headers["content-disposition"]
                self.assertEqual(h.count('"'), 2)              # kutip nama tidak merusak header
                self.assertIn("filename*=UTF-8''", h)

    def test_nama_asli_terbawa_di_filename_bintang(self):
        from urllib.parse import unquote
        from app.repertoire_pgn import header_unduhan
        h = header_unduhan("repertoire-Ray’s-putih.pgn")
        self.assertEqual(unquote(h.split("filename*=UTF-8''")[1]), "repertoire-Ray’s-putih.pgn")


if __name__ == "__main__":
    unittest.main()
