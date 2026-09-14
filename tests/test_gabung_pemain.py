"""
Pengujian penggabungan pemain ganda akibat FIDE ID keliru di sumber.

Kasus nyata yang memicunya: TWIC edisi 1650 memberi Magnus Carlsen (Elo 2841)
FIDE ID 6015549 dan Ivanchuk 373257 — bukan ID mereka. Baris pemain kedua
lahir, dan partainya hilang dari profil yang asli.

Yang dijaga: yang sama digabung berikut tag PGN-nya, yang berbeda tidak.

Jalankan dari akar proyek:
    python -m unittest discover -s tests -v
"""

import unittest

from app.db import buat_skema, koneksi
from gabung_pemain import (cari_pasangan, cari_pasangan_tanpa_fide, gabungkan,
                           jumlah_partai)


def pgn_uji(putih, hitam, fide_putih, fide_hitam, elo_putih, elo_hitam, tanggal):
    return (
        f'[Event "Uji"]\n[Site "-"]\n[Date "{tanggal}"]\n[Round "1"]\n'
        f'[White "{putih}"]\n[Black "{hitam}"]\n[Result "1-0"]\n'
        f'[WhiteElo "{elo_putih}"]\n[BlackElo "{elo_hitam}"]\n'
        f'[WhiteFideId "{fide_putih}"]\n[BlackFideId "{fide_hitam}"]\n\n'
        f'1. e4 e5 2. Nf3 1-0\n'
    )


