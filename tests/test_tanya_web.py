"""
Uji penyambungan Phase 6 ke server dan halaman (app/tanya_web.py).

Modul intinya (`app/tanya_db.py`, `app/peta_aplikasi.py`) sudah punya 48 tes
sendiri sejak 12 Sep 2026; yang belum pernah diuji adalah jalur HTTP-nya,
karena jalur itu memang belum ada sampai 25 Sep 2026.

Dipanggil sebagai fungsi biasa, bukan lewat HTTP: `fastapi.testclient` butuh
httpx yang tidak terpasang, sama seperti alasan di `tests/test_impor_web.py`.
Ollama juga TIDAK dipanggil — yang diuji justru bagian yang harus benar walau
Ollama mati: kode status, bentuk jawaban, dan pesan yang menolong.

Jalankan dari akar proyek:
    python -m unittest discover -s tests -v
"""

import sys
import unittest
from pathlib import Path
from unittest import mock

AKAR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(AKAR))

from fastapi import HTTPException
from pydantic import ValidationError

from app import llm, peta_aplikasi, tanya_db, tanya_web


def minta(pertanyaan="berapa partai", mode="database", model=None):
    return tanya_web.Pertanyaan(pertanyaan=pertanyaan, mode=mode, model=model)


class Status(unittest.TestCase):
    def test_ollama_mati_bukan_kesalahan_server(self):
        """
        Ollama mati adalah keadaan normal di laptop ini, bukan 500. Halaman
        perlu bisa membacanya tanpa menunggu, jadi jawaban biasa + hidup=False.
        """
        with mock.patch.object(llm, "model_tersedia",
                               side_effect=llm.LlmTidakAda("Ollama tidak menjawab di x")):
            d = tanya_web.status()
        self.assertFalse(d["hidup"])
        self.assertFalse(d["siap"])
        self.assertIn("Ollama", d["alasan"])
        self.assertEqual(d["model"], [])

    def test_hidup_tanpa_model_berbeda_dari_mati(self):
        """Ollama hidup tapi modelnya belum diunduh butuh saran yang lain."""
        with mock.patch.object(llm, "model_tersedia", return_value=["llama3:8b"]):
            d = tanya_web.status()
        self.assertTrue(d["hidup"])
        self.assertFalse(d["siap"])
        self.assertEqual(d["model"], ["llama3:8b"])
        self.assertIsNone(d["alasan"])

    def test_siap_kalau_model_bawaan_ada(self):
        with mock.patch.object(llm, "model_tersedia", return_value=[llm.MODEL, "llama3:8b"]):
            d = tanya_web.status()
        self.assertTrue(d["siap"])
        self.assertEqual(d["model_bawaan"], llm.MODEL)


