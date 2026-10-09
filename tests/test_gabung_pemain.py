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

    def tambah_partai(self, putih_id, hitam_id, elo_putih, elo_hitam, tanggal, pgn="",
                      sumber="twic"):
        cur = self.conn.execute(
            "INSERT INTO partai(putih_id, hitam_id, tanggal, hasil, elo_putih, elo_hitam, "
            "jumlah_langkah, pgn, sumber, hash_unik, diimpor_pada) "
            "VALUES (?,?,?,'1-0',?,?,3,?,?,?,'2026-09-12')",
            (putih_id, hitam_id, tanggal, elo_putih, elo_hitam, pgn, sumber,
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

    def test_elo_jauh_beda_tidak_digabung(self):
        # Magnus vs ayahnya Henrik: nama belakang sama, inisial beda pun tidak
        # lolos, tapi ini menguji pengaman Elo untuk inisial yang cocok.
        kuat = self.tambah_pemain("Smith, Magnus", "smith magnus", "555")
        lemah = self.tambah_tanpa_fide("Smith,M", "smith m")
        lawan = self.tambah_pemain("Lawan, D", "lawan d", "666")
        self.tambah_partai(kuat, lawan, 2780, 2600, "2015.01.01")
        self.tambah_partai(lemah, lawan, 2050, 2600, "2015.02.01")
        self.assertEqual(cari_pasangan_tanpa_fide(self.conn), [])

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

    def test_dua_calon_hanya_satu_berbukti_dipakai(self):
        """
        Diubah 25 Sep 2026: yang memutuskan bukti, bukan jumlah calon.

        Versi lama menolak 'Aronian,L' hanya karena ada dua Aronian bernama L,
        walau Elo Laurel (2100) jelas bukan Elo si baris singkatan (2782).
        Aturan "calon harus tepat satu" itulah yang membuat 'Larsen,B' dan
        'Short,N' dilewati diam-diam di database sungguhan. Sekarang calon
        yang Elo-nya tidak cocok gugur lebih dulu, dan yang tersisa tepat satu.
        """
        levon = self.tambah_pemain("Aronian, Levon", "aronian levon", "13300474")
        laurel = self.tambah_pemain("Aronian, Laurel", "aronian laurel", "13300999")
        mentor = self.tambah_tanpa_fide("Aronian,L", "aronian l")
        lawan = self.tambah_pemain("Lawan, C", "lawan c", "777")
        self.tambah_partai(levon, lawan, 2780, 2600, "2015.01.01")
        self.tambah_partai(laurel, lawan, 2100, 2600, "2015.02.01")
        self.tambah_partai(mentor, lawan, 2782, 2600, "2015.03.01")
        pasangan = cari_pasangan_tanpa_fide(self.conn)
        self.assertEqual([(s["id"], b["id"]) for s, b, _ in pasangan], [(levon, mentor)])

    def test_dua_calon_sama_sama_berbukti_tetap_ditolak(self):
        """Kalau Elo tidak memisahkan, tebakan tetap dilarang — dan dicatat."""
        levon = self.tambah_pemain("Aronian, Levon", "aronian levon", "13300474")
        laurel = self.tambah_pemain("Aronian, Laurel", "aronian laurel", "13300999")
        mentor = self.tambah_tanpa_fide("Aronian,L", "aronian l")
        lawan = self.tambah_pemain("Lawan, C", "lawan c", "777")
        for pid in (levon, laurel, mentor):
            self.tambah_partai(pid, lawan, 2780, 2600, f"2015.0{(pid % 8) + 1}.01")
        ragu = []
        self.assertEqual(cari_pasangan_tanpa_fide(self.conn, ragu), [])
        self.assertEqual([r["id"] for r, _, _ in ragu], [mentor])
        self.assertEqual(ragu[0][2], 2)          # dua calon sama-sama berbukti

    def test_dua_baris_otb_tanpa_fide_digabung(self):
        """
        Kasus Bent Larsen: keduanya tanpa FIDE ID (sudah wafat, tidak ada di
        daftar FIDE) dan 100% dari PGN Mentor. 54 partai 'Larsen,B' terdampar
        terpisah dari 2.268 partai 'Larsen, Bent' sampai 25 Sep 2026.
        """
        bent = self.tambah_tanpa_fide("Larsen, Bent", "larsen bent")
        singkat = self.tambah_tanpa_fide("Larsen,B", "larsen b")
        lawan = self.tambah_pemain("Lawan, J", "lawan j", "1212")
        for i in range(3):
            self.tambah_partai(bent, lawan, 2600, 2500, f"1999.0{i + 1}.01",
                               sumber="pgnmentor")
        self.tambah_partai(singkat, lawan, 2610, 2500, "1999.05.01", sumber="pgnmentor")
        pasangan = cari_pasangan_tanpa_fide(self.conn)
        self.assertEqual([(s["id"], b["id"]) for s, b, _ in pasangan], [(bent, singkat)])
        self.assertIn("100% OTB", pasangan[0][2])

    def test_username_online_tidak_pernah_digabung(self):
        """
        Batas yang dijaga kolom `sumber`, bukan oleh "tanpa FIDE ID".

        Audit 13 Sep menemukan bukti Elo meloloskan 'Cabe-pedas' +
        'Cabe-pedas165' — dua username Lichess berbeda. Penjagaannya dulu
        melarang SEMUA baris tanpa FIDE ID, dan Bent Larsen ikut jadi korban.
        Sekarang yang dilarang persis yang dimaksud: baris yang punya partai
        dari akun online.
        """
        a = self.tambah_tanpa_fide("Cabe-pedas", "cabe pedas")
        b = self.tambah_tanpa_fide("Cabe-pedas165", "cabe pedas165")
        lawan = self.tambah_pemain("Lawan, I", "lawan i", "1010")
        self.tambah_partai(a, lawan, 1800, 1800, "2020.01.01", sumber="lichess-user")
        for i in range(3):
            self.tambah_partai(b, lawan, 1810, 1800, f"2020.0{i + 2}.01",
                               sumber="lichess-user")
        self.assertEqual(cari_pasangan_tanpa_fide(self.conn), [])

    def test_nama_depan_dua_kata_tetap_bertemu_singkatannya(self):
        """
        'Short, Nigel D' pernah dikelompokkan bermarga 'short nigel' karena
        batas nama belakang ditebak dari posisi kata, bukan dari koma. Akibatnya
        ia tidak pernah bertemu 'Short,N' dan 83 partainya terdampar.
        """
        asli = self.tambah_pemain("Short, Nigel D", "short nigel d", "400025")
        mentor = self.tambah_tanpa_fide("Short,N", "short n")
        lawan = self.tambah_pemain("Lawan, K", "lawan k", "1313")
        self.tambah_partai(asli, lawan, 2670, 2500, "2000.03.01")
        self.tambah_partai(mentor, lawan, 2683, 2500, "2000.06.01")
        pasangan = cari_pasangan_tanpa_fide(self.conn)
        self.assertEqual([(s["id"], b["id"]) for s, b, _ in pasangan], [(asli, mentor)])

    def test_pernah_berhadapan_berarti_dua_orang(self):
        """Satu partai di antara keduanya sudah cukup membatalkan pencalonan."""
        asli = self.tambah_pemain("Petrov, Martin", "petrov martin", "2911086")
        lain = self.tambah_tanpa_fide("Petrov,M", "petrov m")
        self.tambah_partai(asli, lain, 2500, 2490, "2018.01.01")
        self.assertEqual(cari_pasangan_tanpa_fide(self.conn), [])

    def test_tidak_digabung_ke_arah_nama_yang_lebih_singkat(self):
        """
        'Larsen,Be' tidak boleh menelan 'Larsen,B' atau sebaliknya tanpa FIDE
        ID: tanpa penjagaan arah, rantai dua baris bisa saling menghapus.
        """
        b = self.tambah_tanpa_fide("Larsen,B", "larsen b")
        be = self.tambah_tanpa_fide("Larsen,Be", "larsen be")
        lawan = self.tambah_pemain("Lawan, L", "lawan l", "1414")
        self.tambah_partai(b, lawan, 2600, 2500, "1999.01.01", sumber="pgnmentor")
        self.tambah_partai(be, lawan, 2605, 2500, "1999.02.01", sumber="pgnmentor")
        self.assertEqual(cari_pasangan_tanpa_fide(self.conn), [])

    def test_dua_singkatan_bernomor_bukan_orang_yang_sama(self):
        """
        Angka penanda TWIC berarti "dua orang senama yang BERBEDA", jadi
        'Biolek,R1' dan 'Biolek,R2' tidak pernah digabung — walau Elo keduanya
        berdekatan dan keduanya tanpa FIDE ID. Diukur 25 Sep 2026: ada 6
        pasangan seperti ini di database, semuanya belum tergabung hanya karena
        belum ada tag Elo yang membuktikan.
        """
        r1 = self.tambah_tanpa_fide("Biolek,R1", "biolek r1")
        r2 = self.tambah_tanpa_fide("Biolek,R2", "biolek r2")
        lawan = self.tambah_pemain("Lawan, M", "lawan m", "1515")
        self.tambah_partai(r1, lawan, 2400, 2300, "2012.01.01", sumber="pgnmentor")
        for i in range(3):
            self.tambah_partai(r2, lawan, 2410, 2300, f"2012.0{i + 2}.01",
                               sumber="pgnmentor")
        self.assertEqual(cari_pasangan_tanpa_fide(self.conn), [])

    def test_bernomor_terhadap_nama_lengkap_tetap_boleh(self):
        """Penjagaan itu hanya berlaku kalau KEDUA sisi bernomor."""
        asli = self.tambah_pemain("Biolek, Radim", "biolek radim", "301078")
        twic = self.tambah_tanpa_fide("Biolek,R1", "biolek r1")
        lawan = self.tambah_pemain("Lawan, N", "lawan n", "1616")
        self.tambah_partai(asli, lawan, 2400, 2300, "2012.01.01")
        self.tambah_partai(twic, lawan, 2410, 2300, "2012.02.01")
        pasangan = cari_pasangan_tanpa_fide(self.conn)
        self.assertEqual([(s["id"], b["id"]) for s, b, _ in pasangan], [(asli, twic)])


class NamaBentrok(unittest.TestCase):
    """Menggabung 'Anand,V' ke Vishy membawa partai 'Anand,Vignesh' ikut."""

    def setUp(self):
        import tempfile
        from pathlib import Path
        from app.impor import impor_teks
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = koneksi(Path(self.tmp.name) / "uji.db")
        buat_skema(self.conn)
        # 'Anand,V' dulu, lalu Vignesh (tanpa bukti lain, ikut ke singkatan),
        # lalu Vishy ber-FIDE ID sebagai baris sendiri.
        for i, (putih, fide) in enumerate([("Anand,V", None), ("Anand,Vignesh", None),
                                           ("Anand, Viswanathan", "5000017")]):
            tag = ['[Event "u"]', f'[Date "2024.01.0{i + 1}"]', f'[White "{putih}"]',
                   '[Black "Lawan, Satu"]', '[Result "1-0"]']
            if fide:
                tag.append(f'[WhiteFideId "{fide}"]')
            impor_teks(self.conn, "\n".join(tag) + "\n\n1. e4 e5 2. Nf3 1-0\n", "pgnmentor")
        self.id = {r["nama"]: r["id"] for r in self.conn.execute("SELECT id, nama FROM pemain")}

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_bentrok_terdeteksi(self):
        from gabung_pemain import nama_bentrok
        self.assertNotIn("Anand,Vignesh", self.id)          # ikut ke singkatan, seperti data lama
        self.assertEqual(nama_bentrok(self.conn, self.id["Anand, Viswanathan"], self.id["Anand,V"]),
                         ["Anand,Vignesh"])

    def test_tanpa_bentrok_kosong(self):
        from gabung_pemain import nama_bentrok
        self.conn.execute("DELETE FROM partai WHERE pgn LIKE '%Vignesh%'")
        self.assertEqual(nama_bentrok(self.conn, self.id["Anand, Viswanathan"], self.id["Anand,V"]), [])
