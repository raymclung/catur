"""
Tes `app/evaluasi_aliran.py`: analisis tanpa henti di papan analisis.

Yang paling penting di sini bukan isi garisnya (itu milik Stockfish dan sudah
diuji di test_engine_server.py) tapi KUNCI engine: aliran memegangnya lama,
jadi tiap jalan keluar — selesai, batas waktu, koneksi putus, dibatalkan di
tengah langkah — harus melepasnya, dan melepasnya pada saat yang benar.

Tanpa HTTP (httpx tidak terpasang): generator dan rutenya dipanggil langsung.
Tes terakhir memakai Stockfish sungguhan, dilewati kalau engine tidak ada.
"""

import asyncio
import json
import sys
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

AKAR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(AKAR))

import chess
from fastapi import HTTPException

from app import engine_server, evaluasi_aliran as ea

AWAL = chess.STARTING_FEN
MAT = "rnb1kbnr/pppp1ppp/8/4p3/6Pq/5P2/PPPPP2P/RNBQKBNR w KQkq - 1 3"   # mat Putih


class MesinPalsu:
    """Pengganti ServerEngine: mencatat pegang/lepas, depth naik sesuai permintaan."""

    def __init__(self, jeda=0.0, sibuk=False, penahan=None):
        self.jeda = jeda
        self.sibuk = sibuk
        self.penahan = penahan          # threading.Event: langkah menunggu ini
        self.dipegang = 0
        self.dilepas = 0
        self.depth_diminta = []

    def pegang(self):
        if self.sibuk:
            raise engine_server.EngineSibuk("engine sedang dipakai permintaan lain")
        self.dipegang += 1

    def lepas(self):
        self.dilepas += 1

    def analisis_dipegang(self, board, multipv, depth, waktu, berhenti=None):
        self.depth_diminta.append(depth)
        self.berhenti = berhenti
        if self.penahan is not None:
            self.penahan.wait(5)
        if self.jeda:
            time.sleep(self.jeda)
        return {"fen": board.fen(), "garis": [{"multipv": 1, "cp": 20, "mate": None,
                                               "pv": ["e4"], "depth": depth}],
                "skor": {"cp": 20, "mate": None}, "depth": depth,
                "selesai": board.is_game_over()}


async def tidak_putus():
    return False


def kumpulkan(board, mesin, batas=60, terputus=tidak_putus, multipv=1):
    async def jalan():
        return [k async for k in ea.aliran_analisis(board, multipv, batas, terputus, mesin)]
    return asyncio.run(jalan())


def urai(kiriman):
    jenis = kiriman.split("\n", 1)[0].removeprefix("event: ")
    return jenis, json.loads(kiriman.split("data: ", 1)[1])


