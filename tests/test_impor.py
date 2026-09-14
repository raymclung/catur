"""
Pengujian impor: dedup, pembaruan partai broadcast, penentuan turnamen, dan
batas normalisasi nama.

Jalankan dari akar proyek:
    python -m unittest discover -s tests -v
"""

import tempfile
import unittest
from pathlib import Path

from app.db import buat_skema, koneksi
from app.impor import (impor_teks, normalisasi_nama, periksa_jejak,
                       sidik_partai, turnamen_dari_header)

AKAR = Path(__file__).resolve().parents[1]
FIXTURE = AKAR / "data" / "pgn" / "uji-scholars-mate.pgn"


def pgn(event="Uji Terbuka", babak="1", putih="Carlsen, Magnus", hitam="Nakamura, Hikaru",
        hasil="*", langkah="1. e4 e5 2. Nf3", site="Stavanger NOR", tambahan=""):
    return (
        f'[Event "{event}"]\n[Site "{site}"]\n[Date "2026.06.01"]\n'
        f'[Round "{babak}"]\n[White "{putih}"]\n[Black "{hitam}"]\n'
        f'[Result "{hasil}"]\n{tambahan}\n{langkah} {hasil}\n\n'
    )


class DasarImpor(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = koneksi(Path(self.tmp.name) / "uji.db")
        buat_skema(self.conn)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def jumlah(self, tabel):
        return self.conn.execute(f"SELECT COUNT(*) FROM {tabel}").fetchone()[0]


class UjiDedup(DasarImpor):
    def test_fixture_terbaca(self):
        h = impor_teks(self.conn, FIXTURE.read_text(encoding="utf-8"), "file")
        self.assertEqual((h.baru, h.dilewati), (1, 0), h.alasan_dilewati)

    def test_impor_dua_kali_tidak_menambah_partai(self):
        teks = FIXTURE.read_text(encoding="utf-8")
        impor_teks(self.conn, teks, "file")
        h = impor_teks(self.conn, teks, "file")
        self.assertEqual((h.baru, h.duplikat), (0, 1))
        self.assertEqual(self.jumlah("partai"), 1)

    def test_pgn_sama_beda_spasi_dan_komentar_tetap_duplikat(self):
        impor_teks(self.conn, pgn(hasil="1-0"), "file")
        h = impor_teks(self.conn, pgn(hasil="1-0", langkah="1.e4 {bagus} e5  2.Nf3"), "file")
        self.assertEqual(h.duplikat, 1)
        self.assertEqual(self.jumlah("partai"), 1)

    def test_partai_tanpa_langkah_dilewati(self):
        h = impor_teks(self.conn, pgn(langkah=""), "file")
        self.assertEqual(h.dilewati, 1)
        self.assertEqual(self.jumlah("partai"), 0)


class UjiBroadcastBelumSelesai(DasarImpor):
    """Partai yang sama diambil saat masih berjalan, lalu setelah selesai."""

    def test_versi_selesai_menimpa_versi_berjalan(self):
        impor_teks(self.conn, pgn(hasil="*", langkah="1. e4 e5"), "lichess-broadcast")
        h = impor_teks(self.conn, pgn(hasil="1-0", langkah="1. e4 e5 2. Nf3 Nc6 3. Bb5"),
                       "lichess-broadcast")
        self.assertEqual((h.baru, h.diperbarui), (0, 1))
        self.assertEqual(self.jumlah("partai"), 1)
        baris = self.conn.execute("SELECT hasil, jumlah_langkah FROM partai").fetchone()
        self.assertEqual((baris["hasil"], baris["jumlah_langkah"]), ("1-0", 3))

    def test_versi_lebih_lama_tidak_menimpa(self):
        impor_teks(self.conn, pgn(hasil="*", langkah="1. e4 e5 2. Nf3 Nc6"), "lichess-broadcast")
        h = impor_teks(self.conn, pgn(hasil="*", langkah="1. e4 e5"), "lichess-broadcast")
        self.assertEqual(h.dilewati, 1)
        self.assertEqual(self.conn.execute("SELECT jumlah_langkah FROM partai").fetchone()[0], 2)

    def test_partai_selesai_tidak_pernah_ditimpa(self):
        impor_teks(self.conn, pgn(hasil="1-0", langkah="1. e4 e5"), "lichess-broadcast")
        h = impor_teks(self.conn, pgn(hasil="0-1", langkah="1. e4 e5 2. Nf3"), "lichess-broadcast")
        self.assertEqual(h.baru, 1)  # dianggap partai lain, bukan pembaruan
        self.assertEqual(self.jumlah("partai"), 2)

    def test_tanpa_babak_tidak_ada_pembaruan(self):
        """Partai online Ray semua ber-Round '-', jadi jalur ini tidak boleh menyentuhnya."""
        impor_teks(self.conn, pgn(babak="-", hasil="*", langkah="1. e4 e5"), "lichess-user")
        h = impor_teks(self.conn, pgn(babak="-", hasil="*", langkah="1. e4 e5 2. Nf3"),
                       "lichess-user")
        self.assertEqual(h.baru, 1)
        self.assertEqual(self.jumlah("partai"), 2)


class UjiTurnamen(DasarImpor):
    def test_event_generik_bukan_turnamen(self):
        for event in ("rated bullet game", "Rated Blitz game", "casual rapid game", "Live Chess"):
            with self.subTest(event=event):
                self.assertEqual(turnamen_dari_header({"Event": event}), (None, None))

    def test_arena_adalah_turnamen(self):
        self.assertEqual(turnamen_dari_header({"Event": "Hourly Bullet Arena"}),
                         ("Hourly Bullet Arena", None))

    def test_tag_chesscom_didahulukan(self):
        url = "https://api.chess.com/pub/tournament/arena-kings-31036071"
        nama, sumber = turnamen_dari_header({"Event": "Live Chess", "ChessComTournament": url})
        self.assertEqual((nama, sumber), ("arena-kings-31036071", url))

    def test_tag_chesscom_masuk_database(self):
        url = "https://api.chess.com/pub/tournament/arena-kings-31036071"
        impor_teks(self.conn, pgn(event="Live Chess", babak="-", hasil="1-0",
                                  tambahan=f'[ChessComTournament "{url}"]\n'), "chesscom")
        baris = self.conn.execute("SELECT nama, sumber_url FROM turnamen").fetchone()
        self.assertEqual((baris["nama"], baris["sumber_url"]), ("arena-kings-31036071", url))

    def test_event_generik_tidak_membuat_baris_turnamen(self):
        impor_teks(self.conn, pgn(event="rated bullet game", babak="-", hasil="1-0"), "lichess-user")
        self.assertEqual(self.jumlah("turnamen"), 0)
        self.assertIsNone(self.conn.execute("SELECT turnamen_id FROM partai").fetchone()[0])

    def test_site_url_bukan_lokasi(self):
        impor_teks(self.conn, pgn(hasil="1-0", site="https://lichess.org/abc"), "lichess-user")
        self.assertIsNone(self.conn.execute("SELECT lokasi FROM turnamen").fetchone()[0])


class UjiNormalisasiNama(unittest.TestCase):
    def test_gelar_dan_tanda_baca_dibuang(self):
        self.assertEqual(normalisasi_nama("GM Carlsen, Magnus"), "carlsen magnus")
        self.assertEqual(normalisasi_nama("carlsen magnus"), "carlsen magnus")

    def test_batas_yang_diketahui_nama_singkatan_tidak_cocok(self):
        """
        Batas fungsi normalisasi itu sendiri: ia hanya menyamakan huruf, tanda
        baca, dan gelar, jadi 'carlsen m' tetap beda dari 'carlsen magnus'.

        Sejak B13 (12 Sep 2026) penyatuannya dikerjakan satu lapis di atas,
        di dapatkan_atau_buat_pemain() lewat pencocokan inisial — lihat
        tests/test_nama_inisial.py. Fungsi ini sengaja dibiarkan apa adanya.
        """
        self.assertNotEqual(normalisasi_nama("Carlsen,M."), normalisasi_nama("Carlsen, Magnus"))


if __name__ == "__main__":
    unittest.main()


class UjiJejakAsal(DasarImpor):
    """Audit jejak asal koleksi PGN (impor_pgn.py --jejak).

    Latar: 89.822 partai pgnmentor masuk tanpa sumber_url dan tanpa baris
    sumber_impor, padahal CLAUDE.md bagian 7 mewajibkan pertanyaan "partai
    ini dari mana?" bisa dijawab.
    """

    def berkas(self, *teks_pgn) -> Path:
        f = Path(self.tmp.name) / "Koleksi.pgn"
        f.write_text("".join(teks_pgn), encoding="utf-8")
        return f

    def test_sidik_partai_sama_dengan_hash_yang_tersimpan(self):
        """Kalau ini pecah, audit akan melaporkan partai yang ADA sebagai hilang."""
        import chess.pgn, io
        teks = pgn(hasil="1-0", langkah="1. e4 e5 2. Qh5 Nc6 3. Bc4 Nf6 4. Qxf7#")
        impor_teks(self.conn, teks, "pgnmentor")
        tersimpan = self.conn.execute("SELECT hash_unik FROM partai").fetchone()["hash_unik"]
        game = chess.pgn.read_game(io.StringIO(teks))
        self.assertEqual(sidik_partai(game), tersimpan)

    def test_tiga_golongan_dan_yang_belum_masuk(self):
        milik_koleksi = pgn(putih="Reti, Richard", hitam="Tarrasch, Siegbert",
                            hasil="1-0", langkah="1. Nf3 d5 2. c4")
        sudah_dari_twic = pgn(putih="Carlsen, Magnus", hitam="Anand, Viswanathan",
                              hasil="0-1", langkah="1. d4 Nf6 2. c4")
        belum_masuk = pgn(putih="Euwe, Max", hitam="Keres, Paul",
                          hasil="1/2-1/2", langkah="1. e4 c5 2. Nc3")
        impor_teks(self.conn, milik_koleksi, "pgnmentor")
        impor_teks(self.conn, sudah_dari_twic, "twic")

        f = self.berkas(milik_koleksi, sudah_dari_twic, belum_masuk)
        s = periksa_jejak(self.conn, [f], {f.name: "https://contoh/Koleksi.zip"})[0]
        self.assertEqual((s["partai"], s["cocok"], s["duplikat_sumber_lain"], s["tidak_ada"]),
                         (3, 1, 1, 1))
        # lihat saja: belum ada yang ditulis
        self.assertEqual(s["url_ditulis"], 0)
        self.assertIsNone(self.conn.execute(
            "SELECT sumber_url FROM partai WHERE sumber = 'pgnmentor'").fetchone()["sumber_url"])

    def test_tulis_mengisi_sumber_url_dan_mencatat_sumber_impor(self):
        teks = pgn(putih="Reti, Richard", hitam="Tarrasch, Siegbert",
                   hasil="1-0", langkah="1. Nf3 d5 2. c4")
        impor_teks(self.conn, teks, "pgnmentor")
        f = self.berkas(teks)
        url = "https://contoh/Koleksi.zip"

        s = periksa_jejak(self.conn, [f], {f.name: url}, tulis=True)[0]
        self.assertEqual((s["cocok"], s["url_ditulis"], s["sumber_impor"]), (1, 1, "dicatat"))
        self.assertEqual(self.conn.execute(
            "SELECT sumber_url FROM partai WHERE sumber = 'pgnmentor'").fetchone()["sumber_url"], url)
        baris = self.conn.execute(
            "SELECT jenis, kunci, url, jumlah_partai, jumlah_baru FROM sumber_impor").fetchone()
        self.assertEqual(tuple(baris), ("pgnmentor", f.name, url, 1, 1))

        # dijalankan dua kali: angka sumber_impor tidak berlipat
        s2 = periksa_jejak(self.conn, [f], {f.name: url}, tulis=True)[0]
        self.assertEqual((s2["url_ditulis"], s2["sumber_impor"]),
                         (0, "sudah ada, tidak ditimpa"))
        self.assertEqual(self.conn.execute(
            "SELECT jumlah_baru FROM sumber_impor").fetchone()["jumlah_baru"], 1)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM sumber_impor").fetchone()[0], 1)

    def test_partai_di_dua_koleksi_hanya_dihitung_sekali(self):
        """Carlsen-Anand ada di Carlsen.pgn DAN Anand.pgn: satu baris, bukan dua.

        Tanpa ini jumlah `cocok` melebihi jumlah baris yang benar-benar ada
        (audit pertama 12 Sep: 99.798 padahal barisnya 89.822).
        """
        teks = pgn(putih="Carlsen, Magnus", hitam="Anand, Viswanathan",
                   hasil="1-0", langkah="1. c4 e6 2. g3")
        impor_teks(self.conn, teks, "pgnmentor")
        satu = Path(self.tmp.name) / "Carlsen.pgn"
        dua = Path(self.tmp.name) / "Anand.pgn"
        satu.write_text(teks, encoding="utf-8")
        dua.write_text(teks, encoding="utf-8")

        hasil = periksa_jejak(self.conn, [satu, dua],
                              {satu.name: "https://contoh/Carlsen.zip",
                               dua.name: "https://contoh/Anand.zip"}, tulis=True)
        self.assertEqual([(s["cocok"], s["juga_koleksi_lain"]) for s in hasil],
                         [(1, 0), (0, 1)])
        # URL yang tertulis = koleksi yang pertama mengklaimnya, tidak ditimpa
        self.assertEqual(self.conn.execute(
            "SELECT sumber_url FROM partai").fetchone()["sumber_url"],
            "https://contoh/Carlsen.zip")
        self.assertEqual(sum(s["url_ditulis"] for s in hasil), 1)

    def test_tanpa_url_sah_sumber_url_tetap_null(self):
        """URL tidak pernah dikarang: berkas yang HEAD-nya gagal tetap NULL."""
        teks = pgn(putih="Reti, Richard", hitam="Tarrasch, Siegbert",
                   hasil="1-0", langkah="1. Nf3 d5 2. c4")
        impor_teks(self.conn, teks, "pgnmentor")
        f = self.berkas(teks)
        s = periksa_jejak(self.conn, [f], {f.name: None}, tulis=True)[0]
        self.assertEqual((s["cocok"], s["url_ditulis"]), (1, 0))
        self.assertIsNone(self.conn.execute(
            "SELECT sumber_url FROM partai").fetchone()["sumber_url"])
        self.assertEqual(self.conn.execute(
            "SELECT url FROM sumber_impor").fetchone()["url"], None)
