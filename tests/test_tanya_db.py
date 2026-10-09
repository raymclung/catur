"""
Tes `app/tanya_db.py`: terjemahan pertanyaan jadi SQL, dan pagar-pagarnya.

Yang diuji di sini bukan kepintaran LLM (itu tidak bisa diulang dan bukan
tanggung jawab modul), melainkan bahwa SQL apa pun yang dikembalikan LLM
tidak bisa merusak atau membaca di luar batas. Karena itu LLM tidak pernah
dipanggil: tesnya memberi SQL "seolah-olah dari LLM" langsung ke pemeriksa.

Bagian terpenting adalah `TesDatabaseMenolak`: pemeriksaan teks bisa saja
punya celah, jadi tes itu membuktikan lapis terakhir (`mode=ro` +
`set_authorizer`) menolak tulisan walaupun SQL-nya lolos semua pemeriksaan.
"""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app import tanya_db
from app.tanya_db import (
    BATAS_BARIS,
    SqlDitolak,
    _buka_ro,
    bangun_prompt,
    jalankan_sql,
    kalimat_jawaban,
    periksa_sql,
    peringatan_hasil,
    skema_untuk_prompt,
    tanya,
)


def _db_contoh(folder: Path) -> Path:
    """Database kecil dengan bentuk tabel yang sama seperti yang sungguhan."""
    db = folder / "uji.db"
    con = sqlite3.connect(db)
    con.executescript(
        """
        CREATE TABLE pemain (
            id INTEGER PRIMARY KEY, nama TEXT, nama_normal TEXT, fide_id TEXT,
            negara TEXT, gelar TEXT, elo_terakhir INTEGER,
            terverifikasi INTEGER, dibuat_pada TEXT, akun_pribadi INTEGER
        );
        CREATE TABLE turnamen (
            id INTEGER PRIMARY KEY, nama TEXT, tahun INTEGER, lokasi TEXT,
            sumber_url TEXT, dibuat_pada TEXT
        );
        CREATE TABLE partai (
            id INTEGER PRIMARY KEY, turnamen_id INTEGER, putih_id INTEGER,
            hitam_id INTEGER, tanggal TEXT, babak TEXT, hasil TEXT, eco TEXT,
            pembukaan TEXT, elo_putih INTEGER, elo_hitam INTEGER,
            kontrol_waktu TEXT, jumlah_langkah INTEGER, pgn TEXT, sumber TEXT,
            sumber_url TEXT, hash_unik TEXT, diimpor_pada TEXT, format TEXT
        );
        CREATE TABLE analisis (
            id INTEGER PRIMARY KEY, partai_id INTEGER, engine TEXT,
            depth INTEGER, profil TEXT, akurasi_putih REAL, akurasi_hitam REAL,
            blunder_putih INTEGER, mistake_putih INTEGER,
            inaccuracy_putih INTEGER, blunder_hitam INTEGER,
            mistake_hitam INTEGER, inaccuracy_hitam INTEGER,
            turning_point_ply INTEGER, durasi_detik REAL, dianalisis_pada TEXT
        );
        CREATE TABLE analisis_langkah (
            partai_id INTEGER, ply INTEGER, nomor_langkah INTEGER, warna TEXT,
            san TEXT, uci TEXT, eval_sebelum_cp INTEGER,
            eval_sesudah_cp INTEGER, eval_putih_cp INTEGER, mat_sesudah INTEGER,
            kehilangan_cp INTEGER, klasifikasi TEXT, langkah_terbaik_san TEXT,
            langkah_terbaik_uci TEXT, variasi_terbaik TEXT
        );
        CREATE TABLE sumber_impor (
            id INTEGER PRIMARY KEY, jenis TEXT, kunci TEXT, nama TEXT,
            url TEXT, jumlah_partai INTEGER, jumlah_lolos_saringan INTEGER,
            jumlah_baru INTEGER, selesai INTEGER, diimpor_pada TEXT
        );
        CREATE TABLE posisi_tersimpan (
            id INTEGER PRIMARY KEY, fen TEXT, judul TEXT, catatan TEXT,
            asal TEXT, partai_id INTEGER, dibuat_pada TEXT
        );
        CREATE TABLE posisi (
            fen_kunci TEXT, partai_id INTEGER, ply INTEGER,
            langkah_uci TEXT, langkah_san TEXT
        );
        INSERT INTO pemain (id, nama, gelar, elo_terakhir, akun_pribadi) VALUES
            (1, 'Carlsen, Magnus', 'GM', 2839, 0),
            (2, 'Caruana, Fabiano', 'GM', 2805, 0),
            (3, 'McLung_07', NULL, 1750, 1);
        INSERT INTO partai (id, putih_id, hitam_id, hasil, eco, sumber, tanggal)
        VALUES
            (1, 1, 2, '1-0', 'A29', 'twic', '2026.01.10'),
            (2, 2, 1, '1/2-1/2', 'E04', 'twic', '2026.02.11'),
            (3, 3, 1, '0-1', 'B90', 'lichess-user', '2026.03.12');
        """
    )
    con.commit()
    con.close()
    return db


