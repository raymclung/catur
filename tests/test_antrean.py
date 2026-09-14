"""
Uji antrean analisis (app/antrean.py) dan penjaganya (jaga_antrean.py).

Yang dijaga:
  * pekerjaan tersimpan di berkas, jadi selamat dari server dimuat ulang
    maupun komputer dimatikan;
  * penjaga TIDAK pernah membuat mesin melewati batas batch bersama;
  * pekerjaan yang tidak bisa dijalankan ditandai, bukan diam-diam hilang.

Engine tidak pernah dinyalakan di sini: peluncuran dipalsukan.
"""

import sys
import tempfile
import unittest
from pathlib import Path

AKAR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(AKAR))

from app import antrean
import jaga_antrean as jaga

SARINGAN = {"sumber": "twic", "min_elo": 2700, "terbaru": 5, "profil": "hemat"}



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


class Dasar(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "antrean.json"
        self.asli_path = antrean.PATH_ANTREAN
        antrean.PATH_ANTREAN = self.path
        # log penjaga ikut dialihkan: tanpa ini tes menulis baris palsu ke
        # data/laporan/log-antrean.txt, dan log itu dipakai sebagai bukti.
        self.asli_log = jaga.PATH_LOG
        jaga.PATH_LOG = Path(self.tmp.name) / "log-antrean.txt"

    def tearDown(self):
        antrean.PATH_ANTREAN = self.asli_path
        jaga.PATH_LOG = self.asli_log
        self.tmp.cleanup()


class Berkas(Dasar):
    def test_tambah_lalu_baca_lagi(self):
        k = antrean.tambah(SARINGAN, "uji", jumlah=5, perkiraan_menit=4)
        self.assertEqual(k["id"], 1)
        self.assertEqual(k["status"], "menunggu")
        # dibaca ulang dari berkas: inilah yang membuatnya selamat dari restart
        lagi = antrean.baca()
        self.assertEqual(len(lagi), 1)
        self.assertEqual(lagi[0]["saringan"], SARINGAN)

    def test_nomor_naik_terus(self):
        antrean.tambah(SARINGAN, "a")
        antrean.tambah(SARINGAN, "b")
        antrean.hapus(1)
        self.assertEqual(antrean.tambah(SARINGAN, "c")["id"], 3)

    def test_berikutnya_yang_menunggu_saja(self):
        a = antrean.tambah(SARINGAN, "a")
        antrean.perbarui(a["id"], status="selesai")
        self.assertIsNone(antrean.berikutnya())
        b = antrean.tambah(SARINGAN, "b")
        self.assertEqual(antrean.berikutnya()["id"], b["id"])

    def test_yang_sedang_jalan_tidak_bisa_dibuang(self):
        k = antrean.tambah(SARINGAN, "a")
        antrean.perbarui(k["id"], status="jalan", pid=999)
        self.assertFalse(antrean.hapus(k["id"]))
        self.assertEqual(len(antrean.baca()), 1)

    def test_berkas_rusak_dianggap_kosong(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text("{bukan json", encoding="utf-8")
        self.assertEqual(antrean.baca(), [])

    def test_ringkas_menjumlahkan_perkiraan(self):
        antrean.tambah(SARINGAN, "a", jumlah=10, perkiraan_menit=8)
        antrean.tambah(SARINGAN, "b", jumlah=20, perkiraan_menit=15)
        k = antrean.tambah(SARINGAN, "c", jumlah=30, perkiraan_menit=100)
        antrean.perbarui(k["id"], status="selesai")
        r = antrean.ringkas()
        self.assertEqual(r["hitung"]["menunggu"], 2)
        self.assertEqual(r["perkiraan_menit_sisa"], 23)      # yang selesai tidak dihitung


class Penjaga(Dasar):
    """satu_putaran() dengan daftar proses dan peluncuran yang dipalsukan."""

    def setUp(self):
        super().setUp()
        self.asli = (jaga._pindai_batch, jaga.luncurkan, jaga._ada_yang_belum)
        self.diluncurkan = []
        self.belum = set()                   # (sumber, bagian) yang masih punya partai belum dianalisis
        jaga._ada_yang_belum = lambda sumber, bagian: (sumber, bagian) in self.belum

    def tearDown(self):
        jaga._pindai_batch, jaga.luncurkan, jaga._ada_yang_belum = self.asli
        super().tearDown()

    def pasang(self, berjalan=0, hasil=None, galat=None):
        jaga._pindai_batch = lambda: [{"pid": 100 + i} for i in range(berjalan)]

        def palsu(s):
            if galat:
                raise galat
            self.diluncurkan.append(s)
            return {"tugas": {"pid": 4242, "log": "x.txt", "mulai": "-", "jumlah": 5,
                              "perkiraan_menit": 4, "perintah": "uji"}}
        jaga.luncurkan = hasil or palsu

    def test_slot_kosong_pekerjaan_dijalankan(self):
        antrean.tambah(SARINGAN, "a", jumlah=5, perkiraan_menit=4)
        self.pasang(berjalan=0)
        self.assertTrue(jaga.satu_putaran(2))
        k = antrean.baca()[0]
        self.assertEqual(k["status"], "jalan")
        self.assertEqual(k["pid"], 4242)
        self.assertEqual(len(self.diluncurkan), 1)

    def test_slot_penuh_tidak_menjalankan_apa_pun(self):
        antrean.tambah(SARINGAN, "a")
        self.pasang(berjalan=2)
        self.assertTrue(jaga.satu_putaran(2))       # masih ada yang menunggu
        self.assertEqual(antrean.baca()[0]["status"], "menunggu")
        self.assertEqual(self.diluncurkan, [])

    def test_batas_penjaga_tidak_pernah_melewati_batas_mesin(self):
        antrean.tambah(SARINGAN, "a")
        self.pasang(berjalan=jaga.MAKS_BATCH)
        jaga.satu_putaran(99)                        # minta banyak, tetap ditolak
        self.assertEqual(antrean.baca()[0]["status"], "menunggu")

    def test_antrean_kosong_berhenti(self):
        self.pasang(berjalan=0)
        self.assertFalse(jaga.satu_putaran(2))       # semua partai sudah dianalisis

    def test_slot_kosong_diisi_partai_gm_bagian_berbeda(self):
        self.belum = {("gm", f"{k}/{jaga.MAKS_BATCH}") for k in range(jaga.MAKS_BATCH)}
        self.pasang(berjalan=0)
        self.assertTrue(jaga.satu_putaran(4))          # pengisi pertama langsung dijalankan
        self.assertEqual(antrean.baca()[0]["status"], "jalan")
        jaga.isi_slot_kosong(jaga.MAKS_BATCH, berjalan=1)            # pengisi kedua memilih bagian lain
        kerja = antrean.baca()
        self.assertTrue(all(k["otomatis"] for k in kerja))
        n = jaga.MAKS_BATCH
        self.assertEqual([k["saringan"]["bagian"] for k in kerja], [f"0/{n}", f"1/{n}"])
        s0 = self.diluncurkan[0]
        self.assertEqual((s0.sumber, s0.profil, s0.terbaru), ("gm", "cepat", jaga.ISI_JUMLAH))

    def test_partai_gm_habis_pindah_ke_partai_sendiri(self):
        self.belum = {("lichess-user", f"1/{jaga.MAKS_BATCH}")}
        self.pasang(berjalan=0)
        jaga.satu_putaran(4)
        self.assertEqual((self.diluncurkan[0].sumber, self.diluncurkan[0].bagian), ("lichess-user", f"1/{jaga.MAKS_BATCH}"))

    def test_slot_penuh_tidak_diisi(self):
        self.belum = {("gm", f"0/{jaga.MAKS_BATCH}")}
        self.pasang(berjalan=jaga.MAKS_BATCH)
        jaga.satu_putaran(4)
        self.assertEqual(antrean.baca(), [])

    def test_pengisi_bisa_dimatikan(self):
        self.belum = {("gm", f"0/{jaga.MAKS_BATCH}")}
        self.pasang(berjalan=0)
        self.assertFalse(jaga.satu_putaran(4, isi_otomatis=False))
        self.assertEqual(antrean.baca(), [])

    def test_saringan_tidak_sah_ditandai_gagal(self):
        antrean.tambah({"profil": "ngawur"}, "rusak")
        self.pasang(berjalan=0)
        jaga.satu_putaran(2)
        k = antrean.baca()[0]
        self.assertEqual(k["status"], "gagal")
        self.assertIn("saringan tidak sah", k["alasan"])

    def test_nol_partai_dianggap_selesai_bukan_gagal(self):
        antrean.tambah(SARINGAN, "kosong")
        self.pasang(berjalan=0, galat=RuntimeError("Tidak ada partai yang cocok"))
        jaga.satu_putaran(2)
        self.assertEqual(antrean.baca()[0]["status"], "selesai")

    def test_kegagalan_lain_ditandai_gagal(self):
        antrean.tambah(SARINGAN, "x")
        self.pasang(berjalan=0, galat=RuntimeError("engine hilang"))
        jaga.satu_putaran(2)
        k = antrean.baca()[0]
        self.assertEqual(k["status"], "gagal")
        self.assertIn("engine hilang", k["alasan"])

    def test_status_disegarkan_saat_prosesnya_hilang(self):
        log = Path(self.tmp.name) / "log.txt"
        log.write_text("25 partai dipilih\nengine: Stockfish 18\n"
                       "selesai 25, gagal 0, 20.0 menit  |  database: 1/2 partai dianalisis\n",
                       encoding="utf-8")
        k = antrean.tambah(SARINGAN, "a")
        antrean.perbarui(k["id"], status="jalan", pid=999, log=str(log))
        self.pasang(berjalan=0)                      # PID 999 tidak ada lagi
        jaga.segarkan_status()
        self.assertEqual(antrean.baca()[0]["status"], "selesai")

    def test_terputus_diantrekan_ulang(self):
        """Komputer dimatikan di tengah batch: sisanya harus dikerjakan lagi."""
        log = Path(self.tmp.name) / "log2.txt"
        log.write_text("25 partai dipilih\nengine: Stockfish 18\n  [2/25] ...\n", encoding="utf-8")
        k = antrean.tambah(SARINGAN, "a")
        antrean.perbarui(k["id"], status="jalan", pid=999, log=str(log))
        self.pasang(berjalan=0)
        jaga.segarkan_status()
        kerja = antrean.baca()[0]
        self.assertEqual(kerja["status"], "menunggu")
        self.assertEqual(kerja["ulang"], 1)

    def test_ulang_dibatasi_lalu_gagal(self):
        log = Path(self.tmp.name) / "log3.txt"
        log.write_text("25 partai dipilih\n  [2/25] ...\n", encoding="utf-8")
        k = antrean.tambah(SARINGAN, "a")
        antrean.perbarui(k["id"], status="jalan", pid=999, log=str(log), ulang=jaga.MAKS_ULANG)
        self.pasang(berjalan=0)
        jaga.segarkan_status()
        self.assertEqual(antrean.baca()[0]["status"], "gagal")

    def test_tidak_berhenti_selama_masih_ada_yang_jalan(self):
        """
        Cacat 13 Sep: tidak ada yang MENUNGGU dianggap antrean habis, jadi
        penjaga berhenti (dan mencabut Startup-nya) padahal empat pekerjaan
        masih berjalan.
        """
        k = antrean.tambah(SARINGAN, "a")
        antrean.perbarui(k["id"], status="jalan", pid=100)
        self.pasang(berjalan=1)                      # PID 100 masih hidup
        self.assertTrue(jaga.satu_putaran(5))
        self.assertEqual(antrean.baca()[0]["status"], "jalan")


class Pendaftaran(Dasar):
    """
    Pendaftaran penjaga supaya hidup sendiri tiap logon.

    Task Scheduler ditolak di mesin ini ("Access is denied" walau ONLOGON +
    /RL LIMITED), jadi dipakai berkas .cmd di folder Startup pengguna. Di sini
    foldernya dialihkan ke folder sementara.
    """

    def setUp(self):
        super().setUp()
        self.berkas = Path(self.tmp.name) / "Startup" / "CaturPenjagaAntrean.cmd"
        self.asli_berkas = jaga._berkas_startup
        jaga._berkas_startup = lambda: self.berkas
        # Kalau penjaga sungguhan sedang jalan di mesin ini, main() akan mundur
        # sebelum sampai ke pencabutan. Tes harus lepas dari keadaan mesin.
        self.asli_lain = jaga.penjaga_lain_jalan
        jaga.penjaga_lain_jalan = lambda: None

    def tearDown(self):
        jaga._berkas_startup = self.asli_berkas
        jaga.penjaga_lain_jalan = self.asli_lain
        super().tearDown()

    def test_daftarkan_menulis_perintah_yang_benar(self):
        self.assertTrue(jaga.daftarkan_tugas(4))
        self.assertTrue(jaga.tugas_terdaftar())
        isi = self.berkas.read_text(encoding="utf-8")
        self.assertIn("jaga_antrean.py", isi)
        self.assertIn("--jaga", isi)
        self.assertIn("--maks 4", isi)
        # penting: pendaftaran harus mencabut dirinya sendiri saat antrean habis
        self.assertIn("--cabut-kalau-habis", isi)

    def test_cabut_menghapus_berkasnya(self):
        jaga.daftarkan_tugas(4)
        self.assertTrue(jaga.cabut_tugas())
        self.assertFalse(jaga.tugas_terdaftar())
        self.assertFalse(jaga.cabut_tugas())        # tidak ada lagi

    def test_antrean_habis_mencabut_pendaftaran(self):
        """Permintaan Ray: matikan lagi setelah semua partai dikerjakan."""
        jaga.daftarkan_tugas(4)
        asli_putaran, asli_argv = jaga.satu_putaran, sys.argv
        jaga.satu_putaran = lambda maks: False      # antrean langsung habis
        sys.argv = ["jaga_antrean.py", "--jaga", "--cabut-kalau-habis"]
        try:
            jaga.main()
        finally:
            jaga.satu_putaran, sys.argv = asli_putaran, asli_argv
        self.assertFalse(jaga.tugas_terdaftar())

    def test_tanpa_bendera_pendaftaran_dibiarkan(self):
        jaga.daftarkan_tugas(4)
        asli_putaran, asli_argv = jaga.satu_putaran, sys.argv
        jaga.satu_putaran = lambda maks: False
        sys.argv = ["jaga_antrean.py", "--jaga"]
        try:
            jaga.main()
        finally:
            jaga.satu_putaran, sys.argv = asli_putaran, asli_argv
        self.assertTrue(jaga.tugas_terdaftar())

    def test_penjaga_kedua_mundur(self):
        """Tugas logon tidak boleh menyalakan penjaga kedua di atas yang sudah ada."""
        import os
        asli = jaga._pindai_proses
        jaga._pindai_proses = lambda nama: [{"pid": os.getpid()}, {"pid": 999123}]
        try:
            # fungsi ASLI, bukan yang dipalsukan setUp untuk tes lain
            self.assertEqual(self.asli_lain(), 999123)
            jaga._pindai_proses = lambda nama: [{"pid": os.getpid()}]
            self.assertIsNone(self.asli_lain())
        finally:
            jaga._pindai_proses = asli


if __name__ == "__main__":
    unittest.main()
