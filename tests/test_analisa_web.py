"""
Uji halaman Mesin (app/analisa_web.py): saringan -> perintah CLI, penjagaan
satu batch, pembacaan kemajuan dari log, dan peluncuran proses.

Yang TIDAK diuji di sini: engine itu sendiri. Uji peluncuran memakai
`--lihat-saja`, jadi analisis_partai.py benar-benar dijalankan sebagai proses
terpisah — lengkap dengan berkas log dan catatan tugas — tanpa menyalakan
Stockfish. Aturan mesin ini melarang dua batch engine bersamaan, dan uji
otomatis tidak boleh melanggarnya.
"""

import json
import sys
import tempfile
import time
import unittest
from pathlib import Path

AKAR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(AKAR))

from fastapi import HTTPException

from app import analisa_web as aw


LOG_CONTOH = """1.013 partai dipilih  |  profil hemat, depth 16, RAM ~371 MB  |  perkiraan 676 menit
engine: Stockfish 18

  [1/997] #12     McLung_07              - lawan1                 0-1      30 lgk   88.0% /  91.0%  ?? 2   20.0s  sisa ~500m
  [2/997] #13     lawan2                 - McLung_07              1-0      40 lgk   85.0% /  93.0%  ?? 1   22.0s  sisa ~480m
"""


def penutup() -> str:
    """Baris penutup analisis_partai.py - penanda batch benar-benar beres."""
    return (chr(10) + 'selesai 997, gagal 0, 612.0 menit  |  '
            'database: 1.933/266.805 partai dianalisis' + chr(10))


# Laptop Ray bisa sedang dijeda sungguhan (tanda di data/pengaturan-mesin.json).
# Tes di berkas ini tidak boleh ikut membaca tanda itu.
_tanda_jeda = None


def setUpModule():
    global _tanda_jeda
    import tempfile
    from unittest import mock
    from app import jeda_mesin
    folder = tempfile.mkdtemp()
    _tanda_jeda = mock.patch.object(jeda_mesin, "PATH_PENGATURAN", Path(folder) / "pengaturan.json")
    _tanda_jeda.start()


def tearDownModule():
    _tanda_jeda.stop()


class Perintah(unittest.TestCase):
    """Saringan halaman harus jadi perintah CLI yang persis sama."""

    def test_partai_saya_kalah_blitz(self):
        s = aw.Saringan(hasil="kalah", format="blitz", lawan_min_elo=2201, terbaru=30)
        self.assertEqual(
            aw._perintah(s),
            ["analisis_partai.py", "--lawan-min-elo", "2201", "--hasil", "kalah",
             "--format", "blitz", "--terbaru", "30", "--profil", "hemat"])

    def test_partai_gm(self):
        s = aw.Saringan(sumber="pgnmentor", pemain=["Carlsen", "Ding"], min_elo=2700,
                        terbaru=5, profil="normal")
        self.assertEqual(
            aw._perintah(s),
            ["analisis_partai.py", "--sumber", "pgnmentor", "--pemain", "Carlsen", "Ding",
             "--min-elo", "2700", "--terbaru", "5", "--profil", "normal"])

    def test_nomor_partai_dan_ulang(self):
        s = aw.Saringan(id=[11669, 11657], ulang=True, terbaru=2)
        self.assertEqual(
            aw._perintah(s),
            ["analisis_partai.py", "--id", "11669", "11657", "--ulang",
             "--terbaru", "2", "--profil", "hemat"])

    def test_repertoire_ikut_jadi_flag_dan_dihitung_sebagai_saringan(self):
        s = aw.Saringan(repertoire=True, min_elo=2700, terbaru=20)
        self.assertEqual(
            aw._perintah(s),
            ["analisis_partai.py", "--min-elo", "2700", "--repertoire",
             "--terbaru", "20", "--profil", "hemat"])
        aw._periksa(aw.Saringan(repertoire=True))      # cukup sebagai pemilih
        self.assertTrue(aw._ruang_nama(s).repertoire)

    def test_nama_berspasi_dikutip_di_perintah_tampil(self):
        """
        Perintah yang ditampilkan halaman harus bisa ditempel apa adanya.
        Nama pemain memuat koma dan spasi, jadi tanpa kutip artinya berubah
        jadi dua nama terpisah.
        """
        s = aw.Saringan(sumber="pgnmentor", pemain=["Nakamura, Hikaru"], terbaru=25)
        self.assertEqual(
            aw._perintah_tampil(s),
            'python analisis_partai.py --sumber pgnmentor --pemain "Nakamura, Hikaru" '
            "--terbaru 25 --profil hemat")
        # yang tanpa spasi tidak ikut dikutip
        self.assertNotIn('"', aw._perintah_tampil(aw.Saringan(hasil="kalah", terbaru=5)))

    def test_profil_cepat_diterima(self):
        """Profil depth 10: terukur 30x lebih cepat dari hemat pada partai bullet/blitz Ray."""
        s = aw.Saringan(hasil="kalah", profil="cepat")
        self.assertIn("cepat", aw._perintah(s))
        from pydantic import ValidationError
        with self.assertRaises(ValidationError):
            aw.Saringan(hasil="kalah", profil="kilat")

    def test_gelar_lawan_jadi_huruf_besar(self):
        s = aw.Saringan(gelar_lawan=["gm", "im"], terbaru=4)
        self.assertIn("GM", aw._perintah(s))
        self.assertIn("IM", aw._perintah(s))

    def test_min_langkah_hanya_ditulis_kalau_beda(self):
        self.assertNotIn("--min-langkah", aw._perintah(aw.Saringan(hasil="kalah")))
        self.assertIn("--min-langkah", aw._perintah(aw.Saringan(hasil="kalah", min_langkah=20)))


