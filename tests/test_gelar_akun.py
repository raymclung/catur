"""
Uji pengisian gelar lawan akun online (app/gelar_akun.py).

Jaringan TIDAK disentuh: fungsi pengambilnya disuntik lewat parameter, jadi
tes ini cepat dan hasilnya sama di mesin mana pun. Yang diuji justru bagian
yang menentukan kebenarannya — gelar apa yang boleh disimpan, dan gelar yang
sudah ada tidak boleh hilang.

Jalankan dari akar proyek:
    python -m unittest discover -s tests -v
"""

import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

AKAR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(AKAR))

from app.db import buat_skema, koneksi, sekarang
from app.gelar_akun import (BATAS_BATCH_LICHESS, PERCOBAAN_429, GelarError,
                            _bersihkan_gelar, _minta, gelar_chesscom,
                            gelar_lichess, isi_gelar, lawan_tanpa_gelar)


def ambil_palsu(peta):
    """Pengambil palsu yang mengikuti kontrak asli: melapor per temuan."""
    def ambil(nama, pada_temuan=None, lapor=None):
        for ke, n in enumerate(nama):
            g = peta.get(n.lower())
            if g and pada_temuan:
                pada_temuan(n.lower(), g)
            if lapor:
                lapor(ke + 1, len(nama))
        # nama yang tidak kita tanya pun bisa datang dari situs
        for n, g in peta.items():
            if n not in {x.lower() for x in nama} and pada_temuan:
                pada_temuan(n, g)
        return {n.lower(): peta[n.lower()] for n in nama if peta.get(n.lower())}
    return ambil


class BersihkanGelar(unittest.TestCase):
    def test_gelar_fide_diterima(self):
        for g in ("GM", "IM", "FM", "CM", "NM", "WGM", "WIM", "WFM", "WCM", "WNM"):
            with self.subTest(gelar=g):
                self.assertEqual(_bersihkan_gelar(g), g)

    def test_huruf_kecil_dan_spasi_dirapikan(self):
        self.assertEqual(_bersihkan_gelar(" gm "), "GM")

    def test_bot_ditolak(self):
        """
        Yang paling penting di berkas ini. Kalau BOT tersimpan sebagai gelar,
        partai lawan komputer terhitung "lawan bergelar" dan ikut jadi bahan
        latihan — persis kebalikan dari aturan CLAUDE.md bagian 11.
        """
        self.assertIsNone(_bersihkan_gelar("BOT"))

    def test_lm_ditolak(self):
        """LM adalah penghargaan Lichess, bukan gelar FIDE."""
        self.assertIsNone(_bersihkan_gelar("LM"))

    def test_nilai_baru_yang_tidak_dikenal_ditolak(self):
        """Daftar putih: penanda baru dari situs tidak masuk diam-diam."""
        self.assertIsNone(_bersihkan_gelar("SUPERGM"))

    def test_kosong_aman(self):
        for nilai in ("", "   ", None):
            self.assertIsNone(_bersihkan_gelar(nilai))


class AmbilDariApi(unittest.TestCase):
    def test_lichess_memecah_jadi_batch_300(self):
        """6.442 lawan harus jadi ±22 permintaan, bukan 6.442."""
        panggilan = []

        def palsu(url, data=None, percobaan=3):
            nama = data.decode().split(",")
            panggilan.append(len(nama))
            return [{"id": n.lower(), "title": "GM"} for n in nama]

        hasil = gelar_lichess([f"pemain{i}" for i in range(700)], jeda=0, minta=palsu)
        self.assertEqual(panggilan, [BATAS_BATCH_LICHESS, BATAS_BATCH_LICHESS, 100])
        self.assertEqual(len(hasil), 700)

    def test_lichess_membuang_bot_dan_tanpa_gelar(self):
        def palsu(url, data=None, percobaan=3):
            return [{"id": "juara", "title": "IM"},
                    {"id": "robot", "title": "BOT"},
                    {"id": "biasa"}]

        self.assertEqual(gelar_lichess(["juara", "robot", "biasa"], jeda=0, minta=palsu),
                         {"juara": "IM"})

    def test_chesscom_satu_permintaan_per_pemain(self):
        diminta = []

        def palsu(url, data=None, percobaan=3):
            diminta.append(url)
            return {"title": "FM"} if url.endswith("/juara") else {}

        hasil = gelar_chesscom(["Juara", "Biasa"], jeda=0, minta=palsu)
        self.assertEqual(hasil, {"juara": "FM"})
        self.assertEqual(len(diminta), 2)
        self.assertTrue(diminta[0].endswith("/juara"))     # huruf kecil

    def test_akun_hilang_404_tidak_menjatuhkan_sisanya(self):
        """`_minta` mengembalikan None untuk 404; itu bukan alasan berhenti."""
        def palsu(url, data=None, percobaan=3):
            return None if url.endswith("/hilang") else {"title": "CM"}

        self.assertEqual(gelar_chesscom(["hilang", "ada"], jeda=0, minta=palsu),
                         {"ada": "CM"})


