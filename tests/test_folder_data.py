"""
Berkas keadaan mengikuti CATUR_DB, bukan data/ proyek.

Audit 28 Sep 2026: server uji dengan CATUR_DB lain tetap memegang kunci
penulis Ray, menulis antrean dan catatan tugas asli, dan pekerjaannya
dijalankan penjaga terhadap catur.db sungguhan.
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

AKAR = Path(__file__).resolve().parents[1]

# Tiap nilai dicetak dari proses baru, karena path dihitung saat modul diimpor.
SKRIP = r"""
import json, sys
sys.path.insert(0, sys.argv[1])
from app import antrean, batas_cpu, kunci_penulis, pembantu, tanya_db, tugas_batch
from app import analisa_web
import cadangkan_db, perbarui, jaga_antrean, impor_broadcast, rapikan_turnamen, analisis_partai
print(json.dumps({
    "kunci": str(kunci_penulis.PATH_KUNCI), "info": str(kunci_penulis.PATH_INFO),
    "kunci_bawaan": str(kunci_penulis.KunciPenulis().path),
    "luncur": str(analisa_web.PATH_KUNCI_LUNCUR), "antrean": str(antrean.PATH_ANTREAN),
    "pengaturan": str(batas_cpu.PATH_PENGATURAN), "pembantu": str(pembantu.PATH_STATUS),
    "tanya": str(tanya_db.DB_BAWAAN), "tugas": str(tugas_batch.PATH_TUGAS),
    "log": str(tugas_batch.FOLDER_LOG), "cadangan": str(cadangkan_db.FOLDER_BACKUP),
    "laporan": str(perbarui.FOLDER_LAPORAN), "jaga": str(jaga_antrean.PATH_LOG),
    "broadcast": str(impor_broadcast.PATH_STATUS_MINGGUAN),
    "turnamen": str(rapikan_turnamen.FOLDER_LAPORAN),
    "kemajuan": str(analisis_partai.FOLDER_KEMAJUAN), "klaim": str(analisis_partai.FOLDER_KLAIM),
}))
"""


class IkutCaturDb(unittest.TestCase):
    def test_semua_berkas_keadaan_di_folder_database_uji(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "uji.db"
            env = dict(os.environ, CATUR_DB=str(db))
            keluar = subprocess.run([sys.executable, "-c", SKRIP, str(AKAR)], env=env,
                                    capture_output=True, text=True, timeout=120)
            self.assertEqual(keluar.returncode, 0, keluar.stderr)
            jalur = json.loads(keluar.stdout.strip().splitlines()[-1])
            asli = (AKAR / "data").resolve()
            for nama, p in jalur.items():
                p = Path(p).resolve()
                self.assertTrue(p.is_relative_to(Path(tmp).resolve()),
                                f"{nama} masih di luar folder database uji: {p}")
                self.assertFalse(p.is_relative_to(asli), f"{nama} menunjuk data/ asli")


if __name__ == "__main__":
    unittest.main()
