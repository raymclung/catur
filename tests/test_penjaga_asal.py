"""
Penjaga asal permintaan (audit 27 Sep 2026): situs lain di browser laptop
tidak boleh bisa menyuruh server bekerja, dan Host asing (DNS rebinding)
ditolak. Lihat app/penjaga_asal.py.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.penjaga_asal import asal_diizinkan, nama_host  # noqa: E402

SAH = {"localhost", "127.0.0.1", "::1", "192.168.1.5"}


class UjiFungsi(unittest.TestCase):
    def boleh(self, metode, host, origin=None, pembantu=False):
        return asal_diizinkan(metode, host, origin, pembantu, SAH) is None

    def test_nama_host(self):
        self.assertEqual(nama_host("127.0.0.1:8000"), "127.0.0.1")
        self.assertEqual(nama_host("[::1]:8000"), "::1")
        self.assertEqual(nama_host("LocalHost"), "localhost")
        self.assertIsNone(nama_host(""))
        self.assertIsNone(nama_host(None))

    def test_halaman_sendiri_boleh(self):
        self.assertTrue(self.boleh("GET", "127.0.0.1:8000"))
        self.assertTrue(self.boleh("POST", "127.0.0.1:8000", "http://127.0.0.1:8000"))
        self.assertTrue(self.boleh("DELETE", "localhost:8000", "http://localhost:8000"))
        self.assertTrue(self.boleh("POST", "192.168.1.5:8000", "http://192.168.1.5:8000"))

    def test_tanpa_origin_boleh(self):
        """python/curl dari terminal Ray tidak membawa Origin."""
        self.assertTrue(self.boleh("POST", "127.0.0.1:8000"))

    def test_situs_lain_tidak_boleh_mengubah(self):
        for origin in ("https://situs-jahat.example", "http://127.0.0.1:3000",
                       "http://localhost:8000", "null"):
            with self.subTest(origin=origin):
                self.assertFalse(self.boleh("POST", "127.0.0.1:8000", origin))

    def test_situs_lain_boleh_membaca_tapi_tidak_membaca_jawaban(self):
        """GET lintas situs tetap dilayani (tidak mengubah apa pun, dan CORS
        mencegah situs itu membaca jawabannya)."""
        self.assertTrue(self.boleh("GET", "127.0.0.1:8000", "https://situs-jahat.example"))

    def test_host_asing_ditolak(self):
        """DNS rebinding: domain penyerang diarahkan ke 127.0.0.1."""
        self.assertFalse(self.boleh("GET", "jahat.example:8000"))
        self.assertFalse(self.boleh("POST", "jahat.example:8000", "http://jahat.example:8000"))
        self.assertFalse(self.boleh("GET", None))

    def test_jalur_pembantu_dikecualikan(self):
        """Tablet lewat tunnel: Host dan Origin tidak bisa diketahui di sini."""
        self.assertTrue(self.boleh("POST", "acak.trycloudflare.com",
                                   "https://acak.trycloudflare.com", pembantu=True))


class UjiServer(unittest.TestCase):
    """
    Lewat uvicorn sungguhan (seperti tes halaman): middleware benar-benar
    terpasang. TestClient tidak dipakai karena httpx tidak terpasang di sini.
    Database sementara yang kosong, bukan data Ray.
    """

    @classmethod
    def setUpClass(cls):
        import os
        import socket
        import subprocess
        import tempfile
        import time
        from app.db import buat_skema, koneksi
        cls.tmp = tempfile.TemporaryDirectory()
        db = Path(cls.tmp.name) / "uji.db"
        conn = koneksi(db)
        buat_skema(conn)
        conn.close()
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            cls.port = s.getsockname()[1]
        cls.server = subprocess.Popen(
            [sys.executable, "-X", "utf8", "-m", "uvicorn", "app.main:aplikasi",
             "--host", "127.0.0.1", "--port", str(cls.port)],
            cwd=str(Path(__file__).resolve().parents[1]),
            env={**os.environ, "CATUR_DB": str(db)},
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        habis = time.time() + 45
        while time.time() < habis:
            try:
                socket.create_connection(("127.0.0.1", cls.port), timeout=1).close()
                return
            except OSError:
                time.sleep(0.3)
        raise RuntimeError("server uji tidak menyala")

    @classmethod
    def tearDownClass(cls):
        cls.server.terminate()
        cls.server.wait(timeout=15)
        cls.tmp.cleanup()

    def minta(self, metode, jalur, headers=None, isi=None):
        import json
        import urllib.error
        import urllib.request
        data = json.dumps(isi).encode() if isi is not None else None
        r = urllib.request.Request(f"http://127.0.0.1:{self.port}{jalur}", data=data,
                                   method=metode, headers={"Content-Type": "application/json",
                                                           **(headers or {})})
        try:
            with urllib.request.urlopen(r, timeout=30) as j:
                return j.status, j.read().decode()
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode()

    def test_post_dari_situs_lain_403(self):
        kode, isi = self.minta("POST", "/api/posisi-tersimpan", {"Origin": "https://situs-jahat.example"}, {})
        self.assertEqual(kode, 403)
        self.assertIn("situs-jahat", isi)

    def test_post_dari_halaman_sendiri_lolos_penjaga(self):
        # isi kosong -> 422 dari validasi: bukti penjaga meloloskan, tanpa
        # menulis apa pun ke database
        kode, _ = self.minta("POST", "/api/posisi-tersimpan",
                             {"Origin": f"http://127.0.0.1:{self.port}"}, {})
        self.assertEqual(kode, 422)

    def test_post_tanpa_origin_lolos_penjaga(self):
        kode, _ = self.minta("POST", "/api/posisi-tersimpan", None, {})
        self.assertEqual(kode, 422)

    def test_host_asing_403(self):
        kode, _ = self.minta("GET", "/api/statistik", {"Host": "jahat.example"})
        self.assertEqual(kode, 403)

    def test_jalur_pembantu_dari_host_lain_tidak_ditolak_penjaga_ini(self):
        kode, _ = self.minta("GET", "/static/pembantu.html", {"Host": "acak.trycloudflare.com"})
        self.assertNotEqual(kode, 403)


    def test_id_raksasa_422_bukan_500(self):
        """Bukan soal asal permintaan, tapi butuh server sungguhan yang sama:
        angka di luar INTEGER 64-bit SQLite dulu 500 di tiap endpoint ber-id
        (audit 27 Sep 2026); penangan OverflowError di app/main.py."""
        for jalur in ("/api/partai/99999999999999999999",
                      "/api/pemain/99999999999999999999",
                      "/api/partai/99999999999999999999/analisis"):
            with self.subTest(jalur=jalur):
                kode, isi = self.minta("GET", jalur)
                self.assertEqual(kode, 422, isi)
                self.assertIn("terlalu besar", isi)


if __name__ == "__main__":
    unittest.main()