class IsiGelarDiDatabase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = koneksi(Path(self.tmp.name) / "uji.db")
        buat_skema(self.conn)
        self.saya = self.pemain("McLung_07", pribadi=1)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def pemain(self, nama, gelar=None, pribadi=0):
        cur = self.conn.execute(
            "INSERT INTO pemain(nama, nama_normal, gelar, akun_pribadi, dibuat_pada) "
            "VALUES (?,?,?,?,?)", (nama, nama.lower(), gelar, pribadi, sekarang()))
        return cur.lastrowid

    def partai(self, lawan_id, sumber="lichess-user"):
        self.conn.execute(
            "INSERT INTO partai(putih_id, hitam_id, hasil, jumlah_langkah, pgn, "
            "sumber, hash_unik, diimpor_pada) VALUES (?,?,'1-0',3,'',?,?,?)",
            (self.saya, lawan_id, sumber, f"h{self.saya}-{lawan_id}-{sumber}",
             sekarang()))
        self.conn.commit()

    def test_hanya_lawan_yang_gelarnya_kosong(self):
        kosong = self.pemain("belum")
        terisi = self.pemain("sudah", gelar="GM")
        self.partai(kosong)
        self.partai(terisi)
        self.assertEqual([n for _, n in lawan_tanpa_gelar(self.conn, "lichess-user")],
                         ["belum"])

    def test_akun_ray_sendiri_tidak_ikut(self):
        """Username Ray bukan 'lawan', dan tidak perlu ditanyakan ke API."""
        lawan = self.pemain("orang")
        self.partai(lawan)
        nama = [n for _, n in lawan_tanpa_gelar(self.conn, "lichess-user")]
        self.assertNotIn("McLung_07", nama)

    def test_gelar_ditulis_saat_jalan(self):
        lawan = self.pemain("juara")
        self.partai(lawan)
        isi_gelar(self.conn, "lichess", jalan=True,
                  ambil=ambil_palsu({"juara": "IM"}))
        self.assertEqual(self.conn.execute(
            "SELECT gelar FROM pemain WHERE id = ?", (lawan,)).fetchone()["gelar"], "IM")

    def test_tanpa_jalan_tidak_menulis(self):
        lawan = self.pemain("juara")
        self.partai(lawan)
        hasil = isi_gelar(self.conn, "lichess", jalan=False,
                          ambil=ambil_palsu({"juara": "IM"}))
        self.assertEqual([(n, g) for _, n, g in hasil], [("juara", "IM")])
        self.assertIsNone(self.conn.execute(
            "SELECT gelar FROM pemain WHERE id = ?", (lawan,)).fetchone()["gelar"])

    def test_gelar_yang_sudah_ada_tidak_ditimpa(self):
        """
        Gelar dari TWIC/broadcast lebih dekat ke sumber resmi daripada profil
        akun online, dan sesi lain bisa mengisinya saat kita masih menunggu
        jaringan.
        """
        lawan = self.pemain("juara")
        self.partai(lawan)
        self.conn.execute("UPDATE pemain SET gelar = 'GM' WHERE id = ?", (lawan,))
        self.conn.commit()
        isi_gelar(self.conn, "lichess", jalan=True,
                  ambil=ambil_palsu({"juara": "CM"}))
        self.assertEqual(self.conn.execute(
            "SELECT gelar FROM pemain WHERE id = ?", (lawan,)).fetchone()["gelar"], "GM")

    def test_nama_asing_dari_situs_diabaikan(self):
        """Situs mengembalikan nama yang tidak kita tanya -> jangan ditulis."""
        lawan = self.pemain("juara")
        self.partai(lawan)
        hasil = isi_gelar(self.conn, "lichess", jalan=True,
                          ambil=ambil_palsu({"juara": "IM", "orangasing": "GM"}))
        self.assertEqual([n for _, n, _ in hasil], ["juara"])
        self.assertIsNone(self.conn.execute(
            "SELECT gelar FROM pemain WHERE nama = 'orangasing'").fetchone())

    def test_sumber_dipisah(self):
        lichess = self.pemain("di_lichess")
        chess = self.pemain("di_chesscom")
        self.partai(lichess, "lichess-user")
        self.partai(chess, "chesscom")
        self.assertEqual([n for _, n in lawan_tanpa_gelar(self.conn, "chesscom")],
                         ["di_chesscom"])