def buat_db_uji(folder: Path) -> Path:
    """
    Database kecil untuk tes yang menyentuh halaman Mesin: dua partai TWIC
    Elo 2700+ dan satu partai akun sendiri. Supaya tes tidak pernah membaca
    data/catur.db milik Ray (lolos di mesinnya, gagal di checkout bersih).
    """
    from app.db import buat_skema, koneksi
    from app.impor import impor_teks
    path = folder / "uji-mesin.db"
    conn = koneksi(path)
    buat_skema(conn)
    # 12 langkah: CLI melewati partai di bawah 10 langkah (--min-langkah).
    langkah = ["1. c4 e5 2. Nc3 Nf6 3. g3 d5 4. cxd5 Nxd5 5. Bg2 Nb6 6. d3 Be7 "
               "7. Nf3 O-O 8. O-O Nc6 9. Be3 Re8 10. Rc1 Bf8 11. a3 h6 12. b4 a6",
               "1. d4 Nf6 2. c4 e6 3. g3 d5 4. Bg2 Be7 5. Nf3 O-O 6. O-O dxc4 "
               "7. Qc2 a6 8. a4 Bd7 9. Qxc4 Bc6 10. Bg5 a5 11. Nc3 Na6 12. e3 Nb4",
               "1. e4 c5 2. Nf3 d6 3. d4 cxd4 4. Nxd4 Nf6 5. Nc3 a6 6. Be3 e5 "
               "7. Nb3 Be7 8. f3 Be6 9. Qd2 O-O 10. O-O-O Nbd7 11. g4 b5 12. g5 b4"]
    for i, (putih, hitam, sumber, elo) in enumerate([
            ("Carlsen, Magnus", "Caruana, Fabiano", "twic", 2800),
            ("Nakamura, Hikaru", "Giri, Anish", "twic", 2750),
            ("McLung_07", "lawan", "lichess-user", 2300)]):
        tag = ['[Event "uji"]', '[Site "-"]', f'[Date "2026.01.0{i + 1}"]', '[Round "1"]',
               f'[White "{putih}"]', f'[Black "{hitam}"]', '[Result "1-0"]',
               f'[WhiteElo "{elo}"]', f'[BlackElo "{elo}"]']
        impor_teks(conn, chr(10).join(tag) + chr(10) * 2 + langkah[i] + " 1-0" + chr(10), sumber)
    conn.commit()
    conn.close()
    return path


