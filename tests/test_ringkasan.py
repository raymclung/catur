"""
Uji app/ringkasan.py, terutama `cakupan_analisis()`.

Kenapa fungsi itu ada: partai dipilih untuk dianalisis dengan aturan Ray
sendiri ("prioritas utama scannya partai yang kalah"), jadi partai yang sudah
dianalisis BUKAN sampel acak. Tanpa penanda itu, halaman Ringkasan menyajikan
skor 24% untuk pemain yang skor sebenarnya 53% — terukur pada data asli
13 Sep 2026.

Satu jebakan SQL juga dijaga di sini: alias hasil hitung TIDAK boleh bernama
`hasil`, karena SQLite akan mengelompokkan kolom `partai.hasil` dan labelnya
diambil dari baris acak. Bug itu nyata saat fungsi ini ditulis (menang
terbaca 4.181 padahal 4.247).
"""

import sys
import tempfile
import unittest
from pathlib import Path

AKAR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(AKAR))

from app.db import buat_skema, koneksi, sekarang
from app.ringkasan import cakupan_analisis, ringkasan_pemain


class Dasar(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = koneksi(Path(self.tmp.name) / "uji.db")
        buat_skema(self.conn)
        t = sekarang()
        c = self.conn
        c.execute("INSERT INTO pemain(id,nama,nama_normal,dibuat_pada) VALUES(1,'Saya','saya',?)", (t,))
        c.execute("INSERT INTO pemain(id,nama,nama_normal,dibuat_pada) VALUES(2,'Lawan','lawan',?)", (t,))
        self.nomor = 0

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def partai(self, hasil, saya_putih=True, dianalisis=False, format_="blitz"):
        """Satu partai; `hasil` dari sudut pandang papan ('1-0'/'0-1'/'1/2-1/2')."""
        self.nomor += 1
        i = self.nomor
        putih, hitam = (1, 2) if saya_putih else (2, 1)
        self.conn.execute(
            "INSERT INTO partai(id,putih_id,hitam_id,hasil,pgn,sumber,hash_unik,diimpor_pada,format,jumlah_langkah) "
            "VALUES(?,?,?,?,'x','uji',?,?,?,40)",
            (i, putih, hitam, hasil, f"h{i}", sekarang(), format_))
        if dianalisis:
            self.conn.execute(
                "INSERT INTO analisis(partai_id,engine,profil,depth,dianalisis_pada) "
                "VALUES(?,'uji','hemat',16,?)", (i, sekarang()))
        self.conn.commit()
        return i


class Cakupan(Dasar):
    def test_menang_kalah_dihitung_dari_sisi_pemain(self):
        self.partai("1-0", saya_putih=True)        # menang
        self.partai("0-1", saya_putih=False)       # menang (saya Hitam)
        self.partai("0-1", saya_putih=True)        # kalah
        self.partai("1/2-1/2")                     # remis
        c = cakupan_analisis(self.conn, [1])
        self.assertEqual(c["hasil_semua"], {"menang": 2, "kalah": 1, "remis": 1})
        self.assertEqual(c["partai_total"], 4)
        self.assertEqual(c["partai_dianalisis"], 0)

    def test_condong_kalah_ditandai(self):
        # 45 partai: 20 menang, 25 kalah. Yang dianalisis hanya yang kalah,
        # dan jumlahnya di atas ambang 20 supaya selisihnya berarti.
        for _ in range(20):
            self.partai("1-0", saya_putih=True)
        for _ in range(25):
            self.partai("0-1", saya_putih=True, dianalisis=True)
        c = cakupan_analisis(self.conn, [1])
        self.assertEqual(c["porsi_kalah_semua"], 55.6)
        self.assertEqual(c["porsi_kalah_dianalisis"], 100.0)
        self.assertTrue(c["condong_ke_kalah"])

    def test_sampel_seimbang_tidak_ditandai(self):
        for _ in range(15):
            self.partai("1-0", saya_putih=True, dianalisis=True)
        for _ in range(15):
            self.partai("0-1", saya_putih=True, dianalisis=True)
        c = cakupan_analisis(self.conn, [1])
        self.assertEqual(c["porsi_kalah_semua"], c["porsi_kalah_dianalisis"])
        self.assertFalse(c["condong_ke_kalah"])

    def test_sampel_terlalu_kecil_tidak_ditandai(self):
        """Di bawah 20 partai teranalisis, selisih porsi belum berarti apa-apa."""
        for _ in range(10):
            self.partai("1-0", saya_putih=True)
        self.partai("0-1", saya_putih=True, dianalisis=True)
        c = cakupan_analisis(self.conn, [1])
        self.assertEqual(c["porsi_kalah_dianalisis"], 100.0)
        self.assertFalse(c["condong_ke_kalah"])

    def test_saringan_format_ikut_dipakai(self):
        self.partai("1-0", format_="blitz")
        self.partai("0-1", format_="bullet")
        self.assertEqual(cakupan_analisis(self.conn, [1], "blitz")["partai_total"], 1)
        self.assertEqual(cakupan_analisis(self.conn, [1], "bullet")["partai_total"], 1)
        self.assertEqual(cakupan_analisis(self.conn, [1])["partai_total"], 2)

    def test_tanpa_pemain(self):
        self.assertEqual(cakupan_analisis(self.conn, [])["partai_total"], 0)


class CatatanRingkasan(Dasar):
    def test_peringatan_bias_muncul_di_catatan(self):
        for _ in range(30):
            self.partai("1-0", saya_putih=True)
        for _ in range(25):
            self.partai("0-1", saya_putih=True, dianalisis=True)
        d = ringkasan_pemain(self.conn, [1])
        self.assertTrue(d["cakupan"]["condong_ke_kalah"])
        gabung = " ".join(d["catatan"]).lower()
        self.assertIn("condong", gabung)
        self.assertIn("55", " ".join(d["catatan"]))          # 55 partai total
        self.assertIn("25", " ".join(d["catatan"]))          # 25 dianalisis

    def test_tanpa_bias_tidak_ada_peringatan(self):
        for _ in range(15):
            self.partai("1-0", saya_putih=True, dianalisis=True)
        for _ in range(15):
            self.partai("0-1", saya_putih=True, dianalisis=True)
        d = ringkasan_pemain(self.conn, [1])
        self.assertFalse(d["cakupan"]["condong_ke_kalah"])
        self.assertNotIn("condong", " ".join(d["catatan"]).lower())


if __name__ == "__main__":
    unittest.main()
