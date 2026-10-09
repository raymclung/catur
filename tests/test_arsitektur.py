"""
Uji batas arsitektur yang ditulis CLAUDE.md bagian 4, supaya tidak melenceng
diam-diam lagi.

Latar belakangnya (21 Sep 2026): aturan lama berbunyi "SQL hanya ada di enam
modul, tidak tersebar ke main.py atau skrip", padahal saat diperiksa SQL ada
di 20 modul `app/` dan 8 skrip akar — termasuk `app/main.py` sendiri. Aturan
yang dilanggar tiap hari membuat aturan lain ikut kehilangan wibawa, jadi
aturannya dipersempit jadi yang benar-benar dijaga, lalu dijaga DI SINI:

  * `app/main.py` bebas SQL — rutenya memanggil modul;
  * halaman HTML disajikan satu helper, bukan enam fungsi kembar;
  * tidak ada `on_event` yang sudah usang (memunculkan DeprecationWarning
    tiap kali tes dijalankan, sampai peringatan baru tidak terlihat lagi);
  * berkas kode/dokumen di git memakai LF.
"""

import re
import subprocess
import sys
import unittest
from pathlib import Path

AKAR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(AKAR))

KATA_SQL = re.compile(r"\b(SELECT|INSERT INTO|UPDATE|DELETE FROM|CREATE TABLE)\b")
DEKORATOR_USANG = re.compile(r"^\s*@\w+\.on_event\(", re.M)

# Yang memang CRLF dengan sengaja: berkas data mentah dari Lichess/Chess.com,
# dan berkas .cmd Windows (cmd.exe butuh CRLF).
BOLEH_CRLF = ("data/pgn/",)
BOLEH_CRLF_AKHIRAN = (".cmd", ".bat")


class MainTanpaSql(unittest.TestCase):
    def test_main_py_tidak_memuat_sql(self):
        isi = (AKAR / "app" / "main.py").read_text(encoding="utf-8")
        kode = [b for b in isi.splitlines()
                if not b.lstrip().startswith("#")]       # komentar boleh menyebut SQL
        ketemu = [b.strip() for b in kode if KATA_SQL.search(b)]
        self.assertEqual(ketemu, [],
                         "app/main.py harus bebas SQL; taruh kuerinya di modul "
                         "(mis. repositori.pemain_ada) lalu panggil dari sini")

    def test_pemeriksaan_pemain_lewat_repositori(self):
        from app import repositori
        self.assertTrue(hasattr(repositori, "pemain_ada"))


class HalamanSatuHelper(unittest.TestCase):
    def test_rute_halaman_memakai_helper(self):
        isi = (AKAR / "app" / "main.py").read_text(encoding="utf-8")
        self.assertIn("def halaman(nama: str)", isi)
        # tidak ada lagi enam fungsi kembar yang mengulang pesan 404 yang sama
        self.assertLessEqual(isi.count("belum dibuat"), 1)

    def test_halaman_yang_tidak_ada_jadi_404(self):
        from fastapi import HTTPException

        from app.main import halaman
        with self.assertRaises(HTTPException) as e:
            halaman("halaman-yang-tidak-pernah-ada.html")
        self.assertEqual(e.exception.status_code, 404)
        self.assertIn("halaman-yang-tidak-pernah-ada.html", str(e.exception.detail))

    def test_halaman_yang_ada_dikembalikan(self):
        from app.main import halaman
        self.assertTrue(str(halaman("index.html").path).endswith("index.html"))


class TanpaApiUsang(unittest.TestCase):
    def test_tidak_ada_on_event(self):
        for berkas in (AKAR / "app").glob("*.py"):
            # dicari dekoratornya, bukan sekadar katanya: docstring boleh
            # menyebut on_event untuk menjelaskan kenapa sudah ditinggalkan
            self.assertIsNone(DEKORATOR_USANG.search(berkas.read_text(encoding="utf-8")),
                              f"{berkas.name}: pakai lifespan, bukan on_event (usang di FastAPI)")


class AkhirBaris(unittest.TestCase):
    def test_berkas_kode_dan_dokumen_di_git_memakai_lf(self):
        hasil = subprocess.run(["git", "ls-files", "--eol"], cwd=AKAR,
                               capture_output=True, text=True, encoding="utf-8")
        if hasil.returncode != 0:
            self.skipTest("bukan repo git")
        salah = []
        for baris in hasil.stdout.splitlines():
            if "i/crlf" not in baris:
                continue
            nama = baris.split("\t")[-1].strip()
            if not nama.startswith(BOLEH_CRLF) and not nama.endswith(BOLEH_CRLF_AKHIRAN):
                salah.append(nama)
        self.assertEqual(salah, [], "berkas ini tersimpan CRLF di git; ubah ke LF")

    def test_isi_di_disk_sesuai_gitattributes(self):
        """Audit 28 Sep 2026: tes di atas hanya melihat isi di git. Berkas ber-
        atribut eol=lf yang di DISK CRLF, dan data/pgn (-text) yang di disk
        CRLF sementara di git LF, tidak tertangkap — stat cache git ikut
        menyembunyikannya dari `git status`."""
        hasil = subprocess.run(["git", "ls-files", "--eol"], cwd=AKAR,
                               capture_output=True, text=True, encoding="utf-8")
        if hasil.returncode != 0:
            self.skipTest("bukan repo git")
        salah = []
        for baris in hasil.stdout.splitlines():
            kolom = baris.split()
            if len(kolom) < 4:
                continue
            indeks, disk, nama = kolom[0], kolom[1], baris.split("\t")[-1].strip()
            if "eol=lf" in baris and disk == "w/crlf":
                salah.append(nama)                          # harusnya LF di disk
            elif "attr/-text" in baris and indeks == "i/lf" and disk == "w/crlf":
                salah.append(nama)                          # -text: disk harus = git
        self.assertEqual(salah, [], "akhir baris di disk berbeda dari yang dijanjikan .gitattributes")


if __name__ == "__main__":
    unittest.main()
