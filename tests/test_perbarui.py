"""Uji bagian murni perbarui.py: nomor edisi, saringan mingguan, edisi yang belum tercatat."""

import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

AKAR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(AKAR))

from app.db import buat_skema, koneksi
from app.sumber_impor import catat
from perbarui import (EDISI_JANGKAR, TANGGAL_JANGKAR, edisi_belum, edisi_terbaru,
                      lolos_mingguan, siapkan_skema)

HEADER_OTB = {"Site": "Stavanger NOR", "WhiteElo": "2803", "BlackElo": "2745"}


class UjiEdisi(unittest.TestCase):
    def test_hari_jangkar(self):
        self.assertEqual(edisi_terbaru(TANGGAL_JANGKAR), EDISI_JANGKAR)

    def test_enam_hari_kemudian_masih_edisi_sama(self):
        self.assertEqual(edisi_terbaru(date(2026, 9, 13)), EDISI_JANGKAR)

    def test_senin_berikutnya_naik_satu(self):
        self.assertEqual(edisi_terbaru(date(2026, 9, 14)), EDISI_JANGKAR + 1)
        self.assertEqual(edisi_terbaru(date(2026, 12, 7)), EDISI_JANGKAR + 13)


class UjiSaringan(unittest.TestCase):
    def test_otb_kuat_lolos(self):
        self.assertTrue(lolos_mingguan(HEADER_OTB, 2500))

    def test_online_dibuang(self):
        self.assertFalse(lolos_mingguan({**HEADER_OTB, "Site": "Chess.com INT"}, 2500))

    def test_satu_pemain_di_bawah_ambang_dibuang(self):
        self.assertFalse(lolos_mingguan({**HEADER_OTB, "BlackElo": "2450"}, 2500))
        self.assertTrue(lolos_mingguan({**HEADER_OTB, "BlackElo": "2450"}, 2400))

    def test_tanpa_elo_dibuang_bukan_ditebak(self):
        h = dict(HEADER_OTB); del h["BlackElo"]
        self.assertFalse(lolos_mingguan(h, 2500))


