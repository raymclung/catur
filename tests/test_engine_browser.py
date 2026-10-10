"""
Engine di perangkat ini (web/engine-browser.js, Stockfish WASM di Web Worker)
diuji di peramban sungguhan lewat DevTools Protocol.

Kenapa bukan `--dump-dom` seperti tests/test_halaman_headless.py: dengan
`--virtual-time-budget` waktu peramban dipalsukan dan Web Worker tidak ikut
jalan, jadi halaman sudah di-dump sebelum engine menjawab (dicatat sebagai
batas 9 Okt 2026: "belum ada tes otomatis untuk engine browser"). Di sini
peramban berjalan dengan waktu nyata dan isi halaman dibaca berulang lewat
`Runtime.evaluate` sampai engine menjawab atau batas waktu habis.

`websockets` sudah ikut uvicorn[standard] (requirements.txt), jadi tidak ada
dependency baru. Dilewati kalau engine browser belum diunduh (`engine/` tidak
masuk git, jadi di CI selalu dilewati) atau tidak ada peramban.
"""

import asyncio
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request
from pathlib import Path

AKAR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(AKAR))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_halaman_headless import buat_db, diminta, edge, port_bebas  # noqa: E402

try:
    import websockets  # noqa: E402
except ImportError:      # pragma: no cover - ikut uvicorn[standard]
    websockets = None

ENGINE_WASM = AKAR / "engine" / "browser" / "stockfish-18-lite-single.wasm"
MAT1_PUTIH = "6k1/5ppp/8/8/8/8/5PPP/R5K1 w - - 0 1"       # 1.Ra8#
MAT1_HITAM = "r5k1/5ppp/8/8/8/8/5PPP/6K1 b - - 0 1"       # 1...Ra1#

BACA_HALAMAN = """JSON.stringify({
  skor: document.getElementById("engine-skor").textContent,
  meta: document.getElementById("engine-meta").textContent,
  status: document.getElementById("status-engine").textContent,
  garis: document.getElementById("garis").textContent,
  terus: document.getElementById("terus").textContent,
  terusMati: document.getElementById("terus").disabled
})"""


