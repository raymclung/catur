"""Uji indeks posisi & penjelajah pembukaan, tanpa jaringan."""

import sys
import tempfile
import time
import unittest
from pathlib import Path

AKAR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(AKAR))

import chess

from app.db import buat_skema, koneksi, sekarang
from app.posisi import (baris_posisi, cari_langkah, cari_langkah_langsung, fen_kunci,
                        id_belum_terindeks, indeks_partai,
                        kunci_dari_fen_pengguna)


def pgn(langkah, hasil="1-0", elo=(2700, 2700)):
    return (f'[Event "Uji"]\n[Site "X"]\n[Date "2026.01.01"]\n[Round "1"]\n'
            f'[White "A"]\n[Black "B"]\n[Result "{hasil}"]\n'
            f'[WhiteElo "{elo[0]}"]\n[BlackElo "{elo[1]}"]\n\n{langkah} {hasil}\n')


class UjiKunci(unittest.TestCase):
    def test_transposisi_kunci_sama(self):
        a = chess.Board()
        for m in ["e4", "e6", "d4", "d5"]:
            a.push_san(m)
        b = chess.Board()
        for m in ["d4", "d5", "e4", "e6"]:
            b.push_san(m)
        self.assertNotEqual(a.fen(), b.fen()) if a.fen() != b.fen() else None
        self.assertEqual(fen_kunci(a), fen_kunci(b))

    def test_penghitung_dibuang(self):
        self.assertEqual(fen_kunci("rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 5 9"),
                         "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq -")

    def test_fen_pengguna_rusak_ditolak(self):
        with self.assertRaises(ValueError):
            kunci_dari_fen_pengguna("bukan fen")
        with self.assertRaises(ValueError):
            kunci_dari_fen_pengguna("8/8/8/8/8/8/8/8 w - - 0 1")   # tanpa raja

    def test_baris_posisi_dibatasi_maks_ply(self):
        b = baris_posisi(pgn("1. e4 e5 2. Nf3 Nc6 3. Bb5"), maks_ply=3)
        self.assertEqual([x[1] for x in b], [0, 1, 2])
        self.assertEqual(b[0][3], "e4")
        self.assertEqual(b[2][2], "g1f3")