class Bertanya(unittest.TestCase):
    def test_pertanyaan_kosong_ditolak_pydantic(self):
        with self.assertRaises(ValidationError):
            minta(pertanyaan="")

    def test_mode_asing_ditolak_pydantic(self):
        with self.assertRaises(ValidationError):
            minta(mode="ngawur")

    def test_pertanyaan_terlalu_panjang_ditolak(self):
        with self.assertRaises(ValidationError):
            minta(pertanyaan="a" * 501)

    def test_ollama_mati_jadi_503_dengan_cara_memperbaiki(self):
        """
        503, bukan 500: servernya sehat, layanan luarnya yang mati. Pesannya
        diteruskan apa adanya karena di situlah tertulis perintah perbaikannya.
        """
        with mock.patch.object(tanya_db, "tanya",
                               side_effect=llm.LlmTidakAda("Jalankan `ollama serve`")):
            with self.assertRaises(HTTPException) as e:
                tanya_web.bertanya(minta())
        self.assertEqual(e.exception.status_code, 503)
        self.assertIn("ollama serve", e.exception.detail)

    def test_sql_ditolak_jadi_422(self):
        with mock.patch.object(tanya_db, "tanya",
                               side_effect=tanya_db.SqlDitolak("hanya SELECT yang boleh")):
            with self.assertRaises(HTTPException) as e:
                tanya_web.bertanya(minta("hapus semua partai"))
        self.assertEqual(e.exception.status_code, 422)
        self.assertIn("SELECT", e.exception.detail)

    def test_kesalahan_nilai_lain_juga_422_bukan_500(self):
        with mock.patch.object(tanya_db, "tanya", side_effect=ValueError("Pertanyaan kosong.")):
            with self.assertRaises(HTTPException) as e:
                tanya_web.bertanya(minta())
        self.assertEqual(e.exception.status_code, 422)

    def test_jawaban_database_selalu_memuat_sql(self):
        """
        SQL-nya bukan hiasan: tanpa itu angka dari LLM tidak bisa ditelusuri,
        dan halaman kehilangan satu-satunya cara memeriksanya.
        """
        palsu = {"pertanyaan": "5 pemain dengan partai terbanyak",
                 "sql": "SELECT nama FROM pemain LIMIT 5",
                 "kolom": ["nama"], "baris": [{"nama": "Carlsen, Magnus"}],
                 "jumlah": 1, "terpotong": False, "model": llm.MODEL,
                 "peringatan": []}
        with mock.patch.object(tanya_db, "tanya", return_value=dict(palsu)) as t:
            d = tanya_web.bertanya(minta(palsu["pertanyaan"]))
        self.assertEqual(d["sql"], palsu["sql"])
        self.assertEqual(d["mode"], "database")
        self.assertEqual(t.call_args.args[0], palsu["pertanyaan"])

    def test_peringatan_diteruskan_apa_adanya(self):
        """Peringatan 'angka rapi dari SQL yang salah' tidak boleh hilang di HTTP."""
        pesan = ["Hasilnya kosong. Sering berarti SQL-nya salah, bukan datanya tidak ada."]
        palsu = {"pertanyaan": "x", "sql": "SELECT 1", "kolom": [], "baris": [],
                 "jumlah": 0, "terpotong": False, "model": llm.MODEL, "peringatan": pesan}
        with mock.patch.object(tanya_db, "tanya", return_value=dict(palsu)):
            d = tanya_web.bertanya(minta("x"))
        self.assertEqual(d["peringatan"], pesan)

    def test_mode_aplikasi_memakai_modul_peta_dan_mengembalikan_petanya(self):
        palsu = {"pertanyaan": "halaman apa saja yang ada",
                 "jawaban": "Ada 16 halaman.",
                 "peta": "HALAMAN: beranda, partai, ...", "model": llm.MODEL}
        with mock.patch.object(peta_aplikasi, "tanya_aplikasi", return_value=dict(palsu)) as t, \
             mock.patch.object(tanya_db, "tanya", side_effect=AssertionError("salah modul")):
            d = tanya_web.bertanya(minta(palsu["pertanyaan"], mode="aplikasi"))
        self.assertEqual(d["mode"], "aplikasi")
        self.assertEqual(d["peta"], palsu["peta"])
        self.assertTrue(t.called)

    def test_model_bisa_diturunkan_saat_ram_sempit(self):
        """`model=` ada supaya 3b bisa dipakai tanpa mengubah bawaan app/llm.py."""
        palsu = {"pertanyaan": "x", "sql": "SELECT 1", "kolom": [], "baris": [],
                 "jumlah": 0, "terpotong": False, "model": "qwen2.5-coder:3b",
                 "peringatan": []}
        with mock.patch.object(tanya_db, "tanya", return_value=dict(palsu)) as t:
            tanya_web.bertanya(minta("x", model="qwen2.5-coder:3b"))
        self.assertEqual(t.call_args.kwargs["model"], "qwen2.5-coder:3b")

    def test_tanpa_model_tidak_memaksa_nilai_apa_pun(self):
        """Biar bawaan app/llm.py yang menentukan, bukan None yang dikirim."""
        palsu = {"pertanyaan": "x", "sql": "SELECT 1", "kolom": [], "baris": [],
                 "jumlah": 0, "terpotong": False, "model": llm.MODEL, "peringatan": []}
        with mock.patch.object(tanya_db, "tanya", return_value=dict(palsu)) as t:
            tanya_web.bertanya(minta("x"))
        self.assertNotIn("model", t.call_args.kwargs)


class Tersambung(unittest.TestCase):
    """Fitur yang tidak terdaftar di server/nav sama saja dengan tidak ada."""

    def test_endpoint_terdaftar_di_openapi(self):
        # Menelusuri aplikasi.routes MELEWATKAN router include_router(); jalan
        # yang benar adalah openapi(). Jebakan ini tercatat di SESI.md 09-12.
        from app.main import aplikasi
        jalur = aplikasi.openapi()["paths"]
        self.assertIn("/api/tanya", jalur)
        self.assertIn("/api/tanya/status", jalur)

    def test_halaman_tanya_ada_berkasnya(self):
        from app.main import FOLDER_WEB
        html = FOLDER_WEB / "tanya.html"
        self.assertTrue(html.is_file())
        self.assertIn("tanya.js", html.read_text(encoding="utf-8"))
        self.assertTrue((FOLDER_WEB / "tanya.js").is_file())

    def test_semua_halaman_bernav_menaut_ke_tanya(self):
        """
        Phase 6 selesai 12 Sep tapi tidak pernah bisa dibuka Ray sampai 25 Sep,
        justru karena tidak ada satu pun tautan ke arahnya.
        """
        from app.main import FOLDER_WEB
        kurang = []
        for p in sorted(FOLDER_WEB.glob("*.html")):
            isi = p.read_text(encoding="utf-8")
            if 'class="nav-utama"' in isi and 'href="/tanya"' not in isi:
                kurang.append(p.name)
        self.assertEqual(kurang, [])


if __name__ == "__main__":
    unittest.main()