class TesPeriksaSql(unittest.TestCase):
    """Pemeriksaan teks: menolak yang berbahaya, menerima yang wajar."""

    def test_select_biasa_lolos(self):
        sql = "SELECT nama FROM pemain WHERE gelar = 'GM'"
        self.assertEqual(periksa_sql(sql), sql)

    def test_cte_with_lolos(self):
        sql = ("WITH kuat AS (SELECT id, nama FROM pemain WHERE elo_terakhir > 2700) "
               "SELECT nama FROM kuat")
        self.assertEqual(periksa_sql(sql), sql)

    def test_blok_kode_dibersihkan(self):
        kotor = "```sql\nSELECT COUNT(*) AS jumlah FROM partai;\n```"
        self.assertEqual(periksa_sql(kotor), "SELECT COUNT(*) AS jumlah FROM partai")

    def test_label_sql_dibersihkan(self):
        self.assertEqual(periksa_sql("SQL: SELECT 1 AS satu"), "SELECT 1 AS satu")

    def test_perintah_perubahan_data_ditolak(self):
        for jahat in (
            "DELETE FROM partai",
            "DROP TABLE pemain",
            "UPDATE pemain SET elo_terakhir = 3000",
            "INSERT INTO pemain (nama) VALUES ('x')",
            "ALTER TABLE partai ADD COLUMN x TEXT",
        ):
            with self.subTest(sql=jahat):
                with self.assertRaises(SqlDitolak):
                    periksa_sql(jahat)

    def test_select_yang_menyelundupkan_perintah_kedua_ditolak(self):
        with self.assertRaises(SqlDitolak):
            periksa_sql("SELECT 1; DROP TABLE pemain")

    def test_attach_dan_pragma_ditolak(self):
        with self.assertRaises(SqlDitolak):
            periksa_sql("SELECT * FROM pemain WHERE 1=1 AND ATTACH 'x'")
        with self.assertRaises(SqlDitolak):
            periksa_sql("PRAGMA table_info(pemain)")

    def test_tabel_tak_diizinkan_ditolak(self):
        """`posisi` sengaja di luar daftar: 9,4 juta baris, terlalu mahal."""
        with self.assertRaises(SqlDitolak) as k:
            periksa_sql("SELECT COUNT(*) FROM posisi")
        self.assertIn("posisi", str(k.exception))

    def test_tabel_karangan_ditolak(self):
        with self.assertRaises(SqlDitolak):
            periksa_sql("SELECT * FROM sqlite_master")

    def test_jawaban_kosong_ditolak(self):
        for kosong in ("", "   ", "```sql\n```"):
            with self.subTest(isi=repr(kosong)):
                with self.assertRaises(SqlDitolak):
                    periksa_sql(kosong)

    def test_titik_koma_di_dalam_teks_bukan_perintah_kedua(self):
        sql = "SELECT nama FROM pemain WHERE nama = 'a;b'"
        self.assertEqual(periksa_sql(sql), sql)


