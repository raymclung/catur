"""
Uji app/banding_pemain.py — bandingkan dua pemain berdampingan.

Yang dijaga:
  * skor dan head-to-head dihitung dari sudut pemain yang benar (A sebagai
    Putih maupun Hitam);
  * partai belum selesai ('*') tidak ikut ukuran gaya;
  * Elo yang kosong tidak ikut dirata-rata (bukan dihitung 0);
  * langkah pertama dari tabel posisi, dengan jumlah partai terindeks;
  * halaman Banding tertaut dari menu semua halaman (fitur tanpa tautan sama
    saja tidak ada — pelajaran Phase 6).
"""

import sys
import tempfile
import unittest
from pathlib import Path

AKAR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(AKAR))

from app.banding_pemain import banding, gaya_pemain, head_to_head, langkah_pertama
from app.db import buat_skema, koneksi, sekarang


class Banding(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = koneksi(Path(self.tmp.name) / "uji.db")
        buat_skema(self.conn)
        t = sekarang()
        for i, nama in ((1, "A"), (2, "B"), (3, "C")):
            self.conn.execute("INSERT INTO pemain(id,nama,nama_normal,dibuat_pada) VALUES(?,?,?,?)",
                              (i, nama, nama.lower(), t))
        self.conn.commit()
        self.n = 0

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def partai(self, putih, hitam, hasil, langkah=40, elo_putih=2700, elo_hitam=2700,
               tanggal="2026.01.01", format_="klasik", pertama=None):
        self.n += 1
        self.conn.execute(
            "INSERT INTO partai(id,putih_id,hitam_id,hasil,pgn,sumber,hash_unik,diimpor_pada,"
            "tanggal,format,jumlah_langkah,elo_putih,elo_hitam,eco) "
            "VALUES(?,?,?,?,'','uji',?,?,?,?,?,?,?,'A20')",
            (self.n, putih, hitam, hasil, f"h{self.n}", sekarang(), tanggal, format_, langkah,
             elo_putih, elo_hitam))
        if pertama:
            self.conn.execute("INSERT INTO posisi(fen_kunci,partai_id,ply,langkah_uci,langkah_san) "
                              "VALUES('awal',?,0,'x',?)", (self.n, pertama))
        self.conn.commit()
        return self.n

    def test_skor_dari_sudut_pemain_di_kedua_warna(self):
        self.partai(1, 2, "1-0")          # A menang sebagai Putih
        self.partai(2, 1, "1-0")          # A kalah sebagai Hitam
        self.partai(2, 1, "0-1")          # A menang sebagai Hitam
        self.partai(1, 3, "1/2-1/2")
        g = gaya_pemain(self.conn, 1)
        self.assertEqual((g["menang"], g["remis"], g["kalah"]), (2, 1, 1))
        self.assertEqual(g["skor_persen"], 62.5)
        self.assertEqual(g["remis_persen"], 25.0)

    def test_partai_belum_selesai_tidak_dihitung(self):
        self.partai(1, 2, "*")
        self.partai(1, 2, "1-0")
        self.assertEqual(gaya_pemain(self.conn, 1)["partai"], 1)

    def test_menang_cepat_dan_partai_panjang(self):
        self.partai(1, 2, "1-0", langkah=25)     # menang cepat
        self.partai(1, 2, "1-0", langkah=31)     # menang, bukan cepat
        self.partai(1, 2, "0-1", langkah=20)     # kalah cepat: bukan "menang cepat"
        self.partai(1, 2, "1/2-1/2", langkah=70)  # panjang
        g = gaya_pemain(self.conn, 1)
        self.assertEqual(g["menang_cepat_persen"], 50.0)
        self.assertEqual(g["panjang_persen"], 25.0)
        self.assertEqual(g["rata_langkah"], 36.5)

    def test_elo_kosong_tidak_dirata_rata(self):
        self.partai(1, 2, "1-0", elo_hitam=2600)
        self.partai(1, 2, "1-0", elo_hitam=None)
        g = gaya_pemain(self.conn, 1)
        self.assertEqual(g["rata_elo_lawan"], 2600.0)
        self.assertEqual(g["partai_ber_elo"], 1)

    def test_lawan_lebih_kuat_dan_lebih_lemah(self):
        self.partai(1, 2, "0-1", elo_putih=2600, elo_hitam=2700)      # lawan lebih kuat, kalah
        self.partai(2, 1, "1/2-1/2", elo_putih=2750, elo_hitam=2600)  # lawan lebih kuat, remis
        self.partai(1, 3, "1-0", elo_putih=2600, elo_hitam=2500)      # lawan lebih lemah, menang
        g = gaya_pemain(self.conn, 1)
        self.assertEqual(g["lawan_lebih_kuat"], {"partai": 2, "skor_persen": 25.0})
        self.assertEqual(g["lawan_lebih_lemah"], {"partai": 1, "skor_persen": 100.0})

    def test_saringan_format(self):
        self.partai(1, 2, "1-0", format_="klasik")
        self.partai(1, 2, "1-0", format_="blitz")
        self.assertEqual(gaya_pemain(self.conn, 1, "blitz")["partai"], 1)
        self.assertEqual(head_to_head(self.conn, 1, 2, "blitz")["total"], 1)

    def test_langkah_pertama_hanya_sebagai_putih(self):
        self.partai(1, 2, "1-0", pertama="e4")
        self.partai(1, 2, "1-0", pertama="e4")
        self.partai(1, 2, "1-0", pertama="c4")
        self.partai(2, 1, "1-0", pertama="d4")   # A sebagai Hitam: tidak dihitung
        lp = langkah_pertama(self.conn, 1)
        self.assertEqual(lp["terindeks"], 3)
        self.assertEqual(lp["langkah"][0], {"san": "e4", "partai": 2, "persen": 66.7})

    def test_head_to_head(self):
        self.partai(1, 2, "1-0", tanggal="2026.01.01")
        self.partai(2, 1, "1/2-1/2", tanggal="2026.03.01")
        self.partai(2, 1, "1-0", tanggal="2026.02.01")
        self.partai(1, 3, "1-0")                 # bukan antara A dan B
        h = head_to_head(self.conn, 1, 2)
        self.assertEqual((h["menang_a"], h["remis"], h["menang_b"], h["total"]), (1, 1, 1, 3))
        self.assertEqual(h["skor_a"], 1.5)
        self.assertEqual([g["tanggal"] for g in h["partai"]], ["2026.03.01", "2026.02.01", "2026.01.01"])
        self.assertEqual(h["partai"][0]["warna_a"], "hitam")
        self.assertFalse(h["dipotong"])

    def test_banding_pemain_tidak_ada(self):
        self.assertIsNone(banding(self.conn, 1, 99))
        d = banding(self.conn, 1, 2)
        self.assertEqual((d["a"]["nama"], d["b"]["nama"]), ("A", "B"))
        self.assertEqual(d["head_to_head"]["total"], 0)


class Tersambung(unittest.TestCase):
    def test_endpoint_terdaftar(self):
        from app.main import aplikasi
        self.assertIn("/api/banding", aplikasi.openapi()["paths"])

    def test_semua_halaman_bernav_menaut_ke_banding(self):
        from app.main import FOLDER_WEB
        kurang = [p.name for p in sorted(FOLDER_WEB.glob("*.html"))
                  if 'class="nav-utama"' in (isi := p.read_text(encoding="utf-8"))
                  and 'href="/static/banding.html"' not in isi]
        self.assertEqual(kurang, [])

    def test_api_menolak_masukan_salah(self):
        # Dipanggil langsung: TestClient butuh httpx, yang bukan dependency proyek.
        # Kedua penolakan terjadi sebelum database dibuka.
        from fastapi import HTTPException
        from app.banding_pemain import api_banding
        for a, b, f in ((1, 1, None), (1, 2, "kilat")):
            with self.assertRaises(HTTPException) as e:
                api_banding(a, b, f)
            self.assertEqual(e.exception.status_code, 422)


if __name__ == "__main__":
    unittest.main()
