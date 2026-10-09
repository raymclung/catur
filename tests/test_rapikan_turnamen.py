"""
Pengujian rapikan_turnamen.py: pemulihan nama turnamen partai siaran.

Latar: 25 Sep 2026 ditemukan 3.362 baris `turnamen` bernama label papan
(mis. "Round 1: Vrolijk, Liam - Ernst, Sipke"), menyangkut 3.431 partai,
karena tag Event di PGN siaran Lichess berbeda antar papan dalam ronde yang
sama. Skrip ini memulihkannya dari slug di `partai.sumber_url`.

Jalankan dari akar proyek:
    python -m unittest discover -s tests -v
"""

import contextlib
import io
import tempfile
import unittest
from collections import Counter
from pathlib import Path

from app.db import buat_skema, koneksi
from app.impor import impor_teks
from rapikan_turnamen import (jalankan, kumpulkan, nama_sampah, nama_target,
                              slug_dan_ronde)

SLUG = "dutch-meesterklasse-2026-2027"
RONDE = "isTlRIXU"


def url(gid):
    return f"https://lichess.org/broadcast/{SLUG}/round-1/{RONDE}/{gid}"


def pgn(event, putih, hitam, gid, babak="1.1", tanggal="2026.09.22"):
    return (f'[Event "{event}"]\n[Site "{url(gid)}"]\n[Date "{tanggal}"]\n'
            f'[Round "{babak}"]\n[White "{putih}"]\n[Black "{hitam}"]\n'
            f'[Result "1-0"]\n\n1. e4 e5 2. Nf3 1-0\n\n')


class UjiSlugDanRonde(unittest.TestCase):
    def test_url_lengkap(self):
        self.assertEqual(slug_dan_ronde(url("0NhI5k8a")), (SLUG, RONDE))

    def test_url_lama_tanpa_slug_tidak_bisa_dipulihkan(self):
        self.assertEqual(slug_dan_ronde("https://lichess.org/broadcast/-/-/isTlRIXU"),
                         ("", ""))

    def test_bukan_url_siaran(self):
        for u in (None, "", "https://lichess.org/abcd1234", "berkas.pgn"):
            with self.subTest(u=u):
                self.assertEqual(slug_dan_ronde(u), ("", ""))


class UjiNamaSampah(unittest.TestCase):
    def test_label_papan_dan_placeholder(self):
        for n in ("Round 1: Vrolijk, Liam - Ernst, Sipke",
                  "Round 1 | Rapid: Topalov, Veselin - Kasparov, Garry",
                  "new tournament description"):
            with self.subTest(n=n):
                self.assertTrue(nama_sampah(n))

    def test_nama_asli_tidak_kena(self):
        for n in ("Meesterklasse 2026-2027", "Olymp 2026 Open",
                  "Clutch Chess: Champions", None, ""):
            with self.subTest(n=n):
                self.assertFalse(nama_sampah(n))


class UjiNamaTarget(unittest.TestCase):
    def test_nama_database_menang_atas_cache(self):
        # Nama yang sudah lama dipakai database tidak diubah: kalau API yang
        # menang, Olimpiade akan pecah per bagian.
        bersih = {SLUG: Counter({"Olymp 2026 Open": 40})}
        cache = {SLUG: "46th FIDE Chess Olympiad Samarkand 2026 | Open | Matches 1-12"}
        self.assertEqual(nama_target(SLUG, [], bersih, True, cache), "Olymp 2026 Open")

    def test_cache_dipakai_tanpa_menembak_api(self):
        # pakai_api=False membuktikan tidak ada permintaan jaringan yang terjadi.
        cache = {SLUG: "Dutch Meesterklasse 2026-2027"}
        self.assertEqual(nama_target(SLUG, [], {}, False, cache),
                         "Dutch Meesterklasse 2026-2027")

    def test_tanpa_nama_dan_tanpa_api_kosong(self):
        self.assertEqual(nama_target(SLUG, [], {}, False, {}), "")

    def test_nama_terbanyak_yang_dipilih(self):
        bersih = {SLUG: Counter({"Meesterklasse 2026-2027": 3, "Meesterklasse 202627": 1})}
        self.assertEqual(nama_target(SLUG, [], bersih, False, {}), "Meesterklasse 2026-2027")