class TesDatabaseMenolak(unittest.TestCase):
    """Lapis terakhir: SQLite sendiri yang menolak, bukan regex di atas.

    Tes ini melewati `periksa_sql()` dengan sengaja — inilah gunanya: kalau
    suatu hari pemeriksaan teks bisa dilewati, pagar ini tetap berdiri.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = _db_contoh(Path(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    def test_tulisan_ditolak_walaupun_pemeriksaan_teks_dilewati(self):
        con = _buka_ro(self.db)
        try:
            for jahat in (
                "DELETE FROM partai",
                "UPDATE pemain SET nama = 'x'",
                "INSERT INTO pemain (nama) VALUES ('x')",
                "DROP TABLE partai",
            ):
                with self.subTest(sql=jahat):
                    with self.assertRaises(sqlite3.DatabaseError):
                        con.execute(jahat)
        finally:
            con.close()

    def test_attach_ditolak_authorizer(self):
        con = _buka_ro(self.db)
        try:
            with self.assertRaises(sqlite3.DatabaseError):
                con.execute("ATTACH DATABASE ':memory:' AS lain")
        finally:
            con.close()

    def test_data_tidak_berubah_sesudah_percobaan_tulis(self):
        """Bukti bahwa penolakan itu nyata, bukan diam-diam berhasil."""
        con = _buka_ro(self.db)
        try:
            with self.assertRaises(sqlite3.DatabaseError):
                con.execute("DELETE FROM partai")
        finally:
            con.close()
        periksa = sqlite3.connect(self.db)
        try:
            self.assertEqual(
                periksa.execute("SELECT COUNT(*) FROM partai").fetchone()[0], 3
            )
        finally:
            periksa.close()

    def test_baca_tetap_boleh(self):
        con = _buka_ro(self.db)
        try:
            n = con.execute("SELECT COUNT(*) FROM partai").fetchone()[0]
            self.assertEqual(n, 3)
        finally:
            con.close()


class TesJalankanSql(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = _db_contoh(Path(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    def test_hasil_kolom_dan_baris(self):
        kolom, baris = jalankan_sql(
            "SELECT nama, elo_terakhir FROM pemain WHERE gelar = 'GM' ORDER BY nama",
            db=self.db,
        )
        self.assertEqual(kolom, ["nama", "elo_terakhir"])
        self.assertEqual([b[0] for b in baris], ["Carlsen, Magnus", "Caruana, Fabiano"])

    def test_join_lintas_tabel(self):
        kolom, baris = jalankan_sql(
            "SELECT p.hasil, w.nama AS putih FROM partai p "
            "JOIN pemain w ON w.id = p.putih_id WHERE p.sumber = 'twic' "
            "ORDER BY p.id",
            db=self.db,
        )
        self.assertEqual(kolom, ["hasil", "putih"])
        self.assertEqual(baris[0], ["1-0", "Carlsen, Magnus"])

    def test_batas_dipaksa_walaupun_sql_tanpa_limit(self):
        kolom, baris = jalankan_sql("SELECT id FROM partai", db=self.db, batas=2)
        self.assertEqual(len(baris), 2)

    def test_kolom_karangan_jadi_sqlditolak_bukan_500(self):
        """Audit 27 Sep: OperationalError 'no such column' dulu lolos jadi 500."""
        with self.assertRaises(SqlDitolak) as e:
            jalankan_sql("SELECT kolom_karangan FROM partai", db=self.db)
        self.assertIn("no such column", str(e.exception))
        self.assertIn("kolom_karangan", str(e.exception))     # SQL-nya ikut ditampilkan

    def test_database_terkunci_bukan_salah_pertanyaan(self):
        from app.tanya_db import DatabaseSibuk
        import app.tanya_db as td

        class Palsu:
            def execute(self, *a):
                raise sqlite3.OperationalError("database is locked")

            def close(self):
                pass
        asli = td._buka_ro
        td._buka_ro = lambda db: Palsu()
        self.addCleanup(setattr, td, "_buka_ro", asli)
        with self.assertRaises(DatabaseSibuk):
            jalankan_sql("SELECT id FROM partai", db=self.db)

    def test_batas_tetap_berlaku_meski_sql_minta_lebih(self):
        """LIMIT milik LLM tidak bisa melampaui batas kita, karena dibungkus."""
        kolom, baris = jalankan_sql(
            "SELECT id FROM partai LIMIT 999", db=self.db, batas=1
        )
        self.assertEqual(len(baris), 1)

    def test_order_by_di_dalam_bungkus_tetap_jalan(self):
        kolom, baris = jalankan_sql(
            "SELECT id FROM partai ORDER BY id DESC", db=self.db
        )
        self.assertEqual([b[0] for b in baris], [3, 2, 1])


class TesSkemaDanPrompt(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = _db_contoh(Path(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    def test_skema_memuat_tabel_diizinkan_saja(self):
        skema = skema_untuk_prompt(self.db)
        self.assertIn("partai (", skema)
        self.assertIn("pemain (", skema)
        # `posisi` tidak diizinkan, jadi tidak boleh ditawarkan ke LLM
        self.assertNotRegex(skema, r"(?m)^posisi \(")

    def test_kolom_pgn_tidak_ditawarkan(self):
        """PGN satu partai bisa ribuan karakter; tidak ada gunanya di hasil."""
        self.assertNotIn("pgn TEXT", skema_untuk_prompt(self.db))

    def test_prompt_memuat_pertanyaan_dan_skema(self):
        p = bangun_prompt("berapa partai Carlsen?", "partai (3 baris): id INTEGER")
        self.assertIn("berapa partai Carlsen?", p)
        self.assertIn("partai (3 baris)", p)
        self.assertIn("SELECT", p)


class TesPeringatanHasil(unittest.TestCase):
    """Pola SQL yang jalan tapi menyesatkan — ditemukan saat banding model."""

    def test_hasil_kosong_diperingatkan(self):
        pesan = peringatan_hasil("SELECT nama FROM pemain WHERE nama = 'Magnus Carlsen'", [])
        self.assertTrue(pesan)
        self.assertIn("kosong", pesan[0].lower())

    def test_hasil_berisi_tidak_diperingatkan_soal_kosong(self):
        pesan = peringatan_hasil("SELECT nama FROM pemain", [["Carlsen, Magnus"]])
        self.assertFalse(any("kosong" in p.lower() for p in pesan))

    def test_join_or_dengan_rata_rata_warna_diperingatkan(self):
        """Kejadian nyata: AVG(akurasi_putih) di atas JOIN putih_id OR hitam_id."""
        sql = ("SELECT p.nama, AVG(a.akurasi_putih) FROM pemain p "
               "JOIN partai pa ON p.id = pa.putih_id OR p.id = pa.hitam_id "
               "JOIN analisis a ON pa.id = a.partai_id GROUP BY p.id")
        pesan = peringatan_hasil(sql, [["Giri, Anish", 96.3]])
        self.assertTrue(any("OR" in p for p in pesan))

    def test_pemain_id_disamakan_dengan_partai_id_diperingatkan(self):
        """Kesalahan 3B: JOIN analisis a ON p.id = a.partai_id."""
        sql = ("SELECT p.nama FROM pemain p JOIN analisis a ON p.id = a.partai_id "
               "GROUP BY p.id")
        pesan = peringatan_hasil(sql, [["x"]])
        self.assertTrue(any("partai_id" in p for p in pesan))

    def test_sql_wajar_tidak_diperingatkan(self):
        sql = "SELECT sumber, COUNT(*) AS jumlah FROM partai GROUP BY sumber"
        self.assertEqual(peringatan_hasil(sql, [["twic", 164836]]), [])

    def test_join_analisis_yang_benar_tidak_diperingatkan(self):
        """`pa.id = a.partai_id` itu JOIN yang BENAR — jangan ditandai.

        Versi pertama deteksi ini menandainya sebagai kesalahan; peringatan
        palsu membuat semua peringatan lain ikut diabaikan.
        """
        sql = ("SELECT p.nama, AVG(a.akurasi_putih) FROM pemain p "
               "JOIN partai pa ON pa.putih_id = p.id "
               "JOIN analisis a ON pa.id = a.partai_id GROUP BY p.id")
        pesan = peringatan_hasil(sql, [["Giri, Anish", 96.3]])
        self.assertFalse(
            any("tidak berhubungan" in p for p in pesan),
            f"JOIN yang benar ditandai keliru: {pesan}",
        )

    def test_pemain_tanpa_alias_tidak_bikin_salah_tangkap(self):
        sql = ("SELECT pemain.nama FROM pemain JOIN partai ON partai.putih_id = pemain.id "
               "JOIN analisis ON partai.id = analisis.partai_id")
        pesan = peringatan_hasil(sql, [["Carlsen, Magnus"]])
        self.assertFalse(any("tidak berhubungan" in p for p in pesan))


class TesPromptMemuatJebakanNyata(unittest.TestCase):
    """Prompt harus memuat peringatan atas kesalahan yang terbukti terjadi."""

    def test_format_nama_dijelaskan(self):
        p = bangun_prompt("siapa saja", "partai (3 baris): id INTEGER")
        self.assertIn("Belakang, Depan", p)
        self.assertIn("LIKE", p)

    def test_relasi_analisis_dijelaskan(self):
        p = bangun_prompt("akurasi siapa terbaik", "analisis (1 baris): id INTEGER")
        self.assertIn("partai_id", p)


class TesTanyaTanpaLlm(unittest.TestCase):
    """`tanya()` harus gagal rapi sebelum menyentuh jaringan kalau masukannya kosong."""

    def test_pertanyaan_kosong_ditolak_tanpa_memanggil_llm(self):
        with self.assertRaises(SqlDitolak):
            tanya("   ")

    def test_batas_bawaan_masuk_akal(self):
        self.assertGreater(BATAS_BARIS, 0)
        self.assertLessEqual(BATAS_BARIS, 1000)


class TesKalimatJawaban(unittest.TestCase):
    """Kalimat disusun templat dari baris yang sama — angka tidak boleh berubah."""

    def test_satu_nilai(self):
        self.assertEqual(kalimat_jawaban(["jumlah_partai"], [[272470]]),
                         "Jumlah partai: 272.470.")

    def test_desimal_cara_indonesia(self):
        self.assertEqual(kalimat_jawaban(["rata_akurasi"], [[1234.5678]]),
                         "Rata akurasi: 1.234,57.")

    def test_satu_baris_banyak_kolom(self):
        k = kalimat_jawaban(["nama", "partai"], [["Carlsen, Magnus", 3051]])
        self.assertEqual(k, "Satu baris: nama Carlsen, Magnus, partai 3.051.")

    def test_banyak_baris_tidak_mengaku_teratas(self):
        baris = [[f"s{i}", i] for i in range(7)]
        k = kalimat_jawaban(["sumber", "jumlah"], baris)
        self.assertTrue(k.startswith("7 baris. 5 baris pertama (urutan dari SQL): "))
        self.assertIn("s0 (jumlah 0)", k)
        self.assertNotIn("s5", k)
        self.assertNotIn("teratas", k.lower())

    def test_terpotong_ditandai(self):
        self.assertTrue(kalimat_jawaban(["x"], [[1], [2]], terpotong=True).startswith("2+ baris"))

    def test_kosong_tanpa_kalimat(self):
        self.assertIsNone(kalimat_jawaban(["x"], []))
        self.assertEqual(kalimat_jawaban(["x"], [[None]]), "X: kosong.")


class TesCache(unittest.TestCase):
    """Cache menyimpan SQL, bukan hasil: LLM tidak dipanggil ulang, angka tetap segar."""

    SQL = "SELECT COUNT(*) AS jumlah FROM partai"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = _db_contoh(Path(self.tmp.name))
        tanya_db.kosongkan_cache()
        self.addCleanup(tanya_db.kosongkan_cache)

    def tearDown(self):
        self.tmp.cleanup()

    def test_pertanyaan_sama_tidak_memanggil_llm_lagi(self):
        with mock.patch.object(tanya_db.llm, "panggil", return_value=self.SQL) as p:
            a = tanya("Berapa partai?", db=self.db)
            b = tanya("  berapa   PARTAI ", db=self.db)
        self.assertEqual(p.call_count, 1)
        self.assertFalse(a["dari_cache"])
        self.assertTrue(b["dari_cache"])
        self.assertEqual(b["kalimat"], "Jumlah: 3.")

    def test_hasil_dibaca_ulang_bukan_disimpan(self):
        with mock.patch.object(tanya_db.llm, "panggil", return_value=self.SQL):
            self.assertEqual(tanya("berapa partai", db=self.db)["baris"], [[3]])
            con = sqlite3.connect(self.db)
            con.execute("INSERT INTO partai (id, hasil) VALUES (4, '1-0')")
            con.commit()
            con.close()
            self.assertEqual(tanya("berapa partai", db=self.db)["baris"], [[4]])

    def test_sql_cache_yang_rusak_dibuang_lalu_tanya_ulang(self):
        kunci = (tanya_db._kunci_pertanyaan("berapa partai"), tanya_db.MODEL_BAWAAN,
                 str(self.db.resolve()))
        tanya_db._cache_sql[kunci] = "SELECT kolom_hilang FROM partai"
        with mock.patch.object(tanya_db.llm, "panggil", return_value=self.SQL) as p:
            h = tanya("berapa partai", db=self.db)
        self.assertEqual(p.call_count, 1)
        self.assertFalse(h["dari_cache"])
        self.assertEqual(tanya_db._cache_sql[kunci], self.SQL)

    def test_sql_ditolak_tidak_masuk_cache(self):
        with mock.patch.object(tanya_db.llm, "panggil", return_value="DELETE FROM partai"):
            with self.assertRaises(SqlDitolak):
                tanya("hapus", db=self.db)
        self.assertEqual(len(tanya_db._cache_sql), 0)

    def test_model_berbeda_tidak_berbagi_cache(self):
        with mock.patch.object(tanya_db.llm, "panggil", return_value=self.SQL) as p:
            tanya("berapa partai", db=self.db)
            tanya("berapa partai", db=self.db, model="qwen2.5-coder:3b")
        self.assertEqual(p.call_count, 2)

    def test_cache_dibatasi(self):
        with mock.patch.object(tanya_db, "BATAS_CACHE_SQL", 3), \
             mock.patch.object(tanya_db.llm, "panggil", return_value=self.SQL):
            for i in range(5):
                tanya(f"pertanyaan {i}", db=self.db)
        self.assertEqual(len(tanya_db._cache_sql), 3)

    def test_skema_tidak_dihitung_ulang_dalam_umurnya(self):
        with mock.patch.object(tanya_db, "_bangun_skema", return_value="partai (3 baris)") as b:
            skema_untuk_prompt(self.db)
            skema_untuk_prompt(self.db)
        self.assertEqual(b.call_count, 1)
        with mock.patch.object(tanya_db, "_bangun_skema", return_value="x") as b, \
             mock.patch.object(tanya_db, "UMUR_SKEMA_DETIK", 0):
            skema_untuk_prompt(self.db)
        self.assertEqual(b.call_count, 1)


if __name__ == "__main__":
    unittest.main()
