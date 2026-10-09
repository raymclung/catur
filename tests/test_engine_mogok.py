"""
Engine yang mogok di tengah batch (analisis_partai.py).

12 Sep 2026 dua batch mati dengan EngineTerminatedError: galat python-chess
bukan turunan EngineError milik catur_analysis, jadi lolos dari penangkapnya
dan membunuh seluruh batch. Sekarang partai itu dilewati, engine dinyalakan
ulang, dan batch lanjut. Stockfish di sini dipalsukan: tidak ada engine
sungguhan yang dijalankan.
"""

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import chess.engine

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import analisis_partai as ap                                  # noqa: E402
from app.db import buat_skema, koneksi                        # noqa: E402
from app.impor import impor_teks                              # noqa: E402

# 12 langkah: pilih_partai() melewati partai di bawah 10 langkah (--min-langkah)
LANGKAH = ("1. e4 e5 2. Nf3 Nc6 3. Bb5 a6 4. Ba4 Nf6 5. O-O Be7 6. Re1 b5 7. Bb3 d6 "
           "8. c3 O-O 9. h3 Nb8 10. d4 Nbd7 11. Nbd2 Bb7 12. Bc2 Re8")


class StockfishPalsu:
    dinyalakan = 0

    def __init__(self, profil=None):
        self.nama_engine = "Palsu 1"

    def start(self):
        StockfishPalsu.dinyalakan += 1
        return self

    def stop(self):
        pass

    def __enter__(self):
        return self.start()

    def __exit__(self, *a):
        self.stop()


def laporan_palsu():
    r = SimpleNamespace(akurasi=90.0, blunder=0)
    return SimpleNamespace(ringkasan_putih=r, ringkasan_hitam=r)


class EngineMogok(unittest.TestCase):
    def setUp(self):
        # ignore_cleanup_errors: main() yang mati karena galat masih memegang
        # koneksi SQLite lewat traceback-nya, dan Windows mengunci berkasnya.
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.db = Path(self.tmp.name) / "uji.db"
        conn = koneksi(self.db)
        buat_skema(conn)
        for i in range(3):
            impor_teks(conn, f'[Event "u"]\n[Date "2026.01.0{i + 1}"]\n[White "Putih, {i}"]\n'
                             f'[Black "Hitam, {i}"]\n[Result "1-0"]\n\n{LANGKAH} 1-0\n', "twic")
        conn.commit()
        conn.close()
        StockfishPalsu.dinyalakan = 0
        self.disimpan = []
        self.urutan = []

    def tearDown(self):
        self.tmp.cleanup()

    def jalankan(self, mogok_di):
        def analisis(game, sf, depth=None):
            n = len(self.urutan) + 1
            self.urutan.append(game.headers["White"])
            if n in mogok_di:
                raise chess.engine.EngineTerminatedError("engine process died unexpectedly")
            return laporan_palsu()

        argv = ["analisis_partai.py", "--db", str(self.db), "--terbaru", "3",
                "--profil", "cepat", "--tanpa-tunggu"]
        with mock.patch.object(ap, "Stockfish", StockfishPalsu), \
                mock.patch.object(ap, "analisis_partai", analisis), \
                mock.patch.object(ap, "simpan_laporan",
                                  lambda conn, pid, *a, **k: self.disimpan.append(pid)), \
                mock.patch.object(ap, "FOLDER_KEMAJUAN", Path(self.tmp.name)), \
                mock.patch.object(ap, "FOLDER_KLAIM", Path(self.tmp.name) / "klaim"), \
                mock.patch.object(sys, "argv", argv), \
                mock.patch("builtins.print"):
            return ap.main()

    def test_satu_partai_mogok_batch_lanjut_dan_engine_dinyalakan_ulang(self):
        self.assertEqual(self.jalankan(mogok_di={2}), 0)
        self.assertEqual(len(self.urutan), 3, "partai sesudah yang mogok tetap dikerjakan")
        self.assertEqual(len(self.disimpan), 2)
        self.assertEqual(StockfishPalsu.dinyalakan, 2, "engine dinyalakan ulang sekali")

    def test_mogok_beruntun_menghentikan_batch(self):
        with mock.patch.object(ap, "MAKS_MOGOK_BERUNTUN", 2):
            self.jalankan(mogok_di={1, 2})
        self.assertEqual(len(self.urutan), 2, "berhenti setelah 2 kali mogok beruntun")
        self.assertEqual(self.disimpan, [])

    def test_berkas_kemajuan_dihapus_walau_batch_mati_karena_galat(self):
        def meledak(*a, **k):
            raise RuntimeError("tak terduga")
        argv = ["analisis_partai.py", "--db", str(self.db), "--terbaru", "3",
                "--profil", "cepat", "--tanpa-tunggu"]
        with self.assertRaises(RuntimeError), \
                mock.patch.object(ap, "Stockfish", StockfishPalsu), \
                mock.patch.object(ap, "analisis_partai", meledak), \
                mock.patch.object(ap, "FOLDER_KEMAJUAN", Path(self.tmp.name)), \
                mock.patch.object(ap, "FOLDER_KLAIM", Path(self.tmp.name) / "klaim"), \
                mock.patch.object(sys, "argv", argv), \
                mock.patch("builtins.print"):
            ap.main()
        self.assertEqual(list(Path(self.tmp.name).glob(".kemajuan-*.json")), [])



