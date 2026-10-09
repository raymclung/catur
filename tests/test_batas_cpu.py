"""
Uji app/batas_cpu.py — batas pemakaian CPU engine sebagai persen dari total.

Ray, 13 Sep 2026: "apakah bisa membatasi penggunaan cpu dari total? karena
tadi saya tidak bisa buka apa apa".

Yang dijaga: jumlah inti dan masker yang dihitung dari persen, pengaturan yang
tersimpan, nilai tak sah yang ditolak, dan (di Windows) bahwa affinity benar-
benar terpasang ke proses sungguhan lalu diwarisi anaknya — sifat itulah yang
membuat Stockfish ikut terkurung walau dinyalakan belakangan.
"""

import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

AKAR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(AKAR))

from app import batas_cpu as b


class Hitungan(unittest.TestCase):
    def test_jumlah_inti_dari_persen(self):
        self.assertEqual(b.jumlah_inti(25, 16), 4)
        self.assertEqual(b.jumlah_inti(50, 16), 8)
        self.assertEqual(b.jumlah_inti(75, 16), 12)
        self.assertEqual(b.jumlah_inti(100, 16), 16)

    def test_minimal_dua_inti_supaya_engine_tetap_jalan(self):
        self.assertEqual(b.jumlah_inti(25, 4), 2)
        self.assertEqual(b.jumlah_inti(25, 1), 1)       # mesin satu inti: tidak bisa lebih

    def test_masker_memakai_inti_teratas(self):
        self.assertEqual(b.masker(25, 16), 0xF000)
        self.assertEqual(b.masker(50, 16), 0xFF00)
        self.assertEqual(b.masker(100, 16), 0xFFFF)
        self.assertEqual(b.masker(50, 8), 0xF0)


class Pengaturan(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "pengaturan.json"

    def tearDown(self):
        self.tmp.cleanup()

    def test_bawaan_kalau_belum_ada(self):
        self.assertEqual(b.baca_persen(self.path), b.PERSEN_BAWAAN)

    def test_simpan_lalu_baca(self):
        b.simpan_persen(25, self.path)
        self.assertEqual(b.baca_persen(self.path), 25)

    def test_nilai_tak_sah_ditolak(self):
        with self.assertRaises(ValueError):
            b.simpan_persen(33, self.path)

    def test_berkas_rusak_kembali_ke_bawaan(self):
        self.path.write_text("{rusak", encoding="utf-8")
        self.assertEqual(b.baca_persen(self.path), b.PERSEN_BAWAAN)

    def test_pengaturan_lain_di_berkas_tidak_hilang(self):
        self.path.write_text('{"lain": 1}', encoding="utf-8")
        b.simpan_persen(75, self.path)
        self.assertIn('"lain"', self.path.read_text(encoding="utf-8"))


@unittest.skipUnless(os.name == "nt" and (os.cpu_count() or 1) >= 4, "butuh Windows dengan >= 4 inti")
class ProsesSungguhan(unittest.TestCase):
    def test_affinity_terpasang_dan_diwarisi_anak(self):
        kode = ("import subprocess,sys,time; time.sleep(1.5); "
                "c = subprocess.Popen([sys.executable,'-c','import time; time.sleep(6)']); "
                "print(c.pid, flush=True); time.sleep(6)")
        induk = subprocess.Popen([sys.executable, "-c", kode], stdout=subprocess.PIPE, text=True)
        try:
            m = b.masker(25)
            self.assertTrue(b.terapkan(induk.pid, m))
            anak = int(induk.stdout.readline())
            time.sleep(0.3)
            import ctypes
            k32 = ctypes.windll.kernel32
            h = k32.OpenProcess(0x1000, False, anak)     # QUERY_LIMITED_INFORMATION
            self.assertTrue(h)
            try:
                proses, sistem = ctypes.c_size_t(), ctypes.c_size_t()
                self.assertTrue(k32.GetProcessAffinityMask(h, ctypes.byref(proses), ctypes.byref(sistem)))
                self.assertEqual(proses.value, m)
            finally:
                k32.CloseHandle(h)
        finally:
            # Tanpa menunggu & menutup pipa, unittest melaporkan
            # "subprocess ... is still running" dan "unclosed file", dan proses
            # anak-cucunya sempat hidup sesudah tes selesai (audit 21 Sep 2026).
            for pid in (locals().get("anak"), induk.pid):
                if pid:
                    subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                                   capture_output=True, creationflags=0x08000000)
            induk.kill()
            induk.wait(timeout=10)
            if induk.stdout:
                induk.stdout.close()


if __name__ == "__main__":
    unittest.main()