class Aliran(unittest.TestCase):
    def test_depth_naik_sampai_maks_lalu_selesai(self):
        m = MesinPalsu()
        hasil = [urai(k) for k in kumpulkan(chess.Board(AWAL), m)]
        garis = [d for j, d in hasil if j == "garis"]
        self.assertEqual([g["depth"] for g in garis],
                         list(range(ea.DEPTH_AWAL, engine_server.DEPTH_MAKS + 1)))
        self.assertEqual(hasil[-1], ("selesai", {"alasan": "depth-maks"}))
        self.assertIn("detik", garis[0])
        self.assertEqual((m.dipegang, m.dilepas), (1, 1))

    def test_batas_waktu_menghentikan_dan_melepas(self):
        m = MesinPalsu(jeda=0.2)
        hasil = [urai(k) for k in kumpulkan(chess.Board(AWAL), m, batas=0.5)]
        self.assertEqual(hasil[-1], ("selesai", {"alasan": "batas-waktu"}))
        self.assertLess(len(hasil), 6)
        self.assertEqual(m.dilepas, 1)

    def test_koneksi_putus_berhenti_tanpa_selesai_dan_melepas(self):
        m = MesinPalsu()
        hitung = {"n": 0}

        async def putus_sesudah_dua():
            hitung["n"] += 1
            return hitung["n"] > 2

        hasil = [urai(k) for k in kumpulkan(chess.Board(AWAL), m, terputus=putus_sesudah_dua)]
        self.assertEqual([j for j, _ in hasil], ["garis", "garis"])
        self.assertEqual(m.dilepas, 1)

    def test_dibatalkan_di_tengah_langkah_kunci_lepas_sesudah_engine_selesai(self):
        """Kalau dilepas saat koneksi putus, permintaan lain memakai engine yang masih sibuk."""
        penahan = threading.Event()
        m = MesinPalsu(penahan=penahan)

        async def jalan():
            gen = ea.aliran_analisis(chess.Board(AWAL), 1, 60, tidak_putus, m)
            tugas = asyncio.ensure_future(gen.__anext__())
            await asyncio.sleep(0.2)            # langkah pertama sedang di utas
            tugas.cancel()
            try:
                await tugas
            except asyncio.CancelledError:
                pass
            await gen.aclose()
            belum = m.dilepas
            disuruh_berhenti = m.berhenti.is_set()
            penahan.set()                       # engine menyelesaikan langkahnya
            for _ in range(50):
                if m.dilepas:
                    break
                await asyncio.sleep(0.05)
            return belum, m.dilepas, disuruh_berhenti

        belum, sesudah, disuruh_berhenti = asyncio.run(jalan())
        self.assertTrue(disuruh_berhenti, "engine tidak disuruh berhenti")
        self.assertEqual(belum, 0, "kunci dilepas sebelum engine selesai")
        self.assertEqual(sesudah, 1)

    def test_engine_direbut_jadi_galat_tanpa_melepas_milik_orang(self):
        m = MesinPalsu(sibuk=True)
        hasil = [urai(k) for k in kumpulkan(chess.Board(AWAL), m)]
        self.assertEqual(hasil[0][0], "galat")
        self.assertEqual(m.dilepas, 0)

    def test_posisi_selesai_satu_kiriman(self):
        m = MesinPalsu()
        hasil = [urai(k) for k in kumpulkan(chess.Board(MAT), m)]
        self.assertEqual([j for j, _ in hasil], ["garis", "selesai"])
        self.assertEqual(hasil[-1][1]["alasan"], "posisi-selesai")
        self.assertEqual(m.dilepas, 1)

    def test_waktu_habis_sebelum_depth_tercapai_tidak_mengulang_depth(self):
        """Depth berikutnya = yang dicapai + 1, jadi tidak macet di depth yang sama."""
        m = MesinPalsu()
        dicapai = iter([8, 8, 9, 15, 30])

        def analisis(board, multipv, depth, waktu, berhenti=None):
            m.depth_diminta.append(depth)
            d = next(dicapai)
            return {"garis": [], "depth": d, "selesai": False}

        m.analisis_dipegang = analisis
        kumpulkan(chess.Board(AWAL), m)
        self.assertEqual(m.depth_diminta, [8, 9, 10, 11, 16])

    def test_hasil_lebih_dangkal_dari_sebelumnya_tidak_dikirim(self):
        """Kasus nyata Stockfish: waktu habis di langkah terakhir -> depth 19 lalu 18."""
        m = MesinPalsu()
        dicapai = iter([8, 19, 18, 30])

        def analisis(board, multipv, depth, waktu, berhenti=None):
            return {"garis": [], "depth": next(dicapai), "selesai": False}

        m.analisis_dipegang = analisis
        hasil = [urai(k) for k in kumpulkan(chess.Board(AWAL), m)]
        self.assertEqual([d["depth"] for j, d in hasil if j == "garis"], [8, 19, 30])