class GabungPemain(unittest.TestCase):
    def setUp(self):
        self.conn = koneksi(":memory:")
        buat_skema(self.conn)

    def tearDown(self):
        self.conn.close()

    def tambah_pemain(self, nama, nama_normal, fide_id):
        cur = self.conn.execute(
            "INSERT INTO pemain(nama, nama_normal, fide_id, terverifikasi, dibuat_pada) "
            "VALUES (?,?,?,1,'2026-09-12')", (nama, nama_normal, fide_id))
        return cur.lastrowid

    def tambah_partai(self, putih_id, hitam_id, elo_putih, elo_hitam, tanggal, pgn=""):
        cur = self.conn.execute(
            "INSERT INTO partai(putih_id, hitam_id, tanggal, hasil, elo_putih, elo_hitam, "
            "jumlah_langkah, pgn, sumber, hash_unik, diimpor_pada) "
            "VALUES (?,?,?,'1-0',?,?,3,?,'twic',?,'2026-09-12')",
            (putih_id, hitam_id, tanggal, elo_putih, elo_hitam, pgn,
             f"hash{putih_id}-{hitam_id}-{tanggal}"))
        return cur.lastrowid

    # ---------- deteksi ----------

    def test_fide_id_keliru_terdeteksi(self):
        asli = self.tambah_pemain("Carlsen, Magnus", "carlsen magnus", "1503014")
        palsu = self.tambah_pemain("Carlsen,M", "carlsen m", "6015549")
        lawan = self.tambah_pemain("Ivanchuk,V", "ivanchuk v", "14100010")
        self.tambah_partai(asli, lawan, 2839, 2634, "2026.05.01")
        self.tambah_partai(palsu, lawan, 2841, 2634, "2026.06.17")
        pasangan = cari_pasangan(self.conn)
        self.assertEqual(len(pasangan), 1)
        simpan, buang, _ = pasangan[0]
        self.assertEqual(simpan["id"], asli)      # yang partainya lebih banyak dipertahankan
        self.assertEqual(buang["id"], palsu)

    def test_elo_jauh_berbeda_bukan_orang_yang_sama(self):
        kuat = self.tambah_pemain("Smith, John", "smith john", "111")
        lemah = self.tambah_pemain("Smith,J", "smith j", "222")
        lawan = self.tambah_pemain("Lawan, A", "lawan a", "333")
        self.tambah_partai(kuat, lawan, 2700, 2500, "2026.01.01")
        self.tambah_partai(lemah, lawan, 1900, 2500, "2026.02.01")
        self.assertEqual(cari_pasangan(self.conn), [])

    def test_tahun_tidak_beririsan_tidak_digabung(self):
        a = self.tambah_pemain("Ruck, Robert", "ruck robert", "705713")
        b = self.tambah_pemain("Ruck,R", "ruck r", "724661")
        lawan = self.tambah_pemain("Lawan, A", "lawan a", "333")
        self.tambah_partai(a, lawan, 2570, 2500, "2013.01.01")
        self.tambah_partai(b, lawan, 2575, 2500, "1999.01.01")
        self.assertEqual(cari_pasangan(self.conn), [])

    def test_inisial_berbeda_tidak_digabung(self):
        a = self.tambah_pemain("Aronian, Levon", "aronian levon", "111")
        b = self.tambah_pemain("Aronian,S", "aronian s", "222")
        lawan = self.tambah_pemain("Lawan, A", "lawan a", "333")
        self.tambah_partai(a, lawan, 2750, 2500, "2026.01.01")
        self.tambah_partai(b, lawan, 2740, 2500, "2026.02.01")
        self.assertEqual(cari_pasangan(self.conn), [])

    # ---------- penggabungan ----------

    def test_partai_pindah_dan_baris_dihapus(self):
        asli = self.tambah_pemain("Carlsen, Magnus", "carlsen magnus", "1503014")
        palsu = self.tambah_pemain("Carlsen,M", "carlsen m", "6015549")
        lawan = self.tambah_pemain("Ivanchuk,V", "ivanchuk v", "14100010")
        self.tambah_partai(asli, lawan, 2839, 2634, "2026.05.01")
        self.tambah_partai(palsu, lawan, 2841, 2634, "2026.06.17")

        dipindah, _ = gabungkan(self.conn, asli, palsu)
        self.assertEqual(dipindah, 1)
        self.assertEqual(jumlah_partai(self.conn, asli), 2)
        self.assertIsNone(
            self.conn.execute("SELECT 1 FROM pemain WHERE id = ?", (palsu,)).fetchone())

    def test_tag_fide_id_di_pgn_ikut_diperbaiki(self):
        """Kalau PGN tidak ikut diperbaiki, impor ulang melahirkan duplikat lagi."""
        asli = self.tambah_pemain("Carlsen, Magnus", "carlsen magnus", "1503014")
        palsu = self.tambah_pemain("Carlsen,M", "carlsen m", "6015549")
        lawan = self.tambah_pemain("Ivanchuk,V", "ivanchuk v", "14100010")
        self.tambah_partai(asli, lawan, 2839, 2634, "2026.05.01")
        gid = self.tambah_partai(
            palsu, lawan, 2841, 2634, "2026.06.17",
            pgn=pgn_uji("Carlsen,M", "Ivanchuk,V", "6015549", "14100010", 2841, 2634, "2026.06.17"))

        _, pgn_ok = gabungkan(self.conn, asli, palsu)
        self.assertEqual(pgn_ok, 1)
        pgn = self.conn.execute("SELECT pgn FROM partai WHERE id = ?", (gid,)).fetchone()["pgn"]
        self.assertIn('[WhiteFideId "1503014"]', pgn)
        self.assertNotIn("6015549", pgn)
        # sisi lawan tidak disentuh
        self.assertIn('[BlackFideId "14100010"]', pgn)
        # langkah dan hasil utuh
        self.assertIn("1. e4 e5 2. Nf3 1-0", pgn)

    def test_kolom_kosong_dilengkapi_dari_baris_yang_dihapus(self):
        asli = self.tambah_pemain("Carlsen, Magnus", "carlsen magnus", "1503014")
        palsu = self.tambah_pemain("Carlsen,M", "carlsen m", "6015549")
        self.conn.execute("UPDATE pemain SET gelar = 'GM' WHERE id = ?", (palsu,))
        lawan = self.tambah_pemain("Lawan, A", "lawan a", "333")
        self.tambah_partai(asli, lawan, 2839, 2500, "2026.05.01")
        self.tambah_partai(palsu, lawan, 2841, 2500, "2026.06.17")
        gabungkan(self.conn, asli, palsu)
        gelar = self.conn.execute("SELECT gelar FROM pemain WHERE id = ?", (asli,)).fetchone()["gelar"]
        self.assertEqual(gelar, "GM")

    def test_menolak_menggabungkan_id_yang_sama(self):
        a = self.tambah_pemain("Carlsen, Magnus", "carlsen magnus", "1503014")
        with self.assertRaises(ValueError):
            gabungkan(self.conn, a, a)


