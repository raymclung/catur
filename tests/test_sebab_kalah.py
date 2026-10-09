"""
Uji app/sebab_kalah.py — penggolongan sebab kekalahan yang dipindah dari
web/kekalahan.js ke server (13 Sep 2026).

Yang dijaga di sini terutama aturan yang pernah salah:
  * jam habis saat posisi SUDAH kalah bukan "kalah waktu" (laporan Ray);
  * arah mat dibaca dari sudut pemain, bukan dari tanda mat_sesudah mentah;
  * blunder yang dimainkan sangat cepat digolongkan missclick.
"""

import sys
import tempfile
import unittest
from pathlib import Path

AKAR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(AKAR))

from app.db import buat_skema, koneksi, sekarang
from app.sebab_kalah import (detik_dipakai, golongkan, jam_per_ply, jam_sendiri,
                             kalah_waktu, kekalahan, pembukaan_kalah, posisi_akhir, tag_pgn)

PGN_WAKTU = """[Event "Rated blitz game"]
[Site "https://lichess.org/abc"]
[White "Saya"]
[Black "Lawan"]
[Result "0-1"]
[TimeControl "180+0"]
[Termination "Time forfeit"]

1. e4 { [%clk 0:03:00] } e5 { [%clk 0:03:00] } 2. Nf3 { [%clk 0:02:55] } Nc6 { [%clk 0:02:50] } 0-1
"""

PGN_BIASA = PGN_WAKTU.replace('[Termination "Time forfeit"]', '[Termination "Normal"]')


def langkah(ply, san, hilang=0, eval_sesudah=20, mat=None, klas="terbaik", warna="putih"):
    return {"ply": ply, "nomor_langkah": ply // 2 + 1, "warna": warna, "san": san,
            "kehilangan_cp": hilang, "klasifikasi": klas, "eval_sesudah_cp": eval_sesudah,
            "mat_sesudah": mat, "langkah_terbaik_san": "Qxe6"}


class Pgn(unittest.TestCase):
    def test_tag_dan_jam(self):
        t = tag_pgn(PGN_WAKTU)
        self.assertEqual(t["TimeControl"], "180+0")
        self.assertTrue(kalah_waktu(t))
        self.assertFalse(kalah_waktu(tag_pgn(PGN_BIASA)))
        self.assertEqual(jam_per_ply(PGN_WAKTU), [180.0, 180.0, 175.0, 170.0])

    def test_kalah_waktu_mengenali_beberapa_bentuk(self):
        for teks in ("Time forfeit", "Black won on time", "menang karena waktu"):
            self.assertTrue(kalah_waktu({"Termination": teks}))
        self.assertFalse(kalah_waktu({"Termination": "Normal"}))
        self.assertFalse(kalah_waktu({}))

    def test_detik_dipakai(self):
        jam = jam_per_ply(PGN_WAKTU)
        # ply 0: dari waktu awal 180 ke 180 = 0 detik
        self.assertEqual(detik_dipakai(jam, 0, "180+0"), 0.0)
        # ply 2 (langkah Putih kedua): 180 -> 175 = 5 detik
        self.assertEqual(detik_dipakai(jam, 2, "180+0"), 5.0)
        # inkremen ikut dihitung
        self.assertEqual(detik_dipakai(jam, 2, "180+2"), 7.0)
        self.assertIsNone(detik_dipakai(jam, 99, "180+0"))
        self.assertIsNone(detik_dipakai([], 0, "180+0"))

    def test_jam_sendiri_ambil_ply_warna_itu(self):
        jam = [180.0, 170.0, 175.0, 160.0]
        self.assertEqual(jam_sendiri(jam, "putih", None), 175.0)   # ply genap terakhir
        self.assertEqual(jam_sendiri(jam, "hitam", None), 160.0)   # ply ganjil terakhir
        self.assertEqual(jam_sendiri(jam, "putih", 3), 175.0)      # mundur ke ply 2
        self.assertIsNone(jam_sendiri([], "putih", None))


