"""
Uji penandaan mesin catur dan partai konsultasi (app/jenis_pemain.py).

Temuan Ray 25 Sep 2026: koleksi PGN Mentor memuat partai manusia melawan
program, dan partai konsultasi yang dua pemainnya ditulis jadi satu nama.
Semuanya terhitung sebagai pemain biasa di statistik "partai GM".

Bagian terpenting berkas ini bukan yang membuktikan mesin terdeteksi,
melainkan yang membuktikan **manusia tidak salah ditandai**. Setiap kasus di
`BUKAN_MESIN` adalah salah-tangkap nyata dari percobaan pertama yang memakai
pencocokan substring.

Jalankan dari akar proyek:
    python -m unittest discover -s tests -v
"""

import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

AKAR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(AKAR))

from app.db import buat_skema, koneksi, sekarang
from app.jenis_pemain import tandai, tebak_jenis, usulkan

MESIN = [
    "Comp Deep Blue", "DEEP FRITZ", "Comp Rebel 10", "DEEP JUNIOR",
    "Comp Genius", "FRITZ on Primergy K800", "Comp Fritz 3", "Fritz 6",
    "X3D FRITZ", "Comp Fritz5", "DEEP JUNIOR 6", "FRITZ", "Comp King",
    "Comp Leonardo Maestro", "Comp Deep Thought", "Comp Pentium Fritz4",
    "Comp Kallisto", "Comp Hiarcs", "JUNIOR", "HYDRA", "Shredder 5.32",
]

# Semuanya salah-tangkap NYATA dari percobaan pertama yang memakai substring.
BUKAN_MESIN = [
    ("Saemisch, Fritz", "GM Jerman, bukan program Fritz"),
    ("Riemann, Fritz", "nama depan Fritz"),
    ("Igel, Fritz", "nama depan Fritz"),
    ("Roessel, Fritz", "nama depan Fritz"),
    ("Fritz, Alexander", "nama belakang Fritz"),
    ("Zagrebelny, Sergey", "mengandung 'rebel': Zag-rebel-ny"),
    ("Strebel, Beda", "mengandung 'rebel': St-rebel"),
    ("Grybkauskas,Pijus", "mengandung 'rybka': G-rybka-uskas"),
    ("Albuquerque Junior,Wellington S.", "nama Brasil, bukan Deep Junior"),
    ("Ferreira Junior,Luiz Carlos A.", "nama Brasil"),
    ("Almeida Junior,Alvaro Alves de", "nama Brasil"),
]

KONSULTASI = ["Nikitin/Sakarov", "Beutel,Jens / FRITZ7", "Anand,V / FRITZ7",
              "Bier,M/Dimer,J", "Wallis/Burrell/Large"]


class TebakJenis(unittest.TestCase):
    def test_mesin_dikenali(self):
        for nama in MESIN:
            with self.subTest(nama=nama):
                self.assertEqual(tebak_jenis(nama, None, {"pgnmentor"}), "mesin")

    def test_manusia_tidak_pernah_ditandai(self):
        for nama, sebab in BUKAN_MESIN:
            with self.subTest(nama=nama, sebab=sebab):
                self.assertIsNone(tebak_jenis(nama, None, {"pgnmentor"}))

    def test_konsultasi_dikenali(self):
        for nama in KONSULTASI:
            with self.subTest(nama=nama):
                self.assertEqual(tebak_jenis(nama, None, {"pgnmentor"}), "konsultasi")

    def test_comp_menang_atas_garis_miring(self):
        """'Comp Genius/Pentium' satu program yang namanya memuat '/', bukan konsultasi."""
        self.assertEqual(tebak_jenis("Comp Genius/Pentium", None, {"pgnmentor"}), "mesin")

    def test_fide_id_berarti_manusia(self):
        """FIDE tidak mendaftarkan program. 'Orasji Roger Junior' punya FIDE ID."""
        self.assertIsNone(tebak_jenis("Orasji Roger Junior", "8205663", {"pgnmentor"}))

    def test_baris_dengan_partai_online_tidak_pernah_ditandai(self):
        """
        Di akun online nama adalah username, dan username tidak bisa dipercaya:
        'komodo54' dan 'Juniorcatur17' adalah lawan Ray, bukan engine.
        """
        for nama in ("komodo54", "Juniorcatur17", "R-Stockfish", "Chessmaster_3001"):
            with self.subTest(nama=nama):
                self.assertIsNone(tebak_jenis(nama, None, {"lichess-user"}))

    def test_satu_partai_online_saja_sudah_membatalkan(self):
        self.assertIsNone(tebak_jenis("HYDRA", None, {"pgnmentor", "chesscom"}))

    def test_nama_kosong_aman(self):
        self.assertIsNone(tebak_jenis("", None, {"pgnmentor"}))
        self.assertIsNone(tebak_jenis(None, None, None))


