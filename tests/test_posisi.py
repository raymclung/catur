"""Uji indeks posisi & penjelajah pembukaan, tanpa jaringan."""

import sys
import tempfile
import unittest
from pathlib import Path

AKAR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(AKAR))

import chess

from app.db import buat_skema, koneksi, sekarang
from app.posisi import (baris_posisi, cari_langkah, fen_kunci,
                        id_belum_terindeks, indeks_partai,
                        kunci_dari_fen_pengguna)


def pgn(langkah, hasil="1-0", elo=(2700, 2700)):
    return (f'[Event "Uji"]\n[Site "X"]\n[Date "2026.01.01"]\n[Round "1"]\n'
            f'[White "A"]\n[Black "B"]\n[Result "{hasil}"]\n'
            f'[WhiteElo "{elo[0]}"]\n[BlackElo "{elo[1]}"]\n\n{langkah} {hasil}\n')


class UjiKunci(unittest.TestCase):
    def test_transposisi_kunci_sama(self):
        a = chess.Board()
        for m in ["e4", "e6", "d4", "d5"]:
            a.push_san(m)
        b = chess.Board()
        for m in ["d4", "d5", "e4", "e6"]:
            b.push_san(m)
        self.assertNotEqual(a.fen(), b.fen()) if a.fen() != b.fen() else None
        self.assertEqual(fen_kunci(a), fen_kunci(b))

    def test_penghitung_dibuang(self):
        self.assertEqual(fen_kunci("rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 5 9"),
                         "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq -")

    def test_fen_pengguna_rusak_ditolak(self):
        with self.assertRaises(ValueError):
            kunci_dari_fen_pengguna("bukan fen")
        with self.assertRaises(ValueError):
            kunci_dari_fen_pengguna("8/8/8/8/8/8/8/8 w - - 0 1")   # tanpa raja

    def test_baris_posisi_dibatasi_maks_ply(self):
        b = baris_posisi(pgn("1. e4 e5 2. Nf3 Nc6 3. Bb5"), maks_ply=3)
        self.assertEqual([x[1] for x in b], [0, 1, 2])
        self.assertEqual(b[0][3], "e4")
        self.assertEqual(b[2][2], "g1f3")


