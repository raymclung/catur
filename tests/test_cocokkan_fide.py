"""
Tes `cocokkan_fide.py`: pagar-pagar pencocokan nama ke daftar FIDE.

Yang diuji adalah penolakan, karena kesalahan berbahaya di sini bukan
pencocokan yang terlewat (kolomnya tetap kosong, seperti sebelumnya) tapi
FIDE ID orang lain yang ditempel ke pemain kita. Dua kasus nyata dari uji
kering 9 Okt 2026 jadi tes: Botvinnik (umur) dan Bent Larsen (Elo vs gelar).
"""

import io
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

AKAR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(AKAR))

import cocokkan_fide as cf

JUDUL = ("ID Number      Name                                                         "
         "Fed Sex Tit  WTit OTit           FOA SRtng SGm SK RRtng RGm Rk BRtng BGm BK B-day Flag")


def baris_fide(fide_id, nama, fed="NOR", tit="", wtit="", lahir="1990"):
    """Satu baris lebar-tetap, kolomnya mengikuti posisi di JUDUL."""
    kol = {k: JUDUL.index(k) for k in ("ID Number", "Name", "Fed", "Sex", "Tit", "WTit",
                                         "OTit", "B-day", "Flag")}
    isi = [" "] * (len(JUDUL) + 5)

    def taruh(kunci, teks):
        for i, ch in enumerate(teks):
            isi[kol[kunci] + i] = ch

    taruh("ID Number", fide_id)
    taruh("Name", nama)
    taruh("Fed", fed)
    taruh("Sex", "M")
    taruh("Tit", tit)
    taruh("WTit", wtit)
    taruh("B-day", lahir)
    return "".join(isi).rstrip() + "\r\n"


def pemain(nama_normal, tahun_awal=2010, elo_maks=2400, online=0):
    return {"nama_normal": nama_normal, "tahun_awal": tahun_awal,
            "elo_maks": elo_maks, "di_akun_online": online}


class BacaDaftar(unittest.TestCase):
    def test_kolom_dibaca_dari_judul(self):
        with tempfile.TemporaryDirectory() as tmp:
            z = Path(tmp) / "players_list.zip"
            with zipfile.ZipFile(z, "w") as f:
                f.writestr("players_list_foa.txt", JUDUL + "\r\n" +
                           baris_fide("1503014", "Carlsen, Magnus", tit="GM", lahir="1990") +
                           baris_fide("700037", "Hou, Yifan", fed="CHN", tit="GM", wtit="WGM") +
                           baris_fide("1", "Tanpa, Lahir", lahir="0"))
            d = cf.baca_daftar_fide(z)
        self.assertEqual(d["carlsen magnus"][0]["fide_id"], "1503014")
        self.assertEqual(d["carlsen magnus"][0]["gelar"], "GM")
        self.assertEqual(d["carlsen magnus"][0]["lahir"], 1990)
        self.assertEqual(d["hou yifan"][0]["negara"], "CHN")
        self.assertIsNone(d["tanpa lahir"][0]["lahir"])


class Putuskan(unittest.TestCase):
    DAFTAR = {
        "piket jeroen": [{"fide_id": "1000055", "gelar": "GM", "negara": "NED", "lahir": 1969}],
        "botvinnik mikhail": [{"fide_id": "2805650", "gelar": None, "negara": "ISR", "lahir": 1983}],
        "larsen bent": [{"fide_id": "1483455", "gelar": None, "negara": "DEN", "lahir": 1937}],
        "smith john": [{"fide_id": "1", "gelar": None, "lahir": 1980},
                       {"fide_id": "2", "gelar": None, "lahir": 1985}],
        "tanpa tahun": [{"fide_id": "3", "gelar": "FM", "lahir": None}],
    }

    def putus(self, b, terpakai=()):
        return cf.putuskan(b, self.DAFTAR, set(terpakai))[0]

    def test_cocok_unik_masuk_akal(self):
        self.assertEqual(self.putus(pemain("piket jeroen", 1986, 2670)), "cocok")

    def test_botvinnik_ditolak_umur(self):
        """Partai sejak 1936, orang FIDE lahir 1983: jelas bukan orang yang sama."""
        self.assertEqual(self.putus(pemain("botvinnik mikhail", 1936, 2700)),
                         "umur-tidak-masuk-akal")

    def test_larsen_ditolak_elo_vs_gelar(self):
        """GM 2660 di partai, orang FIDE bernama sama tanpa gelar."""
        self.assertEqual(self.putus(pemain("larsen bent", 1952, 2660)),
                         "elo-tidak-sejalan-gelar")

    def test_tanpa_gelar_boleh_kalau_elo_di_bawah_ambang(self):
        self.assertEqual(self.putus(pemain("larsen bent", 1990, 2100)), "cocok")

    def test_nama_ganda_di_fide_tidak_ditebak(self):
        self.assertEqual(self.putus(pemain("smith john")), "nama-ganda-di-fide")

    def test_singkatan_tidak_pernah_dicocokkan(self):
        self.assertEqual(self.putus(pemain("piket j")), "nama-singkat")
        self.assertEqual(self.putus(pemain("piket")), "nama-singkat")

    def test_akun_online_tidak_disentuh(self):
        self.assertEqual(self.putus(pemain("piket jeroen", online=3)), "akun-online")

    def test_tanpa_elo_tidak_bisa_diperiksa(self):
        self.assertEqual(self.putus(pemain("piket jeroen", 1986, None)), "tanpa-elo")

    def test_tanpa_tahun_lahir_ditolak(self):
        self.assertEqual(self.putus(pemain("tanpa tahun")), "tanpa-tahun-lahir")

    def test_id_yang_sudah_dipakai_tidak_ditempel_lagi(self):
        """UNIQUE fide_id: pemain lain sudah memegangnya = pemain ganda, bukan tugas skrip ini."""
        self.assertEqual(self.putus(pemain("piket jeroen", 1986, 2670), terpakai={"1000055"}),
                         "id-sudah-dipakai")


if __name__ == "__main__":
    unittest.main()