class PosisiAkhir(unittest.TestCase):
    def test_arah_mat_dari_sudut_pemain(self):
        """mat_sesudah bertanda dari sudut PUTIH; yang jalan Hitam berarti kena mat."""
        p = posisi_akhir(langkah(9, "Kh7", eval_sesudah=-100000, mat=2, warna="hitam"))
        self.assertTrue(p["sudah_kalah"])
        self.assertIn("kena mat dalam 2", p["nilai"])

        p = posisi_akhir(langkah(8, "Qxh4", eval_sesudah=100000, mat=2, warna="putih"))
        self.assertFalse(p["sudah_kalah"])
        self.assertIn("mat untuk saya", p["nilai"])

    def test_nilai_biasa(self):
        self.assertFalse(posisi_akhir(langkah(0, "e4", eval_sesudah=40))["sudah_kalah"])
        self.assertTrue(posisi_akhir(langkah(0, "e4", eval_sesudah=-500))["sudah_kalah"])
        self.assertEqual(posisi_akhir(langkah(0, "e4", eval_sesudah=-500))["nilai"], "-5.0")
        self.assertIsNone(posisi_akhir(None))


class Golongkan(unittest.TestCase):
    def test_kalah_waktu_saat_posisi_masih_seimbang(self):
        g = golongkan(PGN_WAKTU, "180+0", "putih", [langkah(2, "Nf3", eval_sesudah=10)], True)
        self.assertEqual(g["sebab"], "waktu")
        self.assertIn("masih seimbang", g["keterangan"])
        self.assertTrue(g["habis_waktu"])

    def test_jam_habis_tapi_posisi_sudah_kalah_bukan_kalah_waktu(self):
        """Laporan Ray: kalau bangunan sudah kalah jauh, sebabnya bukan waktu."""
        lk = [langkah(2, "Nf3", hilang=900, eval_sesudah=-900, klas="blunder")]
        g = golongkan(PGN_WAKTU, "180+0", "putih", lk, True)
        self.assertNotEqual(g["sebab"], "waktu")
        self.assertTrue(g["habis_waktu"])
        self.assertTrue(g["keterangan"].startswith("jam habis, tapi posisi memang sudah kalah"))
        self.assertIn("Nf3", g["keterangan"])

    def test_blunder_cepat_jadi_missclick(self):
        # ply 2 memakai 5 detik -> blunder biasa
        lk = [langkah(2, "Nf3", hilang=400, eval_sesudah=-400, klas="blunder")]
        self.assertEqual(golongkan(PGN_BIASA, "180+0", "putih", lk, True)["sebab"], "blunder")
        # ply 0 memakai 0 detik -> missclick
        lk = [langkah(0, "e4", hilang=400, eval_sesudah=-400, klas="blunder")]
        self.assertEqual(golongkan(PGN_BIASA, "180+0", "putih", lk, True)["sebab"], "missclick")

    def test_tanpa_blunder_jadi_dikalahkan(self):
        lk = [langkah(0, "e4"), langkah(2, "Nf3", hilang=120, klas="mistake")]
        g = golongkan(PGN_BIASA, "180+0", "putih", lk, True)
        self.assertEqual(g["sebab"], "dikalahkan")
        self.assertIn("? 1", g["keterangan"])

    def test_belum_dianalisis(self):
        g = golongkan(PGN_BIASA, "180+0", "putih", [], False)
        self.assertEqual(g["sebab"], "belum")
        self.assertIn("analisis engine", g["keterangan"])

    def test_kalah_waktu_tanpa_analisis_tetap_waktu(self):
        g = golongkan(PGN_WAKTU, "180+0", "putih", [], False)
        self.assertEqual(g["sebab"], "waktu")
        self.assertEqual(g["keterangan"], "belum dianalisis")

    def test_blunder_di_posisi_yang_sudah_kalah_bukan_penentu(self):
        """Audit 28 Sep 2026 (partai 272655): 29…Kd6 dari -6,36 ditunjuk sebagai
        sebab, padahal partainya sudah dibuang lebih awal oleh Nd4 (+0,25 -> -2,82)."""
        lk = [dict(langkah(2, "Nd4", hilang=307, eval_sesudah=-282, klas="blunder"), eval_sebelum_cp=25),
              dict(langkah(0, "Kd6", hilang=1000, eval_sesudah=-100000, klas="blunder"), eval_sebelum_cp=-636)]
        lk.sort(key=lambda l: l["ply"])
        g = golongkan(PGN_BIASA, "180+0", "putih", lk, True)
        self.assertEqual(g["ply"], 2)
        self.assertIn("Nd4", g["keterangan"])
        self.assertIn("2 blunder", g["keterangan"])

    def test_blunder_cepat_yang_bukan_penentu_tidak_jadi_missclick(self):
        # ply 0 (0 detik) terjadi saat posisi sudah kalah; penentunya ply 2 (5 detik)
        lk = [dict(langkah(0, "e4", hilang=900, eval_sesudah=-1500, klas="blunder"), eval_sebelum_cp=-600),
              dict(langkah(2, "Nf3", hilang=400, eval_sesudah=-350, klas="blunder"), eval_sebelum_cp=50)]
        self.assertEqual(golongkan(PGN_BIASA, "180+0", "putih", lk, True)["sebab"], "blunder")

    def test_langkah_terbaik_tidak_pernah_jadi_sebab(self):
        """12 dari 500 kekalahan dulu menunjuk langkah pilihan engine sendiri."""
        lk = [dict(langkah(2, "Rf8", hilang=1000, eval_sesudah=-1000, klas="terbaik"), eval_sebelum_cp=0)]
        self.assertEqual(golongkan(PGN_BIASA, "180+0", "putih", lk, True)["sebab"], "dikalahkan")

    def test_semua_blunder_saat_sudah_kalah_jadi_dikalahkan(self):
        lk = [dict(langkah(2, "Nf3", hilang=500, eval_sesudah=-900, klas="blunder"), eval_sebelum_cp=-400)]
        self.assertEqual(golongkan(PGN_BIASA, "180+0", "putih", lk, True)["sebab"], "dikalahkan")

    def test_inkremen_pecahan(self):
        self.assertEqual(detik_dipakai(jam_per_ply(PGN_WAKTU), 2, "180+0.5"), 5.5)

    def test_kehilangan_di_batas_ditandai(self):
        lk = [langkah(0, "e4", hilang=1000, eval_sesudah=-1200, klas="blunder")]
        g = golongkan(PGN_BIASA, "180+0", "putih", lk, True)
        self.assertIn("batas", g["keterangan"])