class Kunci(unittest.TestCase):
    """ServerEngine sungguhan, proses engine ditambal: kunci dipakai bersama."""

    def setUp(self):
        self.se = engine_server.ServerEngine(idle_detik=3600)
        self.se._pastikan_hidup = lambda: object()

    def tearDown(self):
        if self.se._penjaga is not None:
            self.se._penjaga.cancel()

    def test_selama_aliran_evaluasi_biasa_ditolak(self):
        self.se.pegang()
        self.assertTrue(self.se.sedang_dipakai)
        with self.assertRaises(engine_server.EngineSibuk):
            self.se.evaluasi(AWAL)
        with self.assertRaises(engine_server.EngineSibuk):
            self.se.pegang()
        self.se.lepas()
        self.assertFalse(self.se.sedang_dipakai)

    def test_lepas_dari_utas_lain(self):
        """add_done_callback berjalan di utas engine, bukan utas yang memegang."""
        self.se.pegang()
        t = threading.Thread(target=self.se.lepas)
        t.start()
        t.join()
        self.assertFalse(self.se.sedang_dipakai)

    def test_engine_gagal_menyala_tidak_meninggalkan_kunci(self):
        def gagal():
            raise engine_server.EngineTidakAda("tidak ada")
        self.se._pastikan_hidup = gagal
        with self.assertRaises(engine_server.EngineTidakAda):
            self.se.pegang()
        self.assertFalse(self.se.sedang_dipakai)


class Rute(unittest.TestCase):
    class Permintaan:
        async def is_disconnected(self):
            return False

    def panggil(self, **kw):
        kw.setdefault("multipv", 3)
        kw.setdefault("batas", 60)
        return asyncio.run(ea.api_evaluasi_aliran(self.Permintaan(), **kw))

    def test_fen_rusak_422(self):
        with self.assertRaises(HTTPException) as c:
            self.panggil(fen="bukan fen")
        self.assertEqual(c.exception.status_code, 422)

    def test_engine_dipakai_409_sebelum_aliran(self):
        se = engine_server.server_engine
        with mock.patch.object(type(se), "sedang_dipakai", new=mock.PropertyMock(return_value=True)), \
             mock.patch.object(se, "path_engine", AKAR / "tests" / "test_evaluasi_aliran.py"):
            with self.assertRaises(HTTPException) as c:
                self.panggil(fen=AWAL)
        self.assertEqual(c.exception.status_code, 409)

    def test_engine_tidak_ada_503(self):
        se = engine_server.server_engine
        with mock.patch.object(se, "path_engine", AKAR / "tidak-ada.exe"):
            with self.assertRaises(HTTPException) as c:
                self.panggil(fen=AWAL)
        self.assertEqual(c.exception.status_code, 503)

    def test_terdaftar_di_openapi(self):
        from app.main import aplikasi
        self.assertIn("/api/evaluasi/aliran", aplikasi.openapi()["paths"])


@unittest.skipUnless(engine_server.server_engine.path_engine.is_file(), "Stockfish tidak ada")
class StockfishSungguhan(unittest.TestCase):
    def test_depth_naik_dan_kunci_kembali(self):
        se = engine_server.ServerEngine(idle_detik=5)
        try:
            hasil = [urai(k) for k in kumpulkan(chess.Board(AWAL), se, batas=3, multipv=2)]
        finally:
            se.matikan()
        garis = [d for j, d in hasil if j == "garis"]
        self.assertGreaterEqual(len(garis), 2)
        depth = [g["depth"] for g in garis]
        self.assertEqual(depth, sorted(depth))
        self.assertGreater(depth[-1], depth[0])
        self.assertEqual(len(garis[-1]["garis"]), 2)
        self.assertIn(hasil[-1][0], ("selesai",))
        self.assertFalse(se.sedang_dipakai)

    def test_berhenti_menghentikan_pencarian_panjang(self):
        """Pencarian 10 detik dihentikan sesudah 0,3 detik; engine bebas < 1 detik."""
        se = engine_server.ServerEngine(idle_detik=5)
        try:
            se.pegang()
            berhenti = threading.Event()
            threading.Timer(0.3, berhenti.set).start()
            mulai = time.monotonic()
            hasil = se.analisis_dipegang(chess.Board(AWAL), 2, 30, 10.0, berhenti)
            lama = time.monotonic() - mulai
            se.lepas()
        finally:
            se.matikan()
        self.assertLess(lama, 1.5)
        self.assertTrue(hasil["garis"])
        self.assertTrue(hasil["garis"][0]["pv"])


if __name__ == "__main__":
    unittest.main()