class Penjagaan(unittest.TestCase):
    def test_tanpa_saringan_boleh_karena_jumlahnya_dibatasi(self):
        """
        Ray, 13 Sep: "ini tidak perlu ada saya ingin analisa semuanya".

        Tanpa saringan pun batch tetap terbatas, karena `terbaru` wajib ada dan
        maksimal MAKS_PARTAI. Yang dijaga: jumlahnya benar-benar dibatasi, dan
        perintah yang dibentuk memang tanpa saringan.
        """
        aw._periksa(aw.Saringan(terbaru=10))          # tidak melempar
        self.assertEqual(aw._perintah(aw.Saringan(terbaru=10)),
                         ["analisis_partai.py", "--terbaru", "10", "--profil", "hemat"])
        from pydantic import ValidationError
        with self.assertRaises(ValidationError):
            aw.Saringan(terbaru=aw.MAKS_PARTAI + 1)

    def test_sumber_gm_sah_tanpa_menyentuh_database(self):
        """'gm' = semua sumber partai GM; bukan label database, tapi selalu sah."""
        aw._periksa_sumber(aw.Saringan(sumber="gm", terbaru=5))       # tidak melempar
        self.assertEqual(aw._perintah(aw.Saringan(sumber="gm", terbaru=5))[:3],
                         ["analisis_partai.py", "--sumber", "gm"])

    def test_profil_cepat_diterima(self):
        """
        Profil "cepat" (depth 10) untuk memborong partai GM. Diukur 13 Sep
        2026: 2,7 detik/partai vs 82 detik pada depth 16, menemukan 9 dari 12
        kesalahan besar. Dipakai untuk cakupan, bukan untuk partai yang
        dipelajari satu per satu.
        """
        import sys as _sys
        _sys.path.insert(0, str(aw.AKAR / "src"))
        from catur_analysis.engine import PROFIL
        self.assertEqual(PROFIL["cepat"].depth, 10)
        self.assertIn("--profil", aw._perintah(aw.Saringan(terbaru=5, profil="cepat")))
        self.assertIn("cepat", aw._perintah(aw.Saringan(terbaru=5, profil="cepat")))

    def test_nilai_tidak_dikenal_ditolak(self):
        for buruk in (aw.Saringan(hasil="seri"), aw.Saringan(format="peluru"),
                      aw.Saringan(gelar_lawan=["XX"])):
            with self.assertRaises(HTTPException) as c:
                aw._periksa(buruk)
            self.assertEqual(c.exception.status_code, 422)

    def test_sumber_diperiksa_ke_database_bukan_daftar_tertulis(self):
        """
        Label sumber tidak boleh dibatasi daftar di kode: halaman Impor
        membiarkan Ray memberi label sendiri. Yang sah = yang ada di database.
        """
        asli = aw.koneksi
        aw.koneksi = lambda: self.db_palsu(["twic", "unggahan"])
        try:
            aw._periksa_sumber(aw.Saringan(sumber="unggahan"))      # tidak boleh melempar
            with self.assertRaises(HTTPException) as c:
                aw._periksa_sumber(aw.Saringan(sumber="chessbase"))
            self.assertEqual(c.exception.status_code, 422)
            self.assertIn("twic", c.exception.detail)               # sebutkan yang ada
        finally:
            aw.koneksi = asli

    def db_palsu(self, sumber):
        import sqlite3
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute("CREATE TABLE partai (id INTEGER PRIMARY KEY, sumber TEXT)")
        conn.execute("CREATE TABLE analisis (partai_id INTEGER)")
        for i, s in enumerate(sumber, start=1):
            conn.execute("INSERT INTO partai (id, sumber) VALUES (?, ?)", (i, s))
        return conn

    def test_satu_saringan_cukup(self):
        aw._periksa(aw.Saringan(hasil="kalah"))
        aw._periksa(aw.Saringan(sumber="twic"))
        aw._periksa(aw.Saringan(id=[1]))

    def test_jumlah_partai_dibatasi(self):
        from pydantic import ValidationError
        with self.assertRaises(ValidationError):
            aw.Saringan(hasil="kalah", terbaru=aw.MAKS_PARTAI + 1)

    def test_mulai_ditolak_kalau_slot_batch_penuh(self):
        """Ditolak hanya kalau slot penuh — bukan begitu ada satu batch jalan."""
        asli = aw._pindai_batch
        penuh = [{"pid": 999990 + i, "mulai": "-", "perintah": "analisis_partai.py"}
                 for i in range(aw.MAKS_BATCH)]
        aw._pindai_batch = lambda: penuh
        try:
            with self.assertRaises(HTTPException) as c:
                aw.mulai(aw.Saringan(hasil="kalah", terbaru=1))
            self.assertEqual(c.exception.status_code, 409)
            self.assertIn("999990", c.exception.detail)
            self.assertIn(str(aw.MAKS_BATCH), c.exception.detail)
        finally:
            aw._pindai_batch = asli

    def test_masih_ada_slot_tidak_ditolak_karena_batch_lain(self):
        """Satu batch berjalan tidak boleh memblokir yang kedua."""
        asli, asli_db = aw._pindai_batch, aw.PATH_DB
        folder = Path(tempfile.mkdtemp())
        aw.PATH_DB = buat_db_uji(folder)
        aw._pindai_batch = lambda: [{"pid": 999999, "mulai": "-", "perintah": "analisis_partai.py"}]
        try:
            with self.assertRaises(HTTPException) as c:
                aw.mulai(aw.Saringan(hasil="kalah", terbaru=1, sumber="tidak-ada-sumber-ini"))
            # gagal karena sumbernya, BUKAN karena 409 batch lain
            self.assertNotEqual(c.exception.status_code, 409)
        finally:
            aw._pindai_batch, aw.PATH_DB = asli, asli_db
            import shutil
            shutil.rmtree(folder, ignore_errors=True)

    def test_hentikan_pid_asing_ditolak(self):
        asli = aw._pindai_batch
        aw._pindai_batch = lambda: []
        try:
            with self.assertRaises(HTTPException) as c:
                aw.hentikan(aw.Penghentian(pid=4))
            self.assertEqual(c.exception.status_code, 404)
        finally:
            aw._pindai_batch = asli


