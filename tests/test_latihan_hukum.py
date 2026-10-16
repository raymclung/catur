"""
Uji mode Latihan "hukum kesalahanmu" di peramban sungguhan (16 Okt 2026).

Mode ini seluruhnya di web/latihan.js: posisi SESUDAH kesalahan sendiri yang
membiarkan motif taktis, papan dipegang dari sisi lawan, jawabannya
sanggahan engine (langkah terbaik baris ply+1). Yang dijaga:

  * soal muncul dari kesalahan bermotif taktis milik akun sendiri;
  * jawaban benar dan salah dinilai terhadap sanggahan, dan catatannya
    tersimpan di server dengan kunci berawalan "hukum:" (mode "kesalahan",
    jadi tabel latihan_jawaban tidak perlu migrasi);
  * kartu jawaban menyebut langkah asli lawan dan polanya.

Database sendiri, bukan fixture bersama test_halaman_headless: menambah
partai ke fixture itu menggeser penanda halaman lain ("1 dianalisis", dst).
Pembantunya (peramban, server, fixture dasar) dipakai lewat modul, bukan
diimpor sebagai kelas — kelas yang diimpor ikut dijalankan unittest lagi.
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
import urllib.request
from pathlib import Path

AKAR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(AKAR))

import tests.test_halaman_headless as headless  # noqa: E402


def buat_db_hukum(folder: Path) -> Path:
    """Fixture dasar + partai McLung_07 1.e4 e5 2.Nf3 d6 3.Ng5?? Qxg5 (buah tergantung)."""
    from app.db import koneksi, sekarang
    from app.impor import impor_teks

    path = headless.buat_db(folder)
    conn = koneksi(path)
    pgn = ('[Event "uji hukum"]\n[Date "2026.03.01"]\n[White "McLung_07"]\n[Black "lawan"]\n'
           '[Result "0-1"]\n[WhiteElo "2300"]\n[BlackElo "2300"]\n\n1. e4 e5 2. Nf3 d6 3. Ng5 Qxg5 0-1\n')
    impor_teks(conn, pgn, "lichess-user")
    pid = conn.execute("SELECT MAX(id) FROM partai").fetchone()[0]
    conn.execute("INSERT INTO analisis(partai_id,engine,depth,profil,dianalisis_pada) "
                 "VALUES(?,'uji',12,'cepat',?)", (pid, sekarang()))
    # (san, uci, warna, eval_sebelum (sudut yang jalan), eval_sesudah, eval_putih, hilang, klas, terbaik, variasi)
    baris = [("e4", "e2e4", "putih", 20, 20, 20, 0, "terbaik", "e4", "e2e4", "e4 e5 Nf3"),
             ("e5", "e7e5", "hitam", -20, -20, 20, 0, "terbaik", "e5", "e7e5", "e5 Nf3"),
             ("Nf3", "g1f3", "putih", 20, 20, 20, 0, "terbaik", "Nf3", "g1f3", "Nf3 d6"),
             ("d6", "d7d6", "hitam", -20, -40, 40, 20, "baik", "Nc6", "b8c6", "Nc6 Bb5"),
             ("Ng5", "f3g5", "putih", 40, -300, -300, 340, "blunder", "d4", "d2d4", "d4 exd4 Nxd4"),
             ("Qxg5", "d8g5", "hitam", 300, 300, -300, 0, "terbaik", "Qxg5", "d8g5", "Qxg5 d4 Qg6 Nc3")]
    for ply, b in enumerate(baris):
        conn.execute(
            "INSERT INTO analisis_langkah(partai_id,ply,nomor_langkah,warna,san,uci,eval_sebelum_cp,"
            "eval_sesudah_cp,eval_putih_cp,kehilangan_cp,klasifikasi,langkah_terbaik_san,"
            "langkah_terbaik_uci,variasi_terbaik) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (pid, ply, ply // 2 + 1, b[2], b[0], b[1], b[3], b[4], b[5], b[6], b[7], b[8], b[9], b[10]))
    conn.commit()
    conn.close()
    return path


@unittest.skipUnless(headless.diminta(),
                     "set CATUR_UJI_HEADLESS=1 untuk menjalankan (butuh peramban)")
@unittest.skipIf(headless.edge() is None, "tidak ada Edge/Chrome/Chromium")
class LatihanHukumDiBrowser(unittest.TestCase):
    buka = headless.HalamanDiBrowser.buka
    tunggu_server = classmethod(headless.HalamanDiBrowser.tunggu_server.__func__)
    tearDownClass = classmethod(headless.HalamanDiBrowser.tearDownClass.__func__)

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        folder = Path(cls.tmp.name)
        cls.port = headless.port_bebas()
        lingkungan = {**os.environ, "CATUR_DB": str(buat_db_hukum(folder))}
        cls.log = (folder / "server.log").open("w", encoding="utf-8")
        cls.server = subprocess.Popen(
            [sys.executable, "-X", "utf8", "-m", "uvicorn", "app.main:aplikasi",
             "--host", "127.0.0.1", "--port", str(cls.port)],
            cwd=str(AKAR), env=lingkungan, stdout=cls.log, stderr=subprocess.STDOUT)
        cls.tunggu_server()

    def catatan(self) -> dict:
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/api/latihan/catatan", timeout=30) as r:
            return json.loads(r.read().decode("utf-8"))

    def lupakan(self) -> None:
        permintaan = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/api/latihan/catatan?mode=kesalahan", method="DELETE")
        urllib.request.urlopen(permintaan, timeout=30).close()

    def test_salah_lalu_benar(self):
        # Salah dulu: jawaban benar membuang soal dari kumpulan, jadi urutan ini
        # yang bisa menguji keduanya pada satu soal.
        dom, galat = self.buka("/static/latihan.html?mode=hukum&uji_jawab=salah", jeda_ms=30000)
        self.assertEqual(galat, [])
        self.assertIn("Kamu baru saja bermain Ng5??", dom)
        self.assertIn("cari cara menghukumnya", dom)
        self.assertIn("Bukan itu", dom)
        self.assertIn("lawanmu menjawab Qxg5 — dan menghukumnya", dom)
        self.assertIn("Pola: Buah tergantung — yang dibiarkan langkahmu", dom)
        hukum = {k: v for k, v in self.catatan().items() if k.startswith("hukum:")}
        self.assertEqual([v["h"] for v in hukum.values()], ["s"])

        self.lupakan()
        dom, galat = self.buka("/static/latihan.html?mode=hukum&uji_jawab=terbaik", jeda_ms=30000)
        self.assertEqual(galat, [])
        self.assertIn("Benar", dom)
        self.assertIn("Terbaik: Qxg5", dom)
        hukum = {k: v for k, v in self.catatan().items() if k.startswith("hukum:")}
        self.assertEqual([v["h"] for v in hukum.values()], ["b"])


if __name__ == "__main__":
    unittest.main()
