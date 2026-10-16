"""
Uji app/pola_taktik.py — motif taktik di balik blunder/mistake sendiri.

Yang dijaga:
  * tiap motif dikenali pada posisi yang motifnya pasti;
  * bentuk tanpa hasil (garpu yang tidak memenangkan apa-apa, garis yang
    terpotong di tengah pertukaran) TIDAK diberi label — lebih baik
    "posisional" daripada mengarang motif;
  * motif dari langkah yang lebih awal menang, dan pencarian berhenti
    begitu materi sudah dimenangkan (kasus nyata partai 275113: pin
    kebetulan di langkah ketiga sesudah benteng gratis dimakan);
  * ringkasan hanya menghitung kesalahan pihak akun sendiri, dengan
    saringan lawan/jenis yang sama dengan halaman lain.
"""

import sys
import tempfile
import unittest
from pathlib import Path

import chess

AKAR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(AKAR))

from app.db import buat_skema, koneksi, sekarang
from app.pola_taktik import (NAMA_MOTIF, URUTAN_MOTIF, _garis_ke_langkah, fen_awal,
                             motif_garis, motif_partai, pola_kesalahan, tandai_motif)


def motif(fen: str, san: str, ada_mat: bool = False) -> list[str]:
    b = chess.Board(fen)
    garis = _garis_ke_langkah(b, san.split())
    assert len(garis) == len(san.split()), f"garis uji tidak sah: {san}"
    return motif_garis(b, garis, ada_mat)


class MotifGaris(unittest.TestCase):
    def test_garpu_kuda(self):
        self.assertEqual(motif("r3k3/8/8/3N4/8/8/8/4K3 w - - 0 1", "Nc7+ Ke7 Nxa8")[0], "garpu")

    def test_pin_ke_raja(self):
        self.assertEqual(motif("4k3/8/2n5/8/3P4/8/8/4KB2 w - - 0 1", "Bb5 Kd7 d5 Kd6 dxc6"), ["pin"])

    def test_tusuk_sate(self):
        self.assertEqual(motif("7R/8/8/4k3/8/K7/8/4r3 w - - 0 1", "Re8+ Kd4 Rxe1")[0], "tusuk_sate")

    def test_mat_baris_belakang(self):
        self.assertEqual(motif("6k1/5ppp/8/8/8/8/8/R5K1 w - - 0 1", "Ra8#"), ["mat_baris_belakang"])

    def test_mat_biasa_bukan_baris_belakang(self):
        # Menteri memberi mat di f7, bukan di baris belakang.
        fen = "r1bqkb1r/pppp1ppp/2n2n2/4p2Q/2B1P3/8/PPPP1PPP/RNB1K1NR w KQkq - 4 4"
        self.assertEqual(motif(fen, "Qxf7#"), ["mat"])

    def test_buah_tergantung(self):
        self.assertEqual(motif("4k3/8/8/3n4/8/8/8/3QK3 w - - 0 1", "Qxd5"), ["buah_tergantung"])

    def test_serangan_tersingkap(self):
        self.assertEqual(motif("3qk3/8/8/8/8/3B4/8/3RK3 w - - 0 1", "Bb5+ Ke7 Rxd8"),
                         ["serangan_tersingkap"])

    def test_tanpa_materi_jadi_posisional(self):
        awal = chess.STARTING_FEN
        self.assertEqual(motif(awal, "e4 e5 Nf3 Nc6"), ["posisional"])

    def test_garpu_yang_tidak_menang_apa_apa_bukan_garpu(self):
        # Nc7+ menyerang raja dan benteng, tapi garisnya berhenti tanpa memakan.
        self.assertEqual(motif("r3k3/8/8/3N4/8/8/8/4K3 w - - 0 1", "Nc7+ Ke7"), ["posisional"])

    def test_garis_terpotong_di_tengah_pertukaran(self):
        # Menteri memakan kuda yang dijaga pion e6; garis berhenti sebelum
        # exd5. Itu bukan "buah tergantung" dan bukan untung 3 pion.
        self.assertEqual(motif("4k3/8/4p3/3n4/8/8/8/3QK3 w - - 0 1", "Qxd5"), ["posisional"])

    def test_langkah_lebih_awal_menang(self):
        # Garpu di langkah pertama, buah tergantung (benteng a8) baru di langkah kedua.
        hasil = motif("r3k3/8/8/3N4/8/8/8/4K3 w - - 0 1", "Nc7+ Ke7 Nxa8")
        self.assertEqual(hasil, ["garpu", "buah_tergantung"])

    def test_berhenti_sesudah_menang_materi(self):
        # Partai 275113: benteng c4 gratis dimakan di langkah kedua; Qxf7 sesudahnya
        # kebetulan memaku gajah g7. Pin itu tidak boleh ikut.
        fen = "3r2k1/pp3pb1/1q1P2p1/4p2p/2r5/2P1p1P1/PP2Q1BP/3R1R1K b - - 2 29"
        b = chess.Board(fen)
        b.push_san("Rxd6")
        garis = _garis_ke_langkah(b, "Rxd6 Qxd6 Qxc4 Kh7 Qxf7".split())
        self.assertEqual(motif_garis(b, garis), ["buah_tergantung"])

    def test_garis_kosong(self):
        self.assertEqual(motif_garis(chess.Board(), []), ["tanpa_data"])
        self.assertEqual(motif_garis(chess.Board(), [], ada_mat=True), ["mat"])

    def test_mat_dari_engine_walau_garis_pendek(self):
        self.assertEqual(motif(chess.STARTING_FEN, "e4 e5", ada_mat=True), ["mat"])

    def test_san_tidak_sah_memotong_garis(self):
        b = chess.Board()
        self.assertEqual(len(_garis_ke_langkah(b, ["e4", "Qxh8", "e5"])), 1)

    def test_semua_motif_punya_nama(self):
        self.assertEqual(set(URUTAN_MOTIF), set(NAMA_MOTIF))