class UjiMigrasi(unittest.TestCase):
    """Satu ronde yang pecah dirapikan jadi satu turnamen."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = koneksi(Path(self.tmp.name) / "uji.db")
        buat_skema(self.conn)
        # Tiga papan: satu Event bersih, dua Event label papan -> 3 turnamen.
        teks = (pgn("Meesterklasse 2026-2027", "Bok, Benjamin", "Giri, Anish", "g1")
                + pgn("Round 1: Vrolijk, Liam - Ernst, Sipke",
                      "Vrolijk, Liam", "Ernst, Sipke", "g2", babak="1.2")
                + pgn("Round 1: Werle, Jan - Kunin, Vitaly",
                      "Werle, Jan", "Kunin, Vitaly", "g3", babak="1.3"))
        # Diimpor TANPA nama_turnamen_paksa, meniru data lama sebelum perbaikan.
        impor_teks(self.conn, teks, sumber="lichess-broadcast")

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def jumlah(self, tabel):
        return self.conn.execute(f"SELECT COUNT(*) FROM {tabel}").fetchone()[0]

    def rapikan(self, coba):
        """jalankan() tanpa mencetak, dan laporannya ke folder sementara."""
        with contextlib.redirect_stdout(io.StringIO()):
            return jalankan(self.conn, coba=coba, pakai_api=False,
                            folder_laporan=Path(self.tmp.name) / "laporan")

    def test_keadaan_awal_memang_pecah(self):
        # Dua partai kehilangan turnamennya (EVENT_PAPAN -> turnamen_id NULL),
        # jadi yang tersisa hanya satu baris turnamen yang bersih.
        self.assertEqual(self.jumlah("partai"), 3)
        tanpa = self.conn.execute(
            "SELECT COUNT(*) FROM partai WHERE turnamen_id IS NULL").fetchone()[0]
        self.assertEqual(tanpa, 2)

    def test_kumpulkan_melihat_nama_bersih(self):
        _, nama_bersih, _ = kumpulkan(self.conn)
        self.assertEqual(nama_bersih[SLUG].most_common(1)[0][0], "Meesterklasse 2026-2027")

    def test_migrasi_pada_data_lama_yang_pecah(self):
        # Buat keadaan "sebelum perbaikan": kembalikan dua baris turnamen sampah.
        for nama, gid in (("Round 1: Vrolijk, Liam - Ernst, Sipke", "g2"),
                          ("Round 1: Werle, Jan - Kunin, Vitaly", "g3")):
            cur = self.conn.execute(
                "INSERT INTO turnamen(nama, tahun, dibuat_pada) VALUES (?,?,?)",
                (nama, 2026, "2026-09-22T00:00:00"))
            self.conn.execute("UPDATE partai SET turnamen_id = ? WHERE sumber_url = ?",
                              (cur.lastrowid, url(gid)))
        self.conn.commit()
        self.assertEqual(self.jumlah("turnamen"), 3)

        hasil = self.rapikan(coba=False)

        self.assertEqual(hasil["dipindah"], 2)
        self.assertEqual(hasil["dihapus"], 2)
        self.assertEqual(hasil["gagal"], [])
        self.assertEqual(self.jumlah("turnamen"), 1)
        self.assertEqual(self.jumlah("partai"), 3)
        nama = self.conn.execute("SELECT nama FROM turnamen").fetchone()[0]
        self.assertEqual(nama, "Meesterklasse 2026-2027")
        # Ketiga partai kini menunjuk turnamen yang sama.
        jumlah_turnamen = self.conn.execute(
            "SELECT COUNT(DISTINCT turnamen_id) FROM partai").fetchone()[0]
        self.assertEqual(jumlah_turnamen, 1)

    def test_coba_tidak_menulis(self):
        cur = self.conn.execute(
            "INSERT INTO turnamen(nama, tahun, dibuat_pada) VALUES (?,?,?)",
            ("Round 1: Vrolijk, Liam - Ernst, Sipke", 2026, "2026-09-22T00:00:00"))
        self.conn.execute("UPDATE partai SET turnamen_id = ? WHERE sumber_url = ?",
                          (cur.lastrowid, url("g2")))
        self.conn.commit()
        sebelum = self.jumlah("turnamen")
        self.rapikan(coba=True)
        self.assertEqual(self.jumlah("turnamen"), sebelum)
        masih = self.conn.execute(
            "SELECT nama FROM turnamen WHERE nama LIKE 'Round 1:%'").fetchone()
        self.assertIsNotNone(masih)


if __name__ == "__main__":
    unittest.main()
