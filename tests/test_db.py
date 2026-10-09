"""
Pengujian skema: apakah aturannya benar-benar ditegakkan database.

Jalankan dari akar proyek:
    python -m unittest discover -s tests -v
"""

import sqlite3
import tempfile
import unittest
from pathlib import Path

from app.db import VERSI_SKEMA, buat_skema, koneksi, sekarang, versi_skema


class DasarDB(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = koneksi(Path(self.tmp.name) / "uji.db")
        buat_skema(self.conn)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def pemain(self, nama, fide_id=None, terverifikasi=0):
        cur = self.conn.execute(
            "INSERT INTO pemain(nama, nama_normal, fide_id, terverifikasi, dibuat_pada) "
            "VALUES (?,?,?,?,?)",
            (nama, nama.lower(), fide_id, terverifikasi, sekarang()),
        )
        return cur.lastrowid

    def partai(self, putih_id, hitam_id, hash_unik):
        self.conn.execute(
            "INSERT INTO partai(putih_id, hitam_id, hasil, pgn, sumber, hash_unik, "
            "diimpor_pada) VALUES (?,?,?,?,?,?,?)",
            (putih_id, hitam_id, "*", "1. e4 *", "uji", hash_unik, sekarang()),
        )

    def turnamen(self, nama, tahun):
        self.conn.execute(
            "INSERT INTO turnamen(nama, tahun, dibuat_pada) VALUES (?,?,?)",
            (nama, tahun, sekarang()),
        )

    def jumlah(self, tabel):
        return self.conn.execute(f"SELECT COUNT(*) FROM {tabel}").fetchone()[0]


class UjiKoneksiLintasThread(unittest.TestCase):
    """
    Bug #2 yang sebenarnya (SESI.md bagian 3): FastAPI membuka koneksi di satu
    thread dan menutupnya di thread lain. Dengan pemeriksaan thread bawaan
    sqlite3, close() itu melempar ProgrammingError → server 500.
    """

    def test_koneksi_boleh_ditutup_dari_thread_lain(self):
        import threading

        with tempfile.TemporaryDirectory() as d:
            conn = koneksi(Path(d) / "uji.db")
            buat_skema(conn)
            galat = []

            def tutup():
                try:
                    conn.execute("SELECT COUNT(*) FROM pemain").fetchone()
                    conn.close()
                except Exception as e:      # dulu: sqlite3.ProgrammingError
                    galat.append(e)

            t = threading.Thread(target=tutup)
            t.start()
            t.join(5.0)
            self.assertEqual(galat, [])


class UjiKoneksiSaatTerkunci(unittest.TestCase):
    """
    Mode WAL: pembaca TIDAK menunggu penulis dan tidak gagal — dia membaca
    snapshot sebelum transaksi penulis. Tes ini mengunci perilaku itu supaya
    kalau journal_mode berubah tanpa sengaja, ketahuan.
    """

    def test_pembaca_tidak_menunggu_penulis_di_wal(self):
        import threading
        import time

        import shutil

        # Bukan TemporaryDirectory(): di Windows, penghapusannya gagal kalau
        # file -wal/-shm masih dipegang sepersekian detik setelah close().
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        if True:
            path = Path(d) / "uji.db"
            awal = koneksi(path)
            buat_skema(awal)
            awal.close()

            terkunci = threading.Event()

            # Objek sqlite3 hanya boleh dipakai di thread pembuatnya, jadi
            # seluruh sisi penulis hidup di thread ini.
            def penulis():
                c = koneksi(path)
                c.execute("BEGIN IMMEDIATE")              # kunci tulis dipegang
                c.execute("INSERT INTO turnamen(nama, tahun, dibuat_pada) "
                          "VALUES ('kunci', 2026, ?)", (sekarang(),))
                terkunci.set()
                time.sleep(0.6)
                c.commit()
                c.close()

            t = threading.Thread(target=penulis, daemon=True)
            t.start()
            self.assertTrue(terkunci.wait(5.0))

            mulai = time.time()
            pembaca = koneksi(path)                       # dulu: gagal di sini
            n = pembaca.execute("SELECT COUNT(*) FROM turnamen").fetchone()[0]
            durasi = time.time() - mulai
            t.join(5.0)

            # Snapshot sebelum commit penulis: insert-nya belum terlihat.
            self.assertEqual(n, 0)
            self.assertLess(durasi, 1.0, "pembaca WAL tidak boleh ikut menunggu penulis")
            pembaca.close()


class UjiSkema(DasarDB):
    def test_versi_skema_tercatat(self):
        self.assertEqual(versi_skema(self.conn), VERSI_SKEMA)

    def test_pragma_aktif(self):
        self.assertEqual(self.conn.execute("PRAGMA foreign_keys").fetchone()[0], 1)
        self.assertEqual(self.conn.execute("PRAGMA journal_mode").fetchone()[0], "wal")

    def test_sebelas_index_terpasang(self):
        # v1: 7 index. v2 + idx_analisis_langkah_klas. v4 + idx_posisi_fen
        # (v7: diganti idx_posisi_fen_langkah). v6 + idx_partai_format.
        # v8: idx_partai_sumber diganti idx_partai_sumber_tanggal, jumlah tetap.
        # v9 + idx_latihan_jawaban_kunci. v12 + idx_partai_bisa_dipilih.
        n = self.conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='index' AND name LIKE 'idx_%'"
        ).fetchone()[0]
        self.assertEqual(n, 12)

    def test_tabel_analisis_ada_di_v2(self):
        nama = {r[0] for r in self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertTrue({"analisis", "analisis_langkah"} <= nama)

    def test_buat_skema_dua_kali_tidak_menghapus_data(self):
        self.pemain("A")
        buat_skema(self.conn)
        self.assertEqual(self.jumlah("pemain"), 1)


class UjiPemain(DasarDB):
    def test_fide_id_sama_ditolak(self):
        self.pemain("Carlsen, Magnus", "1503014", 1)
        with self.assertRaises(sqlite3.IntegrityError):
            self.pemain("Magnus Carlsen", "1503014", 1)

    def test_banyak_pemain_tanpa_fide_id_diterima(self):
        self.pemain("A")
        self.pemain("B")
        self.pemain("C")
        self.assertEqual(self.jumlah("pemain"), 3)

    def test_terverifikasi_hanya_0_atau_1(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.pemain("A", terverifikasi=2)

    def test_akun_pribadi_hanya_0_atau_1(self):
        a = self.pemain("A")
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("UPDATE pemain SET akun_pribadi = 5 WHERE id = ?", (a,))

    def test_akun_pribadi_ditegakkan_juga_di_database_hasil_migrasi(self):
        """Audit 28 Sep 2026: kolom yang ditambahkan ALTER TABLE sebelum v11
        tidak punya CHECK, dan database asli termasuk; nilai 5 diterima."""
        self.conn.executescript("""
            DROP TRIGGER IF EXISTS cek_akun_pribadi_insert;
            DROP TRIGGER IF EXISTS cek_akun_pribadi_update;
            ALTER TABLE pemain RENAME TO pemain_baru;
            CREATE TABLE pemain (
                id INTEGER PRIMARY KEY, nama TEXT NOT NULL, nama_normal TEXT NOT NULL,
                fide_id TEXT UNIQUE, negara TEXT, gelar TEXT, elo_terakhir INTEGER,
                terverifikasi INTEGER NOT NULL DEFAULT 0, dibuat_pada TEXT NOT NULL);
            DROP TABLE pemain_baru;
            ALTER TABLE pemain ADD COLUMN akun_pribadi INTEGER NOT NULL DEFAULT 0;
            PRAGMA user_version = 10;
        """)
        a = self.pemain("A")
        self.conn.execute("UPDATE pemain SET akun_pribadi = 5 WHERE id = ?", (a,))  # lubang lama
        self.conn.execute("UPDATE pemain SET akun_pribadi = 0 WHERE id = ?", (a,))
        buat_skema(self.conn)
        self.assertEqual(versi_skema(self.conn), VERSI_SKEMA)
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("UPDATE pemain SET akun_pribadi = 5 WHERE id = ?", (a,))
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("INSERT INTO pemain(nama, nama_normal, dibuat_pada, akun_pribadi) "
                              "VALUES ('B', 'b', 'x', 2)")
        self.conn.execute("UPDATE pemain SET akun_pribadi = 1 WHERE id = ?", (a,))  # sah tetap boleh


class UjiPartai(DasarDB):
    def test_partai_menunjuk_pemain_tidak_ada_ditolak(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.partai(999, 998, "h1")

    def test_hash_unik_sama_ditolak(self):
        a, b = self.pemain("A"), self.pemain("B")
        self.partai(a, b, "h1")
        with self.assertRaises(sqlite3.IntegrityError):
            self.partai(a, b, "h1")


class UjiTurnamen(DasarDB):
    def test_nama_dan_tahun_sama_ditolak(self):
        self.turnamen("Norway Chess", 2026)
        with self.assertRaises(sqlite3.IntegrityError):
            self.turnamen("Norway Chess", 2026)

    def test_nama_sama_tahun_beda_diterima(self):
        self.turnamen("Norway Chess", 2025)
        self.turnamen("Norway Chess", 2026)
        self.assertEqual(self.jumlah("turnamen"), 2)


if __name__ == "__main__":
    unittest.main()
