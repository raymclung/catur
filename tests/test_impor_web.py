"""
Uji impor lewat halaman web (app/impor_web.py).

Dipanggil sebagai fungsi biasa, bukan lewat HTTP: fastapi.testclient butuh
httpx yang tidak terpasang, dan menambah dependensi hanya untuk tes tidak
sepadan. Yang diperiksa tetap yang penting: penolakan masukan yang salah,
penolakan 409 saat penulis lain memegang kunci, partai baru benar-benar
masuk dan terindeks, serta ronde yang sudah lengkap dilewati.
"""

import sys
import tempfile
import types
import unittest
from pathlib import Path

AKAR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(AKAR))

from fastapi import HTTPException
from pydantic import ValidationError

from app import impor_web
from app.db import buat_skema, koneksi
from app.kunci_penulis import KunciPenulis
from app.sumber_impor import catat, sudah_selesai

PGN = """[Event "Uji"]
[Site "?"]
[Date "2026.01.01"]
[White "Satu, Pemain"]
[Black "Dua, Pemain"]
[Result "1-0"]

1. e4 e5 2. Nf3 Nc6 3. Bb5 a6 1-0
"""


def _meledak(*a, **k):
    raise RuntimeError("pura-pura rusak")


class DasarImporWeb(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = Path(self.tmp.name)
        self.path_db = d / "uji.db"
        conn = koneksi(self.path_db)
        buat_skema(conn)
        conn.close()

        # koneksi() di modul diarahkan ke database sementara, dan kunci
        # penulisnya ke berkas sementara juga, supaya tes tidak menyentuh data
        # asli maupun kunci yang mungkin sedang dipakai impor sungguhan.
        self.asli_koneksi = impor_web.koneksi
        self.asli_kunci = impor_web.KunciPenulis
        self.kunci_path, self.info_path = d / "uji.lock", d / "uji.info"
        impor_web.koneksi = lambda: koneksi(self.path_db)
        impor_web.KunciPenulis = lambda ket: KunciPenulis(
            ket, path=self.kunci_path, path_info=self.info_path)

    def tearDown(self):
        impor_web.koneksi = self.asli_koneksi
        impor_web.KunciPenulis = self.asli_kunci
        self.tmp.cleanup()

    def db(self):
        return koneksi(self.path_db)


class UnggahPgn(DasarImporWeb):
    def test_partai_masuk_dan_terindeks(self):
        h = impor_web.impor_pgn(impor_web.UnggahPgn(pgn=PGN, sumber="uji", nama="a.pgn"))
        self.assertEqual(h["baru"], 1)
        self.assertEqual(h["diproses"], 1)
        self.assertEqual(h["diindeks"], 1)
        conn = self.db()
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM partai").fetchone()[0], 1)
        # indeks posisi ikut terbangun, jadi partai langsung muncul di Jelajah
        self.assertGreater(conn.execute("SELECT COUNT(*) FROM posisi").fetchone()[0], 0)
        self.assertEqual(conn.execute("SELECT sumber FROM partai").fetchone()[0], "uji")
        conn.close()

    def test_unggah_kedua_kali_jadi_duplikat(self):
        impor_web.impor_pgn(impor_web.UnggahPgn(pgn=PGN, sumber="uji"))
        h = impor_web.impor_pgn(impor_web.UnggahPgn(pgn=PGN, sumber="uji"))
        self.assertEqual(h["baru"], 0)
        self.assertEqual(h["duplikat"], 1)
        self.assertEqual(h["diindeks"], 0)
        conn = self.db()
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM partai").fetchone()[0], 1)
        conn.close()

    def test_bukan_pgn_ditolak(self):
        with self.assertRaises(HTTPException) as c:
            impor_web.impor_pgn(impor_web.UnggahPgn(pgn="halo, ini bukan pgn"))
        self.assertEqual(c.exception.status_code, 422)
        self.assertIn("[Event]", c.exception.detail)

    def test_terlalu_besar_ditolak_dengan_saran(self):
        besar = "[Event " + "x" * impor_web.MAKS_AKSARA
        with self.assertRaises(HTTPException) as c:
            impor_web.impor_pgn(impor_web.UnggahPgn(pgn=besar))
        self.assertEqual(c.exception.status_code, 413)
        self.assertIn("impor_pgn.py", c.exception.detail)

    def test_label_sumber_harus_sederhana(self):
        for buruk in ("UJI SPASI", "a", "x" * 21, "sumber/aneh"):
            with self.assertRaises(ValidationError):
                impor_web.UnggahPgn(pgn=PGN, sumber=buruk)
        self.assertEqual(impor_web.UnggahPgn(pgn=PGN).sumber, "unggahan")

    def test_ditolak_saat_penulis_lain_jalan(self):
        with KunciPenulis("pura-pura impor twic", path=self.kunci_path,
                          path_info=self.info_path):
            with self.assertRaises(HTTPException) as c:
                impor_web.impor_pgn(impor_web.UnggahPgn(pgn=PGN))
            self.assertEqual(c.exception.status_code, 409)
            self.assertIn("pura-pura impor twic", c.exception.detail)
        # setelah penulis itu selesai, impor yang sama jalan
        self.assertEqual(impor_web.impor_pgn(impor_web.UnggahPgn(pgn=PGN))["baru"], 1)

    def test_kunci_dilepas_walau_impor_gagal(self):
        asli = impor_web.impor_teks
        impor_web.impor_teks = _meledak
        try:
            with self.assertRaises(RuntimeError):
                impor_web.impor_pgn(impor_web.UnggahPgn(pgn=PGN))
        finally:
            impor_web.impor_teks = asli
        # kalau kunci tidak dilepas, impor berikutnya akan kena 409
        self.assertEqual(impor_web.impor_pgn(impor_web.UnggahPgn(pgn=PGN))["baru"], 1)


class DaftarAkun(DasarImporWeb):
    """
    GET /api/impor/akun — dipakai halaman Impor untuk membuat satu tombol per
    akun. Daftarnya harus datang dari `ambil_partai.AKUN`, bukan disalin ke
    berkas web, supaya akun yang ditambah/dihapus di satu tempat ikut di mana-mana.
    """

    def test_akun_datang_dari_ambil_partai(self):
        from ambil_partai import AKUN

        hasil = impor_web.daftar_akun()["akun"]
        self.assertEqual([a["akun"] for a in hasil], [a.username for a in AKUN])
        for a in hasil:
            self.assertIn(a["platform"], ("lichess", "chesscom"))
            self.assertEqual(a["partai"], 0)        # database uji masih kosong
            self.assertIsNone(a["terakhir"])

    def test_jumlah_partai_dihitung_per_akun(self):
        # lewat jalur impor sungguhan supaya barisnya sah menurut skema
        pgn = PGN.replace("Satu, Pemain", "McLung_07").replace("Dua, Pemain", "lawan")
        impor_web.impor_pgn(impor_web.UnggahPgn(pgn=pgn, sumber="uji"))
        satu = [a for a in impor_web.daftar_akun()["akun"] if a["akun"] == "McLung_07"][0]
        self.assertEqual(satu["partai"], 1)
        self.assertEqual(satu["terakhir"], "2026.01.01")

    def test_akun_dihitung_di_kedua_warna_tanpa_peka_huruf(self):
        # Nama di PGN berbeda huruf dari AKUN ("mclung_07"), dan sekali jadi
        # Putih, sekali jadi Hitam: keduanya harus terhitung, lawan tidak.
        putih = PGN.replace("Satu, Pemain", "mclung_07").replace("Dua, Pemain", "lawan")
        hitam = (PGN.replace("Satu, Pemain", "lawan2").replace("Dua, Pemain", "MCLUNG_07")
                    .replace("2026.01.01", "2026.02.02"))
        impor_web.impor_pgn(impor_web.UnggahPgn(pgn=putih + "\n\n" + hitam, sumber="uji"))
        satu = [a for a in impor_web.daftar_akun()["akun"] if a["akun"] == "McLung_07"][0]
        self.assertEqual(satu["partai"], 2)
        self.assertEqual(satu["terakhir"], "2026.02.02")

    def test_kueri_akun_tidak_memindai_seluruh_partai(self):
        """Penjaga performa: bentuk JOIN lama memindai semua partai (18 detik)."""
        conn = impor_web.koneksi()
        try:
            rencana = " | ".join(r[3] for r in conn.execute(
                "EXPLAIN QUERY PLAN " + impor_web.KUERI_JUMLAH_AKUN, ("x",)))
        finally:
            conn.close()
        self.assertNotRegex(rencana, r"SCAN (partai|g)\b", rencana)
        self.assertIn("idx_partai_putih", rencana)
        self.assertIn("idx_partai_hitam", rencana)

    def test_saringan_turnamen_ikut_dilaporkan(self):
        """RyuMcLungG hanya dihitung untuk Arena Kings — halaman harus bisa menyebutnya."""
        hasil = {a["akun"].lower(): a for a in impor_web.daftar_akun()["akun"]}
        self.assertEqual(hasil["ryumclungg"]["saring_turnamen"], "arena-kings")
        self.assertFalse(hasil["mclung_07"]["saring_turnamen"])


class Broadcast(DasarImporWeb):
    """Jaringan dipalsukan: yang diuji logika modul, bukan Lichess."""

    def pasang(self, ronde, pgn_per_ronde, tour="Turnamen Uji", ronde_tunggal=False):
        palsu = types.ModuleType("impor_broadcast")

        class BroadcastError(Exception):
            pass

        def info_ronde(rid):
            if not ronde_tunggal:
                raise BroadcastError("bukan ronde")
            return {"round": {"name": "Ronde 1"}, "tour": {"name": tour, "id": "tourX"}}

        palsu.BroadcastError = BroadcastError
        palsu.id_ronde = lambda u: u.rstrip("/").split("/")[-1]
        palsu.info_ronde = info_ronde
        palsu.info_turnamen = lambda tid: {"tour": {"name": tour}, "rounds": ronde}
        palsu.daftar_ronde = lambda tid: ronde
        palsu.pgn_ronde = lambda rid: pgn_per_ronde[rid]
        sys.modules["impor_broadcast"] = palsu
        self.addCleanup(sys.modules.pop, "impor_broadcast", None)

    def test_tautan_ronde_mengambil_seluruh_turnamen(self):
        """13 Sep 2026: tautan satu ronde Global Chess League hanya mengambil 1 dari 30 ronde."""
        self.pasang(ronde=[{"id": "r1", "name": "Ronde 1"}, {"id": "r2", "name": "Ronde 2"}],
                    pgn_per_ronde={"r1": PGN, "r2": PGN.replace("Bb5 a6", "Bc4 Bc5")},
                    ronde_tunggal=True)
        h = impor_web.impor_broadcast(
            impor_web.UnggahBroadcast(url="https://lichess.org/broadcast/a/b/r1"))
        self.assertEqual(len(h["ronde"]), 2)
        self.assertEqual(h["baru"], 2)

    def test_tautan_ronde_bisa_dibatasi_satu_ronde(self):
        self.pasang(ronde=[{"id": "r1", "name": "Ronde 1"}, {"id": "r2", "name": "Ronde 2"}],
                    pgn_per_ronde={"r1": PGN, "r2": PGN.replace("Bb5 a6", "Bc4 Bc5")},
                    ronde_tunggal=True)
        h = impor_web.impor_broadcast(impor_web.UnggahBroadcast(
            url="https://lichess.org/broadcast/a/b/r1", seluruh_turnamen=False))
        self.assertEqual(len(h["ronde"]), 1)
        self.assertEqual(h["baru"], 1)

    def test_evaluasi_lichess_langsung_jadi_analisis(self):
        ber_eval = PGN.replace("1. e4 e5 2. Nf3 Nc6 3. Bb5 a6 1-0",
                               "1. e4 { [%eval 0.3] } e5 { [%eval 0.3] } 2. Nf3 { [%eval 0.2] } "
                               "Nc6 { [%eval 0.3] } 3. Bb5 { [%eval 0.3] } a6 { [%eval 0.4] } 1-0")
        self.pasang(ronde=[{"id": "r1", "name": "Ronde 1"}], pgn_per_ronde={"r1": ber_eval})
        h = impor_web.impor_broadcast(
            impor_web.UnggahBroadcast(url="https://lichess.org/broadcast/a/b/tour1234"))
        self.assertEqual(h["dianalisis_lichess"], 1)
        conn = self.db()
        r = conn.execute("SELECT engine, profil FROM analisis").fetchone()
        conn.close()
        self.assertEqual(r["profil"], "lichess")

    def test_turnamen_semua_ronde(self):
        self.pasang(ronde=[{"id": "r1", "name": "Ronde 1"}, {"id": "r2", "name": "Ronde 2"}],
                    pgn_per_ronde={"r1": PGN, "r2": PGN.replace("Bb5 a6", "Bc4 Bc5")})
        h = impor_web.impor_broadcast(
            impor_web.UnggahBroadcast(url="https://lichess.org/broadcast/a/b/tour1234"))
        self.assertEqual(h["turnamen"], "Turnamen Uji")
        self.assertEqual(h["baru"], 2)
        self.assertEqual([r["status"] for r in h["ronde"]], ["final", "final"])
        conn = self.db()
        self.assertEqual(conn.execute(
            "SELECT COUNT(*) FROM partai WHERE sumber = 'lichess-broadcast'").fetchone()[0], 2)
        conn.close()

    def test_ronde_yang_sudah_lengkap_dilewati(self):
        self.pasang(ronde=[{"id": "r1", "name": "Ronde 1"}], pgn_per_ronde={"r1": PGN})
        conn = self.db()
        catat(conn, "lichess-broadcast", "r1", "Ronde 1", None, 1, 1, 1, selesai=True)
        conn.close()
        h = impor_web.impor_broadcast(
            impor_web.UnggahBroadcast(url="https://lichess.org/broadcast/a/b/tour1234"))
        self.assertEqual(h["baru"], 0)
        self.assertIn("dilewati", h["ronde"][0]["status"])

    def test_ronde_masih_berjalan_tidak_ditandai_selesai(self):
        belum = PGN.replace('[Result "1-0"]', '[Result "*"]').replace("a6 1-0", "a6 *")
        self.pasang(ronde=[{"id": "r1", "name": "Ronde 1"}], pgn_per_ronde={"r1": belum})
        h = impor_web.impor_broadcast(
            impor_web.UnggahBroadcast(url="https://lichess.org/broadcast/a/b/tour1234"))
        self.assertEqual(h["ronde"][0]["status"], "masih berjalan")
        conn = self.db()
        self.assertFalse(sudah_selesai(conn, "lichess-broadcast", "r1"))
        conn.close()

    def test_url_bukan_broadcast_ditolak(self):
        palsu = types.ModuleType("impor_broadcast")

        class BroadcastError(Exception):
            pass

        def id_ronde(u):
            raise BroadcastError("Tidak menemukan ID ronde di: " + u)

        palsu.BroadcastError = BroadcastError
        palsu.id_ronde = id_ronde
        palsu.info_ronde = palsu.info_turnamen = lambda *a: None
        palsu.daftar_ronde = palsu.pgn_ronde = lambda *a: None
        sys.modules["impor_broadcast"] = palsu
        self.addCleanup(sys.modules.pop, "impor_broadcast", None)

        with self.assertRaises(HTTPException) as c:
            impor_web.impor_broadcast(impor_web.UnggahBroadcast(url="https://contoh.com/x"))
        self.assertEqual(c.exception.status_code, 422)


class PembaruanMingguan(DasarImporWeb):
    """
    Tombol mingguan menarik TWIC juga (Ray, 9 Okt 2026): dulu hanya broadcast,
    jadi TWIC sebulan tertinggal tanpa ada yang melihatnya.
    """

    def test_tombol_menjalankan_perbarui_lengkap(self):
        # Tanpa --hanya: semua tahap, termasuk cadangan sebelum menulis dan TWIC.
        self.assertEqual(impor_web.PERINTAH_MINGGUAN, ["perbarui.py"])
        self.assertTrue((AKAR / impor_web.PERINTAH_MINGGUAN[0]).is_file())

    def test_twic_terakhir_kosong(self):
        conn = self.db()
        self.assertIsNone(impor_web.twic_terakhir(conn))
        conn.close()

    def test_twic_terakhir_urut_angka_dan_hanya_yang_selesai(self):
        conn = self.db()
        # "999" < "1665" sebagai angka tapi > sebagai teks: urutan harus angka.
        catat(conn, "twic", "999", "TWIC 999", None, 10, 5, 5)
        catat(conn, "twic", "1665", "TWIC 1665", None, 10, 5, 3)
        catat(conn, "twic", "1666", "TWIC 1666", None, 10, 5, 0, selesai=False)
        catat(conn, "lichess-broadcast", "99999", "ronde", None, 1, 1, 1)
        hasil = impor_web.twic_terakhir(conn)
        conn.close()
        self.assertEqual(hasil["edisi"], 1665)
        self.assertEqual(hasil["baru"], 3)

    def test_status_memuat_twic(self):
        conn = self.db()
        catat(conn, "twic", "1665", "TWIC 1665", None, 10, 5, 3)
        conn.close()
        self.assertEqual(impor_web.status_mingguan()["twic"]["edisi"], 1665)


if __name__ == "__main__":
    unittest.main()