class UjiEdisiBelum(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = koneksi(Path(self.tmp.name) / "uji.db")
        buat_skema(self.conn)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_tanpa_catatan_mulai_setelah_jangkar(self):
        self.assertEqual(edisi_belum(self.conn, EDISI_JANGKAR + 2), [EDISI_JANGKAR + 1, EDISI_JANGKAR + 2])

    def test_edisi_tercatat_dilewati(self):
        catat(self.conn, "twic", str(EDISI_JANGKAR + 1), "TWIC", "u", 7000, 90, 90)
        self.assertEqual(edisi_belum(self.conn, EDISI_JANGKAR + 3), [EDISI_JANGKAR + 2, EDISI_JANGKAR + 3])

    def test_sudah_terkini_kosong(self):
        catat(self.conn, "twic", str(EDISI_JANGKAR + 1), "TWIC", "u", 7000, 90, 90)
        self.assertEqual(edisi_belum(self.conn, EDISI_JANGKAR + 1), [])


class UjiSaringanBintang(unittest.TestCase):
    """
    impor_broadcast.saring_bintang: broadcast mingguan hanya partai pemain kuat.

    Aturan berjalan (Ray, 25 Sep 2026, "opsi F"): salah satu pemain Elo >= 2400,
    ATAU salah satu bergelar GM/WGM berapa pun ratingnya. Lawannya tidak
    diperiksa -- satu pemain kuat sudah cukup. Riwayat ambang: 2700 (15 Sep)
    -> 2500 (15 Sep) -> 2400 (25 Sep); gelar: GM (21 Sep) -> GM+WGM (25 Sep).
    """

    @staticmethod
    def partai(event, putih=None, hitam=None, gelar_putih="", gelar_hitam=""):
        kepala = f'[Event "{event}"]\n'
        if putih:
            kepala += f'[WhiteElo "{putih}"]\n'
        if hitam:
            kepala += f'[BlackElo "{hitam}"]\n'
        if gelar_putih:
            kepala += f'[WhiteTitle "{gelar_putih}"]\n'
        if gelar_hitam:
            kepala += f'[BlackTitle "{gelar_hitam}"]\n'
        return kepala + "\n1. e4 e5 *\n\n"

    def test_gelar_gm_lolos_berapa_pun_ratingnya(self):
        # Ray, 21 Sep 2026 memilih opsi 3: semua partai ber-GM ikut disimpan
        from impor_broadcast import partai_bintang, saring_bintang
        self.assertTrue(partai_bintang(2342, 1784, gelar_putih="GM"))
        self.assertTrue(partai_bintang(1990, 2447, gelar_hitam="gm"))     # huruf kecil pun kena
        self.assertFalse(partai_bintang(2350, 2100, gelar_putih="IM"))    # gelar lain tidak
        teks = self.partai("GM lawan amatir", 2342, 1784, "GM") + self.partai("IM lawan amatir", 2350, 1800, "", "IM")
        sisa, dibuang = saring_bintang(teks)
        self.assertIn("GM lawan amatir", sisa)
        self.assertEqual(dibuang, 1)

    def test_aturan_elo(self):
        # Ambang 2400 sejak 25 Sep 2026 (opsi F); sebelumnya 2500.
        from impor_broadcast import partai_bintang
        self.assertTrue(partai_bintang(2780, 2100))       # super GM lawan amatir (Olimpiade ronde awal)
        self.assertTrue(partai_bintang(2530, 2410))       # pemain putri top (Candidates Women)
        self.assertTrue(partai_bintang(2499, 2450))       # dua IM kuat: dulu dibuang, kini masuk
        self.assertTrue(partai_bintang(2400, 0))          # tepat di ambang
        self.assertFalse(partai_bintang(2399, 2350))      # satu poin di bawah ambang
        self.assertFalse(partai_bintang(2399, 0))

    def test_gelar_putri_hanya_wgm_yang_lolos(self):
        # Ray, 25 Sep 2026 (opsi F): WGM ikut lolos, gelar putri lain tidak.
        from impor_broadcast import partai_bintang
        self.assertTrue(partai_bintang(2251, 2180, gelar_putih="WGM"))
        self.assertTrue(partai_bintang(2100, 2290, gelar_hitam="wgm"))    # huruf kecil pun kena
        for gelar in ("WIM", "WFM", "WCM", "IM", "FM", "CM"):
            with self.subTest(gelar=gelar):
                self.assertFalse(partai_bintang(2300, 2100, gelar_putih=gelar))

    def test_batas_terukur_olimpiade_samarkand(self):
        """
        Batas yang diketahui, diukur 25 Sep 2026 pada PGN mentah 27 ronde
        Olimpiade Samarkand (2.364 partai): aturan lama meloloskan 426 (18%),
        opsi F meloloskan 660 (28%).

        Yang dicatat di sini adalah partai NYATA dari pengukuran itu, supaya
        kalau ambang atau daftar gelar diubah lagi, tes ini gagal lebih dulu --
        bukan pembaruan mingguan yang diam-diam berubah cakupannya.
        """
        from impor_broadcast import GELAR_LOLOS, MIN_ELO_BINTANG, partai_bintang
        self.assertEqual(MIN_ELO_BINTANG, 2400)
        self.assertEqual(GELAR_LOLOS, ("GM", "WGM"))
        # Dulu dibuang, sejak opsi F masuk:
        self.assertTrue(partai_bintang(2491, 2026, gelar_putih="IM", gelar_hitam="FM"))
        self.assertTrue(partai_bintang(2453, 2374, gelar_putih="IM", gelar_hitam="WGM"))
        self.assertTrue(partai_bintang(2230, 2392, gelar_putih="WIM", gelar_hitam="WGM"))
        # Tetap dibuang -- dua partai dengan Elo tertinggi yang masih tersaring:
        self.assertFalse(partai_bintang(2399, 2354, gelar_putih="IM", gelar_hitam="FM"))
        self.assertFalse(partai_bintang(2359, 2399, gelar_putih="IM", gelar_hitam="IM"))

    def test_pgn_disaring_per_partai(self):
        from impor_broadcast import saring_bintang
        teks = (self.partai("Candidates", 2780, 2760) + self.partai("Open amatir", 2100, 1950)
                + self.partai("IM kuat", 2480, 2490) + self.partai("Di bawah ambang", 2399, 2350)
                + self.partai("Tanpa Elo") + self.partai("Olimpiade", 1900, 2805))
        sisa, dibuang = saring_bintang(teks)
        self.assertEqual(dibuang, 3)
        self.assertEqual(sisa.count("[Event "), 3)
        self.assertIn("Candidates", sisa)
        self.assertIn("IM kuat", sisa)
        self.assertIn("Olimpiade", sisa)
        self.assertNotIn("amatir", sisa)
        self.assertNotIn("Di bawah ambang", sisa)

    def test_aturan_dilonggarkan_ronde_diambil_ulang(self):
        from impor_broadcast import jenis_catatan
        self.assertEqual(jenis_catatan(None), "lichess-broadcast")
        self.assertNotEqual(jenis_catatan((2700, 2600)), jenis_catatan((2500, 2500)))

    def test_perbarui_meneruskan_saringan(self):
        import perbarui
        dipanggil = []
        asli = perbarui.jalankan
        perbarui.jalankan = lambda skrip, argumen, log: dipanggil.append((skrip, argumen)) or 0
        try:
            perbarui.tahap_broadcast(4, False, print)
        finally:
            perbarui.jalankan = asli
        skrip, argumen = dipanggil[0]
        self.assertEqual(skrip, "impor_broadcast.py")
        self.assertIn("--min-elo-bintang", argumen)
        self.assertEqual(argumen[argumen.index("--min-elo-bintang") + 1], "2400")
        self.assertEqual(argumen[argumen.index("--hari") + 1], "10")


class UjiGabungTurnamen(unittest.TestCase):
    """
    impor_broadcast.gabung_turnamen: tier tertinggi menang.

    Bug yang ditutup 25 Sep 2026: Lichess melaporkan tier berbeda untuk
    turnamen yang sama di /api/broadcast dan /top. Karena /top dibaca
    belakangan dan menimpa, turnamen tier 4 ikut terbuang saringan
    `tier >= 4`. Terukur hari itu: 3 turnamen bertentangan, 1 benar-benar
    hilang dari pembaruan mingguan ("Dutch Meesterklasse 2026-2027").
    """

    def test_top_tidak_boleh_menurunkan_tier(self):
        from impor_broadcast import gabung_turnamen
        tours = {}
        gabung_turnamen(tours, {"id": "KLL9kgXP", "name": "Dutch Meesterklasse", "tier": 4})
        gabung_turnamen(tours, {"id": "KLL9kgXP", "name": "Dutch Meesterklasse", "tier": 3})
        self.assertEqual(tours["KLL9kgXP"]["tier"], 4)
        self.assertEqual(len(tours), 1)

    def test_tier_lebih_tinggi_yang_datang_belakangan_tetap_dipakai(self):
        from impor_broadcast import gabung_turnamen
        tours = {}
        gabung_turnamen(tours, {"id": "a", "tier": 3})
        gabung_turnamen(tours, {"id": "a", "tier": 5})
        self.assertEqual(tours["a"]["tier"], 5)

    def test_selain_tier_data_terbaru_yang_dipakai(self):
        # Hanya tier yang dipertahankan; sisanya (mis. dates) ikut data terbaru.
        from impor_broadcast import gabung_turnamen
        tours = {}
        gabung_turnamen(tours, {"id": "a", "tier": 4, "dates": [1, 2], "name": "lama"})
        gabung_turnamen(tours, {"id": "a", "tier": 3, "dates": [3, 4], "name": "baru"})
        self.assertEqual(tours["a"]["tier"], 4)
        self.assertEqual(tours["a"]["dates"], [3, 4])
        self.assertEqual(tours["a"]["name"], "baru")

    def test_tier_tidak_ada_dianggap_nol(self):
        from impor_broadcast import gabung_turnamen
        tours = {}
        gabung_turnamen(tours, {"id": "a", "tier": 4})
        gabung_turnamen(tours, {"id": "a"})                 # tanpa kunci tier
        self.assertEqual(tours["a"]["tier"], 4)
        tours = {}
        gabung_turnamen(tours, {"id": "b", "tier": None})
        self.assertIsNone(tours["b"]["tier"])

    def test_turnamen_baru_masuk_apa_adanya(self):
        from impor_broadcast import gabung_turnamen
        tours = {}
        gabung_turnamen(tours, {"id": "a", "tier": 3})
        gabung_turnamen(tours, {"id": "b", "tier": 5})
        self.assertEqual({i: t["tier"] for i, t in tours.items()}, {"a": 3, "b": 5})


class UjiNamaTurnamenRonde(unittest.TestCase):
    """
    impor_broadcast.nama_turnamen_ronde: satu nama untuk seluruh ronde siaran.

    Ditemukan 25 Sep 2026 pada ronde 1 Dutch Meesterklasse: tag Event berbeda
    antar papan dalam ronde yang sama, jadi 20 partai masuk ke 18 baris
    `turnamen`. Nama dari PGN didahulukan agar nama lama tidak berubah;
    nama dari API Lichess hanya dipakai kalau tidak ada Event yang layak.
    """

    @staticmethod
    def pgn(*events):
        return "".join(
            f'[Event "{e}"]\n[White "A"]\n[Black "B"]\n\n1. e4 *\n\n'
            for e in events)

    def test_event_bersih_terbanyak_yang_dipakai(self):
        from impor_broadcast import nama_turnamen_ronde
        teks = self.pgn("Meesterklasse 2026-2027",
                        "Round 1: Vrolijk, Liam - Ernst, Sipke",
                        "Round 1: Pham, Khoi - Slagboom, Leandro",
                        "Meesterklasse 2026-2027",
                        "Meesterklasse 202627")
        self.assertEqual(nama_turnamen_ronde(teks, "Dutch Meesterklasse 2026-2027"),
                         "Meesterklasse 2026-2027")

    def test_semua_label_papan_pakai_cadangan_dari_api(self):
        from impor_broadcast import nama_turnamen_ronde
        teks = self.pgn("Round 1: A, A - B, B", "Round 2: C, C - D, D")
        self.assertEqual(nama_turnamen_ronde(teks, "Sofia Cup 2026"), "Sofia Cup 2026")

    def test_nama_lama_olimpiade_tidak_berubah(self):
        # Kalau API yang menang, Olimpiade akan pecah per bagian. Jangan.
        from impor_broadcast import nama_turnamen_ronde
        teks = self.pgn("Olymp 2026 Open", "Olymp 2026 Open", "Olymp 2026 Open")
        self.assertEqual(
            nama_turnamen_ronde(teks, "46th FIDE Chess Olympiad Samarkand 2026 | Open | Matches 1-12"),
            "Olymp 2026 Open")

    def test_event_generik_tidak_dianggap_nama(self):
        from impor_broadcast import nama_turnamen_ronde
        self.assertEqual(nama_turnamen_ronde(self.pgn("rated blitz game"), "Cadangan"),
                         "Cadangan")

    def test_tanpa_event_dan_tanpa_cadangan_kosong(self):
        from impor_broadcast import nama_turnamen_ronde
        self.assertEqual(nama_turnamen_ronde("", ""), "")
        self.assertEqual(nama_turnamen_ronde(self.pgn("Round 1: A, A - B, B"), ""), "")



class UjiModeCoba(unittest.TestCase):
    """--coba harus hanya-baca: tidak menulis user_version, tidak bermigrasi diam-diam."""

    def setUp(self):
        from app.db import VERSI_SKEMA
        self.versi = VERSI_SKEMA
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "uji.db"
        self.conn = koneksi(self.db)
        buat_skema(self.conn)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def versi_sekarang(self):
        return self.conn.execute("PRAGMA user_version").fetchone()[0]

    def test_coba_pada_skema_mutakhir_lolos_tanpa_menulis(self):
        self.assertTrue(siapkan_skema(self.conn, coba=True))
        self.assertEqual(self.versi_sekarang(), self.versi)

    def test_coba_pada_skema_tertinggal_berhenti_dan_tidak_mengubah(self):
        self.conn.execute(f"PRAGMA user_version = {self.versi - 1}")
        self.assertFalse(siapkan_skema(self.conn, coba=True))
        self.assertEqual(self.versi_sekarang(), self.versi - 1)   # tidak bermigrasi diam-diam

    def test_tanpa_coba_bermigrasi(self):
        self.conn.execute(f"PRAGMA user_version = {self.versi - 1}")
        self.assertTrue(siapkan_skema(self.conn, coba=False))
        self.assertEqual(self.versi_sekarang(), self.versi)

if __name__ == "__main__":
    unittest.main()


class UjiBukaBacaSaja(unittest.TestCase):
    """Audit 28 Sep 2026: --coba lewat koneksi() mengubah journal_mode berkas
    dan membuat berkas kosong untuk path yang salah."""

    def setUp(self):
        import sqlite3
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "uji.db"
        c = sqlite3.connect(self.db)
        c.execute("CREATE TABLE t(x)")
        c.commit()
        c.close()

    def tearDown(self):
        self.tmp.cleanup()

    def test_tidak_mengubah_berkas(self):
        import hashlib
        from perbarui import buka_baca_saja
        sebelum = hashlib.md5(self.db.read_bytes()).hexdigest()
        conn = buka_baca_saja(self.db)
        self.assertEqual(conn.execute("PRAGMA journal_mode").fetchone()[0], "delete")
        conn.close()
        self.assertEqual(hashlib.md5(self.db.read_bytes()).hexdigest(), sebelum)

    def test_path_salah_tidak_membuat_berkas(self):
        from perbarui import buka_baca_saja
        salah = Path(self.tmp.name) / "tidakada.db"
        with self.assertRaises(FileNotFoundError):
            buka_baca_saja(salah)
        self.assertFalse(salah.exists())

    def test_db_diteruskan_ke_skrip_anak(self):
        import perbarui
        dipanggil = []
        asli = perbarui.jalankan
        perbarui.jalankan = lambda skrip, argumen, log: dipanggil.append((skrip, argumen)) or 0
        try:
            perbarui.tahap_indeks(False, print, self.db)
            perbarui.tahap_broadcast(5, False, print, db=self.db)
        finally:
            perbarui.jalankan = asli
        for skrip, argumen in dipanggil:
            self.assertIn("--db", argumen, skrip)
            self.assertEqual(argumen[argumen.index("--db") + 1], str(self.db), skrip)

