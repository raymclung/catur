"""Uji query daftar partai di app/repositori.py, khususnya saringan hasil."""

import sys
import tempfile
import unittest
from pathlib import Path

AKAR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(AKAR))

from app.db import buat_skema, koneksi, sekarang
from app.repositori import _butuh_join, ambil_partai, cari_partai, cari_pemain, pola_like


class UjiSaringanHasil(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = koneksi(Path(self.tmp.name) / "uji.db")
        buat_skema(self.conn)
        t = sekarang()
        self.conn.execute("INSERT INTO pemain(id,nama,nama_normal,dibuat_pada) VALUES(1,'A','a',?)", (t,))
        self.conn.execute("INSERT INTO pemain(id,nama,nama_normal,dibuat_pada) VALUES(2,'B','b',?)", (t,))
        for i, hasil in enumerate(["1-0", "0-1", "1/2-1/2", "1/2-1/2", "*"], start=1):
            self.conn.execute(
                "INSERT INTO partai(id,putih_id,hitam_id,hasil,pgn,sumber,hash_unik,diimpor_pada) "
                "VALUES(?,1,2,?,'x','uji',?,?)", (i, hasil, f"h{i}", t))
        self.conn.commit()

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def hasil(self, **kw):
        return sorted(g["hasil"] for g in cari_partai(self.conn, **kw)["partai"])

    def test_tanpa_saringan_semua_termasuk_remis(self):
        self.assertEqual(cari_partai(self.conn)["total"], 5)

    def test_menang_kalah_menyembunyikan_remis_dan_berjalan(self):
        self.assertEqual(self.hasil(hasil="menang-kalah"), ["0-1", "1-0"])

    def test_remis_tetap_ada_di_database(self):
        # Saringan hanya menyembunyikan; remis tidak dihapus.
        self.assertEqual(self.hasil(hasil="1/2-1/2"), ["1/2-1/2", "1/2-1/2"])

    def test_satu_hasil_tetap_bekerja(self):
        self.assertEqual(self.hasil(hasil="1-0"), ["1-0"])
        self.assertEqual(self.hasil(hasil="*"), ["*"])


PGN_E4 = ('[Event "u"]\n[Site "x"]\n[Date "2026.01.01"]\n[Round "1"]\n[White "A"]\n'
          '[Black "B"]\n[Result "1-0"]\n\n1. e4 e5 2. Nf3 1-0\n')
PGN_D4 = PGN_E4.replace("1. e4 e5 2. Nf3", "1. d4 d5 2. c4")


class UjiSaringanPosisiDanSumber(unittest.TestCase):
    """Permintaan frontend #3: fen= dan sumber=gm di /api/partai."""

    def setUp(self):
        from app.posisi import indeks_partai
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = koneksi(Path(self.tmp.name) / "uji.db")
        buat_skema(self.conn)
        t = sekarang()
        self.conn.execute("INSERT INTO pemain(id,nama,nama_normal,dibuat_pada) VALUES(1,'A','a',?)", (t,))
        self.conn.execute("INSERT INTO pemain(id,nama,nama_normal,dibuat_pada) VALUES(2,'B','b',?)", (t,))
        for pid, pgn, sumber in [(1, PGN_E4, "twic"), (2, PGN_E4, "lichess-user"), (3, PGN_D4, "lichess-broadcast")]:
            self.conn.execute(
                "INSERT INTO partai(id,putih_id,hitam_id,hasil,pgn,sumber,hash_unik,diimpor_pada) "
                "VALUES(?,1,2,'1-0',?,?,?,?)", (pid, pgn, sumber, f"h{pid}", t))
            indeks_partai(self.conn, pid, pgn)
        self.conn.commit()

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_fen_hanya_partai_yang_mencapai_posisi(self):
        import chess
        from app.posisi import fen_kunci
        b = chess.Board(); b.push_san("e4"); b.push_san("e5")
        d = cari_partai(self.conn, fen_kunci=fen_kunci(b))
        self.assertEqual(sorted(g["id"] for g in d["partai"]), [1, 2])

    def test_fen_digabung_sumber_gm(self):
        import chess
        from app.posisi import fen_kunci
        b = chess.Board(); b.push_san("e4")
        d = cari_partai(self.conn, fen_kunci=fen_kunci(b), sumber="gm")
        self.assertEqual([g["id"] for g in d["partai"]], [1])   # lichess-user dibuang

    def test_sumber_gm_tanpa_fen(self):
        d = cari_partai(self.conn, sumber="gm")
        self.assertEqual(sorted(g["id"] for g in d["partai"]), [1, 3])

    def test_sumber_gm_tanpa_partai_mesin(self):
        """Kasparov-Deep Blue (pemain.jenis = 'mesin') bukan partai GM; penjelajah
        sudah menyaringnya, daftar partai belum (audit 27 Sep 2026: 16 partai)."""
        self.conn.execute("UPDATE pemain SET jenis = 'mesin' WHERE id = 2")
        self.assertEqual(cari_partai(self.conn, sumber="gm")["partai"], [])
        self.assertEqual(cari_partai(self.conn)["total"], 3)      # tanpa saringan tetap ada

    def test_posisi_awal_semua_partai(self):
        import chess
        from app.posisi import fen_kunci
        self.assertEqual(cari_partai(self.conn, fen_kunci=fen_kunci(chess.Board()))["total"], 3)


class UjiFieldKontrolNormal(unittest.TestCase):
    """Daftar dan detail partai mengirim `kontrol_normal` (dihitung, bukan kolom)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = koneksi(Path(self.tmp.name) / "uji.db")
        buat_skema(self.conn)
        t = sekarang()
        self.conn.execute("INSERT INTO pemain(id,nama,nama_normal,dibuat_pada) VALUES(1,'A','a',?)", (t,))
        self.conn.execute("INSERT INTO pemain(id,nama,nama_normal,dibuat_pada) VALUES(2,'B','b',?)", (t,))
        for i, (tc, sumber) in enumerate([("180+0", "lichess-user"), ("3+2", "lichess-broadcast"),
                                          (None, "twic")], start=1):
            self.conn.execute(
                "INSERT INTO partai(id,putih_id,hitam_id,hasil,pgn,sumber,kontrol_waktu,hash_unik,diimpor_pada) "
                "VALUES(?,1,2,'1-0','x',?,?,?,?)", (i, sumber, tc, f"h{i}", t))
        self.conn.commit()

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_daftar(self):
        oleh_id = {g["id"]: g["kontrol_normal"] for g in cari_partai(self.conn)["partai"]}
        self.assertEqual(oleh_id, {1: "3+0", 2: "3+2", 3: ""})

    def test_detail(self):
        self.assertEqual(ambil_partai(self.conn, 1)["kontrol_normal"], "3+0")
        self.assertEqual(ambil_partai(self.conn, 1)["kontrol_waktu"], "180+0")
        self.assertIn("pgn", ambil_partai(self.conn, 1))

class UjiTotalTanpaJoin(unittest.TestCase):
    """
    Total daftar partai dihitung dari tabel `partai` saja kalau saringannya
    tidak menyentuh tabel lain.

    Diukur 13 Sep 2026 pada 266 ribu partai: COUNT(*) lewat empat sambungan
    tabel 1,0 detik, dari `partai` saja 0,01 detik. Yang dijaga tes ini:
    jalur cepat dan jalur lama harus memberi ANGKA YANG SAMA.
    """

    def setUp(self):
        import shutil
        self.tmp = tempfile.mkdtemp()
        self._rmtree = shutil.rmtree
        self.conn = koneksi(Path(self.tmp) / "uji.db")
        buat_skema(self.conn)
        from app.impor import impor_teks
        def pgn(putih, hitam, hasil, eco, sumber, tc="180+0"):
            tag = ['[Event "u"]', '[Site "x"]', f'[Date "2026.01.0{len(hasil)}"]', '[Round "-"]',
                   f'[White "{putih}"]', f'[Black "{hitam}"]', f'[Result "{hasil}"]',
                   f'[ECO "{eco}"]', f'[TimeControl "{tc}"]']
            langkah = {"A22": "1. c4 e5 2. Nc3 Nf6", "B90": "1. e4 c5 2. Nf3 d6",
                       "D38": "1. d4 Nf6 2. c4 e6 3. Nc3 Bb4"}[eco]
            teks = chr(10).join(tag) + chr(10) * 2 + f"{langkah} {hasil}" + chr(10)
            impor_teks(self.conn, teks, sumber)
        pgn("GM Satu, A", "GM Dua, B", "1-0", "A22", "twic")
        pgn("GM Satu, A", "GM Tiga, C", "0-1", "B90", "twic")
        pgn("McLung_07", "lawan", "1/2-1/2", "D38", "lichess-user")
        # gelar tidak ada di tag PGN uji ini, jadi dipasang langsung — yang
        # sedang diuji jalur hitungnya, bukan pembacaan gelar dari PGN
        self.conn.execute("UPDATE pemain SET gelar = 'GM' WHERE nama LIKE 'GM %'")
        self.conn.commit()

    def tearDown(self):
        self.conn.close()
        self._rmtree(self.tmp, ignore_errors=True)

    def test_saringan_mana_yang_butuh_join(self):
        self.assertFalse(_butuh_join(None, None, None))
        self.assertTrue(_butuh_join("Carlsen", None, None))   # nama pemain/turnamen
        self.assertTrue(_butuh_join(None, "GM", None))        # gelar ada di tabel pemain
        self.assertTrue(_butuh_join(None, None, True))        # tabel analisis
        self.assertTrue(_butuh_join(None, None, False))

    def test_total_sama_lewat_kedua_jalur(self):
        from app.repositori import DARI_DAFTAR, _syarat
        kasus = [
            {},
            {"sumber": "twic"},
            {"sumber": "gm"},
            {"hasil": "menang-kalah"},
            {"eco": "A"},
            {"format_": "blitz"},
            {"eco_dari": "A10", "eco_sampai": "A39"},
        ]
        for k in kasus:
            with self.subTest(**k):
                cepat = cari_partai(self.conn, **k)["total"]
                where, params = _syarat(k.get("cari"), k.get("sumber"), k.get("eco"),
                                        k.get("hasil"), None, None, None, None,
                                        k.get("eco_dari"), k.get("eco_sampai"), None,
                                        k.get("format_"))
                lama = self.conn.execute(
                    f"SELECT COUNT(*) {DARI_DAFTAR} {where}", params).fetchone()[0]
                self.assertEqual(cepat, lama)

    def test_saringan_bersambung_tetap_benar(self):
        self.assertEqual(cari_partai(self.conn, cari="McLung")["total"], 1)
        self.assertEqual(cari_partai(self.conn, gelar="GM")["total"], 2)   # lewat tabel pemain
        self.assertEqual(cari_partai(self.conn, dianalisis=False)["total"], 3)

if __name__ == "__main__":
    unittest.main()


class UjiPencarianDanTanggal(unittest.TestCase):
    """Audit 27 Sep 2026: % dan _ tidak di-escape; tanggal '??' terurut terbaru."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = koneksi(Path(self.tmp.name) / "uji.db")
        buat_skema(self.conn)
        t = sekarang()
        for id_, nama in ((1, "McLung_07"), (2, "McLungX07"), (3, "Diskon 50%"), (4, "Biasa")):
            self.conn.execute("INSERT INTO pemain(id,nama,nama_normal,dibuat_pada) VALUES(?,?,?,?)",
                              (id_, nama, nama.lower(), t))
        for id_, tanggal in ((1, "2023.12.31"), (2, "2023.??.??"), (3, "2023.06.??"), (4, "2022.01.01")):
            self.conn.execute(
                "INSERT INTO partai(id,putih_id,hitam_id,hasil,pgn,sumber,hash_unik,diimpor_pada,tanggal) "
                "VALUES(?,1,4,'1-0','x','uji',?,?,?)", (id_, f"h{id_}", t, tanggal))
        self.conn.commit()

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def nama(self, cari):
        return sorted(p["nama"] for p in cari_pemain(self.conn, cari))

    def test_persen_dan_garis_bawah_harfiah(self):
        self.assertEqual(self.nama("%"), ["Diskon 50%"])
        self.assertEqual(self.nama("_"), ["McLung_07"])
        self.assertEqual(self.nama("Lung_0"), ["McLung_07"])      # dulu McLungX07 ikut
        self.assertEqual(self.nama("lung"), ["McLungX07", "McLung_07"])

    def test_pola_like(self):
        self.assertEqual(pola_like("a%b_c\\"), "%a\\%b\\_c\\\\%")
        self.assertEqual(pola_like("A1", awal=False), "A1%")

    def test_cari_partai_tidak_menganggap_persen_wildcard(self):
        self.assertEqual(cari_partai(self.conn, cari="%")["total"], 0)

    def test_tanggal_tak_diketahui_tidak_terurut_teratas(self):
        terbaru = [g["tanggal"] for g in cari_partai(self.conn, urut="terbaru")["partai"]]
        self.assertEqual(terbaru, ["2023.12.31", "2023.06.??", "2023.??.??", "2022.01.01"])
        terlama = [g["tanggal"] for g in cari_partai(self.conn, urut="terlama")["partai"]]
        self.assertEqual(terlama, ["2022.01.01", "2023.??.??", "2023.06.??", "2023.12.31"])
