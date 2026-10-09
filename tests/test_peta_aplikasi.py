"""
Tes `app/peta_aplikasi.py`: peta aplikasi dibangun dari sumber yang nyata.

Yang diuji bukan kualitas jawaban LLM (tidak bisa diulang, dan bukan
tanggung jawab modul), melainkan bahwa petanya benar dan tidak bisa usang:
halaman dibaca dari berkas `web/*.html` yang ada, endpoint dari spesifikasi
OpenAPI aplikasi. Kalau sesi lain menambah halaman atau endpoint, tes ini
tetap lolos — itu memang tujuannya.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app import peta_aplikasi


class TesDaftarHalaman(unittest.TestCase):
    """Pembacaan halaman diuji dengan berkas buatan, bukan `web/` sungguhan."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.folder = Path(self.tmp.name)
        (self.folder / "contoh.html").write_text(
            "<!DOCTYPE html><html><head>"
            "<title>Kelemahan — McLung ChessBase</title></head><body>"
            "<h1>Kelemahan</h1>"
            "<h2>Pembukaan terburuk</h2>"
            "<h2>Lawan yang sering menang</h2>"
            "</body></html>",
            encoding="utf-8",
        )

    def tearDown(self):
        self.tmp.cleanup()

    def test_judul_dipotong_di_pemisah(self):
        (berkas, judul, bagian), = peta_aplikasi.daftar_halaman(self.folder)
        self.assertEqual(berkas, "contoh.html")
        self.assertEqual(judul, "Kelemahan")

    def test_bagian_diambil_dari_heading(self):
        _, _, bagian = peta_aplikasi.daftar_halaman(self.folder)[0]
        self.assertIn("Pembukaan terburuk", bagian)
        self.assertIn("Lawan yang sering menang", bagian)

    def test_heading_yang_mengulang_judul_dibuang(self):
        """`<h1>Kelemahan</h1>` tidak perlu disebut dua kali di peta."""
        _, judul, bagian = peta_aplikasi.daftar_halaman(self.folder)[0]
        self.assertNotIn(judul, bagian)

    def test_bagian_dibatasi(self):
        (self.folder / "banyak.html").write_text(
            "<title>Banyak</title>" + "".join(f"<h2>Bagian {i}</h2>" for i in range(20)),
            encoding="utf-8",
        )
        for berkas, _, bagian in peta_aplikasi.daftar_halaman(self.folder):
            with self.subTest(berkas=berkas):
                self.assertLessEqual(len(bagian), peta_aplikasi._MAKS_BAGIAN)

    def test_tag_html_di_dalam_heading_dibuang(self):
        (self.folder / "tebal.html").write_text(
            "<title>Uji</title><h2>Skor <b>terburuk</b> kamu</h2>", encoding="utf-8"
        )
        peta = {j: b for _, j, b in peta_aplikasi.daftar_halaman(self.folder)}
        self.assertIn("Skor terburuk kamu", peta["Uji"])

    def test_folder_tanpa_html_menghasilkan_daftar_kosong(self):
        kosong = Path(tempfile.mkdtemp())
        self.assertEqual(peta_aplikasi.daftar_halaman(kosong), [])


class TesDaftarEndpoint(unittest.TestCase):
    """Diuji terhadap aplikasi sungguhan: di sini justru itu yang penting."""

    def setUp(self):
        self.endpoint = peta_aplikasi.daftar_endpoint()

    def test_ada_endpoint_inti(self):
        path = {p for _, p, _ in self.endpoint}
        for wajib in ("/api/partai", "/api/pemain", "/api/statistik"):
            with self.subTest(path=wajib):
                self.assertIn(wajib, path)

    def test_router_yang_disertakan_ikut_terbaca(self):
        """Inti kenapa `openapi()` dipakai, bukan menelusuri `aplikasi.routes`.

        Endpoint dari `include_router()` (impor_web, analisa_web) tidak punya
        path di daftar rute atas, jadi cara lama melewatkannya.
        """
        path = {p for _, p, _ in self.endpoint}
        tersertakan = [p for p in path if p.startswith(("/api/impor", "/api/analisa"))]
        self.assertTrue(
            tersertakan,
            "endpoint dari router yang disertakan tidak terbaca — "
            "daftar_endpoint() kembali memakai aplikasi.routes?",
        )

    def test_halaman_dokumentasi_fastapi_dilewati(self):
        path = {p for _, p, _ in self.endpoint}
        for bukan in ("/openapi.json", "/docs", "/redoc"):
            with self.subTest(path=bukan):
                self.assertNotIn(bukan, path)

    def test_metode_huruf_besar_dan_head_dibuang(self):
        for metode, _, _ in self.endpoint:
            with self.subTest(metode=metode):
                self.assertEqual(metode, metode.upper())
                self.assertNotEqual(metode, "HEAD")

    def test_terurut_supaya_peta_stabil(self):
        """Peta yang urutannya berubah-ubah membuat jawaban LLM tidak bisa diulang."""
        kunci = [(p, m) for m, p, _ in self.endpoint]
        self.assertEqual(kunci, sorted(kunci))


class TesPetaDanPrompt(unittest.TestCase):
    def test_peta_memuat_halaman_dan_endpoint(self):
        teks = peta_aplikasi.peta()
        self.assertIn("HALAMAN WEB", teks)
        self.assertIn("ENDPOINT API", teks)
        self.assertIn("/api/partai", teks)

    def test_prompt_mengurung_jawaban_pada_peta(self):
        p = peta_aplikasi.bangun_prompt("halaman apa saja?", "HALAMAN WEB:\n- Beranda")
        self.assertIn("halaman apa saja?", p)
        self.assertIn("- Beranda", p)
        # instruksi anti-karangan harus ada
        self.assertIn("Jangan mengarang", p)
        self.assertIn("belum ada", p)

    def test_pertanyaan_kosong_ditolak_tanpa_memanggil_llm(self):
        with self.assertRaises(ValueError):
            peta_aplikasi.tanya_aplikasi("   ")


if __name__ == "__main__":
    unittest.main()
