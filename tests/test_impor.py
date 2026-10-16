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
from app.impor import (EVENT_PAPAN, impor_teks, normalisasi_nama, periksa_jejak,
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


class UjiEcoHurufBesar(DasarImpor):
    def test_eco_kecil_dari_pgn_disimpan_huruf_besar(self):
        """PGN boleh menulis [ECO "b90"]; database v14 menolak huruf kecil,
        jadi impor harus menyeragamkannya, bukan gagal."""
        impor_teks(self.conn, pgn(hasil="1-0", tambahan='[ECO "b90"]'), "file")
        self.assertEqual(self.conn.execute("SELECT eco FROM partai").fetchone()[0], "B90")

    def test_eco_tidak_diketahui_tetap_null(self):
        impor_teks(self.conn, pgn(hasil="1-0", tambahan='[ECO "?"]'), "file")
        self.assertIsNone(self.conn.execute("SELECT eco FROM partai").fetchone()[0])


class UjiEloNol(DasarImpor):
    """Audit 27 Sep: siaran menulis Elo "0" untuk pemain tanpa rating; 51 partai
    tersimpan 0 dan merusak saringan serta rata-rata Elo."""

    def test_elo_nol_disimpan_null(self):
        impor_teks(self.conn, pgn(hasil="1-0", tambahan='[WhiteElo "0"]\n[BlackElo "2710"]'),
                   "lichess-broadcast")
        r = self.conn.execute("SELECT elo_putih, elo_hitam FROM partai").fetchone()
        self.assertEqual((r[0], r[1]), (None, 2710))

    def test_elo_nol_pada_pembaruan_broadcast(self):
        impor_teks(self.conn, pgn(hasil="*", langkah="1. e4 e5"), "lichess-broadcast")
        impor_teks(self.conn, pgn(hasil="1-0", langkah="1. e4 e5 2. Nf3",
                                  tambahan='[WhiteElo "2700"]\n[BlackElo "0"]'),
                   "lichess-broadcast")
        r = self.conn.execute("SELECT elo_putih, elo_hitam FROM partai").fetchone()
        self.assertEqual((r[0], r[1]), (2700, None))


class UjiBroadcastBelumSelesai(DasarImpor):
    """Partai yang sama diambil saat masih berjalan, lalu setelah selesai."""

    def test_analisis_dan_posisi_versi_terpotong_dibuang(self):
        """Audit 27 Sep: 103 partai selesai membawa analisis versi '*'-nya."""
        from app.posisi import id_belum_terindeks, indeks_partai
        impor_teks(self.conn, pgn(hasil="*", langkah="1. e4 e5"), "lichess-broadcast")
        pid = self.conn.execute("SELECT id FROM partai").fetchone()[0]
        indeks_partai(self.conn, pid, self.conn.execute("SELECT pgn FROM partai").fetchone()[0])
        self.conn.execute("INSERT INTO analisis(partai_id,engine,depth,profil,dianalisis_pada) "
                          "VALUES(?,'uji',10,'cepat','2026-01-01')", (pid,))
        self.conn.execute("INSERT INTO analisis_langkah(partai_id,ply,nomor_langkah,warna,san,uci,"
                          "eval_sebelum_cp,eval_sesudah_cp,eval_putih_cp,kehilangan_cp,klasifikasi) "
                          "VALUES(?,0,1,'putih','e4','e2e4',20,15,15,5,'baik')", (pid,))
        self.conn.commit()

        h = impor_teks(self.conn, pgn(hasil="1-0", langkah="1. e4 e5 2. Nf3 Nc6 3. Bb5"),
                       "lichess-broadcast")
        self.assertEqual(h.diperbarui, 1)
        for tabel in ("analisis", "analisis_langkah"):
            with self.subTest(tabel=tabel):
                self.assertEqual(self.conn.execute(
                    f"SELECT COUNT(*) FROM {tabel} WHERE partai_id = ?", (pid,)).fetchone()[0], 0)
        self.assertEqual(self.conn.execute(
            "SELECT COUNT(*) FROM posisi WHERE partai_id = ?", (pid,)).fetchone()[0], 5)
        self.assertEqual(id_belum_terindeks(self.conn), [])

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


class UjiEventLabelPapan(DasarImpor):
    """
    Tag Event yang isinya label PAPAN tidak boleh jadi nama turnamen.

    Ditemukan 25 Sep 2026: PGN siaran Lichess kadang menulis
    "Round 1: Vrolijk, Liam - Ernst, Sipke" di tag Event untuk sebagian papan
    dalam ronde yang SAMA, jadi satu ronde Dutch Meesterklasse pecah menjadi
    18 baris `turnamen`. Di seluruh database saat itu: 3.362 baris turnamen
    bernama seperti ini, menyangkut 3.431 partai.
    """

    def test_label_papan_dikenali(self):
        for ev in ("Round 1: Vrolijk, Liam - Ernst, Sipke",
                   "Round 3: Brixius, Dirk - Cloete, Willem Francois",
                   "round 12: A, B - C, D",
                   # Varian dengan bagian di tengah, dari duel Kasparov-Topalov
                   "Round 1 | Rapid: Topalov, Veselin - Kasparov, Garry",
                   "Round 11 | Blitz: Topalov, Veselin - Kasparov, Garry",
                   "new tournament description"):
            with self.subTest(ev=ev):
                self.assertTrue(EVENT_PAPAN.match(ev))

    def test_nama_turnamen_asli_tidak_ikut_kena(self):
        # Nama turnamen asli yang memang memuat titik dua tidak boleh ikut kena.
        for ev in ("Meesterklasse 2026-2027", "Olymp 2026 Open",
                   "Round Robin Masters 2026", "Tata Steel Masters 2026",
                   "Armageddon: Chess for TV", "Checkmate: USA vs India",
                   "Clutch Chess: Champions", "Clutch Chess: The Legends",
                   "Room 1 : Pra Lebaran 1441 H Arena",
                   "46th FIDE Chess Olympiad Samarkand 2026 | Open | Matches 1-12"):
            with self.subTest(ev=ev):
                self.assertFalse(EVENT_PAPAN.match(ev))

    def test_label_papan_bukan_turnamen(self):
        self.assertEqual(turnamen_dari_header({"Event": "Round 1: A, B - C, D"}), (None, None))

    def test_label_papan_tidak_membuat_baris_turnamen(self):
        impor_teks(self.conn, pgn(event="Round 1: Carlsen, Magnus - Nakamura, Hikaru"),
                   sumber="lichess-broadcast")
        self.assertEqual(self.jumlah("turnamen"), 0)
        self.assertEqual(self.jumlah("partai"), 1)

    def test_nama_paksa_menang_atas_event(self):
        # Satu ronde siaran = satu turnamen, walau tag Event-nya berbeda-beda.
        teks = (pgn(event="Meesterklasse 2026-2027", putih="Bok, Benjamin", hitam="Giri, Anish")
                + pgn(event="Round 1: Vrolijk, Liam - Ernst, Sipke", babak="1.4",
                      putih="Vrolijk, Liam", hitam="Ernst, Sipke")
                + pgn(event="Meesterklasse 202627", babak="1.5",
                      putih="Werle, Jan", hitam="Kunin, Vitaly"))
        impor_teks(self.conn, teks, sumber="lichess-broadcast",
                   nama_turnamen_paksa="Meesterklasse 2026-2027")
        self.assertEqual(self.jumlah("partai"), 3)
        self.assertEqual(self.jumlah("turnamen"), 1)
        nama = self.conn.execute("SELECT nama FROM turnamen").fetchone()[0]
        self.assertEqual(nama, "Meesterklasse 2026-2027")

    def test_tanpa_nama_paksa_event_biasa_tetap_dipakai(self):
        impor_teks(self.conn, pgn(event="Tata Steel Masters 2026"), sumber="twic")
        self.assertEqual(
            self.conn.execute("SELECT nama FROM turnamen").fetchone()[0],
            "Tata Steel Masters 2026")


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


class UjiAngkaDariTag(unittest.TestCase):
    """ke_int memotong "12345" jadi 1234 (audit 27 Sep 2026)."""

    def test_ke_int_membaca_semua_digit(self):
        from app.impor_bantu import ke_int
        self.assertEqual(ke_int("12345"), 12345)
        self.assertEqual(ke_int("2710"), 2710)
        self.assertEqual(ke_int(" 2650 (prov)"), 2650)
        self.assertIsNone(ke_int("?"))

    def test_ke_elo_menolak_yang_mustahil(self):
        from app.impor_bantu import ke_elo
        self.assertEqual(ke_elo("2850"), 2850)
        self.assertIsNone(ke_elo("0"))
        self.assertIsNone(ke_elo("12345"))       # dulu tersimpan sebagai 1234


# 16 langkah penuh: di atas KEMBAR_MIN_LANGKAH (partai "panjang")
PANJANG = ("1. e4 e5 2. Nf3 Nc6 3. Bb5 a6 4. Ba4 Nf6 5. O-O Be7 6. Re1 b5 7. Bb3 d6 "
           "8. c3 O-O 9. h3 Nb8 10. d4 Nbd7 11. Nbd2 Bb7 12. Bc2 Re8 13. Nf1 Bf8 "
           "14. Ng3 g6 15. a4 c5 16. d5 c4")
PENDEK = "1. e4 e5 2. Nf3 Nc6 3. Bb5 a6 4. Ba4 Nf6 5. O-O Be7"


def pgn_tgl(tanggal, putih="Caruana, Fabiano", hitam="Giri, Anish", langkah=PANJANG,
            hasil="1/2-1/2", tambahan=""):
    return (f'[Event "Uji"]\n[Date "{tanggal}"]\n[Round "1"]\n[White "{putih}"]\n'
            f'[Black "{hitam}"]\n[Result "{hasil}"]\n{tambahan}\n{langkah} {hasil}\n\n')


class UjiKembarLintasSumber(DasarImpor):
    """Audit 27 Sep 2026: 500-an partai tersimpan dua kali karena siaran dan
    TWIC menulis tanggal berbeda (Candidates 2026: 2 Mar vs 15 Apr)."""

    def test_tanggal_beda_sumber_beda_tetap_duplikat(self):
        impor_teks(self.conn, pgn_tgl("2026.03.02"), "lichess-broadcast")
        h = impor_teks(self.conn, pgn_tgl("2026.04.15", putih="Caruana,F", hitam="Giri,A"), "twic")
        self.assertEqual((h.baru, h.duplikat), (0, 1))
        self.assertEqual(self.jumlah("partai"), 1)

    def test_garis_berulang_di_tahun_lain_bukan_duplikat(self):
        """So-Vachier-Lagrave: 25 langkah identik di blitz 2023, 2024, 2026."""
        impor_teks(self.conn, pgn_tgl("2024.08.16"), "twic")
        h = impor_teks(self.conn, pgn_tgl("2026.05.07"), "lichess-broadcast")
        self.assertEqual(h.baru, 1)

    def test_partai_pendek_hanya_kembar_kalau_tanggalnya_dekat(self):
        impor_teks(self.conn, pgn_tgl("2026.03.02", langkah=PENDEK), "lichess-broadcast")
        jauh = impor_teks(self.conn, pgn_tgl("2026.03.20", langkah=PENDEK), "twic")
        self.assertEqual(jauh.baru, 1)
        dekat = impor_teks(self.conn, pgn_tgl("2026.03.04", langkah=PENDEK), "pgnmentor")
        self.assertEqual(dekat.duplikat, 1)

    def test_sumber_sama_tidak_dianggap_kembar(self):
        """Dua ronde berbeda satu turnamen boleh punya langkah yang sama."""
        impor_teks(self.conn, pgn_tgl("2026.03.02"), "twic")
        h = impor_teks(self.conn, pgn_tgl("2026.03.05"), "twic")
        self.assertEqual(h.baru, 1)

    def test_putih_sama_dengan_hitam_dilewati(self):
        tambahan = '[WhiteFideId "651081709"]\n[BlackFideId "651081709"]'
        h = impor_teks(self.conn, pgn_tgl("2026.07.20", putih="Tureshbayev, Anuar",
                                          hitam="Tureshbayev, Anuar", tambahan=tambahan),
                       "lichess-broadcast")
        self.assertEqual((h.baru, h.dilewati), (0, 1))


class UjiInisialAmbigu(DasarImpor):
    """49 partai 'Anand,Vignesh' (Elo ~2030) masuk ke 'Anand,V' lalu ke Vishy."""

    def impor(self, putih, tanggal):
        impor_teks(self.conn, pgn_tgl(tanggal, putih=putih, hitam="Lawan, Satu", langkah=PENDEK),
                   "pgnmentor")

    def id_putih(self, nama):
        import re
        for pid, pgn in self.conn.execute("SELECT putih_id, pgn FROM partai"):
            if re.search(r'\[White "([^"]*)"\]', pgn).group(1) == nama:
                return pid
        raise AssertionError(nama)

    def test_urutan_lengkap_dulu(self):
        self.impor("Anand, Viswanathan", "2024.01.01")
        self.impor("Anand,V", "2024.01.02")
        self.impor("Anand,Vignesh", "2024.01.03")
        self.assertEqual(self.id_putih("Anand,V"), self.id_putih("Anand, Viswanathan"))
        self.assertNotEqual(self.id_putih("Anand,Vignesh"), self.id_putih("Anand, Viswanathan"))

    def test_urutan_singkatan_dulu(self):
        """Nama Viswanathan hanya ada di PGN baris 'Anand,V': tetap terlihat."""
        self.impor("Anand,V", "2024.01.01")
        self.impor("Anand, Viswanathan", "2024.01.02")
        self.impor("Anand,Vignesh", "2024.01.03")
        self.assertEqual(self.id_putih("Anand, Viswanathan"), self.id_putih("Anand,V"))
        self.assertNotEqual(self.id_putih("Anand,Vignesh"), self.id_putih("Anand,V"))

    def test_singkatan_tanpa_saingan_tetap_cocok(self):
        self.impor("Aronian,L", "2024.01.01")
        self.impor("Aronian, Levon", "2024.01.02")
        self.assertEqual(self.id_putih("Aronian, Levon"), self.id_putih("Aronian,L"))

    def test_nama_konsultasi_tidak_dicocokkan_lewat_inisial(self):
        self.impor("Anand,V", "2024.01.01")
        self.impor("Anand,Viswanathan:Borgialli,Mihail Harasic", "2024.01.02")
        self.assertNotEqual(self.id_putih("Anand,Viswanathan:Borgialli,Mihail Harasic"),
                            self.id_putih("Anand,V"))


class UjiIsiPgnDiperiksa(DasarImpor):
    """Audit 28 Sep 2026: semua kasus di bawah ini dulu diterima diam-diam."""

    def test_varian_bukan_catur_ditolak_dengan_alasan(self):
        for varian in ("Crazyhouse", "Atomic", "Three-check", "Antichess"):
            h = impor_teks(self.conn, pgn(hasil="1-0", putih=varian, tambahan=f'[Variant "{varian}"]'), "file")
            self.assertEqual(h.baru, 0, varian)
            self.assertTrue(any("varian" in k for k in h.alasan_dilewati), h.alasan_dilewati)
        self.assertEqual(self.jumlah("partai"), 0)

    def test_chess960_dan_standard_tetap_diterima(self):
        for i, varian in enumerate(("Standard", "Chess960", "chess 960")):
            h = impor_teks(self.conn, pgn(hasil="1-0", babak=str(i), putih=f"P{i}, A",
                                          tambahan=f'[Variant "{varian}"]'), "file")
            self.assertEqual(h.baru, 1, (varian, h.alasan_dilewati))

    def test_fen_tanpa_raja_ditolak(self):
        h = impor_teks(self.conn, pgn(hasil="1-0", langkah="1. e3",
                                      tambahan='[SetUp "1"]\n[FEN "8/8/8/8/8/8/4P3/8 w - - 0 1"]'), "file")
        self.assertEqual(h.baru, 0)

    def test_hasil_tidak_sah_ditolak(self):
        h = impor_teks(self.conn, pgn(hasil="2-0"), "file")
        self.assertEqual(h.baru, 0)
        self.assertIn("hasil tidak sah (2-0)", h.alasan_dilewati)

    def test_tanggal_bukan_tanggal_jadi_null(self):
        teks = pgn(hasil="1-0").replace('[Date "2026.06.01"]', '[Date "abcd"]')
        self.assertEqual(impor_teks(self.conn, teks, "file").baru, 1)
        self.assertIsNone(self.conn.execute("SELECT tanggal FROM partai").fetchone()[0])

    def test_fide_id_sama_beda_nama_ditolak(self):
        h = impor_teks(self.conn, pgn(hasil="1-0", putih="Kembar, A", hitam="Kembar, B",
                                      tambahan='[WhiteFideId "999"]\n[BlackFideId "999"]'), "file")
        self.assertEqual(h.baru, 0)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM partai WHERE putih_id = hitam_id")
                         .fetchone()[0], 0)

    def test_komentar_tak_ditutup_tidak_menelan_partai_berikutnya(self):
        teks = (pgn(hasil="1-0", putih="M1, A") +
                pgn(hasil="1-0", putih="M2, A", langkah="1. e4 { komentar tak ditutup e5 2. Nf3") +
                pgn(hasil="0-1", putih="M3, A") + pgn(hasil="1/2-1/2", putih="M4, A"))
        h = impor_teks(self.conn, teks, "file")
        nama = {r[0] for r in self.conn.execute(
            "SELECT p.nama FROM partai g JOIN pemain p ON p.id = g.putih_id")}
        self.assertEqual(nama, {"M1, A", "M3, A", "M4, A"})
        self.assertIn("PGN rusak: komentar { tidak ditutup", h.alasan_dilewati)

    def test_galat_satu_partai_tidak_menggagalkan_unggahan(self):
        """Surrogate tunggal (sah di JSON, tidak bisa disimpan SQLite) dulu jadi
        500 setelah partai-partai sebelumnya ter-commit."""
        teks = pgn(hasil="1-0", putih="Baik, A") + pgn(hasil="1-0", putih="Rusak\udc80, A") \
            + pgn(hasil="0-1", putih="Baik, B")
        h = impor_teks(self.conn, teks, "file")
        self.assertEqual(h.baru, 2)
        self.assertEqual(h.dilewati, 1)
        self.assertEqual(self.jumlah("partai"), 2)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM pemain WHERE nama LIKE 'Rusak%'")
                         .fetchone()[0], 0, "pemain dari partai yang batal ikut dibatalkan")
