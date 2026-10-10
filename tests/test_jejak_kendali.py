"""
Jejak kendali mesin (app/jejak_kendali.py).

10 Okt 2026 15:54 dua batch analisis teliti dihentikan lewat
/api/analisa/hentikan dan tidak ada yang bisa menjawab siapa: statusnya cuma
"dihentikan" dan log server sudah ditimpa restart 15:57. Tes ini menjaga
bahwa penghentian berikutnya meninggalkan jejak yang tidak ikut terhapus.
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.dependencies.utils import get_dependant  # noqa: E402

from app import analisa_web as aw  # noqa: E402
from app import jejak_kendali as jk  # noqa: E402
from app import tugas_batch as tb  # noqa: E402

UA_BROWSER = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/141.0 Safari/537.36 Edg/141.0")
UA_POWERSHELL = "Mozilla/5.0 (Windows NT; Windows NT 10.0; id-ID) WindowsPowerShell/5.1.26100"


def permintaan(agen=None, rujukan=None, alamat="127.0.0.1"):
    h = {}
    if agen:
        h["user-agent"] = agen
    if rujukan:
        h["referer"] = rujukan
    return SimpleNamespace(headers=h, client=SimpleNamespace(host=alamat))


class AsalPermintaan(unittest.TestCase):
    def test_halaman_mesin_di_browser(self):
        a = jk.asal_permintaan(permintaan(UA_BROWSER, "http://127.0.0.1:8000/static/mesin.html?x=1"))
        self.assertEqual(a["jenis"], "browser")
        self.assertEqual(a["halaman"], "/static/mesin.html")
        self.assertEqual(jk.teks_asal(a), "browser /static/mesin.html")

    def test_powershell_tidak_dikira_browser(self):
        """Invoke-RestMethod juga mengaku Mozilla/5.0."""
        a = jk.asal_permintaan(permintaan(UA_POWERSHELL))
        self.assertEqual(a["jenis"], "PowerShell")

    def test_curl_dan_python(self):
        self.assertEqual(jk.asal_permintaan(permintaan("curl/8.9.1"))["jenis"], "curl")
        self.assertEqual(jk.asal_permintaan(permintaan("Python-urllib/3.12"))["jenis"],
                         "Python-urllib")
        self.assertEqual(jk.asal_permintaan(permintaan())["jenis"], "tanpa User-Agent")

    def test_tanpa_http_dan_alasan_dirapikan(self):
        a = jk.asal_permintaan(None, "  restart\nserver  ")
        self.assertEqual(a["jenis"], "tanpa HTTP")
        self.assertEqual(a["alasan"], "restart server")

    def test_alamat_luar_disebut(self):
        a = jk.asal_permintaan(permintaan("curl/8", alamat="192.168.1.9"))
        self.assertIn("dari 192.168.1.9", jk.teks_asal(a))


class BerkasJejak(unittest.TestCase):
    def test_hanya_ditambah_tidak_ditimpa(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "laporan" / "jejak.txt"
            jk.catat("hentikan batch", jk.asal_permintaan(None), "PID 1", path=p)
            jk.catat("jeda", jk.asal_permintaan(permintaan("curl/8")), path=p)
            baris = p.read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(baris), 2)
            self.assertIn("PID 1", baris[0])
            self.assertIn("curl", baris[1])

    def test_gagal_menulis_tidak_menggagalkan_tindakan(self):
        with tempfile.TemporaryDirectory() as d:
            folder = Path(d) / "bukan-folder"
            folder.write_text("x")            # parent berupa berkas -> OSError
            jk.catat("jeda", jk.asal_permintaan(None), path=folder / "jejak.txt")


class HentikanLewatRute(unittest.TestCase):
    """
    TestClient butuh httpx, yang tidak terpasang (dan tidak ditambah demi tes
    ini). Jadi dua hal diuji terpisah: FastAPI memang menyuntikkan Request ke
    parameter `request` (bukan membiarkannya None atau menganggapnya query), lalu
    fungsinya dipanggil dengan permintaan tiruan.
    """

    def test_request_disuntik_fastapi(self):
        for fungsi, jalur in ((aw.hentikan, "/api/analisa/hentikan"),
                              (aw.jeda_semua, "/api/analisa/jeda"),
                              (aw.jaga_antrean, "/api/analisa/antrean/jaga")):
            with self.subTest(jalur=jalur):
                d = get_dependant(path=jalur, call=fungsi)
                self.assertEqual(d.request_param_name, "request")
                self.assertNotIn("request", [q.name for q in d.query_params])

    def setUp(self):
        from app import antrean
        self.antrean = antrean
        self.tmp = tempfile.TemporaryDirectory()
        d = Path(self.tmp.name)
        self.asli = (antrean.PATH_ANTREAN, tb.PATH_TUGAS, aw._hidup, aw._matikan, jk.PATH_JEJAK)
        antrean.PATH_ANTREAN = d / "antrean.json"
        tb.PATH_TUGAS = d / "tugas.json"
        jk.PATH_JEJAK = d / "log-kendali-mesin.txt"
        aw._hidup = lambda pid, sejak=None: True
        aw._matikan = lambda pid, **k: None
        tb.PATH_TUGAS.write_text(json.dumps(
            [{"pid": 9516, "status": "berjalan", "perintah": "python analisis_partai.py"}]),
            encoding="utf-8")

    def tearDown(self):
        (self.antrean.PATH_ANTREAN, tb.PATH_TUGAS, aw._hidup, aw._matikan,
         jk.PATH_JEJAK) = self.asli
        self.tmp.cleanup()

    def test_penghentian_tercatat_dengan_asal(self):
        aw.hentikan(aw.Penghentian(pid=9516, alasan="restart server"),
                    permintaan("curl/8.9.1"))
        tugas = json.loads(tb.PATH_TUGAS.read_text(encoding="utf-8"))[0]
        self.assertEqual(tugas["status"], "dihentikan")
        self.assertEqual(tugas["dihentikan_oleh"]["jenis"], "curl")
        self.assertEqual(tugas["dihentikan_oleh"]["alasan"], "restart server")
        jejak = jk.PATH_JEJAK.read_text(encoding="utf-8")
        self.assertIn("hentikan batch", jejak)
        self.assertIn("PID 9516", jejak)
        self.assertIn("alasan: restart server", jejak)

    def test_tanpa_alasan_tetap_boleh(self):
        """Halaman Mesin lama hanya mengirim {pid}."""
        aw.hentikan(aw.Penghentian.model_validate({"pid": 9516}),
                    permintaan(UA_BROWSER, "http://127.0.0.1:8000/static/mesin.html"))
        self.assertIn("browser /static/mesin.html",
                      jk.PATH_JEJAK.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
