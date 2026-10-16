"""
Uji app/banding_gm.py — kamu vs GM dari posisi yang sama.

Yang dijaga:
  * titik keluar buku = posisi TERDALAM yang masih punya cukup partai GM;
  * hanya partai GM (sumber GM, antar-manusia) yang dihitung sebagai buku —
    partai akun sendiri di posisi itu tidak menambah "partai GM";
  * skor sendiri dan skor GM dari sudut WARNA SAYA, dan "vs rata-rata"
    memakai rata-rata partai yang sama-sama dihitung;
  * kehilangan cp hanya di jendela sesudah titik, hanya langkah warna sendiri;
  * partai yang keluar buku sebelum PLY_MIN dicatat, bukan dibuang diam-diam;
  * halaman tertaut dari menu semua halaman.
"""

import sys
import tempfile
import unittest
from pathlib import Path

import chess

AKAR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(AKAR))

from app.banding_gm import hitung, rincian
from app.db import buat_skema, koneksi, sekarang
from app.posisi import kunci_dari_fen_pengguna

# 1.c4 e5 2.Nc3 Nf6 3.g3 d5 4.cxd5 Nxd5 = buku (English); sesudahnya bercabang.
BUKU = "c4 e5 Nc3 Nf6 g3 d5 cxd5 Nxd5".split()


