"""
Uji ke_mysql.py — salinan database ke MySQL/MariaDB untuk belajar SQL
(Ray, 19 Sep 2026: "pindah pakai my sql saja dulu sekalian saya belajar").

MySQL-nya sendiri tidak dipanggil di sini (butuh server XAMPP menyala). Yang
dijaga justru bagian yang paling mudah meleset diam-diam: daftar kolom skema
MySQL harus memuat kolom yang SAMA dengan tabel SQLite. Urutannya sengaja
TIDAK ikut dijaga: kolom `akun_pribadi` ada di tempat berbeda antara database
Ray (ditambahkan migrasi) dan skema baru, sementara INSERT menyebut nama
kolomnya satu per satu dari PRAGMA table_info, jadi urutan tidak berpengaruh.
Kalau nanti ada kolom baru di app/db.py, tes ini gagal dan skema MySQL ikut
diperbarui.
"""

import re
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

AKAR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(AKAR))

import ke_mysql
from app.db import buat_skema, koneksi


def kolom_skema_mysql(tabel: str) -> list[str]:
    """Nama kolom dari potongan CREATE TABLE di ke_mysql.SKEMA."""
    kolom = []
    for baris in ke_mysql.SKEMA[tabel].strip().splitlines():
        baris = baris.strip().rstrip(",")
        # Komentar SQL dilewati: skema di ke_mysql.py boleh dijelaskan sendiri,
        # sama seperti app/db.py. Tanpa ini '--' terbaca sebagai nama kolom.
        if (not baris or baris.startswith("--")
                or baris.upper().startswith(("PRIMARY KEY", "UNIQUE", "KEY ", "INDEX"))):
            continue
        kolom.append(baris.split()[0])
    return kolom


class SkemaCocok(unittest.TestCase):
    """Skema MySQL dibandingkan dengan skema SQLite yang dibuat app/db.py."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.conn = koneksi(Path(cls.tmp.name) / "uji.db")
        buat_skema(cls.conn)

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()
        cls.tmp.cleanup()

    def kolom_sqlite(self, tabel):
        return [r[1] for r in self.conn.execute(f"PRAGMA table_info({tabel})")]

    def test_semua_tabel_sqlite_ikut_disalin(self):
        """Audit 28 Sep 2026: latihan_jawaban (v9) tidak pernah ikut disalin."""
        tabel = {r[0] for r in self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'")}
        self.assertEqual(sorted(tabel - set(ke_mysql.SKEMA)), [])

    def test_tanpa_insert_ignore(self):
        """IGNORE menelan galat 'data terlalu panjang' dan memotong diam-diam."""
        self.assertNotIn("INSERT IGNORE", (AKAR / "ke_mysql.py").read_text(encoding="utf-8")
                         .replace("BUKAN INSERT IGNORE", "").replace("INSERT IGNORE di", ""))

    def test_kontrol_waktu_muat_kalimat_siaran(self):
        import re
        batas = int(re.search(r"kontrol_waktu\s+VARCHAR\((\d+)\)", ke_mysql.SKEMA["partai"]).group(1))
        self.assertGreaterEqual(batas, 179, "TimeControl siaran terpanjang di data asli = 179 aksara")

    def test_semua_tabel_yang_disalin_ada_di_sqlite(self):
        ada = {r[0] for r in self.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for t in ke_mysql.SEMUA:
            self.assertIn(t, ada, f"tabel {t} tidak ada di skema SQLite")

    def test_kolom_sama(self):
        for t in ke_mysql.SEMUA:
            self.assertEqual(sorted(kolom_skema_mysql(t)), sorted(self.kolom_sqlite(t)),
                             f"kolom tabel {t} berbeda antara MySQL dan SQLite")

    def test_kunci_lanjut_ada_untuk_tiap_tabel(self):
        for t in ke_mysql.SEMUA:
            self.assertIn(t, ke_mysql.KUNCI_LANJUT, t)
            for k in ke_mysql.KUNCI_LANJUT[t]:
                self.assertIn(k, self.kolom_sqlite(t), f"{t}.{k}")

    def test_indeks_menyebut_kolom_yang_ada(self):
        for tabel, perintah in ke_mysql.INDEKS.items():
            kolom = set(self.kolom_sqlite(tabel))
            for p in perintah:
                di_dalam = re.search(r"\(([^)]*)\)\s*$", p).group(1)
                for k in (x.strip() for x in di_dalam.split(",")):
                    self.assertIn(k, kolom, f"{tabel}: indeks menyebut kolom {k}")


class Bantu(unittest.TestCase):
    def test_kelompok_tabel(self):
        self.assertEqual(ke_mysql.SEMUA, ke_mysql.KECIL + ke_mysql.BESAR)
        # tabel raksasa sengaja dipisah supaya bisa disalin belakangan
        self.assertIn("analisis_langkah", ke_mysql.BESAR)
        self.assertIn("posisi", ke_mysql.BESAR)

    def test_contoh_query_memakai_tabel_sungguhan(self):
        for t in ("partai", "pemain", "analisis", "analisis_langkah", "posisi"):
            self.assertIn(t, ke_mysql.CONTOH)


if __name__ == "__main__":
    unittest.main()
