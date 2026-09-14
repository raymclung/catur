"""
Uji app/pembantu.py — tablet/HP ikut menganalisis lewat Stockfish di browser.

Yang dijaga:
  * perangkat lain di WiFi hanya bisa membuka halaman pembantu (bukan impor,
    hentikan batch, dsb.), laptop sendiri tetap bisa membuka semuanya;
  * kode wajib dan salah kode ditolak;
  * satu partai tidak dibagikan ke dua perangkat, dan kembali ke antrean
    kalau dilepas;
  * evaluasi browser disusun dengan analisis_partai() yang sama dengan batch
    laptop: sudut pandang, kehilangan, langkah terbaik, klasifikasi "terbaik";
  * kiriman yang tidak cocok dengan partainya ditolak dan tidak dibagikan lagi.

Engine browser sendiri tidak diuji di sini (butuh browser); hasilnya
dipalsukan dalam bentuk yang persis dikirim pembantu.js.
"""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

AKAR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(AKAR))
sys.path.insert(0, str(AKAR / "src"))

import asyncio

import chess
from fastapi import HTTPException

from app import pembantu as pb
from app.analisis_db import ambil_analisis
from app.db import buat_skema, koneksi, sekarang

PGN = """[Event "Uji"]
[White "Saya"]
[Black "Lawan"]
[Result "1-0"]

1. e4 e5 2. Nf3 f6 3. Nxe5 fxe5 4. Qh5+ g6 5. Qxe5+ Qe7 6. Qxh8 1-0
"""


def evaluasi_palsu(pgn=PGN, blunder_ply=3):
    """Evaluasi per posisi seperti kiriman pembantu.js: sudut Putih, pv UCI, null di posisi akhir."""
    import io
    import chess.pgn
    game = chess.pgn.read_game(io.StringIO(pgn))
    papan = game.board()
    hasil = []
    langkah = list(game.mainline_moves())
    for i in range(len(langkah) + 1):
        if papan.is_game_over():
            hasil.append(None)
            break
        # langkah terbaik = langkah yang dimainkan, kecuali di ply blunder (2...f6)
        if i < len(langkah) and i != blunder_ply:
            pv = [langkah[i].uci()]
        else:
            pv = [next(iter(papan.legal_moves)).uci()]
        cp = 30 if i <= blunder_ply else 350
        hasil.append({"cp": cp, "pv": pv, "depth": 10})
        if i < len(langkah):
            papan.push(langkah[i])
    return hasil


class Akses(unittest.TestCase):
    def test_laptop_boleh_semua(self):
        self.assertTrue(pb.boleh_dari_luar("127.0.0.1", "/api/impor/akun"))
        self.assertTrue(pb.boleh_dari_luar("::1", "/api/analisa/hentikan"))

    def test_perangkat_lain_hanya_pembantu(self):
        for jalur in ("/pembantu", "/static/pembantu.html", "/static/pembantu.js",
                      "/api/pembantu/ambil", "/api/pembantu/kirim", "/api/pembantu/lepas",
                      "/pembantu/engine/stockfish-18-lite-single.wasm"):
            self.assertTrue(pb.boleh_dari_luar("192.168.1.20", jalur), jalur)
        for jalur in ("/", "/api/partai", "/api/impor/akun", "/api/analisa/hentikan",
                      "/api/pembantu/info", "/api/pembantu/kode", "/docs", "/openapi.json",
                      "/static/mesin.html", "/pembantu-lain", "/static/pembantu.html.bak"):
            self.assertFalse(pb.boleh_dari_luar("192.168.1.20", jalur), jalur)

    def test_middleware_menolak_perangkat_lain(self):
        from app.main import aplikasi

        def minta(jalur, host, header=()):
            """Satu GET langsung lewat ASGI (tanpa httpx), kembalikan status HTTP."""
            status = []
            scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
                     "method": "GET", "scheme": "http", "path": jalur, "raw_path": jalur.encode(),
                     "root_path": "", "query_string": b"", "headers": [(b"host", b"laptop:8000")] + list(header),
                     "client": (host, 50000), "server": ("laptop", 8000)}

            async def terima():
                return {"type": "http.request", "body": b"", "more_body": False}

            async def kirim(pesan):
                if pesan["type"] == "http.response.start":
                    status.append(pesan["status"])

            asyncio.run(aplikasi(scope, terima, kirim))
            return status[0]

        self.assertEqual(minta("/api/partai/1", "192.168.1.20"), 403)
        self.assertEqual(minta("/api/pembantu/info", "192.168.1.20"), 403)
        self.assertIn(minta("/pembantu", "192.168.1.20"), (302, 307))
        self.assertIn(minta("/pembantu", "127.0.0.1"), (302, 307))
        # lewat tunnel: datang dari 127.0.0.1 tapi membawa header proxy -> dianggap internet
        tunnel = [(b"cf-connecting-ip", b"203.0.113.9")]
        self.assertEqual(minta("/api/partai/1", "127.0.0.1", tunnel), 403)
        self.assertEqual(minta("/static/mesin.html", "127.0.0.1", [(b"x-forwarded-for", b"203.0.113.9")]), 403)
        self.assertIn(minta("/pembantu", "127.0.0.1", tunnel), (302, 307))

    def test_proxy_tidak_dianggap_laptop(self):
        self.assertFalse(pb.boleh_dari_luar("127.0.0.1", "/api/impor/akun", lewat_proxy=True))
        self.assertTrue(pb.boleh_dari_luar("127.0.0.1", "/api/pembantu/ambil", lewat_proxy=True))