def baris(ply, san, uci, klas="terbaik", variasi="", terbaik=None, mat=None):
    return {"ply": ply, "nomor_langkah": ply // 2 + 1, "warna": "putih" if ply % 2 == 0 else "hitam",
            "san": san, "uci": uci, "klasifikasi": klas, "kehilangan_cp": 400 if klas == "blunder" else 0,
            "mat_sesudah": mat, "langkah_terbaik_san": terbaik or (variasi.split()[0] if variasi else san),
            "variasi_terbaik": variasi}


# 1.e4 e5 2.Bc4 Nc6 3.Qh5 Nf6?? 4.Qxf7#
PARTAI_SKOLAR = [
    baris(0, "e4", "e2e4", variasi="e4 e5"),
    baris(1, "e5", "e7e5", variasi="e5 Nf3"),
    baris(2, "Bc4", "f1c4", variasi="Bc4 Nc6"),
    baris(3, "Nc6", "b8c6", variasi="Nc6 Qh5"),
    baris(4, "Qh5", "d1h5", klas="mistake", variasi="Nf3 Nf6"),
    baris(5, "Nf6", "g8f6", klas="blunder", variasi="g6 Qf3 Nf6", mat=1),
    baris(6, "Qxf7#", "h5f7", variasi="Qxf7#"),
]


class MotifPartai(unittest.TestCase):
    def test_kesalahan_yang_membiarkan_mat(self):
        m = motif_partai(PARTAI_SKOLAR)
        self.assertEqual(set(m), {4, 5})          # hanya blunder/mistake
        self.assertEqual(m[5]["utama"], "mat")
        self.assertEqual(m[5]["sisi"], "membiarkan")

    def test_variasi_boleh_list(self):
        sebagai_list = [dict(l, variasi_terbaik=l["variasi_terbaik"].split()) for l in PARTAI_SKOLAR]
        self.assertEqual(motif_partai(sebagai_list)[5]["utama"], "mat")

    def test_kesalahan_di_ply_terakhir_tanpa_sanggahan(self):
        m = motif_partai(PARTAI_SKOLAR[:6])
        self.assertEqual(m[5]["utama"], "mat")    # dari mat_sesudah, bukan dari garis

    def test_uci_rusak_berhenti_tanpa_menebak(self):
        rusak = PARTAI_SKOLAR[:2] + [dict(PARTAI_SKOLAR[2], uci="a1a8")] + PARTAI_SKOLAR[3:]
        self.assertEqual(motif_partai(rusak), {})

    def test_fen_awal_dari_tag(self):
        self.assertIsNone(fen_awal('[White "a"]\n\n1. e4'))
        self.assertEqual(fen_awal('[FEN "8/8/8/8/8/8/8/K6k w - - 0 1"]'), "8/8/8/8/8/8/8/K6k w - - 0 1")

    def test_tandai_motif_hanya_kesalahan(self):
        hasil = tandai_motif({"langkah": [dict(l) for l in PARTAI_SKOLAR]}, None)
        bertanda = [l["ply"] for l in hasil["langkah"] if "motif" in l]
        self.assertEqual(bertanda, [4, 5])


