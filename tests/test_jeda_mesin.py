"""
Uji app/jeda_mesin.py — tombol Jeda/Lanjutkan di halaman Mesin.

Ray, 13 Sep 2026: "pause dulu agar tidak memakan banyak cpu, saya mau pakai
untuk yang lain".

Yang dijaga: tanda jeda tersimpan dan tidak menghapus pengaturan lain, jeda
tidak membekukan dua kali, batch baru ditolak selama dijeda (tombol Mulai dan
penjaga antrean), dan (di Windows) proses yang dibekukan benar-benar berhenti
memakan CPU lalu jalan lagi setelah dicairkan.
"""

import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

AKAR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(AKAR))

from fastapi import HTTPException

from app import analisa_web as aw
from app import jeda_mesin as jm


class Tanda(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "pengaturan.json"

    def tearDown(self):
        self.tmp.cleanup()

    def test_bawaan_tidak_dijeda(self):
        self.assertFalse(jm.sedang_jeda(self.path))

    def test_jeda_lalu_lanjutkan(self):
        self.path.write_text('{"batas_cpu_persen": 25}', encoding="utf-8")
        with mock.patch.object(jm, "pid_stockfish", return_value=[]):
            h = jm.jeda(self.path)
            self.assertTrue(h["jeda"])
            self.assertTrue(h["sejak"])
            self.assertTrue(jm.sedang_jeda(self.path))
            jm.lanjutkan(self.path)
        self.assertFalse(jm.sedang_jeda(self.path))
        self.assertIn('"batas_cpu_persen": 25', self.path.read_text(encoding="utf-8"))

    def test_jeda_dua_kali_tidak_membekukan_lagi(self):
        dibekukan = []
        with mock.patch.object(jm, "pid_stockfish", return_value=[111]), \
             mock.patch.object(jm, "_ubah_proses", side_effect=lambda pid, b: dibekukan.append((pid, b)) or True):
            jm.jeda(self.path)
            jm.jeda(self.path)
        self.assertEqual(dibekukan, [(111, True)])


class BatchBaruDitahan(unittest.TestCase):
    def test_mulai_ditolak_selama_jeda(self):
        with mock.patch.object(jm, "sedang_jeda", return_value=True):
            with self.assertRaises(HTTPException) as e:
                aw.luncurkan(aw.Saringan(terbaru=1, profil="cepat", sumber="gm"))
        self.assertEqual(e.exception.status_code, 409)

    def test_penjaga_tidak_menjalankan_apa_pun(self):
        import jaga_antrean as jaga
        with mock.patch.object(jm, "sedang_jeda", return_value=True), \
             mock.patch.object(jm, "bekukan_yang_lolos", return_value=0), \
             mock.patch.object(jaga, "segarkan_status"), \
             mock.patch.object(jaga, "_pindai_batch", return_value=[]), \
             mock.patch.object(jaga.antrean, "berikutnya") as berikutnya, \
             mock.patch.object(jaga, "luncurkan") as luncurkan:
            self.assertTrue(jaga.satu_putaran(2))
        berikutnya.assert_not_called()
        luncurkan.assert_not_called()


def _waktu_cpu(pid: int) -> float:
    import ctypes
    from ctypes import wintypes
    k32 = ctypes.windll.kernel32
    h = k32.OpenProcess(0x1000, False, pid)
    try:
        a, b, kernel, user = (wintypes.FILETIME() for _ in range(4))
        k32.GetProcessTimes(h, ctypes.byref(a), ctypes.byref(b), ctypes.byref(kernel), ctypes.byref(user))
        ubah = lambda ft: (ft.dwHighDateTime << 32 | ft.dwLowDateTime) / 1e7   # noqa: E731
        return ubah(kernel) + ubah(user)
    finally:
        k32.CloseHandle(h)


@unittest.skipUnless(os.name == "nt", "pembekuan proses diuji di Windows")
class ProsesSungguhan(unittest.TestCase):
    def test_proses_beku_tidak_memakai_cpu_lalu_jalan_lagi(self):
        p = subprocess.Popen([sys.executable, "-c", "while True: pass"])
        try:
            time.sleep(0.5)
            self.assertTrue(jm._ubah_proses(p.pid, True))
            time.sleep(0.2)
            awal = _waktu_cpu(p.pid)
            time.sleep(1.0)
            self.assertLess(_waktu_cpu(p.pid) - awal, 0.05)        # beku: praktis nol
            self.assertTrue(jm._beku(p.pid))
            self.assertTrue(jm._ubah_proses(p.pid, False))
            awal = _waktu_cpu(p.pid)
            time.sleep(1.0)
            self.assertGreater(_waktu_cpu(p.pid) - awal, 0.3)      # cair: jalan lagi
            self.assertFalse(jm._beku(p.pid))
        finally:
            p.kill()


if __name__ == "__main__":
    unittest.main()