class Peramban:
    """Satu tab peramban headless yang dikendalikan lewat DevTools Protocol."""

    def __init__(self):
        self.port = port_bebas()
        self.profil = tempfile.TemporaryDirectory()
        self.proses = subprocess.Popen(
            [str(edge()), "--headless=new", "--disable-gpu", "--no-first-run",
             f"--remote-debugging-port={self.port}", f"--user-data-dir={self.profil.name}",
             "about:blank"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.alamat_ws = self._alamat_tab()
        self._id = 0

    def _alamat_tab(self, batas: float = 30.0) -> str:
        habis = time.time() + batas
        while time.time() < habis:
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/json/list", timeout=2) as r:
                    for t in json.load(r):
                        if t.get("type") == "page":
                            return t["webSocketDebuggerUrl"]
            except OSError:
                pass
            time.sleep(0.3)
        raise RuntimeError("peramban tidak membuka DevTools")

    def tutup(self):
        self.proses.terminate()
        try:
            self.proses.wait(timeout=15)
        except subprocess.TimeoutExpired:
            self.proses.kill()
        self.profil.cleanup()

    async def _kirim(self, ws, metode, **param):
        self._id += 1
        nomor = self._id
        await ws.send(json.dumps({"id": nomor, "method": metode, "params": param}))
        while True:
            pesan = json.loads(await ws.recv())
            if pesan.get("id") == nomor:
                if "error" in pesan:
                    raise RuntimeError(f"{metode}: {pesan['error']}")
                return pesan.get("result", {})

    def jalankan(self, url: str, aksi: str | None, sampai, batas: float) -> list[dict]:
        """
        Buka `url`, jalankan JS `aksi` (boleh None), lalu baca halaman tiap
        0,25 s sampai `sampai(isi)` benar. Mengembalikan SEMUA bacaan, supaya tes
        bisa memeriksa yang terjadi di tengah jalan (depth Terus yang naik).
        """
        async def utama():
            async with websockets.connect(self.alamat_ws, max_size=None) as ws:
                await self._kirim(ws, "Page.enable")
                await self._kirim(ws, "Page.navigate", url=url)
                bacaan = []
                habis = time.time() + batas
                siap = False
                while time.time() < habis:
                    await asyncio.sleep(0.25)
                    try:
                        r = await self._kirim(ws, "Runtime.evaluate", expression=BACA_HALAMAN,
                                              returnByValue=True)
                        isi = json.loads(r["result"]["value"])
                    except (RuntimeError, KeyError, TypeError, ValueError):
                        continue          # halaman belum selesai dimuat
                    if not siap:
                        siap = True
                        if aksi:
                            await self._kirim(ws, "Runtime.evaluate", expression=aksi)
                            continue
                    bacaan.append(isi)
                    if sampai(isi):
                        return bacaan
                raise AssertionError(f"batas {batas} s habis; bacaan terakhir: "
                                     f"{bacaan[-1] if bacaan else None}")
        return asyncio.run(utama())


@unittest.skipUnless(diminta(),
                     "set CATUR_UJI_HEADLESS=1 untuk menjalankan (butuh peramban, ±1 menit)")
@unittest.skipIf(edge() is None, "tidak ada Edge/Chrome/Chromium")
@unittest.skipIf(websockets is None, "paket websockets tidak ada")
@unittest.skipUnless(ENGINE_WASM.is_file(),
                     "engine browser belum diunduh: python -m app.pembantu --unduh-engine")
class EngineDiPeramban(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        folder = Path(cls.tmp.name)
        cls.port = port_bebas()
        lingkungan = {**os.environ, "CATUR_DB": str(buat_db(folder))}
        cls.log = (folder / "server.log").open("w", encoding="utf-8")
        cls.server = subprocess.Popen(
            [sys.executable, "-X", "utf8", "-m", "uvicorn", "app.main:aplikasi",
             "--host", "127.0.0.1", "--port", str(cls.port)],
            cwd=str(AKAR), env=lingkungan, stdout=cls.log, stderr=subprocess.STDOUT)
        habis = time.time() + 45
        while time.time() < habis:
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{cls.port}/api/statistik", timeout=2)
                break
            except OSError:
                time.sleep(0.5)
        cls.peramban = Peramban()

    @classmethod
    def tearDownClass(cls):
        cls.peramban.tutup()
        cls.server.terminate()
        try:
            cls.server.wait(timeout=15)
        except subprocess.TimeoutExpired:
            cls.server.kill()
        cls.log.close()
        cls.tmp.cleanup()

    def url(self, fen: str, tambahan: str = "") -> str:
        from urllib.parse import quote
        return (f"http://127.0.0.1:{self.port}/static/analisis.html?engine=browser"
                f"&fen={quote(fen)}{tambahan}")

    def test_mat_satu_putih(self):
        bacaan = self.peramban.jalankan(
            self.url(MAT1_PUTIH), 'document.getElementById("analisa").click()',
            lambda d: "M1" in d["skor"], batas=90)
        akhir = bacaan[-1]
        self.assertIn("+M1", akhir["skor"])
        self.assertIn("Ra8#", akhir["garis"])

    def test_mat_satu_hitam_dari_sudut_putih(self):
        """Skor selalu dari sudut pandang Putih, sama dengan /api/evaluasi."""
        bacaan = self.peramban.jalankan(
            self.url(MAT1_HITAM), 'document.getElementById("analisa").click()',
            lambda d: "M1" in d["skor"], batas=90)
        self.assertIn("-M1", bacaan[-1]["skor"].replace("−", "-"))
        self.assertIn("Ra1#", bacaan[-1]["garis"])

    def test_terus_makin_dalam_lalu_selesai(self):
        """Terus dengan engine perangkat: tombol tidak lagi dimatikan, depth
        naik selama berjalan, lalu berhenti sendiri saat batas waktu habis."""
        fen = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
        bacaan = self.peramban.jalankan(
            self.url(fen, "&uji_terus=4"), None,
            lambda d: d["status"].startswith("Selesai"), batas=90)
        self.assertFalse(any(d["terusMati"] for d in bacaan))
        depth = [int(d["status"].split("depth ")[1].split()[0]) for d in bacaan
                 if d["status"].startswith("Terus menganalisis · depth ")]
        self.assertGreaterEqual(len(set(depth)), 2, f"depth tidak naik: {depth}")
        self.assertEqual(depth, sorted(depth))
        self.assertIn("4 detik habis", bacaan[-1]["status"])
        self.assertEqual(bacaan[-1]["terus"], "Terus")
        self.assertIn("terus", bacaan[-1]["meta"])


if __name__ == "__main__":
    unittest.main()