class TandaiDiDatabase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = koneksi(Path(self.tmp.name) / "uji.db")
        buat_skema(self.conn)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def pemain(self, nama, fide_id=None):
        cur = self.conn.execute(
            "INSERT INTO pemain(nama, nama_normal, fide_id, dibuat_pada) VALUES (?,?,?,?)",
            (nama, nama.lower(), fide_id, sekarang()))
        return cur.lastrowid

    def partai(self, putih_id, hitam_id, sumber="pgnmentor", n=1):
        for i in range(n):
            self.conn.execute(
                "INSERT INTO partai(putih_id, hitam_id, hasil, jumlah_langkah, pgn, "
                "sumber, hash_unik, diimpor_pada) VALUES (?,?,'1-0',3,'',?,?,?)",
                (putih_id, hitam_id, sumber, f"h{putih_id}-{hitam_id}-{sumber}-{i}",
                 sekarang()))
        self.conn.commit()

    def test_mesin_ditandai_manusia_tidak(self):
        mesin = self.pemain("Comp Deep Blue")
        manusia = self.pemain("Kasparov, Garry", "4100018")
        self.partai(manusia, mesin, n=3)
        tandai(self.conn, jalan=True)
        self.assertEqual(self.conn.execute(
            "SELECT jenis FROM pemain WHERE id = ?", (mesin,)).fetchone()["jenis"], "mesin")
        self.assertIsNone(self.conn.execute(
            "SELECT jenis FROM pemain WHERE id = ?", (manusia,)).fetchone()["jenis"])

    def test_tanpa_jalan_tidak_menulis_apa_pun(self):
        mesin = self.pemain("HYDRA")
        lawan = self.pemain("Adams, Michael", "400041")
        self.partai(lawan, mesin)
        usul = tandai(self.conn, jalan=False)
        self.assertEqual([u[1] for u in usul], ["HYDRA"])
        self.assertIsNone(self.conn.execute(
            "SELECT jenis FROM pemain WHERE id = ?", (mesin,)).fetchone()["jenis"])

    def test_baris_tanpa_partai_dilewati(self):
        """Baris tanpa partai tidak mengganggu statistik apa pun."""
        self.pemain("Comp Fritz 3")
        self.assertEqual(usulkan(self.conn), [])

    def test_aman_dijalankan_dua_kali(self):
        mesin = self.pemain("DEEP FRITZ")
        lawan = self.pemain("Kramnik, Vladimir", "4101588")
        self.partai(lawan, mesin, n=2)
        tandai(self.conn, jalan=True)
        self.assertEqual(usulkan(self.conn), [])      # tidak ada sisa usulan

    def test_database_menolak_jenis_asing(self):
        """Aturan ditegakkan skema, bukan cuma dijanjikan kode."""
        pid = self.pemain("Seseorang")
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("UPDATE pemain SET jenis = 'robot' WHERE id = ?", (pid,))

    def test_urut_dari_yang_partainya_terbanyak(self):
        besar = self.pemain("Comp Deep Blue")
        kecil = self.pemain("Comp Kallisto")
        lawan = self.pemain("Kasparov, Garry", "4100018")
        self.partai(lawan, besar, n=5)
        self.partai(lawan, kecil, n=1)
        self.assertEqual([u[1] for u in usulkan(self.conn)],
                         ["Comp Deep Blue", "Comp Kallisto"])


if __name__ == "__main__":
    unittest.main()