class Ringkasan(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = koneksi(Path(self.tmp.name) / "uji.db")
        buat_skema(self.conn)
        t = sekarang()
        c = self.conn
        c.execute("INSERT INTO pemain(id,nama,nama_normal,dibuat_pada,akun_pribadi) VALUES(1,'Saya','saya',?,1)", (t,))
        c.execute("INSERT INTO pemain(id,nama,nama_normal,dibuat_pada,gelar) VALUES(2,'Kuat','kuat',?,'GM')", (t,))
        c.execute("INSERT INTO pemain(id,nama,nama_normal,dibuat_pada) VALUES(3,'Lemah','lemah',?)", (t,))
        c.commit()

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def partai(self, id_, putih, hitam, elo_putih=1800, elo_hitam=1800, tanggal="2026.10.01"):
        c = self.conn
        c.execute(
            "INSERT INTO partai(id,putih_id,hitam_id,hasil,pgn,sumber,hash_unik,diimpor_pada,"
            "tanggal,kontrol_waktu,format,jumlah_langkah,elo_putih,elo_hitam) "
            "VALUES(?,?,?,'1-0','','uji',?,?,?,'180+0','blitz',4,?,?)",
            (id_, putih, hitam, f"h{id_}", sekarang(), tanggal, elo_putih, elo_hitam))
        c.execute("INSERT INTO analisis(partai_id,engine,depth,profil,dianalisis_pada) "
                  "VALUES(?,'uji',10,'cepat',?)", (id_, sekarang()))
        for l in PARTAI_SKOLAR:
            c.execute(
                "INSERT INTO analisis_langkah(partai_id,ply,nomor_langkah,warna,san,uci,eval_sebelum_cp,"
                "eval_sesudah_cp,eval_putih_cp,mat_sesudah,kehilangan_cp,klasifikasi,"
                "langkah_terbaik_san,langkah_terbaik_uci,variasi_terbaik) "
                "VALUES(?,?,?,?,?,?,0,0,0,?,?,?,?,NULL,?)",
                (id_, l["ply"], l["nomor_langkah"], l["warna"], l["san"], l["uci"], l["mat_sesudah"],
                 l["kehilangan_cp"], l["klasifikasi"], l["langkah_terbaik_san"], l["variasi_terbaik"]))
        c.commit()

    def test_hanya_kesalahan_pihak_sendiri(self):
        self.partai(1, 3, 1)         # saya Hitam: blunder Nf6 (ply 5) milik saya
        r = pola_kesalahan(self.conn, [1], lawan="semua")
        self.assertEqual(r["total_kesalahan"], 1)
        self.assertEqual(r["motif"][0]["kunci"], "mat")
        self.assertEqual(r["motif"][0]["contoh"][0]["san"], "Nf6")
        self.assertEqual(r["motif"][0]["fase"]["pembukaan"], 1)
        self.partai(2, 1, 3)         # saya Putih: mistake Qh5 (ply 4)
        r = pola_kesalahan(self.conn, [1], lawan="semua")
        self.assertEqual(r["total_partai"], 2)
        self.assertEqual(r["total_kesalahan"], 2)

    def test_saringan_lawan_dan_jenis(self):
        self.partai(1, 3, 1)                          # lawan lemah
        self.partai(2, 2, 1, elo_putih=2600)          # lawan GM
        self.assertEqual(pola_kesalahan(self.conn, [1], lawan="ketentuan")["total_partai"], 1)
        self.assertEqual(pola_kesalahan(self.conn, [1], lawan="semua")["total_partai"], 2)
        self.assertEqual(pola_kesalahan(self.conn, [1], lawan="semua", jenis="mistake")["total_kesalahan"], 0)
        self.assertEqual(pola_kesalahan(self.conn, [1], lawan="semua", jenis="blunder")["total_kesalahan"], 2)

    def test_posisional_selalu_di_bawah(self):
        self.partai(1, 1, 3)                          # mistake Qh5 milik saya
        r = pola_kesalahan(self.conn, [1], lawan="semua")
        kunci = [k["kunci"] for k in r["motif"]]
        taktis = [k for k in kunci if k not in ("posisional", "tanpa_data")]
        self.assertEqual(kunci[:len(taktis)], taktis)
        self.assertAlmostEqual(sum(k["persen"] for k in r["motif"]), 100.0, delta=0.2)

    def test_tanpa_pemain(self):
        self.assertEqual(pola_kesalahan(self.conn, [])["total_kesalahan"], 0)


if __name__ == "__main__":
    unittest.main()