class Kode(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "pengaturan.json"

    def tearDown(self):
        self.tmp.cleanup()

    def test_kode_dibuat_sekali_dan_tetap(self):
        k = pb.baca_kode(self.path)
        self.assertRegex(k, r"^\d{6}$")
        self.assertEqual(pb.baca_kode(self.path), k)

    def test_pengaturan_lain_tidak_hilang(self):
        self.path.write_text('{"batas_cpu_persen": 25}', encoding="utf-8")
        pb.ganti_kode(self.path)
        self.assertIn('"batas_cpu_persen": 25', self.path.read_text(encoding="utf-8"))


class SusunLaporan(unittest.TestCase):
    def test_rumus_sama_dengan_batch(self):
        lap = pb.susun_laporan(PGN, evaluasi_palsu())
        self.assertEqual(len(lap.langkah), 11)
        self.assertEqual(lap.depth, 10)
        e4 = lap.langkah[0]
        self.assertEqual(e4.klasifikasi, "terbaik")          # pv sama dengan langkah yang dimainkan
        f6 = lap.langkah[3]
        self.assertEqual(f6.warna, "hitam")
        self.assertEqual(f6.eval_sebelum_cp, -30)            # sudut Hitam
        self.assertEqual(f6.eval_sesudah_cp, -350)
        self.assertEqual(f6.kehilangan_cp, 320)
        self.assertEqual(f6.klasifikasi, "blunder")
        self.assertTrue(f6.langkah_terbaik_san)              # pv UCI diubah jadi SAN
        self.assertEqual(lap.ringkasan_hitam.blunder, 1)

    def test_mat_dari_sudut_putih(self):
        pos = evaluasi_palsu()
        pos[5] = {"mate": 4, "pv": pos[5]["pv"], "depth": 10}
        lap = pb.susun_laporan(PGN, pos)
        self.assertEqual(lap.langkah[4].mat_sesudah, 4)

    def test_jumlah_posisi_salah_ditolak(self):
        with self.assertRaises(ValueError):
            pb.susun_laporan(PGN, evaluasi_palsu()[:-2])

    def test_langkah_terbaik_tidak_sah_ditolak(self):
        pos = evaluasi_palsu()
        pos[2]["pv"] = ["e1e8"]
        with self.assertRaises(ValueError):
            pb.susun_laporan(PGN, pos)

    def test_posisi_tengah_bolong_ditolak(self):
        pos = evaluasi_palsu()
        pos[4] = None
        with self.assertRaises(ValueError):
            pb.susun_laporan(PGN, pos)

    def test_nilai_ngawur_ditolak(self):
        pos = evaluasi_palsu()
        pos[1]["cp"] = 10 ** 9
        with self.assertRaises(ValueError):
            pb.susun_laporan(PGN, pos)


class AlurApi(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "uji.db"
        conn = koneksi(self.db)
        buat_skema(conn)
        t = sekarang()
        conn.execute("INSERT INTO pemain(id,nama,nama_normal,dibuat_pada) VALUES(1,'Saya','saya',?)", (t,))
        conn.execute("INSERT INTO pemain(id,nama,nama_normal,dibuat_pada) VALUES(2,'Lawan','lawan',?)", (t,))
        for id_, sumber in ((1, "twic"), (2, "lichess-user"), (3, "lichess-user")):
            conn.execute(
                "INSERT INTO partai(id,putih_id,hitam_id,hasil,pgn,sumber,hash_unik,diimpor_pada,jumlah_langkah) "
                "VALUES(?,1,2,'1-0',?,?,?,?,11)", (id_, PGN, sumber, f"h{id_}", t))
        conn.commit()
        conn.close()

        self.pengaturan = Path(self.tmp.name) / "pengaturan.json"
        self.tambalan = [
            mock.patch.object(pb, "koneksi", lambda path=None: koneksi(self.db)),
            mock.patch.object(pb, "PATH_PENGATURAN", self.pengaturan),
        ]
        for t in self.tambalan:
            t.start()
        pb._sewa.clear()
        pb._perangkat.clear()
        pb._ditolak.clear()
        pb._gagal.clear()
        self.kode = pb.baca_kode()
        self.conn = koneksi(self.db)

    def tearDown(self):
        self.conn.close()
        for t in self.tambalan:
            t.stop()
        pb._sewa.clear()
        pb._perangkat.clear()
        pb._ditolak.clear()
        self.tmp.cleanup()

    def ambil(self, nama="Tablet", dukung960=True):
        return pb.api_ambil(pb.Ambil(kode=self.kode, nama=nama, dukung960=dukung960), self.conn)

    def kirim(self, partai_id, posisi, detik=0.0):
        return pb.api_kirim(pb.Kirim(kode=self.kode, nama="Tablet", partai_id=partai_id,
                                     posisi=posisi, detik=detik), self.conn)

    def test_tebakan_kode_dibatasi(self):
        salah = "000000" if self.kode != "000000" else "111111"
        for _ in range(pb.GAGAL_PER_ASAL):
            with self.assertRaises(HTTPException) as e:
                pb._periksa_kode(salah, "203.0.113.9")
            self.assertEqual(e.exception.status_code, 403)
        with self.assertRaises(HTTPException) as e:
            pb._periksa_kode(self.kode, "203.0.113.9")      # kode benar pun ditahan sementara
        self.assertEqual(e.exception.status_code, 429)
        pb._periksa_kode(self.kode, "198.51.100.7")          # alamat lain tidak ikut tertahan

    def test_kode_salah_ditolak(self):
        salah = "000000" if self.kode != "000000" else "111111"
        with self.assertRaises(HTTPException) as e:
            pb.api_ambil(pb.Ambil(kode=salah, nama="Tablet"), self.conn)
        self.assertEqual(e.exception.status_code, 403)

    def test_partai_gm_dulu_lalu_saya_terlama_dulu_dan_tidak_ganda(self):
        a = self.ambil("Tablet")
        b = self.ambil("HP")
        c = self.ambil("Laptop teman")
        self.assertEqual([a["partai_id"], b["partai_id"], c["partai_id"]], [1, 2, 3])
        self.assertEqual(len(a["langkah"]), 11)
        self.assertEqual(a["langkah"][0], "e2e4")
        self.assertEqual(a["fen_awal"], chess.STARTING_FEN)
        self.assertIsNone(self.ambil("Keempat")["partai_id"])

    def test_dilepas_kembali_ke_antrean(self):
        a = self.ambil("Tablet")
        pb.api_lepas(pb.Lepas(kode=self.kode, nama="Tablet", partai_id=a["partai_id"]))
        self.assertEqual(self.ambil("HP")["partai_id"], a["partai_id"])

    def test_kirim_tersimpan_dengan_tanda_pembantu(self):
        a = self.ambil()
        self.assertTrue(self.kirim(a["partai_id"], evaluasi_palsu(), 12.5)["tersimpan"])
        baris = self.conn.execute("SELECT engine, profil, depth, durasi_detik FROM analisis WHERE partai_id = ?",
                                  (a["partai_id"],)).fetchone()
        self.assertEqual(tuple(baris), (pb.NAMA_ENGINE, "cepat", 10, None))
        self.assertEqual(len(ambil_analisis(self.conn, a["partai_id"])["langkah"]), 11)
        self.assertEqual(pb._perangkat["Tablet"]["selesai"], 1)
        self.assertNotEqual(self.ambil()["partai_id"], a["partai_id"])   # sudah dianalisis

    def test_tahap_mendalam_setelah_semua_punya_analisis(self):
        # tahap 1: ketiga partai dianalisis cepat
        for _ in range(3):
            a = self.ambil()
            self.assertEqual(a["depth"], 10)
            self.kirim(a["partai_id"], evaluasi_palsu())
        # tahap 2: partai sendiri dulu (2, 3 — terbaru dulu), depth hemat
        b = self.ambil("Tablet")
        self.assertEqual((b["partai_id"], b["depth"], b["mendalam"]), (3, 16, True))
        self.assertTrue(pb.api_kirim(pb.Kirim(kode=self.kode, nama="Tablet", partai_id=3,
                                              posisi=evaluasi_palsu()), self.conn)["tersimpan"])
        baris = self.conn.execute("SELECT profil, depth FROM analisis WHERE partai_id = 3").fetchone()
        self.assertEqual(tuple(baris), ("hemat", 16))      # depth diambil dari catatan sewa
        urutan = [self.ambil(n)["partai_id"] for n in ("HP", "PC")]
        self.assertEqual(urutan, [2, 1])                    # partai GM (#1) terakhir
        self.assertIsNone(self.ambil("Keempat")["partai_id"])   # #3 sudah mendalam, tidak diulang

    def test_analisis_server_lichess_tidak_diulang(self):
        for _ in range(3):
            a = self.ambil()
            self.kirim(a["partai_id"], evaluasi_palsu())
        self.conn.execute("UPDATE analisis SET profil = 'lichess', engine = 'Lichess (analisis server)'")
        self.conn.commit()
        self.assertIsNone(self.ambil()["partai_id"])

    def test_chess960_ditandai_dan_rokadenya_diterima(self):
        import io
        import chess.pgn
        # posisi 960 tempat Putih bisa rokade: raja b1, benteng a1 (UCI 960: b1a1)
        pgn960 = """[Event "Uji 960"]
[White "A"]
[Black "B"]
[Result "*"]
[Variant "Chess960"]
[SetUp "1"]
[FEN "rk6/8/8/8/8/8/8/RK6 w Aa - 0 1"]

1. Kc1 Kc8 *
"""
        self.conn.execute("UPDATE partai SET pgn = ?, sumber = 'twic' WHERE id = 1", (pgn960,))
        self.conn.commit()
        a = self.ambil()
        self.assertEqual(a["partai_id"], 1)
        self.assertTrue(a["chess960"])
        self.assertFalse(self.ambil("lain")["chess960"])
        pb.api_lepas(pb.Lepas(kode=self.kode, nama="Tablet", partai_id=1))
        self.assertNotEqual(self.ambil("tab lama", dukung960=False)["partai_id"], 1)   # tab lama tidak diberi 960
        # langkah terbaik di posisi awal = rokade 960, dikirim dalam notasi raja-makan-benteng
        pos = [{"cp": 0, "pv": ["b1a1"], "depth": 10},
               {"cp": 0, "pv": ["b8a8"], "depth": 10},
               {"cp": 0, "pv": ["a1a2"], "depth": 10}]
        lap = pb.susun_laporan(pgn960, pos)
        self.assertEqual(lap.langkah[0].langkah_terbaik_uci, "b1a1")
        self.assertEqual(lap.langkah[0].langkah_terbaik_san, "O-O-O")

    def test_kiriman_tidak_cocok_tidak_dibagikan_lagi(self):
        a = self.ambil()
        with self.assertRaises(HTTPException) as e:
            self.kirim(a["partai_id"], evaluasi_palsu()[:3])
        self.assertEqual(e.exception.status_code, 422)
        self.assertNotEqual(self.ambil()["partai_id"], a["partai_id"])


class SemuaRuteTertutupDariInternet(unittest.TestCase):
    """
    SETIAP rute aplikasi — termasuk yang ditambahkan belakangan oleh sesi mana
    pun — harus tertutup untuk permintaan lewat tunnel, kecuali JALUR_LUAR.

    Kenapa diuji per rute, bukan contoh: aplikasi terus bertambah rute
    (impor, antrean, cpu, latihan, ...). Tes yang hanya menyebut beberapa jalur
    tidak akan tahu kalau rute baru ikut terbuka. Diperiksa 14 Sep 2026 dengan
    permintaan mentah ke server sungguhan: 20 cara tembus (jalur ../ lewat
    awalan pembantu, %2e%2e, // ganda, huruf besar, garis miring di akhir,
    variasi header proxy) semuanya tertolak.

    Permintaan yang tertolak berhenti di middleware, jadi tidak ada handler
    yang berjalan dan database tidak disentuh.
    """

    TUNNEL = [(b"cf-connecting-ip", b"203.0.113.9")]

    def minta(self, metode, jalur, header=()):
        from app.main import aplikasi
        status = []
        scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
                 "method": metode, "scheme": "http", "path": jalur,
                 "raw_path": jalur.encode(), "root_path": "", "query_string": b"",
                 "headers": [(b"host", b"contoh.trycloudflare.com")] + list(header),
                 "client": ("127.0.0.1", 50000), "server": ("127.0.0.1", 8000)}

        async def terima():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def kirim(pesan):
            if pesan["type"] == "http.response.start":
                status.append(pesan["status"])

        asyncio.run(aplikasi(scope, terima, kirim))
        return status[0]

    @staticmethod
    def semua_rute(daftar):
        """
        Semua rute, termasuk yang ada di dalam router yang disertakan.

        FastAPI 0.141 membungkus `include_router` sebagai `_IncludedRouter`
        tanpa atribut `path`, sehingga `aplikasi.routes` hanya memperlihatkan
        31 rute dari main.py — seluruh /api/impor, /api/analisa, /api/pembantu,
        dan halaman kekalahan/latihan TIDAK kelihatan. Tes pertama saya
        melewatkannya dan hanya diselamatkan oleh batas minimal di bawah.
        """
        for r in daftar:
            if getattr(r, "path", None) is not None:
                yield r
            asli = getattr(r, "original_router", None)
            if asli is not None:
                yield from SemuaRuteTertutupDariInternet.semua_rute(asli.routes)

    def rute_dan_metode(self):
        import re
        from app.main import aplikasi
        for rute in self.semua_rute(aplikasi.routes):
            contoh = re.sub(r"\{[^}]+\}", "1", rute.path)      # {partai_id} -> 1
            for m in sorted(getattr(rute, "methods", None) or {"GET"}):
                if m not in ("HEAD", "OPTIONS"):
                    yield m, rute.path, contoh

    def test_setiap_rute_non_pembantu_ditolak_lewat_tunnel(self):
        diperiksa = 0
        for m, _, contoh in self.rute_dan_metode():
            if pb.boleh_dari_luar("203.0.113.9", contoh, lewat_proxy=True):
                continue                                    # memang boleh dari luar
            with self.subTest(metode=m, jalur=contoh):
                self.assertEqual(self.minta(m, contoh, self.TUNNEL), 403)
            diperiksa += 1
        # jaring pengaman: kalau rute router tidak terbaca lagi, tes ini gagal
        # alih-alih diam-diam hanya menguji sebagian (52 pada 14 Sep 2026)
        self.assertGreater(diperiksa, 45)

    def test_yang_terbuka_hanya_rute_pembantu(self):
        """Menambah rute yang terbuka ke internet harus disengaja: ubah tes ini."""
        terbuka = {f"{m} {jalur}" for m, jalur, contoh in self.rute_dan_metode()
                   if pb.boleh_dari_luar("203.0.113.9", contoh, lewat_proxy=True)}
        self.assertEqual(terbuka, {
            "GET /pembantu",
            "GET /pembantu/engine/{nama}",
            "POST /api/pembantu/ambil",
            "POST /api/pembantu/kirim",
            "POST /api/pembantu/lepas",
        })

    def test_cara_tembus_jalur_ditolak(self):
        for jalur in ("/pembantu/engine/../../api/statistik",
                      "/pembantu/engine/%2e%2e/%2e%2e/api/statistik",
                      "//api/statistik", "/API/statistik", "/api/statistik/",
                      "/docs", "/openapi.json", "/redoc"):
            with self.subTest(jalur=jalur):
                self.assertIn(self.minta("GET", jalur, self.TUNNEL), (403, 404))

    def test_variasi_header_proxy_semua_dianggap_internet(self):
        for header in ([(b"x-forwarded-for", b"203.0.113.9")],
                       [(b"x-real-ip", b"203.0.113.9")],
                       [(b"forwarded", b"for=203.0.113.9")]):
            with self.subTest(header=header[0][0]):
                self.assertEqual(self.minta("GET", "/api/statistik", header), 403)


if __name__ == "__main__":
    unittest.main()