class Kumpulan(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = koneksi(Path(self.tmp.name) / "uji.db")
        buat_skema(self.conn)
        t = sekarang()
        c = self.conn
        c.execute("INSERT INTO pemain(id,nama,nama_normal,dibuat_pada,akun_pribadi) VALUES(1,'Saya','saya',?,1)", (t,))
        c.execute("INSERT INTO pemain(id,nama,nama_normal,dibuat_pada,gelar,elo_terakhir) "
                  "VALUES(2,'Kuat','kuat',?,'GM',2600)", (t,))
        c.execute("INSERT INTO pemain(id,nama,nama_normal,dibuat_pada) VALUES(3,'Lemah','lemah',?)", (t,))
        c.commit()

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def partai(self, id_, lawan_id, elo_lawan, pgn=PGN_WAKTU, tanggal="2026.09.01"):
        self.conn.execute(
            "INSERT INTO partai(id,putih_id,hitam_id,hasil,pgn,sumber,hash_unik,diimpor_pada,"
            "tanggal,kontrol_waktu,format,jumlah_langkah,elo_putih,elo_hitam) "
            "VALUES(?,1,?,'0-1',?,'uji',?,?,?,'180+0','blitz',40,1800,?)",
            (id_, lawan_id, pgn, f"h{id_}", sekarang(), tanggal, elo_lawan))
        self.conn.commit()

    def test_saringan_lawan(self):
        self.partai(1, 2, 2600)      # GM kuat
        self.partai(2, 3, 1500)      # lawan lemah tanpa gelar
        semua = kekalahan(self.conn, [1], lawan="semua")
        self.assertEqual(len(semua["partai"]), 2)
        ketentuan = kekalahan(self.conn, [1], lawan="ketentuan")
        self.assertEqual([p["id"] for p in ketentuan["partai"]], [1])
        self.assertEqual(len(kekalahan(self.conn, [1], lawan="gelar")["partai"]), 1)
        self.assertEqual(len(kekalahan(self.conn, [1], lawan="2200")["partai"]), 1)

    def test_saringan_lawan_sebelum_batas(self):
        """Audit 27 Sep: saringan dijalankan SETELAH LIMIT, jadi kekalahan lama
        melawan GM hilang di balik banyak kekalahan terbaru melawan non-GM."""
        self.partai(1, 2, 2600, tanggal="2026.01.01")          # satu-satunya lawan bergelar, lama
        for i in range(2, 12):
            self.partai(i, 3, 1500, tanggal=f"2026.09.{i:02d}")  # 10 kekalahan terbaru, tanpa gelar
        d = kekalahan(self.conn, [1], lawan="gelar", batas=3)
        self.assertEqual([p["id"] for p in d["partai"]], [1])
        self.assertEqual(len(kekalahan(self.conn, [1], lawan="semua", batas=3)["partai"]), 3)

    def test_pemain_sebagai_hitam_juga_disaring_dari_sisi_lawan(self):
        self.conn.execute(
            "INSERT INTO partai(id,putih_id,hitam_id,hasil,pgn,sumber,hash_unik,diimpor_pada,"
            "tanggal,kontrol_waktu,format,jumlah_langkah,elo_putih,elo_hitam) "
            "VALUES(50,2,1,'1-0',?,'uji','h50',?,'2026.09.01','180+0','blitz',40,2600,1800)",
            (PGN_WAKTU, sekarang()))
        self.conn.commit()
        self.assertEqual([p["id"] for p in kekalahan(self.conn, [1], lawan="gelar")["partai"]], [50])
        self.assertEqual([p["id"] for p in kekalahan(self.conn, [1], lawan="2200")["partai"]], [50])

    def test_ikhtisar_dan_isi_baris(self):
        self.partai(1, 2, 2600)
        d = kekalahan(self.conn, [1], lawan="semua")
        p = d["partai"][0]
        self.assertEqual(p["warna"], "putih")
        self.assertEqual(p["lawan"], "Kuat")
        self.assertEqual(p["lawan_gelar"], "GM")
        self.assertEqual(p["sebab"], "waktu")          # belum dianalisis + kalah waktu
        self.assertEqual(d["ikhtisar"]["waktu"], 1)
        self.assertEqual(sum(d["ikhtisar"].values()), 1)

    def test_batas_dan_urutan_terbaru_dulu(self):
        self.partai(1, 2, 2600, tanggal="2026.09.01")
        self.partai(2, 2, 2600, tanggal="2026.09.05")
        d = kekalahan(self.conn, [1], lawan="semua", batas=1)
        self.assertEqual([p["id"] for p in d["partai"]], [2])

    def test_tanpa_pemain(self):
        self.assertEqual(kekalahan(self.conn, [])["partai"], [])


class PembukaanKalah(Kumpulan):
    """Pembukaan yang paling sering kalah melawan lawan kuat (Ray, 9 Okt 2026)."""

    def setUp(self):
        super().setUp()
        self.conn.execute("INSERT INTO pemain(id,nama,nama_normal,dibuat_pada,gelar) "
                          "VALUES(4,'Fm','fm',?,'FM')", (sekarang(),))
        self.conn.commit()

    def main(self, id_, lawan_id, hasil, eco, saya_putih=True, tanggal="2026.09.01", fmt="blitz"):
        putih, hitam = (1, lawan_id) if saya_putih else (lawan_id, 1)
        self.conn.execute(
            "INSERT INTO partai(id,putih_id,hitam_id,hasil,pgn,sumber,hash_unik,diimpor_pada,"
            "tanggal,format,jumlah_langkah,eco,elo_putih,elo_hitam) "
            "VALUES(?,?,?,?,'','uji',?,?,?,?,40,?,1800,1800)",
            (id_, putih, hitam, hasil, f"p{id_}", sekarang(), tanggal, fmt, eco))
        self.conn.commit()

    def test_gmim_hanya_gm_dan_im(self):
        self.main(1, 2, "0-1", "A10")     # kalah vs GM
        self.main(2, 4, "0-1", "A10")     # kalah vs FM: bukan GM/IM
        self.main(3, 3, "0-1", "A10")     # tanpa gelar
        d = pembukaan_kalah(self.conn, [1], lawan="gmim")
        self.assertEqual(d["total"]["partai"], 1)
        self.assertEqual(len(pembukaan_kalah(self.conn, [1], lawan="gelar")["pembukaan"]), 1)
        self.assertEqual(pembukaan_kalah(self.conn, [1], lawan="gelar")["total"]["partai"], 2)
        self.assertEqual(pembukaan_kalah(self.conn, [1], lawan="semua")["total"]["partai"], 3)

    def test_hasil_dari_sudut_saya_dan_dipisah_warna(self):
        self.main(1, 2, "1-0", "A10")                      # Putih, menang
        self.main(2, 2, "1/2-1/2", "A15")                  # Putih, remis (keluarga sama)
        self.main(3, 2, "0-1", "A20")                      # Putih, kalah
        self.main(4, 2, "0-1", "B90", saya_putih=False)    # Hitam, MENANG
        d = pembukaan_kalah(self.conn, [1], lawan="gmim")
        per = {(p["warna"], p["keluarga"]): p for p in d["pembukaan"]}
        eng = per[("putih", "English Opening")]
        self.assertEqual((eng["menang"], eng["remis"], eng["kalah"]), (1, 1, 1))
        self.assertEqual(eng["skor_persen"], 50.0)
        sic = per[("hitam", "Sicilian Defense")]
        self.assertEqual((sic["menang"], sic["kalah"]), (1, 0))
        self.assertEqual(d["total"], {"partai": 4, "menang": 2, "remis": 1, "kalah": 1})

    def test_urut_paling_sering_kalah_dan_kalah_terakhir(self):
        for i, tgl in enumerate(["2026.01.01", "2026.03.01"], start=1):
            self.main(i, 2, "1-0", "B90", saya_putih=False, tanggal=tgl)   # Hitam kalah 2x
        self.main(3, 2, "0-1", "A10")                                        # Putih kalah 1x
        d = pembukaan_kalah(self.conn, [1], lawan="gmim")
        self.assertEqual(d["pembukaan"][0]["keluarga"], "Sicilian Defense")
        self.assertEqual(d["pembukaan"][0]["kalah_terakhir"]["id"], 2)
        self.assertTrue(d["pembukaan"][0]["sampel_kecil"])

    def test_partai_belum_selesai_dan_format_disaring(self):
        self.main(1, 2, "*", "A10")
        self.main(2, 2, "0-1", "A10", fmt="bullet")
        self.assertEqual(pembukaan_kalah(self.conn, [1], lawan="gmim")["total"]["partai"], 1)
        self.assertEqual(pembukaan_kalah(self.conn, [1], "blitz", "gmim")["total"]["partai"], 0)

    def test_kekalahan_juga_menerima_gmim(self):
        self.partai(1, 2, 2600)      # GM
        self.partai(2, 4, 2300)      # FM
        self.assertEqual([p["id"] for p in kekalahan(self.conn, [1], lawan="gmim")["partai"]], [1])

    def test_tanpa_akun(self):
        self.assertEqual(pembukaan_kalah(self.conn, [])["pembukaan"], [])


if __name__ == "__main__":
    unittest.main()
