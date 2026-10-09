"""Uji cadangkan_db.py pada database kecil sementara (bukan catur.db asli)."""

import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

AKAR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(AKAR))

from app.db import buat_skema, koneksi, sekarang
from cadangkan_db import cadangkan, daftar_cadangan


class UjiCadangan(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.akar = Path(self.tmp.name)
        self.db = self.akar / "uji.db"
        self.folder = self.akar / "backup"
        conn = koneksi(self.db)
        buat_skema(conn)
        conn.execute("INSERT INTO pemain(nama, nama_normal, dibuat_pada) VALUES (?,?,?)",
                     ("Carlsen, Magnus", "carlsen magnus", sekarang()))
        conn.commit()
        self.conn = conn      # dibiarkan terbuka: meniru server yang sedang hidup

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_cadangan_berisi_data_yang_sama(self):
        tujuan = cadangkan(self.db, self.folder, simpan=5)
        self.assertTrue(tujuan.is_file())
        c = sqlite3.connect(tujuan)
        self.assertEqual(c.execute("SELECT nama FROM pemain").fetchone()[0], "Carlsen, Magnus")
        self.assertEqual(c.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        c.close()

    def test_tidak_ada_berkas_part_tersisa(self):
        cadangkan(self.db, self.folder)
        self.assertEqual(list(self.folder.glob("*.part")), [])

    def test_rotasi_menyisakan_n_terbaru(self):
        for i in range(4):
            b = self.folder / f"catur-2026010{i + 1}-000000.db"
            self.folder.mkdir(exist_ok=True)
            sqlite3.connect(b).close()       # berkas lama tiruan
        cadangkan(self.db, self.folder, simpan=3)
        sisa = [b.name for b in daftar_cadangan(self.folder)]
        self.assertEqual(len(sisa), 3)
        self.assertNotIn("catur-20260101-000000.db", sisa)
        self.assertNotIn("catur-20260102-000000.db", sisa)

    def test_database_tidak_ada_ditolak(self):
        with self.assertRaises(FileNotFoundError):
            cadangkan(self.akar / "tidak-ada.db", self.folder)



class SalinKeCloud(unittest.TestCase):
    """
    Ekspor inti disalin ke folder yang disinkronkan OneDrive (audit 25 Sep 2026:
    ekspor yang dibuat sekali lalu tidak diunggah akan basi seperti cadangan
    13 Sep dulu). Yang diuji adalah bukti yang bisa diperiksa: berkas ada di
    folder tujuan, ukurannya sama, tidak ada sisa .part, dan yang lama dibuang.
    """

    def setUp(self):
        from cadangkan_db import buang_inti_lama, salin_ke_cloud
        self.salin = salin_ke_cloud
        self.buang = buang_inti_lama
        self.tmp = tempfile.TemporaryDirectory()
        self.akar = Path(self.tmp.name)
        self.cloud = self.akar / "OneDrive" / "catur-cadangan"

    def tearDown(self):
        self.tmp.cleanup()

    def buat_inti(self, folder, cap, isi=b"x" * 1000):
        folder.mkdir(parents=True, exist_ok=True)
        b = folder / f"catur-inti-{cap}.db.gz"
        b.write_bytes(isi)
        return b

    def test_tersalin_utuh_tanpa_sisa_part(self):
        sumber = self.buat_inti(self.akar / "backup", "20260925-080000", b"abc" * 5000)
        tujuan = self.salin(sumber, self.cloud)
        self.assertEqual(tujuan, self.cloud / sumber.name)
        self.assertEqual(tujuan.stat().st_size, sumber.stat().st_size)
        self.assertEqual(tujuan.read_bytes(), sumber.read_bytes())
        self.assertEqual(list(self.cloud.glob("*.part")), [])

    def test_hanya_n_terbaru_yang_dipertahankan_di_cloud(self):
        for cap in ("20260901-000000", "20260908-000000", "20260915-000000"):
            self.buat_inti(self.cloud, cap)
        sumber = self.buat_inti(self.akar / "backup", "20260922-000000")
        self.salin(sumber, self.cloud, simpan=2)
        sisa = sorted(p.name for p in self.cloud.glob("catur-inti-*.db.gz"))
        self.assertEqual(sisa, ["catur-inti-20260915-000000.db.gz",
                                "catur-inti-20260922-000000.db.gz"])

    def test_simpan_nol_tidak_membuang_apa_pun(self):
        for cap in ("20260901-000000", "20260908-000000"):
            self.buat_inti(self.cloud, cap)
        self.assertEqual(self.buang(self.cloud, simpan=0), [])
        self.assertEqual(len(list(self.cloud.glob("catur-inti-*.db.gz"))), 2)

    def test_cadangan_penuh_tidak_ikut_dibuang(self):
        # Pola inti (catur-inti-*.db.gz) tidak boleh menyentuh catur-*.db penuh.
        penuh = self.buat_inti(self.cloud, "x")
        penuh = penuh.rename(self.cloud / "catur-20260901-000000.db")
        self.buat_inti(self.cloud, "20260908-000000")
        self.buat_inti(self.cloud, "20260915-000000")
        self.buang(self.cloud, simpan=1)
        self.assertTrue(penuh.exists())
        self.assertEqual(len(list(self.cloud.glob("catur-inti-*.db.gz"))), 1)

if __name__ == "__main__":
    unittest.main()


class RetensiRuang(unittest.TestCase):
    """
    Audit 25 Sep 2026: rotasi "simpan N" tidak melihat sisa ruang, padahal
    database tumbuh ±90 MB/hari — N berkas bulan depan jauh lebih besar.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.folder = Path(self.tmp.name) / "backup"
        self.folder.mkdir()
        import cadangkan_db as cb
        self.cb = cb
        self.sisa_asli = cb.sisa_gb

    def tearDown(self):
        self.cb.sisa_gb = self.sisa_asli
        self.tmp.cleanup()

    def isi(self, jumlah):
        dibuat = []
        for i in range(jumlah):
            p = self.folder / f"catur-2026091{i}-120000.db"
            p.write_bytes(b"x")
            dibuat.append(p)
        return dibuat

    def test_ruang_cukup_tidak_membuang_apa_pun(self):
        self.isi(3)
        self.cb.sisa_gb = lambda folder: 100.0
        self.assertEqual(self.cb.rapikan(self.folder, simpan=5, sisakan_gb=20), [])
        self.assertEqual(len(self.cb.daftar_cadangan(self.folder)), 3)

    def test_ruang_sempit_membuang_yang_terlama_dulu(self):
        berkas = self.isi(3)
        self.cb.sisa_gb = lambda folder: 5.0          # selalu di bawah batas
        dibuang = self.cb.rapikan(self.folder, simpan=5, sisakan_gb=20)
        self.assertEqual(dibuang, berkas[:2], "yang dibuang dua tertua")
        self.assertEqual(self.cb.daftar_cadangan(self.folder), [berkas[2]],
                         "cadangan terbaru tidak pernah dibuang")

    def test_cadangan_terakhir_tidak_pernah_dibuang(self):
        berkas = self.isi(1)
        self.cb.sisa_gb = lambda folder: 0.1
        self.assertEqual(self.cb.rapikan(self.folder, simpan=5, sisakan_gb=20), [])
        self.assertEqual(self.cb.daftar_cadangan(self.folder), berkas)

    def test_batas_nol_mematikan_penjagaan_ruang(self):
        self.isi(3)
        self.cb.sisa_gb = lambda folder: 0.0
        self.assertEqual(self.cb.rapikan(self.folder, simpan=5, sisakan_gb=0), [])

    def test_drive_sama_terdeteksi(self):
        a = Path(self.tmp.name) / "a.db"
        a.write_bytes(b"x")
        self.assertTrue(self.cb.drive_sama(a, self.folder))


class CadanganSaatDatabaseSibuk(UjiCadangan):
    """
    Cadangan harus jadi walau ada penulis lain — itu keadaan normal di sini.

    Ditemukan 25 Sep 2026: `Connection.backup()` MENGULANG DARI HALAMAN PERTAMA
    tiap kali sumbernya ditulis. Pada catur.db 4,8 GB dengan dua batch engine
    yang menulis tiap ±30 detik, cadangannya tidak pernah selesai — terukur
    19,5 detik CPU per 30 detik selama 6 menit dengan berkas tujuan mandek di
    1,6 GB. Cadangan yang tidak pernah selesai sama bahayanya dengan cadangan
    rusak: ia gagal justru ketika database sedang sibuk.
    """

    def test_selesai_walau_ada_penulis_lain(self):
        import threading
        berhenti = threading.Event()

        def penulis():
            # Koneksi sendiri: menulis dari thread lain persis seperti batch
            # engine yang berjalan berbarengan.
            c = koneksi(self.db)
            n = 0
            while not berhenti.is_set():
                n += 1
                c.execute("INSERT INTO pemain(nama, nama_normal, dibuat_pada) "
                          "VALUES (?,?,?)", (f"P{n}", f"p{n}", sekarang()))
                c.commit()
            c.close()

        t = threading.Thread(target=penulis, daemon=True)
        t.start()
        try:
            tujuan = cadangkan(self.db, self.folder, simpan=5)
        finally:
            berhenti.set()
            t.join(timeout=10)

        self.assertTrue(tujuan.is_file())
        c = sqlite3.connect(tujuan)
        try:
            self.assertEqual(c.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            # Snapshot yang konsisten, bukan setengah jalan: Carlsen (baris
            # yang sudah ada sebelum penulis mulai) harus selalu ikut.
            self.assertEqual(
                c.execute("SELECT COUNT(*) FROM pemain WHERE nama = ?",
                          ("Carlsen, Magnus",)).fetchone()[0], 1)
        finally:
            c.close()

    def test_sisa_part_dari_percobaan_gagal_tidak_memblokir(self):
        """
        VACUUM INTO menolak menulis ke berkas yang sudah ada. Percobaan yang
        mati di tengah jalan meninggalkan .part, dan tanpa pembersihan setiap
        cadangan berikutnya ikut gagal — tepat kasus 25 Sep 2026, ketika satu
        .part 1,6 GB tertinggal setelah proses backup yang berputar dihentikan.
        """
        self.folder.mkdir(parents=True, exist_ok=True)
        from cadangkan_db import nama_cadangan
        sisa = (self.folder / nama_cadangan()).with_suffix(".part")
        sisa.write_bytes(b"sampah dari percobaan yang gagal")
        tujuan = cadangkan(self.db, self.folder, simpan=5)
        self.assertTrue(tujuan.is_file())
        c = sqlite3.connect(tujuan)
        try:
            self.assertEqual(c.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        finally:
            c.close()


class EksporInti(unittest.TestCase):
    """
    Ekspor kecil untuk cloud: hanya yang tidak bisa dibuat ulang.
    Ray memilih cloud 25 Sep 2026, tapi 4,5 GB per pekan tidak masuk akal.
    """

    def setUp(self):
        from app.db import buat_skema, koneksi, sekarang
        from cadangkan_db import ekspor_inti
        self.ekspor_inti = ekspor_inti
        self.tmp = tempfile.TemporaryDirectory()
        self.akar = Path(self.tmp.name)
        self.db = self.akar / "asal.db"
        conn = koneksi(self.db)
        buat_skema(conn)
        t = sekarang()
        conn.execute("INSERT INTO pemain(id,nama,nama_normal,dibuat_pada,akun_pribadi) "
                     "VALUES(1,'Saya','saya',?,1)", (t,))
        conn.execute("INSERT INTO pemain(id,nama,nama_normal,dibuat_pada,akun_pribadi) "
                     "VALUES(2,'GM A','gm a',?,0)", (t,))
        for pid, putih, hitam in ((10, 1, 2), (11, 2, 2)):      # 10 = punya Ray, 11 = partai GM
            conn.execute("INSERT INTO partai(id,putih_id,hitam_id,hasil,pgn,sumber,hash_unik,"
                         "diimpor_pada) VALUES(?,?,?,'1-0','x','uji',?,?)",
                         (pid, putih, hitam, f"h{pid}", t))
            conn.execute("INSERT INTO analisis(partai_id,engine,profil,depth,dianalisis_pada) "
                         "VALUES(?,'uji','cepat',10,?)", (pid, t))
            conn.execute("INSERT INTO analisis_langkah(partai_id,ply,nomor_langkah,warna,san,uci,"
                         "eval_sebelum_cp,eval_sesudah_cp,eval_putih_cp,kehilangan_cp,klasifikasi) "
                         "VALUES(?,0,1,'putih','e4','e2e4',10,10,10,0,'ok')", (pid,))
        conn.execute("INSERT INTO latihan_jawaban(mode,kunci,hasil,dijawab_pada) "
                     "VALUES('kesalahan','10:5','benar',?)", (t,))
        conn.commit()
        conn.close()

    def tearDown(self):
        self.tmp.cleanup()

    def buka(self, path):
        return sqlite3.connect(path)

    def test_hanya_partai_sendiri_beserta_analisisnya(self):
        hasil = self.ekspor_inti(self.db, self.akar / "keluar", mampatkan=False)
        c = self.buka(hasil)
        self.assertEqual([r[0] for r in c.execute("SELECT id FROM partai")], [10])
        self.assertEqual([r[0] for r in c.execute("SELECT partai_id FROM analisis")], [10])
        self.assertEqual([r[0] for r in c.execute("SELECT partai_id FROM analisis_langkah")], [10])
        self.assertEqual(c.execute("SELECT COUNT(*) FROM latihan_jawaban").fetchone()[0], 1)
        c.close()

    def test_akun_pribadi_tidak_tergeser_walau_urutan_kolom_beda(self):
        """Database lama menaruh akun_pribadi/format di belakang (hasil ALTER
        TABLE). Disalin dengan SELECT *, nilainya masuk kolom yang salah."""
        # tiru database lama: buat ulang tabel pemain tanpa kolom jenis
        conn = sqlite3.connect(self.db)
        conn.executescript("""
            ALTER TABLE pemain RENAME TO pemain_lama;
            CREATE TABLE pemain (
                id INTEGER PRIMARY KEY, nama TEXT NOT NULL, nama_normal TEXT NOT NULL,
                fide_id TEXT UNIQUE, negara TEXT, gelar TEXT, elo_terakhir INTEGER,
                terverifikasi INTEGER NOT NULL DEFAULT 0,
                dibuat_pada TEXT NOT NULL,
                akun_pribadi INTEGER NOT NULL DEFAULT 0);
            INSERT INTO pemain(id,nama,nama_normal,dibuat_pada,akun_pribadi)
                SELECT id,nama,nama_normal,dibuat_pada,akun_pribadi FROM pemain_lama;
            DROP TABLE pemain_lama;
        """)
        conn.commit()
        conn.close()
        hasil = self.ekspor_inti(self.db, self.akar / "keluar2", mampatkan=False)
        c = self.buka(hasil)
        self.assertEqual([r[0] for r in c.execute(
            "SELECT nama FROM pemain WHERE akun_pribadi = 1")], ["Saya"])
        c.close()

    def test_berkas_gz_jauh_lebih_kecil_dan_bisa_dibuka(self):
        import gzip
        gz = self.ekspor_inti(self.db, self.akar / "keluar3", mampatkan=True)
        self.assertEqual(gz.suffix, ".gz")
        self.assertEqual(list((self.akar / "keluar3").glob("*.db")), [], "yang tidak dimampatkan dibuang")
        isi = gzip.open(gz, "rb").read(16)
        self.assertTrue(isi.startswith(b"SQLite format 3"), "hasilnya tetap database SQLite")

    def test_posisi_tersimpan_dari_partai_gm_tidak_menggantung(self):
        """Audit 28 Sep 2026: posisi tersimpan disalin semua, partainya tidak,
        jadi hasil ekspor gagal foreign_key_check. Tautannya dilepas (SET NULL,
        sama dengan skema), posisinya tetap ikut."""
        conn = sqlite3.connect(self.db)
        conn.execute("INSERT INTO posisi_tersimpan(fen,partai_id,dibuat_pada) VALUES('8/8/8/8/8/8/8/K1k5 w - - 0 1',11,'x')")
        conn.execute("INSERT INTO posisi_tersimpan(fen,partai_id,dibuat_pada) VALUES('8/8/8/8/8/8/8/K1k5 w - - 0 1',10,'x')")
        conn.commit()
        conn.close()
        hasil = self.ekspor_inti(self.db, self.akar / "keluar4", mampatkan=False)
        c = self.buka(hasil)
        self.assertEqual(c.execute("PRAGMA foreign_key_check").fetchall(), [])
        self.assertEqual(sorted(r[0] for r in c.execute("SELECT partai_id FROM posisi_tersimpan")
                                if r[0] is not None), [10])
        self.assertEqual(c.execute("SELECT COUNT(*) FROM posisi_tersimpan").fetchone()[0], 2)
        c.close()


class InitBukanCadanganPenuh(unittest.TestCase):
    """
    Audit 28 Sep 2026: pola `catur-*.db` juga cocok dengan `catur-inti-*.db`,
    dan karena "catur-inti" > "catur-2026" berkas inti dianggap cadangan
    TERBARU — rotasi membuang cadangan penuh, termasuk yang baru dibuat.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.folder = Path(self.tmp.name)
        for nama in ("catur-20260901-000000.db", "catur-20260908-000000.db",
                     "catur-inti-20260910-000000.db", "catur-inti-20260910-000000.db.gz"):
            (self.folder / nama).write_bytes(b"x")

    def tearDown(self):
        self.tmp.cleanup()

    def test_inti_tidak_dihitung_sebagai_cadangan(self):
        self.assertEqual([b.name for b in daftar_cadangan(self.folder)],
                         ["catur-20260901-000000.db", "catur-20260908-000000.db"])

    def test_rotasi_menyisakan_cadangan_penuh_terbaru(self):
        from cadangkan_db import rapikan
        rapikan(self.folder, simpan=1)
        sisa = sorted(b.name for b in self.folder.iterdir())
        self.assertIn("catur-20260908-000000.db", sisa)
        self.assertNotIn("catur-20260901-000000.db", sisa)
        self.assertIn("catur-inti-20260910-000000.db", sisa, "berkas inti bukan urusan rotasi ini")