class GabungTanpaFideId(GabungPemain):
    """
    Mode kedua: baris TANPA FIDE ID hasil impor PGN Mentor.

    Kasus nyata 12 Sep 2026: 'Nakamura,Hi' (6.990 partai, tanpa FIDE ID) berdiri
    terpisah dari 'Nakamura, Hikaru' (2.803 partai, ber-FIDE ID), sehingga satu
    orang terbelah di halaman pemain, repertoire, dan penjelajah.
    """

    def tambah_tanpa_fide(self, nama, nama_normal):
        cur = self.conn.execute(
            "INSERT INTO pemain(nama, nama_normal, fide_id, terverifikasi, dibuat_pada) "
            "VALUES (?,?,NULL,0,'2026-09-12')", (nama, nama_normal))
        return cur.lastrowid

    def test_singkatan_dua_huruf_digabung_ke_baris_terverifikasi(self):
        asli = self.tambah_pemain("Nakamura, Hikaru", "nakamura hikaru", "2016192")
        mentor = self.tambah_tanpa_fide("Nakamura,Hi", "nakamura hi")
        lawan = self.tambah_pemain("Lawan, A", "lawan a", "999")
        self.tambah_partai(asli, lawan, 2780, 2600, "2016.05.01")
        for i in range(3):                      # baris tanpa FIDE ID lebih banyak partainya
            self.tambah_partai(mentor, lawan, 2789, 2600, f"2016.06.0{i + 1}")
        pasangan = cari_pasangan_tanpa_fide(self.conn)
        self.assertEqual(len(pasangan), 1)
        simpan, buang, alasan = pasangan[0]
        # yang dipertahankan = yang ber-FIDE ID, WALAU partainya lebih sedikit
        self.assertEqual(simpan["id"], asli)
        self.assertEqual(buang["id"], mentor)
        self.assertIn("tanpa FIDE ID", alasan)

        gabungkan(self.conn, simpan["id"], buang["id"])
        self.assertEqual(jumlah_partai(self.conn, asli), 4)
        self.assertIsNone(self.conn.execute(
            "SELECT 1 FROM pemain WHERE id = ?", (mentor,)).fetchone())

    def test_tanda_hubung_pada_nama_belakang_tetap_cocok(self):
        asli = self.tambah_pemain("Vachier-Lagrave, Maxime", "vachier lagrave maxime", "623539")
        mentor = self.tambah_tanpa_fide("Vachier Lagrave,M", "vachier lagrave m")
        lawan = self.tambah_pemain("Lawan, B", "lawan b", "888")
        self.tambah_partai(asli, lawan, 2750, 2600, "2019.03.01")
        self.tambah_partai(mentor, lawan, 2758, 2600, "2019.04.01")
        self.assertEqual(len(cari_pasangan_tanpa_fide(self.conn)), 1)

    def test_dua_calon_tidak_ditebak(self):
        # 'Aronian,L' bisa berarti Levon atau Laurel -> biarkan terpisah.
        levon = self.tambah_pemain("Aronian, Levon", "aronian levon", "13300474")
        laurel = self.tambah_pemain("Aronian, Laurel", "aronian laurel", "13300999")
        mentor = self.tambah_tanpa_fide("Aronian,L", "aronian l")
        lawan = self.tambah_pemain("Lawan, C", "lawan c", "777")
        self.tambah_partai(levon, lawan, 2780, 2600, "2015.01.01")
        self.tambah_partai(laurel, lawan, 2100, 2600, "2015.02.01")
        self.tambah_partai(mentor, lawan, 2782, 2600, "2015.03.01")
        self.assertEqual(cari_pasangan_tanpa_fide(self.conn), [])

    def test_elo_jauh_beda_tidak_digabung(self):
        # Magnus vs ayahnya Henrik: nama belakang sama, inisial beda pun tidak
        # lolos, tapi ini menguji pengaman Elo untuk inisial yang cocok.
        kuat = self.tambah_pemain("Smith, Magnus", "smith magnus", "555")
        lemah = self.tambah_tanpa_fide("Smith,M", "smith m")
        lawan = self.tambah_pemain("Lawan, D", "lawan d", "666")
        self.tambah_partai(kuat, lawan, 2780, 2600, "2015.01.01")
        self.tambah_partai(lemah, lawan, 2050, 2600, "2015.02.01")
        self.assertEqual(cari_pasangan_tanpa_fide(self.conn), [])

    def test_baris_tanpa_fide_tidak_saling_digabung(self):
        a = self.tambah_tanpa_fide("Popov,I", "popov i")
        b = self.tambah_tanpa_fide("Popov, Ivan", "popov ivan")
        lawan = self.tambah_pemain("Lawan, E", "lawan e", "444")
        self.tambah_partai(a, lawan, 2600, 2500, "2018.01.01")
        self.tambah_partai(b, lawan, 2605, 2500, "2018.02.01")
        self.assertEqual(cari_pasangan_tanpa_fide(self.conn), [])

    # ---------- angka penanda TWIC (13 Sep 2026) ----------

    def test_angka_penanda_twic_digabung_kalau_elo_membuktikan(self):
        """'Fedoseev,Vl3' (379 partai) adalah Vladimir Fedoseev: Elo 2012 sama."""
        asli = self.tambah_pemain("Fedoseev, Vladimir", "fedoseev vladimir", "24130737")
        twic = self.tambah_tanpa_fide("Fedoseev,Vl3", "fedoseev vl3")
        lawan = self.tambah_pemain("Lawan, F", "lawan f", "777")
        self.tambah_partai(asli, lawan, 2546, 2500, "2012.03.01")
        self.tambah_partai(twic, lawan, 2525, 2500, "2012.04.01")
        pasangan = cari_pasangan_tanpa_fide(self.conn)
        self.assertEqual([(s["id"], b["id"]) for s, b, _ in pasangan], [(asli, twic)])

    def test_angka_penanda_twic_dengan_dua_calon_tidak_ditebak(self):
        """'Sokolov,And1': calonnya 'Sokolov, Andrei' DAN 'Sokolov,And2' (FIDE lain)."""
        andrei = self.tambah_pemain("Sokolov, Andrei", "sokolov andrei", "627089")
        and2 = self.tambah_pemain("Sokolov,And2", "sokolov and2", "11600306")
        and1 = self.tambah_tanpa_fide("Sokolov,And1", "sokolov and1")
        lawan = self.tambah_pemain("Lawan, G", "lawan g", "888")
        for pid in (andrei, and2, and1):
            self.tambah_partai(pid, lawan, 2550, 2500, "2014.01.01")
        self.assertEqual(cari_pasangan_tanpa_fide(self.conn), [])

    def test_angka_penanda_twic_elo_jauh_tetap_terpisah(self):
        asli = self.tambah_pemain("Petrov, Nikita", "petrov nikita", "4166000")
        twic = self.tambah_tanpa_fide("Petrov,N2", "petrov n2")
        lawan = self.tambah_pemain("Lawan, H", "lawan h", "999")
        self.tambah_partai(asli, lawan, 2600, 2500, "2019.01.01")
        self.tambah_partai(twic, lawan, 2150, 2500, "2019.02.01")
        self.assertEqual(cari_pasangan_tanpa_fide(self.conn), [])

    def test_batas_username_mirip_tanpa_fide_tidak_pernah_digabung(self):
        """Batas yang dicatat, bukan disembunyikan (CLAUDE.md bagian 1).

        Audit 13 Sep: di antara sesama baris tanpa FIDE ID, bukti Elo meloloskan
        'Cabe-pedas' + 'Cabe-pedas165' — dua username online yang berbeda. Maka
        sesama baris tanpa FIDE ID tetap tidak digabung, walau Elo-nya dekat;
        akibatnya pasangan asli seperti 'Larsen,B' / 'Larsen, Bent' juga tetap
        terpisah.
        """
        a = self.tambah_tanpa_fide("Cabe-pedas", "cabe pedas")
        b = self.tambah_tanpa_fide("Cabe-pedas165", "cabe pedas165")
        lawan = self.tambah_pemain("Lawan, I", "lawan i", "1010")
        self.tambah_partai(a, lawan, 1800, 1800, "2020.01.01")
        self.tambah_partai(b, lawan, 1810, 1800, "2020.02.01")
        self.assertEqual(cari_pasangan_tanpa_fide(self.conn), [])


if __name__ == "__main__":
    unittest.main()