class Penghentian(unittest.TestCase):
    """
    Mematikan PENJAGA tidak boleh ikut membunuh batch yang ia jalankan.
    Batch adalah anak proses penjaga, jadi taskkill /T menyapu semuanya —
    terjadi nyata 13 Sep 2026.
    """

    def setUp(self):
        self.perintah = []
        self.asli = (aw.subprocess.run, aw._proses_ada)
        aw.subprocess.run = lambda arg, **k: self.perintah.append(arg)
        aw._proses_ada = lambda pid: False

    def tearDown(self):
        aw.subprocess.run, aw._proses_ada = self.asli

    @unittest.skipUnless(aw.os.name == 'nt', 'taskkill hanya di Windows')
    def test_batch_dimatikan_beserta_stockfish(self):
        aw._matikan(123)
        self.assertIn('/T', self.perintah[0])

    @unittest.skipUnless(aw.os.name == 'nt', 'taskkill hanya di Windows')
    def test_penjaga_dimatikan_tanpa_anaknya(self):
        aw._matikan(123, pohon=False)
        self.assertNotIn('/T', self.perintah[0])
        self.assertIn('123', self.perintah[0])


class Kemajuan(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.log = Path(self.tmp.name) / "log.txt"

    def tearDown(self):
        self.tmp.cleanup()

    def test_baris_terakhir_yang_dibaca(self):
        self.log.write_text(LOG_CONTOH, encoding="utf-8")
        m = aw._kemajuan(self.log)
        self.assertEqual(m["selesai"], 2)
        self.assertEqual(m["dari"], 997)          # dari [i/N], bukan baris "dipilih"
        self.assertEqual(m["sisa_menit"], 480)
        self.assertEqual(len(m["ekor"]), 4)

    def test_baru_memilih_belum_ada_kemajuan(self):
        self.log.write_text("25 partai dipilih  |  profil hemat\n", encoding="utf-8")
        m = aw._kemajuan(self.log)
        self.assertEqual(m["dari"], 25)
        self.assertNotIn("selesai", m)

    def test_log_hilang_tidak_meledak(self):
        self.assertEqual(aw._kemajuan(Path(self.tmp.name) / "tidak-ada.txt"), {})


class CatatanTugas(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.asli_path, self.asli_pindai = aw.PATH_TUGAS, aw._pindai_batch
        aw.PATH_TUGAS = Path(self.tmp.name) / "tugas.json"
        aw._pindai_batch = lambda: []

    def tearDown(self):
        aw.PATH_TUGAS, aw._pindai_batch = self.asli_path, self.asli_pindai
        self.tmp.cleanup()

    def test_tugas_yang_mati_di_tengah_dilabeli_gagal(self):
        """
        Prosesnya hilang saja tidak berarti beres: 12 Sep 2026 empat batch
        mati di partai ke-2 dan sempat dilabeli 'selesai'. Penanda beres yang
        benar adalah baris penutup analisis_partai.py.
        """
        log = Path(self.tmp.name) / "log.txt"
        log.write_text(LOG_CONTOH, encoding="utf-8")   # berhenti di [2/997]
        aw._tulis_tugas([{"pid": 999999, "status": "berjalan", "log": str(log), "jumlah": 997}])
        t = aw._rangkum_tugas()[0]
        self.assertEqual(t["status"], "gagal")
        self.assertFalse(t["berjalan"])
        self.assertEqual(t["selesai"], 2)         # kemajuan ikut dibaca dari log
        # perubahannya disimpan, bukan cuma di jawaban
        self.assertEqual(json.loads(aw.PATH_TUGAS.read_text(encoding="utf-8"))[0]["status"],
                         "gagal")

    def test_tugas_dengan_baris_penutup_dilabeli_selesai(self):
        log = Path(self.tmp.name) / "log.txt"
        log.write_text(LOG_CONTOH + penutup(), encoding="utf-8")
        aw._tulis_tugas([{"pid": 999999, "status": "berjalan", "log": str(log), "jumlah": 997}])
        self.assertEqual(aw._rangkum_tugas()[0]["status"], "selesai")

    def test_catatan_dibatasi(self):
        aw._tulis_tugas([{"pid": i, "status": "selesai", "log": "x"} for i in range(40)])
        self.assertEqual(len(json.loads(aw.PATH_TUGAS.read_text(encoding="utf-8"))), aw.MAKS_TUGAS)


class Peluncuran(unittest.TestCase):
    """
    Benar-benar meluncurkan analisis_partai.py sebagai proses terpisah.

    `--lihat-saja` ditambahkan supaya skripnya memilih partai, menulis log,
    lalu keluar TANPA menyalakan engine.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = Path(self.tmp.name)
        self.asli = (aw.PATH_TUGAS, aw.FOLDER_LOG, aw._pindai_batch, aw._perintah)
        self.asli_db = aw.PATH_DB
        aw.PATH_DB = buat_db_uji(d)
        aw.PATH_TUGAS = d / "tugas.json"
        aw.FOLDER_LOG = d
        aw._pindai_batch = lambda: []
        asli_perintah = self.asli[3]
        aw._perintah = lambda s: asli_perintah(s) + ["--lihat-saja"]

    def tearDown(self):
        aw.PATH_TUGAS, aw.FOLDER_LOG, aw._pindai_batch, aw._perintah = self.asli
        aw.PATH_DB = self.asli_db
        try:
            self.tmp.cleanup()
        except OSError:
            pass          # Windows: proses anak kadang masih memegang log sesaat

    def test_proses_jalan_dan_lognya_terbaca(self):
        jawab = aw.mulai(aw.Saringan(sumber="twic", min_elo=2700, terbaru=3))
        tugas = jawab["tugas"]
        self.assertGreater(tugas["pid"], 0)
        self.assertEqual(tugas["status"], "berjalan")
        self.assertEqual(tugas["jumlah"], jawab["pratinjau"]["jumlah"])

        log = Path(tugas["log"])
        for _ in range(120):                      # tunggu proses menulis log
            if log.exists() and "partai dipilih" in log.read_text(encoding="utf-8", errors="replace"):
                break
            time.sleep(0.5)
        isi = log.read_text(encoding="utf-8", errors="replace")
        self.assertIn("partai dipilih", isi)
        self.assertIn("profil hemat", isi)

        # catatan tugas tersimpan dan bisa dirangkum
        disimpan = json.loads(aw.PATH_TUGAS.read_text(encoding="utf-8"))
        self.assertEqual(disimpan[-1]["pid"], tugas["pid"])
        rangkum = aw._rangkum_tugas()[0]
        # --lihat-saja keluar tanpa baris penutup, jadi statusnya bukan 'selesai';
        # yang diuji di sini prosesnya benar-benar jalan lalu berhenti.
        self.assertFalse(rangkum["berjalan"])


class DaftarPemain(unittest.TestCase):
    """
    Dropdown Pemain (Ray: "kalau salah ketik repot").

    Yang dijaga: urutan mengikuti jumlah partai BELUM dianalisis, saringan
    sumber ikut berlaku, dan partai yang sudah dianalisis tidak dihitung.
    """

    def setUp(self):
        import shutil
        from app.db import buat_skema, koneksi, sekarang
        self.tmp = tempfile.mkdtemp()
        self._rmtree = shutil.rmtree
        self.conn = koneksi(Path(self.tmp) / "uji.db")
        buat_skema(self.conn)
        self.sekarang = sekarang

    def tearDown(self):
        self.conn.close()
        self._rmtree(self.tmp, ignore_errors=True)

    def pemain(self, nama, gelar=None):
        return self.conn.execute(
            "INSERT INTO pemain(nama, nama_normal, gelar, dibuat_pada) VALUES (?,?,?,?)",
            (nama, nama.lower(), gelar, self.sekarang())).lastrowid

    def partai(self, putih, hitam, sumber, dianalisis=False, n=1):
        for i in range(n):
            pid = self.conn.execute(
                "INSERT INTO partai(putih_id, hitam_id, hasil, jumlah_langkah, pgn, sumber, "
                "hash_unik, diimpor_pada) VALUES (?,?,'1-0',40,'x',?,?,?)",
                (putih, hitam, sumber, f"h{putih}-{hitam}-{sumber}-{i}-{dianalisis}",
                 self.sekarang())).lastrowid
            if dianalisis:
                self.conn.execute(
                    "INSERT INTO analisis(partai_id, engine, depth, profil, dianalisis_pada) "
                    "VALUES (?,'sf',16,'hemat',?)", (pid, self.sekarang()))
        self.conn.commit()

    def test_urut_dari_yang_paling_banyak_belum(self):
        from app.analisa_web import daftar_pemain
        a = self.pemain("Carlsen, Magnus", "GM")
        b = self.pemain("Nakamura, Hikaru", "GM")
        # lawan dibedakan: lawan bersama akan wajar muncul teratas, dan itu
        # bukan yang sedang diuji di sini
        self.partai(a, self.pemain("Lawan, A"), "twic", n=2)
        self.partai(b, self.pemain("Lawan, B"), "twic", n=5)
        hasil = daftar_pemain(self.conn, batas=10)
        naka = next(h for h in hasil if h["nama"] == "Nakamura, Hikaru")
        self.assertEqual(naka["belum"], 5)
        self.assertEqual(naka["gelar"], "GM")
        urut = [h["nama"] for h in hasil]
        self.assertLess(urut.index("Nakamura, Hikaru"), urut.index("Carlsen, Magnus"))

    def test_yang_sudah_dianalisis_tidak_dihitung(self):
        from app.analisa_web import daftar_pemain
        a = self.pemain("Carlsen, Magnus", "GM")
        lawan = self.pemain("Lawan, X")
        self.partai(a, lawan, "twic", dianalisis=True, n=3)
        self.partai(a, lawan, "twic", n=1)
        hasil = daftar_pemain(self.conn, batas=10)
        self.assertEqual([h["belum"] for h in hasil if h["nama"] == "Carlsen, Magnus"], [1])

    def test_kelompok_gm_menyingkirkan_akun_sendiri(self):
        """Ray: "hilangkan partai ini dari partai gm ... sudah ada di partai saya"."""
        from app.analisa_web import daftar_pemain
        saya = self.pemain("McLung_07")
        self.conn.execute("UPDATE pemain SET akun_pribadi = 1 WHERE id = ?", (saya,))
        gm = self.pemain("Carlsen, Magnus", "GM")
        self.partai(saya, self.pemain("Lawan, A"), "lichess-user", n=3)
        self.partai(gm, self.pemain("Lawan, B"), "twic", n=2)
        self.conn.commit()

        nama_gm = [h["nama"] for h in daftar_pemain(self.conn, kelompok="gm")]
        self.assertIn("Carlsen, Magnus", nama_gm)
        self.assertNotIn("McLung_07", nama_gm)
        self.assertNotIn("Lawan, A", nama_gm, "lawan di partai saya juga bukan partai GM")

        nama_saya = [h["nama"] for h in daftar_pemain(self.conn, kelompok="saya")]
        self.assertEqual(nama_saya, ["McLung_07"])

        # tanpa kelompok, semuanya tetap muncul (perilaku lama tidak berubah)
        self.assertIn("McLung_07", [h["nama"] for h in daftar_pemain(self.conn)])

    def test_sumber_ditandai_pribadi(self):
        from app.analisa_web import daftar_sumber
        saya = self.pemain("McLung_07")
        gm = self.pemain("Carlsen, Magnus", "GM")
        self.partai(saya, self.pemain("Lawan, A"), "lichess-user", n=1)
        self.partai(gm, self.pemain("Lawan, B"), "twic", n=1)
        tanda = {x["nama"]: x["pribadi"] for x in daftar_sumber(self.conn)}
        self.assertTrue(tanda["lichess-user"])
        self.assertFalse(tanda["twic"])

    def test_saringan_sumber_dan_cari(self):
        from app.analisa_web import daftar_pemain
        a = self.pemain("Carlsen, Magnus", "GM")
        b = self.pemain("Shirov, Alexei", "GM")
        self.partai(a, self.pemain("Lawan, A"), "twic", n=2)
        self.partai(b, self.pemain("Lawan, B"), "pgnmentor", n=4)
        nama_twic = [h["nama"] for h in daftar_pemain(self.conn, sumber="twic")]
        self.assertIn("Carlsen, Magnus", nama_twic)
        self.assertNotIn("Shirov, Alexei", nama_twic)
        self.assertEqual([h["nama"] for h in daftar_pemain(self.conn, cari="shirov")],
                         ["Shirov, Alexei"])


class KemajuanDariTerminal(unittest.TestCase):
    """
    Batch yang dijalankan dari terminal harus tetap kelihatan kemajuannya.

    Ray, 12 Sep: "ini 2 ini tidak berjalannya? saya tidak melihat
    persentasenya" — batchnya jalan, tapi halaman hanya tahu log batch yang
    diluncurkannya sendiri. analisis_partai.py sekarang menulis catatan kecil
    bernama PID-nya.
    """

    def setUp(self):
        import shutil
        self.tmp = tempfile.mkdtemp()
        self._rmtree = shutil.rmtree
        self.asli_akar = aw.AKAR
        aw.AKAR = Path(self.tmp)
        (Path(self.tmp) / "data" / "laporan").mkdir(parents=True)

    def tearDown(self):
        aw.AKAR = self.asli_akar
        self._rmtree(self.tmp, ignore_errors=True)

    def tulis(self, pid, isi):
        (Path(self.tmp) / "data" / "laporan" / f".kemajuan-{pid}.json").write_text(
            json.dumps(isi), encoding="utf-8")

    def test_persen_dihitung_dari_catatan(self):
        self.tulis(4242, {"selesai": 57, "dari": 904, "sisa_menit": 581,
                          "mulai": "2026-09-12 21:48"})
        m = aw.kemajuan_proses(4242)
        self.assertEqual(m["selesai"], 57)
        self.assertEqual(m["dari"], 904)
        self.assertEqual(m["persen"], 6.3)
        self.assertEqual(m["sisa_menit"], 581)

    def test_tanpa_catatan_kosong_bukan_gagal(self):
        self.assertEqual(aw.kemajuan_proses(999999), {})

    def test_catatan_rusak_diabaikan(self):
        (Path(self.tmp) / "data" / "laporan" / ".kemajuan-7.json").write_text(
            "{bukan json", encoding="utf-8")
        self.assertEqual(aw.kemajuan_proses(7), {})

    def test_catatan_yatim_dibersihkan(self):
        self.tulis(111, {"selesai": 1, "dari": 2})
        self.tulis(222, {"selesai": 1, "dari": 2})
        aw.bersihkan_kemajuan_yatim({222})
        sisa = sorted(p.name for p in (Path(self.tmp) / "data" / "laporan").glob(".kemajuan-*.json"))
        self.assertEqual(sisa, [".kemajuan-222.json"])


if __name__ == "__main__":
    unittest.main()