class UjiPenjelajah(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = koneksi(Path(self.tmp.name) / "uji.db")
        buat_skema(self.conn)
        t = sekarang()
        self.conn.execute("INSERT INTO pemain(id,nama,nama_normal,dibuat_pada) VALUES(1,'A','a',?)", (t,))
        self.conn.execute("INSERT INTO pemain(id,nama,nama_normal,dibuat_pada) VALUES(2,'B','b',?)", (t,))
        self.partai = [
            (10, "1. e4 e5 2. Nf3", "1-0", "twic", (2700, 2650)),
            (11, "1. e4 c5 2. Nf3", "0-1", "twic", (2600, 2700)),
            (12, "1. e4 c5 2. Nc3", "1/2-1/2", "lichess-broadcast", (2500, 2500)),
            (13, "1. d4 d5", "1-0", "lichess-user", (2400, 2400)),
        ]
        for pid, l, h, s, elo in self.partai:
            self.conn.execute(
                "INSERT INTO partai(id,putih_id,hitam_id,hasil,pgn,sumber,hash_unik,diimpor_pada,"
                "elo_putih,elo_hitam,tanggal) VALUES(?,1,2,?,?,?,?,?,?,?,'2026.01.01')",
                (pid, h, pgn(l, h, elo), s, f"h{pid}", t, elo[0], elo[1]))
        self.conn.commit()

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def indeks_semua(self):
        for pid in id_belum_terindeks(self.conn):
            pgn_ = self.conn.execute("SELECT pgn FROM partai WHERE id=?", (pid,)).fetchone()[0]
            indeks_partai(self.conn, pid, pgn_)
        self.conn.commit()

    def test_indeks_bisa_dilanjutkan(self):
        self.assertEqual(len(id_belum_terindeks(self.conn)), 4)
        self.indeks_semua()
        self.assertEqual(id_belum_terindeks(self.conn), [])

    def test_posisi_awal_sumber_gm(self):
        self.indeks_semua()
        d = cari_langkah(self.conn, fen_kunci(chess.Board()), sumber="gm")
        self.assertEqual(d["total"], 3)                     # partai lichess-user tidak ikut
        self.assertEqual(d["langkah"][0]["san"], "e4")
        self.assertEqual(d["langkah"][0]["n"], 3)
        # 1-0, 0-1, 1/2 -> skor putih 50%
        self.assertEqual(d["langkah"][0]["skor_putih"], 50.0)

    def test_sumber_semua_dan_min_elo(self):
        self.indeks_semua()
        semua = cari_langkah(self.conn, fen_kunci(chess.Board()), sumber="semua")
        self.assertEqual(semua["total"], 4)
        kuat = cari_langkah(self.conn, fen_kunci(chess.Board()), sumber="gm", min_elo=2600)
        self.assertEqual(kuat["total"], 2)

    def test_setelah_1e4_c5(self):
        self.indeks_semua()
        b = chess.Board()
        b.push_san("e4"); b.push_san("c5")
        d = cari_langkah(self.conn, fen_kunci(b))
        self.assertEqual(sorted(l["san"] for l in d["langkah"]), ["Nc3", "Nf3"])
        self.assertEqual(len(d["partai"]), 2)

    def test_saringan_pemain_dan_warna(self):
        # Semua fixture: pemain 1 = Putih, pemain 2 = Hitam (permintaan #5).
        self.indeks_semua()
        awal = fen_kunci(chess.Board())
        self.assertEqual(cari_langkah(self.conn, awal, sumber="semua", pemain_id=1, warna="putih")["total"], 4)
        self.assertEqual(cari_langkah(self.conn, awal, sumber="semua", pemain_id=1, warna="hitam")["total"], 0)
        self.assertEqual(cari_langkah(self.conn, awal, sumber="semua", pemain_id=2)["total"], 4)
        self.assertEqual(cari_langkah(self.conn, awal, sumber="gm", pemain_id=2, warna="hitam")["total"], 3)
        self.assertEqual(cari_langkah(self.conn, awal, sumber="semua", pemain_id=99)["total"], 0)
        # partai contoh ikut tersaring, bukan hanya tabel langkah
        d = cari_langkah(self.conn, awal, sumber="semua", pemain_id=1, warna="hitam")
        self.assertEqual(d["partai"], [])

    def test_hapus_partai_menghapus_posisi(self):
        self.indeks_semua()
        self.conn.execute("DELETE FROM partai WHERE id = 10")
        self.conn.commit()
        n = self.conn.execute("SELECT COUNT(*) FROM posisi WHERE partai_id = 10").fetchone()[0]
        self.assertEqual(n, 0)


if __name__ == "__main__":
    unittest.main()


class UjiIndeksPenutup(unittest.TestCase):
    """
    Penjelajah pembukaan harus terlayani indeks penutup (skema v7).

    Tanpa kolom langkah di dalam indeks, tiap baris yang cocok perlu pencarian
    balik ke tabel: pada 9,4 juta baris posisi, permintaan pertama ke posisi
    awal memakan 30 detik. Tes ini menjaga indeksnya tidak terhapus diam-diam.
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.conn = koneksi(Path(self.tmp) / "uji.db")
        buat_skema(self.conn)

    def tearDown(self):
        import shutil
        self.conn.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_indeks_penutup_ada_dan_yang_lama_dibuang(self):
        nama = {r[0] for r in self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='posisi'")}
        self.assertIn("idx_posisi_fen_langkah", nama)
        self.assertNotIn("idx_posisi_fen", nama, "indeks lama mubazir, awalannya sama")

    def test_kueri_penjelajah_memakai_indeks_penutup(self):
        rencana = " ".join(str(r[-1]) for r in self.conn.execute(
            "EXPLAIN QUERY PLAN "
            "SELECT p.langkah_san, COUNT(*) FROM posisi p JOIN partai g ON g.id = p.partai_id "
            "WHERE p.fen_kunci = ? GROUP BY p.langkah_san", ("x",)))
        self.assertIn("idx_posisi_fen_langkah", rencana)