class UjiPenjelajah(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = koneksi(Path(self.tmp.name) / "uji.db")
        buat_skema(self.conn)
        t = sekarang()
        self.conn.execute("INSERT INTO pemain(id,nama,nama_normal,dibuat_pada) VALUES(1,'A','a',?)", (t,))
        self.conn.execute("INSERT INTO pemain(id,nama,nama_normal,dibuat_pada) VALUES(2,'B','b',?)", (t,))
        self.partai = [
            (10, "1. e4 e5 2. Nf3", "1-0", "twic", (2700, 2650)),
            (11, "1. e4 c5 2. Nf3", "0-1", "twic", (2600, 2700)),
            (12, "1. e4 c5 2. Nc3", "1/2-1/2", "lichess-broadcast", (2500, 2500)),
            (13, "1. d4 d5", "1-0", "lichess-user", (2400, 2400)),
        ]
        for pid, l, h, s, elo in self.partai:
            self.conn.execute(
                "INSERT INTO partai(id,putih_id,hitam_id,hasil,pgn,sumber,hash_unik,diimpor_pada,"
                "elo_putih,elo_hitam,tanggal) VALUES(?,1,2,?,?,?,?,?,?,?,'2026.01.01')",
                (pid, h, pgn(l, h, elo), s, f"h{pid}", t, elo[0], elo[1]))
        self.conn.commit()

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def indeks_semua(self):
        for pid in id_belum_terindeks(self.conn):
            pgn_ = self.conn.execute("SELECT pgn FROM partai WHERE id=?", (pid,)).fetchone()[0]
            indeks_partai(self.conn, pid, pgn_)
        self.conn.commit()

    def test_indeks_bisa_dilanjutkan(self):
        self.assertEqual(len(id_belum_terindeks(self.conn)), 4)
        self.indeks_semua()
        self.assertEqual(id_belum_terindeks(self.conn), [])

    def test_posisi_awal_sumber_gm(self):
        self.indeks_semua()
        d = cari_langkah(self.conn, fen_kunci(chess.Board()), sumber="gm")
        self.assertEqual(d["total"], 3)                     # partai lichess-user tidak ikut
        self.assertEqual(d["langkah"][0]["san"], "e4")
        self.assertEqual(d["langkah"][0]["n"], 3)
        # 1-0, 0-1, 1/2 -> skor putih 50%
        self.assertEqual(d["langkah"][0]["skor_putih"], 50.0)

    def test_jalur_peta_sama_persis_dengan_sql(self):
        """langkah_dari_peta / agregasi_langkah (unduh repertoire, 29 Sep 2026)
        harus memberi daftar yang sama dengan cari_langkah_langsung: n, hasil,
        elo_rata (pembulatan ala SQLite), skor, dan urutan."""
        from app.posisi import (agregasi_langkah, baris_posisi_partai, langkah_dari_peta,
                                peta_partai, peta_partai_gm)
        self.conn.execute(
            "INSERT INTO partai(id,putih_id,hitam_id,hasil,pgn,sumber,hash_unik,diimpor_pada,"
            "elo_putih,elo_hitam,tanggal) VALUES(30,1,2,'1/2-1/2',?,'twic','h30',?,NULL,2600,'2026.01.03')",
            (pgn("1. Nf3 Nf6 2. Ng1 Ng8 3. Nf3", "1/2-1/2", (2600, 2600)), sekarang()))
        self.conn.commit()
        self.indeks_semua()
        gm = peta_partai_gm(self.conn)
        sendiri = peta_partai(self.conn, "g.putih_id = ?", [1])
        baris = baris_posisi_partai(self.conn, sendiri)
        for (kunci,) in self.conn.execute("SELECT DISTINCT fen_kunci FROM posisi").fetchall():
            a = cari_langkah_langsung(self.conn, kunci, "gm", jumlah_contoh=0)["langkah"]
            for l in a:
                l.pop("terakhir")
            self.assertEqual(langkah_dari_peta(self.conn, kunci, gm), a, kunci)
            b = cari_langkah_langsung(self.conn, kunci, "semua", jumlah_contoh=0,
                                      pemain_id=1, warna="putih")["langkah"]
            for l in b:
                l.pop("terakhir")
            self.assertEqual(agregasi_langkah(baris.get(kunci, ()), sendiri), b, kunci)

    def test_posisi_berulang_dalam_satu_partai_dihitung_sekali(self):
        """Audit 27 Sep: 13.850 pasangan (partai, posisi) berulang terhitung ganda."""
        self.conn.execute(
            "INSERT INTO partai(id,putih_id,hitam_id,hasil,pgn,sumber,hash_unik,diimpor_pada,"
            "elo_putih,elo_hitam,tanggal) VALUES(30,1,2,'1/2-1/2',?,'twic','h30',?,2600,2600,'2026.01.03')",
            (pgn("1. Nf3 Nf6 2. Ng1 Ng8 3. Nf3", "1/2-1/2", (2600, 2600)), sekarang()))
        self.conn.commit()
        self.indeks_semua()
        d = cari_langkah_langsung(self.conn, fen_kunci(chess.Board()), sumber="gm")
        nf3 = next(l for l in d["langkah"] if l["san"] == "Nf3")
        self.assertEqual(nf3["n"], 1)                 # dulu 2: posisi awal muncul dua kali
        self.assertEqual(nf3["remis"], 1)
        self.assertEqual(d["total"], 4)               # 3 partai GM + partai ini
        self.assertEqual(len({p["id"] for p in d["partai"]}), len(d["partai"]))

    def test_sumber_semua_dan_min_elo(self):
        self.indeks_semua()
        semua = cari_langkah(self.conn, fen_kunci(chess.Board()), sumber="semua")
        self.assertEqual(semua["total"], 4)
        kuat = cari_langkah(self.conn, fen_kunci(chess.Board()), sumber="gm", min_elo=2600)
        self.assertEqual(kuat["total"], 2)

    def test_setelah_1e4_c5(self):
        self.indeks_semua()
        b = chess.Board()
        b.push_san("e4"); b.push_san("c5")
        d = cari_langkah(self.conn, fen_kunci(b))
        self.assertEqual(sorted(l["san"] for l in d["langkah"]), ["Nc3", "Nf3"])
        self.assertEqual(len(d["partai"]), 2)

    def test_saringan_pemain_dan_warna(self):
        # Semua fixture: pemain 1 = Putih, pemain 2 = Hitam (permintaan #5).
        self.indeks_semua()
        awal = fen_kunci(chess.Board())
        self.assertEqual(cari_langkah(self.conn, awal, sumber="semua", pemain_id=1, warna="putih")["total"], 4)
        self.assertEqual(cari_langkah(self.conn, awal, sumber="semua", pemain_id=1, warna="hitam")["total"], 0)
        self.assertEqual(cari_langkah(self.conn, awal, sumber="semua", pemain_id=2)["total"], 4)
        self.assertEqual(cari_langkah(self.conn, awal, sumber="gm", pemain_id=2, warna="hitam")["total"], 3)
        self.assertEqual(cari_langkah(self.conn, awal, sumber="semua", pemain_id=99)["total"], 0)
        # partai contoh ikut tersaring, bukan hanya tabel langkah
        d = cari_langkah(self.conn, awal, sumber="semua", pemain_id=1, warna="hitam")
        self.assertEqual(d["partai"], [])

    def test_hapus_partai_menghapus_posisi(self):
        self.indeks_semua()
        self.conn.execute("DELETE FROM partai WHERE id = 10")
        self.conn.commit()
        n = self.conn.execute("SELECT COUNT(*) FROM posisi WHERE partai_id = 10").fetchone()[0]
        self.assertEqual(n, 0)


if __name__ == "__main__":
    unittest.main()


class UjiIndeksPenutup(unittest.TestCase):
    """
    Penjelajah pembukaan harus terlayani indeks penutup (skema v7).

    Tanpa kolom langkah di dalam indeks, tiap baris yang cocok perlu pencarian
    balik ke tabel: pada 9,4 juta baris posisi, permintaan pertama ke posisi
    awal memakan 30 detik. Tes ini menjaga indeksnya tidak terhapus diam-diam.
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.conn = koneksi(Path(self.tmp) / "uji.db")
        buat_skema(self.conn)

    def tearDown(self):
        import shutil
        self.conn.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_indeks_penutup_ada_dan_yang_lama_dibuang(self):
        nama = {r[0] for r in self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='posisi'")}
        self.assertIn("idx_posisi_fen_langkah", nama)
        self.assertNotIn("idx_posisi_fen", nama, "indeks lama mubazir, awalannya sama")

    def test_kueri_penjelajah_memakai_indeks_penutup(self):
        rencana = " ".join(str(r[-1]) for r in self.conn.execute(
            "EXPLAIN QUERY PLAN "
            "SELECT p.langkah_san, COUNT(*) FROM posisi p JOIN partai g ON g.id = p.partai_id "
            "WHERE p.fen_kunci = ? GROUP BY p.langkah_san", ("x",)))
        self.assertIn("idx_posisi_fen_langkah", rencana)


class UjiPenjelajahTanpaMesin(UjiPenjelajah):
    """
    "Partai GM" berarti partai antar-MANUSIA (temuan Ray, 25 Sep 2026).

    Koleksi PGN Mentor memuat Kasparov melawan Deep Blue dan partai konsultasi
    yang dua pemainnya ditulis jadi satu nama. Sampai 25 Sep 2026 semuanya ikut
    terhitung di statistik pembukaan. Partainya tidak dihapus — hanya tidak
    lagi dihitung sebagai statistik antar-manusia.
    """

    def tambah_lawan_mesin(self, jenis="mesin"):
        t = sekarang()
        self.conn.execute(
            "INSERT INTO pemain(id,nama,nama_normal,jenis,dibuat_pada) "
            "VALUES(3,'Comp Deep Blue','comp deep blue',?,?)", (jenis, t))
        self.conn.execute(
            "INSERT INTO partai(id,putih_id,hitam_id,hasil,pgn,sumber,hash_unik,"
            "diimpor_pada,elo_putih,elo_hitam,tanggal) "
            "VALUES(14,1,3,'0-1',?,'pgnmentor','h14',?,2800,2800,'2026.01.01')",
            (pgn("1. e4 e5 2. Nf3", "0-1", (2800, 2800)), t))
        self.conn.commit()
        self.indeks_semua()

    def jumlah_gm_awal(self):
        d = cari_langkah(self.conn, fen_kunci(chess.Board()), sumber="gm")
        return sum(l["n"] for l in d["langkah"])

    def test_partai_lawan_mesin_tidak_dihitung(self):
        sebelum = (self.indeks_semua(), self.jumlah_gm_awal())[1]
        self.tambah_lawan_mesin()
        self.assertEqual(self.jumlah_gm_awal(), sebelum)

    def test_partai_konsultasi_juga_tidak_dihitung(self):
        sebelum = (self.indeks_semua(), self.jumlah_gm_awal())[1]
        self.tambah_lawan_mesin(jenis="konsultasi")
        self.assertEqual(self.jumlah_gm_awal(), sebelum)

    def test_partainya_tetap_ada_di_database(self):
        """Ditandai, bukan dihapus: Kasparov-Deep Blue 1997 tidak bisa dibuat ulang."""
        self.tambah_lawan_mesin()
        self.assertIsNotNone(
            self.conn.execute("SELECT 1 FROM partai WHERE id = 14").fetchone())

    def test_pemain_manusia_tetap_dihitung(self):
        """Penjagaan ini tidak boleh menyenggol partai antar-manusia."""
        self.indeks_semua()
        sebelum = self.jumlah_gm_awal()
        self.tambah_lawan_mesin(jenis=None)      # baris pemain biasa
        self.assertEqual(self.jumlah_gm_awal(), sebelum + 1)


class UjiCacheLangkah(UjiPenjelajah):
    """
    Cache posisi mahal (27 Sep 2026). Ambang diturunkan ke 1 supaya data uji
    yang kecil ikut disimpan; yang diuji: kapan simpanan BOLEH dipakai dan
    kapan TIDAK.
    """

    def setUp(self):
        super().setUp()
        import app.posisi as posisi
        self.posisi = posisi
        self.ambang_asli = posisi.CACHE_AMBANG
        posisi.CACHE_AMBANG = 1
        posisi.kosongkan_cache()
        self.awal = fen_kunci(chess.Board())

    def tearDown(self):
        self.posisi.CACHE_AMBANG = self.ambang_asli
        self.posisi.kosongkan_cache()
        super().tearDown()

    def hitung_langsung(self):
        """Berapa kali kueri sungguhan dijalankan selama blok."""
        asli = self.posisi.cari_langkah_langsung
        jumlah = [0]

        def pembungkus(*a, **k):
            jumlah[0] += 1
            return asli(*a, **k)
        self.posisi.cari_langkah_langsung = pembungkus
        self.addCleanup(setattr, self.posisi, "cari_langkah_langsung", asli)
        return jumlah

    def test_panggilan_kedua_dari_simpanan(self):
        self.indeks_semua()
        jumlah = self.hitung_langsung()
        a = cari_langkah(self.conn, self.awal, sumber="gm")
        b = cari_langkah(self.conn, self.awal, sumber="gm")
        self.assertEqual(a, b)
        self.assertEqual(jumlah[0], 1)

    def test_argumen_berbeda_tidak_berbagi_simpanan(self):
        self.indeks_semua()
        gm = cari_langkah(self.conn, self.awal, sumber="gm")["total"]
        semua = cari_langkah(self.conn, self.awal, sumber="semua")["total"]
        self.assertEqual((gm, semua), (3, 4))

    def test_partai_baru_dari_proses_lain_langsung_terlihat(self):
        """Impor terminal tidak memanggil kosongkan_cache(); MAX(id) yang menjaganya."""
        self.indeks_semua()
        self.assertEqual(cari_langkah(self.conn, self.awal, sumber="semua")["total"], 4)
        self.conn.execute(
            "INSERT INTO partai(id,putih_id,hitam_id,hasil,pgn,sumber,hash_unik,diimpor_pada,"
            "tanggal) VALUES(20,1,2,'1-0',?,'twic','h20',?,'2026.01.02')",
            (pgn("1. c4 e5", "1-0", (2600, 2600)), sekarang()))
        self.conn.commit()
        self.indeks_semua()
        self.assertEqual(cari_langkah(self.conn, self.awal, sumber="semua")["total"], 5)

    def test_hasil_diubah_basi_sampai_dikosongkan(self):
        """Batas yang DITERIMA: perubahan tanpa partai baru baru terlihat setelah
        kosongkan_cache() (impor web) atau CACHE_DETIK lewat."""
        self.indeks_semua()
        sebelum = cari_langkah(self.conn, self.awal, sumber="semua")["langkah"]
        e4 = next(l for l in sebelum if l["san"] == "e4")
        self.conn.execute("UPDATE partai SET hasil = '0-1' WHERE id = 10")
        self.conn.commit()
        basi = next(l for l in cari_langkah(self.conn, self.awal, sumber="semua")["langkah"]
                    if l["san"] == "e4")
        self.assertEqual(basi["menang_putih"], e4["menang_putih"])
        self.posisi.kosongkan_cache()
        segar = next(l for l in cari_langkah(self.conn, self.awal, sumber="semua")["langkah"]
                     if l["san"] == "e4")
        self.assertEqual(segar["menang_putih"], e4["menang_putih"] - 1)

    def test_kedaluwarsa_setelah_cache_detik(self):
        self.indeks_semua()
        jumlah = self.hitung_langsung()
        cari_langkah(self.conn, self.awal, sumber="gm")
        for k, (waktu, hasil) in list(self.posisi._cache.items()):
            self.posisi._cache[k] = (waktu - self.posisi.CACHE_DETIK - 1, hasil)
        cari_langkah(self.conn, self.awal, sumber="gm")
        self.assertEqual(jumlah[0], 2)

    def test_mengubah_hasil_tidak_mencemari_simpanan(self):
        self.indeks_semua()
        a = cari_langkah(self.conn, self.awal, sumber="gm")
        a["langkah"].clear()
        a["total"] = -1
        b = cari_langkah(self.conn, self.awal, sumber="gm")
        self.assertEqual(b["total"], 3)
        self.assertTrue(b["langkah"])

    def test_hasil_murah_tidak_disimpan(self):
        self.indeks_semua()
        self.posisi.CACHE_AMBANG = 1000
        jumlah = self.hitung_langsung()
        cari_langkah(self.conn, self.awal, sumber="gm")
        cari_langkah(self.conn, self.awal, sumber="gm")
        self.assertEqual(jumlah[0], 2)
        self.assertEqual(self.posisi._cache, {})

    def test_jumlah_simpanan_dibatasi(self):
        self.indeks_semua()
        self.posisi.CACHE_MAKS = 2
        self.addCleanup(setattr, self.posisi, "CACHE_MAKS", 500)
        for elo in (None, 2000, 2100, 2200):
            cari_langkah(self.conn, self.awal, sumber="semua", min_elo=elo)
        self.assertLessEqual(len(self.posisi._cache), 2)

    def test_aman_dipanggil_serentak_dari_banyak_thread(self):
        """
        Race temuan audit 27 Sep: server menjalankan permintaan di threadpool.
        CACHE_MAKS=1 dan CACHE_DETIK=0 memaksa buang-isi-kedaluwarsa terjadi
        terus-menerus, jadi tanpa kunci KeyError/RuntimeError muncul di sini.
        """
        import threading
        self.indeks_semua()
        self.posisi.CACHE_MAKS, self.posisi.CACHE_DETIK = 1, 0
        self.addCleanup(setattr, self.posisi, "CACHE_MAKS", 500)
        self.addCleanup(setattr, self.posisi, "CACHE_DETIK", 600)
        berkas = self.conn.execute("PRAGMA database_list").fetchone()[2]
        galat, total = [], []

        # Celah antara "cek" dan "ambil" terlalu sempit untuk muncul sendiri
        # (terbukti: tanpa ini tes lolos juga pada kode lama tanpa kunci).
        # Membaca dict dibuat lambat supaya thread lain pasti menyela.
        class DictLambat(dict):
            def __getitem__(self, k):
                time.sleep(0.0005)
                return super().__getitem__(k)
        asli = self.posisi._cache
        self.posisi._cache = DictLambat()
        self.addCleanup(setattr, self.posisi, "_cache", asli)

        def kerja(n):
            conn = koneksi(berkas)
            try:
                for i in range(150):
                    d = cari_langkah(conn, self.awal, sumber="semua", min_elo=(None, 2000)[(i + n) % 2])
                    total.append(d["total"])
            except Exception as e:           # noqa: BLE001 — yang diuji justru ini
                galat.append(repr(e))
            finally:
                conn.close()
        benang = [threading.Thread(target=kerja, args=(n,)) for n in range(8)]
        for b in benang:
            b.start()
        for b in benang:
            b.join()
        self.assertEqual(galat, [])
        self.assertEqual(len(total), 8 * 150)