class BandingGm(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = koneksi(Path(self.tmp.name) / "uji.db")
        buat_skema(self.conn)
        t = sekarang()
        c = self.conn
        c.execute("INSERT INTO pemain(id,nama,nama_normal,dibuat_pada,akun_pribadi) VALUES(1,'Saya','saya',?,1)", (t,))
        c.execute("INSERT INTO pemain(id,nama,nama_normal,dibuat_pada,gelar) VALUES(2,'Lawan','lawan',?,'GM')", (t,))
        c.execute("INSERT INTO pemain(id,nama,nama_normal,dibuat_pada) VALUES(3,'GmA','gma',?)", (t,))
        c.execute("INSERT INTO pemain(id,nama,nama_normal,dibuat_pada) VALUES(4,'GmB','gmb',?)", (t,))
        c.commit()
        self.n = 0

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def partai(self, putih, hitam, hasil, langkah, sumber="twic", tanggal="2020.01.01", analisis=None):
        """Partai + indeks posisi. analisis = {ply: kehilangan_cp} untuk partai teranalisis."""
        self.n += 1
        c = self.conn
        c.execute("INSERT INTO partai(id,putih_id,hitam_id,hasil,pgn,sumber,hash_unik,diimpor_pada,"
                  "tanggal,format,jumlah_langkah,elo_putih,elo_hitam) "
                  "VALUES(?,?,?,?,'',?,?,?,?,'blitz',?,2700,2700)",
                  (self.n, putih, hitam, hasil, sumber, f"h{self.n}", sekarang(), tanggal, len(langkah) // 2))
        b = chess.Board()
        for ply, san in enumerate(langkah):
            m = b.parse_san(san)
            c.execute("INSERT INTO posisi(fen_kunci,partai_id,ply,langkah_uci,langkah_san) VALUES(?,?,?,?,?)",
                      (kunci_dari_fen_pengguna(b.fen()), self.n, ply, m.uci(), san))
            b.push(m)
        if analisis is not None:
            c.execute("INSERT INTO analisis(partai_id,engine,depth,profil,dianalisis_pada) "
                      "VALUES(?,'uji',10,'cepat',?)", (self.n, sekarang()))
            for ply, san in enumerate(langkah):
                c.execute("INSERT INTO analisis_langkah(partai_id,ply,nomor_langkah,warna,san,uci,"
                          "eval_sebelum_cp,eval_sesudah_cp,eval_putih_cp,kehilangan_cp,klasifikasi) "
                          "VALUES(?,?,?,?,?,'x',0,0,0,?,?)",
                          (self.n, ply, ply // 2 + 1, "putih" if ply % 2 == 0 else "hitam", san,
                           analisis.get(ply, 0), "blunder" if analisis.get(ply, 0) >= 300 else "baik"))
        c.commit()
        return self.n

    def gm(self, jumlah, hasil="1-0", lanjut=("Bg2", "Nb6")):
        for _ in range(jumlah):
            self.partai(3, 4, hasil, BUKU + list(lanjut))

    def test_titik_keluar_buku_terdalam(self):
        self.gm(3)                                         # buku sampai ply 10 (Bg2 Nb6)
        for _ in range(3):
            self.partai(1, 2, "0-1", BUKU + ["Nf3", "Nc6"], sumber="lichess-user")
        r = hitung(self.conn, [1], lawan="semua", min_partai_gm=3, min_partai_saya=2)
        self.assertEqual(len(r["posisi"]), 1)
        p = r["posisi"][0]
        self.assertEqual(p["ply"], 8)                      # posisi sesudah 4...Nxd5; Nf3 sudah keluar buku
        self.assertEqual(p["jalur"], BUKU)
        self.assertTrue(p["giliran_saya"])
        self.assertEqual(p["saya"]["langkah"][0]["san"], "Nf3")
        self.assertEqual(p["pembukaan"], "English")

    def test_partai_akun_tidak_dihitung_sebagai_buku(self):
        self.gm(2)
        for _ in range(5):                                 # partaiku sendiri jauh lebih banyak
            self.partai(1, 2, "1-0", BUKU + ["Bg2", "Nb6"], sumber="lichess-user")
        r = hitung(self.conn, [1], lawan="semua", min_partai_gm=3, min_partai_saya=1)
        self.assertEqual(r["posisi"], [])
        self.assertEqual(r["ringkasan"]["keluar_sebelum_ply_min"], 5)
        self.assertEqual(r["ringkasan"]["keluar_awal_teratas"][0][1], 5)

    def test_skor_dari_sudut_warna_saya_dan_selisih_rata_rata(self):
        self.gm(4, hasil="0-1")                            # Hitam (= warnaku) menang di buku GM
        for hasil in ("0-1", "0-1", "1-0"):                # aku Hitam: 2 menang, 1 kalah di titik ini
            self.partai(2, 1, hasil, BUKU + ["Nf3", "Nc6"], sumber="lichess-user")
        for hasil in ("1-0", "1-0", "1-0"):                # titik lain (keluar di ply 6): 3 kalah
            self.partai(2, 1, hasil, BUKU[:6] + ["e4", "dxe4"], sumber="lichess-user")
        r = hitung(self.conn, [1], lawan="semua", min_partai_gm=4, min_partai_saya=3)
        self.assertEqual(r["ringkasan"]["skor_rata_saya"], round(100 * 2 / 6, 1))
        per = {p["ply"]: p for p in r["posisi"]}
        self.assertEqual(per[8]["warna"], "hitam")
        self.assertEqual(per[8]["saya"]["skor"], 66.7)
        self.assertEqual(per[8]["gm"]["skor"], 100.0)
        self.assertEqual(per[8]["selisih_dari_rata"], round(66.7 - 33.3, 1))
        self.assertEqual(per[6]["saya"]["skor"], 0.0)
        # Peringkat: poin hilang terbesar (ply 6) di atas.
        self.assertEqual(r["posisi"][0]["ply"], 6)

    def test_kehilangan_cp_hanya_jendela_dan_warna_sendiri(self):
        self.gm(3)
        lanjut = ["Nf3", "Nc6", "Bg2", "Nb6"]
        # ply 4 (sebelum titik) dan ply 9 (lawan) tidak boleh terhitung
        self.partai(1, 2, "0-1", BUKU + lanjut, sumber="lichess-user", analisis={4: 500, 8: 40, 9: 900, 10: 20})
        self.partai(1, 2, "0-1", BUKU + lanjut, sumber="lichess-user")     # belum dianalisis
        r = hitung(self.conn, [1], lawan="semua", min_partai_gm=3, min_partai_saya=2)
        e = r["posisi"][0]["saya"]["engine"]
        self.assertEqual(e["partai"], 1)
        self.assertEqual(e["langkah"], 2)                  # ply 8 dan 10 (Putih), dalam jendela
        self.assertEqual(e["rata_kehilangan"], 30.0)

    def test_partai_saya_dan_kesalahan_pertama(self):
        self.gm(3)
        lanjut = ["Nf3", "Nc6", "Bg2", "Nb6", "O-O", "Be7"]
        # ply 4 (sebelum titik) dan ply 9 (lawan) bukan "kesalahan pertamaku"; ply 10 ya.
        kalah = self.partai(1, 2, "0-1", BUKU + lanjut, sumber="lichess-user", tanggal="2020.01.01",
                            analisis={4: 500, 9: 900, 10: 400, 12: 350})
        menang = self.partai(1, 2, "1-0", BUKU + lanjut, sumber="lichess-user", tanggal="2021.01.01",
                             analisis={})
        belum = self.partai(1, 2, "1/2-1/2", BUKU + lanjut, sumber="lichess-user", tanggal="2022.01.01")
        r = hitung(self.conn, [1], lawan="semua", min_partai_gm=3, min_partai_saya=3)
        daftar = r["posisi"][0]["partai_saya"]
        self.assertEqual([x["partai_id"] for x in daftar], [kalah, belum, menang])   # kalah dulu, lalu terbaru
        self.assertEqual(daftar[0]["hasil"], "kalah")
        self.assertEqual(daftar[0]["kesalahan_pertama"]["ply"], 10)
        self.assertEqual(daftar[0]["kesalahan_pertama"]["san"], "Bg2")     # ply 10 = 6.Bg2
        self.assertEqual(daftar[0]["kesalahan_pertama"]["klasifikasi"], "blunder")
        self.assertFalse(daftar[1]["dianalisis"])
        self.assertIsNone(daftar[1]["kesalahan_pertama"])
        self.assertTrue(daftar[2]["dianalisis"])                          # teranalisis, tanpa kesalahan
        self.assertIsNone(daftar[2]["kesalahan_pertama"])

    def test_saringan_lawan(self):
        self.gm(3)
        self.conn.execute("INSERT INTO pemain(id,nama,nama_normal,dibuat_pada) VALUES(5,'Lemah','lemah',?)", (sekarang(),))
        self.partai(1, 5, "1-0", BUKU + ["Nf3"], sumber="lichess-user")
        self.conn.execute("UPDATE partai SET elo_hitam = 1500 WHERE id = ?", (self.n,))
        self.conn.commit()
        self.assertEqual(hitung(self.conn, [1], lawan="ketentuan")["ringkasan"]["partai"], 0)
        self.assertEqual(hitung(self.conn, [1], lawan="semua")["ringkasan"]["partai"], 1)

    def test_rincian(self):
        self.gm(2, hasil="1-0")
        self.gm(1, hasil="0-1", lanjut=("Nf3", "Nc6"))
        b = chess.Board()
        for san in BUKU:
            b.push_san(san)
        r = rincian(self.conn, kunci_dari_fen_pengguna(b.fen()), "putih")
        self.assertEqual([x["san"] for x in r["langkah_gm"]], ["Bg2", "Nf3"])
        self.assertEqual(len(r["contoh_gm"]), 3)
        self.assertEqual(r["contoh_gm"][0]["ply"], 8)
        self.assertEqual(r["engine_gm"]["partai"], 0)

    def test_tanpa_pemain(self):
        self.assertEqual(hitung(self.conn, [])["posisi"], [])


class Tersambung(unittest.TestCase):
    def test_endpoint_terdaftar(self):
        from app.main import aplikasi
        jalur = aplikasi.openapi()["paths"]
        self.assertIn("/api/banding-gm", jalur)
        self.assertIn("/api/banding-gm/rincian", jalur)

    def test_semua_halaman_bernav_menaut_ke_vs_gm(self):
        from app.main import FOLDER_WEB
        kurang = [p.name for p in sorted(FOLDER_WEB.glob("*.html"))
                  if 'class="nav-utama"' in (isi := p.read_text(encoding="utf-8"))
                  and 'href="/static/posisi-gm.html"' not in isi]
        self.assertEqual(kurang, [])

    def test_api_menolak_masukan_salah(self):
        from fastapi import HTTPException
        from app.banding_gm import api_banding_gm
        for lawan, f in (("salah", None), ("semua", "kilat")):
            with self.assertRaises(HTTPException) as e:
                api_banding_gm([1], lawan, f)
            self.assertEqual(e.exception.status_code, 422)


if __name__ == "__main__":
    unittest.main()