class KlaimPartai(EngineMogok):
    """27 Sep: dua batch searah beriringan menganalisis 66 dari 67 partai yang sama."""

    def klaim_milik(self, pid_partai, isi):
        folder = Path(self.tmp.name) / "klaim"
        folder.mkdir(exist_ok=True)
        (folder / str(pid_partai)).write_text(isi, encoding="utf-8")

    def id_partai(self):
        from app.db import koneksi
        conn = koneksi(self.db)
        ids = [r[0] for r in conn.execute("SELECT id FROM partai ORDER BY id")]
        conn.close()
        return ids

    def test_partai_yang_diklaim_batch_hidup_dilewati(self):
        import subprocess
        lain = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        self.addCleanup(lain.wait)            # dipanen, bukan dibiarkan jadi yatim
        self.addCleanup(lain.kill)            # (cleanup LIFO: kill dulu, lalu wait)
        time_ok = False
        for _ in range(50):                          # tunggu prosesnya benar-benar lahir
            if ap._identitas_proses(lain.pid).count(" "):
                time_ok = True
                break
            import time
            time.sleep(0.1)
        ids = self.id_partai()
        self.klaim_milik(ids[1], ap._identitas_proses(lain.pid) if time_ok else str(lain.pid))
        self.jalankan(mogok_di=set())
        self.assertEqual(len(self.urutan), 2)
        self.assertNotIn(ids[1], self.disimpan)
        # klaim milik batch lain tidak disentuh; klaim sendiri dilepas
        self.assertEqual(sorted(p.name for p in (Path(self.tmp.name) / "klaim").iterdir()),
                         [str(ids[1])])

    def test_klaim_batch_mati_diambil_alih(self):
        ids = self.id_partai()
        self.klaim_milik(ids[0], "999999 1")         # PID yang tidak ada
        self.jalankan(mogok_di=set())
        self.assertEqual(len(self.urutan), 3)

    def test_klaim_pid_bekas_pakai_diambil_alih(self):
        """PID klaim masih ada, tapi prosesnya lahir di waktu lain = orang lain."""
        import os
        ids = self.id_partai()
        self.klaim_milik(ids[0], f"{os.getppid()} 12345")
        self.jalankan(mogok_di=set())
        self.assertEqual(len(self.urutan), 3)


class Perkiraan(unittest.TestCase):
    """Log 27 Sep: sisa melompat 69 -> 416 menit setelah satu kali macet."""

    def test_sisa_dari_partai_terbaru_bukan_sejak_mulai(self):
        daftar = [{"jumlah_langkah": 40}] * 10              # 80 ply per partai
        p = ap.PerkiraanSisa(daftar, jendela=3)
        self.assertIsNone(p.menit(1))                      # belum ada yang dianalisis
        p.catat(240.0, 40)                                 # partai macet 4 menit
        for _ in range(3):
            p.catat(4.0, 40)                               # lalu normal lagi
        # jendela 3 terakhir: 0,05 dtk/ply x 6 partai sisa x 80 ply = 24 dtk
        self.assertAlmostEqual(p.menit(4), 0.4)

    def test_perkiraan_awal_hanya_riwayat_terbaru(self):
        import sqlite3
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE partai (id INTEGER PRIMARY KEY, jumlah_langkah INT)")
        conn.execute("CREATE TABLE analisis (id INTEGER PRIMARY KEY, partai_id INT, profil TEXT, durasi_detik REAL)")
        for i in range(1, 1001):
            conn.execute("INSERT INTO partai VALUES (?, 50)", (i,))
            lama = i <= 500                                 # 500 lama cepat, 500 baru lambat
            conn.execute("INSERT INTO analisis VALUES (?, ?, 'cepat', ?)", (i, i, 2.5 if lama else 7.0))
        with mock.patch.object(ap, "RIWAYAT_PERKIRAAN", 500):
            self.assertAlmostEqual(ap.detik_per_ply_terukur(conn, "cepat"), 0.07)


if __name__ == "__main__":
    unittest.main()
