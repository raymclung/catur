"""
Uji app/mentor.py — kurikulum 4 minggu dari partai satu GM.

Yang dijaga:
  * tiap langkah sendiri masuk tepat satu ember: "konversi" kalau skor
    engine sebelum langkah >= +2,00, selain itu menurut fase;
  * ukuran dihitung dari sudut warna pemain itu (Putih maupun Hitam);
  * urutan minggu = kesalahan berlebih (selisih × porsi langkah), BUKAN rasio
    — rasio membuat minggu 1 selalu pembukaan karena GM nyaris tak pernah
    salah di pembukaan;
  * kriteria tiap tema, variasi lawan, dan satu partai hanya di satu minggu;
  * halaman tertaut dari menu semua halaman.
"""

import sys
import tempfile
import unittest
from pathlib import Path

import chess

AKAR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(AKAR))

from app.db import buat_skema, koneksi, sekarang
from app.mentor import kurikulum, pilih_partai, profil_kesalahan, urutan_tema
from app.posisi import kunci_dari_fen_pengguna

ENGLISH = ["c4", "e5", "Nc3", "Nf6", "g3", "d5"]
LAIN = ["e4", "e5", "Nf3", "Nc6", "Bb5", "a6"]


class Dasar(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = koneksi(Path(self.tmp.name) / "uji.db")
        buat_skema(self.conn)
        t = sekarang()
        for i, nama in ((1, "Gm"), (2, "Lawan A"), (3, "Lawan B"), (4, "Lawan C"), (9, "Saya")):
            self.conn.execute("INSERT INTO pemain(id,nama,nama_normal,dibuat_pada,akun_pribadi) VALUES(?,?,?,?,?)",
                              (i, nama, nama.lower(), t, 1 if i == 9 else 0))
        self.conn.commit()
        self.n = 0

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def partai(self, putih, hitam, hasil, evals, klas=None, awal=LAIN, elo_lawan=2700, sebelum=None):
        """
        evals = skor engine SUDUT PUTIH sesudah tiap ply; panjang partai = len(evals).
        klas = {ply: klasifikasi}; sebelum = {ply: eval_sebelum_cp} (sudut yang jalan).
        """
        self.n += 1
        c = self.conn
        c.execute("INSERT INTO partai(id,putih_id,hitam_id,hasil,pgn,sumber,hash_unik,diimpor_pada,"
                  "tanggal,jumlah_langkah,elo_putih,elo_hitam) VALUES(?,?,?,?,'','twic',?,?,'2024.01.01',?,?,?)",
                  (self.n, putih, hitam, hasil, f"h{self.n}", sekarang(), len(evals) // 2, elo_lawan, elo_lawan))
        c.execute("INSERT INTO analisis(partai_id,engine,depth,profil,dianalisis_pada) VALUES(?,'uji',10,'cepat',?)",
                  (self.n, sekarang()))
        b = chess.Board()
        for ply, san in enumerate(awal):
            m = b.parse_san(san)
            c.execute("INSERT INTO posisi(fen_kunci,partai_id,ply,langkah_uci,langkah_san) VALUES(?,?,?,?,?)",
                      (kunci_dari_fen_pengguna(b.fen()), self.n, ply, m.uci(), san))
            b.push(m)
        prev = 0
        for ply, ev in enumerate(evals):
            warna = "putih" if ply % 2 == 0 else "hitam"
            ev_sebelum = (sebelum or {}).get(ply, prev if warna == "putih" else -prev)
            c.execute("INSERT INTO analisis_langkah(partai_id,ply,nomor_langkah,warna,san,uci,eval_sebelum_cp,"
                      "eval_sesudah_cp,eval_putih_cp,kehilangan_cp,klasifikasi) VALUES(?,?,?,?,'x','x',?,0,?,0,?)",
                      (self.n, ply, ply // 2 + 1, warna, ev_sebelum, ev, (klas or {}).get(ply, "baik")))
            prev = ev
        c.commit()
        return self.n


class Profil(Dasar):
    def test_ember_dan_sudut_warna(self):
        # Putih: blunder di ply 0 (pembukaan) dan ply 30 (tengah).
        p1 = self.partai(1, 2, "1-0", [0] * 40, klas={0: "blunder", 30: "mistake", 31: "blunder"})
        # Hitam: skor sebelum langkah ply 41 = +300 untuk Hitam -> ember konversi.
        p2 = self.partai(2, 1, "0-1", [0] * 40 + [-300, -300, -300], klas={41: "mistake"},
                         sebelum={41: 300})
        ukuran, fitur = profil_kesalahan(self.conn, {p1: {"warna": "putih", "hasil": "1-0"},
                                                     p2: {"warna": "hitam", "hasil": "0-1"}})
        e = ukuran["ember"]
        self.assertEqual(e["pembukaan"]["kesalahan"], 1)
        self.assertEqual(e["posisional"]["kesalahan"], 1)      # ply 31 milik lawan, tidak dihitung
        self.assertEqual(e["konversi"], {"langkah": 1, "kesalahan": 1, "per_100": 100.0})
        self.assertEqual(ukuran["langkah"], 20 + 21)        # Hitam: ply ganjil 1..41
        self.assertEqual(fitur[p1]["lawan_blunder"], 1)
        self.assertEqual(fitur[p2]["unggul_ply"], 40)          # -300 sudut Putih = +300 untuk Hitam

    def test_persen_gagal_konversi(self):
        a = self.partai(1, 2, "1-0", [0, 250, 250])           # unggul, menang
        b = self.partai(1, 2, "1/2-1/2", [0, 250, 0])         # unggul, remis = gagal
        c = self.partai(1, 2, "0-1", [0, 0, 0])               # tidak pernah unggul
        ukuran, _ = profil_kesalahan(self.conn, {p: {"warna": "putih", "hasil": h}
                                                 for p, h in ((a, "1-0"), (b, "1/2-1/2"), (c, "0-1"))})
        self.assertEqual(ukuran["konversi"], {"pernah_unggul": 2, "gagal": 1, "persen_gagal": 50.0})


class Urutan(unittest.TestCase):
    @staticmethod
    def ukuran(per100, langkah):
        return {"langkah": sum(langkah.values()),
                "ember": {k: {"langkah": langkah[k], "kesalahan": 0, "per_100": per100[k]} for k in per100}}

    def test_kesalahan_berlebih_bukan_rasio(self):
        # Pembukaan: rasio raksasa (5 / 0,2 = 25x) tapi porsi langkahnya kecil.
        saya = self.ukuran({"pembukaan": 5.0, "konversi": 8.0, "posisional": 10.0, "endgame": 7.0},
                           {"pembukaan": 100, "konversi": 100, "posisional": 700, "endgame": 100})
        gm = self.ukuran({"pembukaan": 0.2, "konversi": 4.0, "posisional": 2.0, "endgame": 2.0},
                         {"pembukaan": 1, "konversi": 1, "posisional": 1, "endgame": 1})
        u = urutan_tema(saya, gm)
        self.assertEqual([t["tema"] for t in u], ["posisional", "endgame", "pembukaan", "konversi"])
        self.assertEqual(u[0]["berlebih"], round((10.0 - 2.0) * 0.7, 2))
        self.assertEqual(u[0]["porsi_langkah_saya"], 70.0)

    def test_tanpa_data_di_belakang(self):
        saya = self.ukuran({"pembukaan": None, "konversi": 1.0, "posisional": 1.0, "endgame": 1.0},
                           {"pembukaan": 0, "konversi": 1, "posisional": 1, "endgame": 1})
        gm = self.ukuran({"pembukaan": 0.1, "konversi": 0.1, "posisional": 0.1, "endgame": 0.1},
                         {"pembukaan": 1, "konversi": 1, "posisional": 1, "endgame": 1})
        self.assertEqual(urutan_tema(saya, gm)[-1]["tema"], "pembukaan")


class PilihPartai(Dasar):
    def pilih(self):
        partai = {r[0]: {"warna": "putih" if r[1] == 1 else "hitam", "hasil": r[2]}
                  for r in self.conn.execute("SELECT id, putih_id, hasil FROM partai")}
        _, fitur = profil_kesalahan(self.conn, partai)
        return pilih_partai(self.conn, 1, partai, fitur)

    def test_kriteria_tiap_tema(self):
        konversi = self.partai(1, 2, "1-0", [0] * 20 + [250] * 40)          # unggul ply 20, 40 ply lagi
        runtuh = self.partai(1, 2, "1-0", [0] * 20 + [250] * 5)             # menang terlalu cepat
        goyah = self.partai(1, 2, "1-0", [0] * 20 + [250, 50] + [250] * 40)  # turun di bawah +1
        posisional = self.partai(1, 3, "1-0", [0] * 80, klas={1: "mistake"})
        lawan_blunder = self.partai(1, 3, "1-0", [0] * 80, klas={1: "blunder"})
        endgame = self.partai(1, 4, "1-0", [0] * 80 + [100] + [0] * 30)
        sudah_menang = self.partai(1, 4, "1-0", [0] * 80 + [500] + [500] * 30)
        kalah = self.partai(1, 4, "0-1", [0] * 120)
        english = self.partai(1, 2, "1-0", [0] * 10, awal=ENGLISH)
        d = self.pilih()
        ids = {t: [p["partai_id"] for p in d[t]["partai"]] for t in d}
        self.assertIn(konversi, ids["konversi"])
        self.assertNotIn(runtuh, ids["konversi"])
        self.assertNotIn(goyah, ids["konversi"])
        self.assertIn(posisional, ids["posisional"])
        self.assertNotIn(lawan_blunder, ids["posisional"])
        self.assertIn(endgame, ids["endgame"])
        self.assertNotIn(sudah_menang, ids["endgame"])
        self.assertEqual(ids["pembukaan"], [english])
        semua = [p for daftar in ids.values() for p in daftar]
        self.assertNotIn(kalah, semua)
        self.assertEqual(len(semua), len(set(semua)))                       # satu partai, satu minggu
        self.assertIn("English", d["pembukaan"]["partai"][0]["alasan"])

    def test_paling_banyak_dua_per_lawan(self):
        for _ in range(4):
            self.partai(1, 2, "1-0", [0] * 20 + [250] * 40)
        self.partai(1, 3, "1-0", [0] * 20 + [250] * 40, elo_lawan=2500)
        d = self.pilih()
        lawan = [p["lawan"] for p in d["konversi"]["partai"]]
        self.assertEqual(lawan.count("Lawan A"), 2)
        self.assertIn("Lawan B", lawan)
        self.assertEqual(d["konversi"]["kandidat"], 5)


class Kurikulum(Dasar):
    def test_gm_tipis_dan_tidak_ada(self):
        self.partai(1, 2, "1-0", [0] * 10)
        k = kurikulum(self.conn, 1, [9])
        self.assertFalse(k["cukup"])
        self.assertEqual(k["minggu"], [])
        self.assertIsNone(kurikulum(self.conn, 99, [9]))


class Tersambung(unittest.TestCase):
    def test_endpoint_terdaftar(self):
        from app.main import aplikasi
        self.assertIn("/api/mentor", aplikasi.openapi()["paths"])

    def test_semua_halaman_bernav_menaut_ke_mentor(self):
        from app.main import FOLDER_WEB
        kurang = [p.name for p in sorted(FOLDER_WEB.glob("*.html"))
                  if 'class="nav-utama"' in (isi := p.read_text(encoding="utf-8"))
                  and 'href="/static/mentor.html"' not in isi]
        self.assertEqual(kurang, [])

    def test_api_menolak_lawan_salah(self):
        from fastapi import HTTPException
        from app.mentor import api_mentor
        with self.assertRaises(HTTPException) as e:
            api_mentor(1, [9], "salah")
        self.assertEqual(e.exception.status_code, 422)


if __name__ == "__main__":
    unittest.main()