if __name__ == "__main__":
    unittest.main()


class BatasLaju(unittest.TestCase):
    """
    429 bukan kerusakan, cuma perlu sabar — tapi harus terlihat kalau menyerah.

    Versi pertama menulis "gagal setelah 3 percobaan" tanpa menyebut kodenya,
    dan saat Lichess benar-benar menjawab 429 (25 Sep 2026) pesan itu tidak
    memberi petunjuk apa pun. `tidur` disuntik supaya tes ini instan.
    """

    def galat_http(self, kode):
        import urllib.error
        return urllib.error.HTTPError("http://x", kode, "x", {}, None)

    def test_429_dicoba_lagi_lalu_berhasil(self):
        sisa = [self.galat_http(429), self.galat_http(429)]
        tidur = []

        class Jawab:
            def __enter__(self_): return self_
            def __exit__(self_, *a): return False
            def read(self_): return b'[{"id": "juara", "title": "GM"}]'

        def buka(req, timeout=None):
            if sisa:
                raise sisa.pop(0)
            return Jawab()

        with unittest.mock.patch("urllib.request.urlopen", buka):
            hasil = _minta("http://x", b"juara", tidur=tidur.append)
        self.assertEqual(hasil, [{"id": "juara", "title": "GM"}])
        self.assertEqual(len(tidur), 2)          # dua kali menunggu, bukan menyerah

    def test_429_terus_menerus_menyerah_dengan_sebab_yang_jelas(self):
        def buka(req, timeout=None):
            raise self.galat_http(429)

        with unittest.mock.patch("urllib.request.urlopen", buka):
            with self.assertRaises(GelarError) as e:
                _minta("http://x", b"juara", tidur=lambda d: None)
        self.assertIn("429", str(e.exception))

    def test_429_tidak_memakan_jatah_percobaan_biasa(self):
        """Satu 429 tidak boleh menghabiskan kesempatan untuk gangguan jaringan."""
        panggil = []

        def buka(req, timeout=None):
            panggil.append(1)
            raise self.galat_http(429)

        with unittest.mock.patch("urllib.request.urlopen", buka):
            with self.assertRaises(GelarError):
                _minta("http://x", b"juara", percobaan=3, tidur=lambda d: None)
        self.assertEqual(len(panggil), PERCOBAAN_429)      # bukan 3

    def test_404_bukan_kegagalan(self):
        def buka(req, timeout=None):
            raise self.galat_http(404)

        with unittest.mock.patch("urllib.request.urlopen", buka):
            self.assertIsNone(_minta("http://x", tidur=lambda d: None))


class DitulisSambilJalan(IsiGelarDiDatabase):
    """
    Gelar ditulis begitu ketemu, bukan ditumpuk sampai akhir.

    Chess.com tidak punya endpoint massal: 585 lawan ≈ 8 menit permintaan
    berurutan. Versi pertama mengumpulkan semuanya lalu menulis sekali, jadi
    proses yang dihentikan di menit ketujuh kehilangan seluruh hasilnya.
    """

    def test_temuan_sudah_tersimpan_sebelum_pengambilan_selesai(self):
        a = self.pemain("juara")
        b = self.pemain("belakangan")
        self.partai(a, "chesscom")
        self.partai(b, "chesscom")
        terlihat = {}

        def ambil(nama, pada_temuan=None, lapor=None):
            pada_temuan("juara", "IM")
            # Sebelum pemain kedua diambil, yang pertama HARUS sudah tersimpan.
            terlihat["saat_tengah"] = self.conn.execute(
                "SELECT gelar FROM pemain WHERE id = ?", (a,)).fetchone()["gelar"]
            pada_temuan("belakangan", "FM")
            return {}

        isi_gelar(self.conn, "chesscom", jalan=True, ambil=ambil)
        self.assertEqual(terlihat["saat_tengah"], "IM")
        self.assertEqual(self.conn.execute(
            "SELECT gelar FROM pemain WHERE id = ?", (b,)).fetchone()["gelar"], "FM")

    def test_kemajuan_dilaporkan(self):
        lawan = self.pemain("juara")
        self.partai(lawan, "chesscom")
        catatan = []

        def ambil(nama, pada_temuan=None, lapor=None):
            lapor(1, 1)
            return {}

        isi_gelar(self.conn, "chesscom", jalan=False, ambil=ambil,
                  lapor=lambda sudah, total: catatan.append((sudah, total)))
        self.assertEqual(catatan, [(1, 1)])
