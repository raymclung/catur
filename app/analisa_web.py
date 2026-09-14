"""
Menjalankan analisis engine dari halaman web, bukan dari terminal.

Analisis satu partai penuh butuh puluhan detik sampai beberapa menit, jadi
TIDAK dikerjakan di dalam request. Yang dilakukan modul ini: memilih partai,
menghitung perkiraan waktu, lalu MELUNCURKAN `analisis_partai.py` sebagai
proses terpisah yang lepas dari server — persis perintah yang selama ini
diketik di terminal. Servernya sendiri tidak pernah menjalankan engine batch.

    POST /api/analisa/pratinjau   berapa partai, berapa lama, perintah setara
    POST /api/analisa/mulai       luncurkan batch di latar belakang
    GET  /api/analisa/status      statistik + tugas yang berjalan/selesai
    POST /api/analisa/hentikan    hentikan satu batch

Kenapa proses terpisah, bukan thread di server:

  * Batch berjam-jam tidak boleh ikut mati kalau server di-restart. Proses
    diluncurkan DETACHED, jadi ia tetap jalan.
  * Aturan mesin ini (ARSITEKTUR.md Phase 5): maksimal dua proses engine
    bersamaan — satu batch + engine server papan analisis. Karena itu
    `/mulai` MENOLAK kalau sudah ada batch yang berjalan, termasuk batch yang
    dijalankan dari terminal oleh sesi lain.
  * Pemilihan partai memakai `pilih_partai()` milik CLI, bukan salinan SQL
    baru. Kalau saringannya berubah, halaman ini ikut berubah sendiri.

Penghentian memakai taskkill/SIGTERM. Itu aman: `analisis_partai.py`
menyimpan hasil tiap partai begitu selesai, jadi yang hilang paling banyak
satu partai yang sedang dihitung — dan partai itu akan terpilih lagi di
perintah berikutnya karena belum ada di tabel `analisis`.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .db import koneksi

router = APIRouter(prefix="/api/analisa", tags=["analisa"])

AKAR = Path(__file__).resolve().parents[1]
FOLDER_LOG = AKAR / "data" / "laporan"

# Database yang dipakai halaman ini. None = data/catur.db bawaan. Tes mengisinya
# dengan database sementara: tanpa ini dua tes diam-diam membaca database Ray
# yang sebenarnya, lolos di mesinnya tapi gagal di checkout bersih ("no such
# table: partai") — dilaporkan sesi frontend 13 Sep 00:20.
PATH_DB: Path | None = None


def _buka() -> sqlite3.Connection:
    # koneksi() tanpa argumen saat PATH_DB kosong: tes lain mengganti `koneksi`
    # dengan fungsi tanpa parameter, dan itu harus tetap bekerja.
    return koneksi() if PATH_DB is None else koneksi(PATH_DB)
PATH_TUGAS = AKAR / "data" / "tugas-analisa.json"

# Tugas yang disimpan di catatan; yang lebih lama dibuang.
MAKS_TUGAS = 20

# Batas jumlah partai per batch. Bukan batas teknis melainkan rem: 2.000
# partai profil hemat sudah belasan jam. Batch raksasa tetap lewat terminal.
MAKS_PARTAI = 2000

# Hasil pemindaian daftar proses ditahan sebentar supaya halaman yang menyegar
# tiap beberapa detik tidak memanggil PowerShell terus-menerus (satu panggilan
# ~0,3 detik).
JEDA_PINDAI = 15.0

# Di Windows, setiap subprocess konsol (powershell, taskkill) memunculkan
# jendela hitam sekejap. Halaman Mesin memanggil /api/analisa/status berulang,
# jadi jendelanya muncul terus-menerus dan mengganggu (dilaporkan Ray,
# 12 Sep 2026). CREATE_NO_WINDOW menjalankannya tanpa jendela.
TANPA_JENDELA = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
_pindai: dict = {"waktu": 0.0, "hasil": []}

GELAR_VALID = ("GM", "IM", "FM", "CM", "WGM", "WIM", "WFM", "WCM", "NM")
FORMAT_VALID = ("bullet", "blitz", "rapid", "klasik", "tanpa")
HASIL_VALID = ("kalah", "menang", "remis")

POLA_MAJU = re.compile(r"\[(\d+)/(\d+)\]")
POLA_SISA = re.compile(r"sisa ~(\d+)m")
POLA_DIPILIH = re.compile(r"^(\d+) partai dipilih")


class Saringan(BaseModel):
    """Saringan pemilihan partai — namanya sama dengan flag CLI-nya."""

    id: list[int] | None = Field(None, description="id partai tertentu")
    sumber: str | None = Field(None, pattern=r"^[a-z0-9-]{2,30}$",
                               description="label sumber seperti tersimpan di database")
    pemain: list[str] | None = Field(None, description="potongan nama pemain")
    min_elo: int | None = Field(None, ge=1000, le=3000, description="KEDUA pemain minimal sekian")
    lawan_min_elo: int | None = Field(None, ge=1000, le=3000, description="lawan Ray minimal sekian")
    gelar_lawan: list[str] | None = None
    hasil: str | None = Field(None, description=" | ".join(HASIL_VALID))
    format: str | None = Field(None, description=" | ".join(FORMAT_VALID))
    terbaru: int = Field(50, ge=1, le=MAKS_PARTAI, description="jumlah partai terbaru")
    min_langkah: int = Field(10, ge=0, le=200)
    repertoire: bool = Field(False, description="hanya pembukaan repertoire Ray "
                                                "(English, Catalan, Ragozin, Sicilian)")
    ulang: bool = False
    profil: str = Field("hemat", pattern=r"^(cepat|hemat|normal|dalam)$")
    bagian: str | None = Field(None, pattern=r"^\d{1,2}/\d{1,2}$",
                               description="K/N: hanya partai id % N == K (pengisi slot penjaga)")


class Penghentian(BaseModel):
    pid: int


# ------------------------------------------------------------------ bantu

def _modul():
    """
    Muat analisis_partai.py (ada di akar proyek, bukan paket app).

    Diimpor saat dipakai, bukan saat server start: kalau berkas itu sedang
    diedit sesi lain, server tidak boleh ikut gagal hidup.
    """
    akar = str(AKAR)
    if akar not in sys.path:
        sys.path.insert(0, akar)
    try:
        import analisis_partai
    except Exception as e:      # noqa: BLE001 - apa pun sebabnya, jawab jujur
        raise HTTPException(status_code=500,
                            detail=f"analisis_partai.py tidak bisa dimuat: {e}") from e
    return analisis_partai


# Sumber yang isinya partai Ray sendiri. Ditulis sekali di sini supaya
# pembagian "partai saya" vs "partai GM" tidak tersebar di beberapa kueri dan
# di JavaScript. Ray, 12 Sep: "hilangkan partai ini dari partai gm karena ini
# merupakan akun saya sudah ada di partai saya".
SUMBER_SAYA = ("lichess-user", "chesscom")


def daftar_sumber(conn: sqlite3.Connection) -> list[dict]:
    """
    Label sumber yang benar-benar ada di database, beserta jumlahnya.

    Sengaja tidak ditulis sebagai daftar tetap: halaman Impor membiarkan Ray
    memberi label sendiri, jadi daftar tertulis di kode pasti ketinggalan.
    """
    q = """
        SELECT g.sumber AS nama, COUNT(*) AS partai,
               SUM(CASE WHEN a.partai_id IS NULL THEN 1 ELSE 0 END) AS belum
        FROM partai g LEFT JOIN analisis a ON a.partai_id = g.id
        WHERE g.sumber IS NOT NULL
        GROUP BY g.sumber ORDER BY partai DESC
    """
    return [{"nama": r["nama"], "partai": r["partai"], "belum": r["belum"] or 0,
             "pribadi": r["nama"] in SUMBER_SAYA}
            for r in conn.execute(q)]


def daftar_pemain(conn: sqlite3.Connection, sumber: str | None = None,
                  cari: str | None = None, batas: int = 40,
                  kelompok: str | None = None) -> list[dict]:
    """
    Pemain yang paling banyak punya partai BELUM dianalisis, untuk dipilih dari
    dropdown.

    Ray: "pemainnya kalau bisa pilihan sama kayak kirinya, karena kalau salah
    ketik repot" — kotak teks bebas berarti 'Carsen' menghasilkan nol partai
    tanpa penjelasan. Urutannya mengikuti jumlah yang belum dianalisis, sama
    seperti daftar sumber, supaya yang paling berguna ada di atas.

    `nama` dikembalikan apa adanya dan bisa langsung dikirim balik sebagai
    `pemain`: pencocokan `--pemain` memakai nama yang dinormalisasi, jadi
    "Carlsen, Magnus" tetap cocok.
    """
    syarat = ["a.partai_id IS NULL", "g.jumlah_langkah >= 10"]
    params: list = []
    if sumber:
        syarat.append("g.sumber = ?")
        params.append(sumber)
    # kelompok 'gm': akun Ray sendiri tidak ditawarkan di sini — partainya
    # sudah punya tab sendiri, dan memunculkannya dua kali membingungkan.
    if kelompok == "gm":
        syarat.append("p.akun_pribadi = 0")
        syarat.append(f"g.sumber NOT IN ({','.join('?' * len(SUMBER_SAYA))})")
        params += list(SUMBER_SAYA)
    elif kelompok == "saya":
        syarat.append("p.akun_pribadi = 1")
    if cari:
        syarat.append("p.nama LIKE ?")
        params.append(f"%{cari.strip()}%")
    q = f"""
        SELECT p.id, p.nama, p.gelar, COUNT(*) AS belum
        FROM partai g
        JOIN pemain p ON p.id IN (g.putih_id, g.hitam_id)
        LEFT JOIN analisis a ON a.partai_id = g.id
        WHERE {' AND '.join(syarat)}
        GROUP BY p.id
        ORDER BY belum DESC, p.nama
        LIMIT ?
    """
    return [{"id": r["id"], "nama": r["nama"], "gelar": r["gelar"], "belum": r["belum"]}
            for r in conn.execute(q, params + [max(1, min(batas, 200))])]


def _periksa_sumber(s: Saringan, conn: sqlite3.Connection | None = None) -> None:
    """
    Sumber diperiksa terhadap database, bukan daftar tertulis.

    'gm' adalah label khusus (semua sumber partai GM), jadi selalu sah.
    `conn` bisa diberikan dari luar; tanpa itu dibuka koneksi ke database
    nyata — tes memberi koneksi sementara supaya tidak bergantung pada
    data/catur.db milik Ray.
    """
    if not s.sumber or s.sumber == "gm":
        return
    if conn is not None:
        ada = [d["nama"] for d in daftar_sumber(conn)]
        if s.sumber not in ada:
            raise HTTPException(
                status_code=422,
                detail=f"Belum ada partai dengan sumber '{s.sumber}'. Yang ada: "
                       + (", ".join(ada) if ada else "(database kosong)"))
        return
    conn = _buka()
    try:
        ada = [d["nama"] for d in daftar_sumber(conn)]
    finally:
        conn.close()
    if s.sumber not in ada:
        raise HTTPException(
            status_code=422,
            detail=f"Belum ada partai dengan sumber '{s.sumber}'. Yang ada: "
                   + (", ".join(ada) if ada else "(database kosong)"))


def _periksa(s: Saringan) -> None:
    if s.hasil and s.hasil not in HASIL_VALID:
        raise HTTPException(status_code=422, detail=f"Hasil tidak dikenal: {s.hasil}")
    if s.format and s.format not in FORMAT_VALID:
        raise HTTPException(status_code=422, detail=f"Format tidak dikenal: {s.format}")
    for g in (s.gelar_lawan or []):
        if g.upper() not in GELAR_VALID:
            raise HTTPException(status_code=422, detail=f"Gelar tidak dikenal: {g}")
    # Tanpa saringan sama sekali BOLEH: `terbaru` selalu ada dan dibatasi
    # MAKS_PARTAI, jadi "analisis apa saja, yang terbaru dulu" tetap terbatas.
    # Dulu ini ditolak karena takut seluruh database ikut terjaring — ketakutan
    # yang sudah dijawab oleh batas `terbaru` itu sendiri. Ray, 13 Sep:
    # "ini tidak perlu ada saya ingin analisa semuanya".
    # Yang tetap dijaga di CLI: di sana `--terbaru` opsional, jadi tanpa
    # saringan artinya benar-benar 266 ribu partai.


def _ruang_nama(s: Saringan) -> SimpleNamespace:
    """Bentuk yang diminta pilih_partai(): sama persis dengan hasil argparse."""
    return SimpleNamespace(
        id=s.id, sumber=s.sumber, pemain=s.pemain, min_elo=s.min_elo,
        gelar_lawan=[g.upper() for g in s.gelar_lawan] if s.gelar_lawan else None,
        lawan_min_elo=s.lawan_min_elo, hasil=s.hasil, format_=s.format,
        terbaru=s.terbaru, min_langkah=s.min_langkah, ulang=s.ulang,
        repertoire=s.repertoire, bagian=s.bagian,
    )


def _perintah(s: Saringan) -> list[str]:
    """Perintah CLI yang setara dengan saringan ini."""
    p = ["analisis_partai.py"]
    if s.id:
        p += ["--id"] + [str(i) for i in s.id]
    if s.sumber:
        p += ["--sumber", s.sumber]
    if s.pemain:
        p += ["--pemain"] + list(s.pemain)
    if s.min_elo:
        p += ["--min-elo", str(s.min_elo)]
    if s.gelar_lawan:
        p += ["--gelar-lawan"] + [g.upper() for g in s.gelar_lawan]
    if s.lawan_min_elo:
        p += ["--lawan-min-elo", str(s.lawan_min_elo)]
    if s.hasil:
        p += ["--hasil", s.hasil]
    if s.format:
        p += ["--format", s.format]
    if s.repertoire:
        p += ["--repertoire"]
    if s.ulang:
        p += ["--ulang"]
    if s.min_langkah != 10:
        p += ["--min-langkah", str(s.min_langkah)]
    if s.bagian:
        p += ["--bagian", s.bagian]
    p += ["--terbaru", str(s.terbaru), "--profil", s.profil]
    if PATH_DB is not None:
        p += ["--db", str(PATH_DB)]
    return p


def _perintah_tampil(s: Saringan) -> str:
    """
    Perintah setara yang bisa ditempel ke terminal apa adanya.

    Argumen bertanda spasi dikutip: nama pemain memuat koma dan spasi
    ("Nakamura, Hikaru"), jadi tanpa kutip perintahnya berubah arti
    menjadi dua nama terpisah waktu ditempel.
    """
    bagian = ["python"]
    for a in _perintah(s):
        bagian.append(f'"{a}"' if " " in a else a)
    return " ".join(bagian)


def _hitung(s: Saringan) -> dict:
    """Jumlah partai + perkiraan waktu, memakai perhitungan CLI yang sama."""
    m = _modul()
    from catur_analysis.engine import PROFIL      # noqa: PLC0415 - butuh sys.path dari _modul()

    conn = _buka()
    try:
        daftar = m.pilih_partai(conn, _ruang_nama(s))
        profil = PROFIL[s.profil]
        total_ply = sum(p["jumlah_langkah"] * 2 for p in daftar)
        terukur = m.detik_per_ply_terukur(conn, s.profil)
        per_ply = terukur or m.DETIK_PER_PLY_CADANGAN[s.profil]
        contoh = [m.label(p).strip() for p in daftar[:12]]
    finally:
        conn.close()
    return {
        "jumlah": len(daftar),
        "perkiraan_menit": round(total_ply * per_ply / 60),
        "dasar_perkiraan": "dari riwayat" if terukur else "cadangan, belum ada riwayat",
        "detik_per_ply": round(per_ply, 2),
        "profil": s.profil,
        "depth": profil.depth,
        "ram_mb": profil.perkiraan_ram_mb,
        "contoh": contoh,
        "perintah": _perintah_tampil(s),
    }


# --------------------------------------------------------- daftar proses

# Berapa batch engine boleh jalan bersamaan.
#
# Diukur 12 Sep 2026 di mesin ini (10 inti fisik / 16 logis, profil hemat =
# 2 utas + ~371 MB per batch): dua batch memakai 38% CPU dan RAM bebas masih
# 4,9 GB. Jadi batas lama "satu batch saja" membuang lebih dari separuh mesin.
# Empat batch ≈ 8 utas (setengah dari yang logis), ~1,5 GB, masih menyisakan
# ruang untuk server web dan pekerjaan Ray sendiri.
#
# Ray, 12 Sep: "ini tidak bisa ditabrak" — batas ini memang aturan kita, bukan
# batas mesin, jadi angkanya boleh diubah di sini saja.
#
# Ray, 13 Sep: dinaikkan 4 -> 5 bersamaan dengan antrean otomatis.
# Lima batch ≈ 10 utas, ~1,9 GB. Penjaga antrean sengaja memakai batas
# sendiri yang lebih kecil (jaga_antrean.py --maks) supaya masih ada slot
# untuk batch yang dijalankan Ray sendiri.
#
# Ray, 13 Sep siang: 5 membuat laptop tidak bisa dipakai — "batas maks laptop
# sepertinya 3". Turun ke 3, dan batch kini berprioritas idle.
#
# Ray, 13 Sep sore: "tambahkan jadi 4 dong biar cepat". Naik ke 4 karena kini
# ada batas CPU (app/batas_cpu.py): semua engine dikurung di sebagian inti,
# jadi batch keempat berbagi inti yang sama, bukan merebut inti milik Ray.
#
# Ray, 13 Sep malam: laptop lag lagi dengan 4 batch (RAM komit 22,7/23,2 GB,
# bukan CPU). Ray: "buat di laptop ini max 2 saja yang jalan, sisanya akan
# dibantu dari komputer lain lewat rdc".
MAKS_BATCH = 2


def _pindai_proses(nama_skrip: str) -> list[dict]:
    """
    Proses python yang baris perintahnya memuat nama skrip itu.

    Dipakai untuk batch analisis DAN untuk penjaga antrean, supaya
    keduanya dibaca dari kenyataan (daftar proses), bukan dari catatan
    yang bisa basi.
    """
    simpanan = _pindai.setdefault(nama_skrip, {"waktu": 0.0, "hasil": []})
    sekarang = time.time()
    # `_pindai["waktu"] = 0` dipakai pemanggil untuk memaksa pindai ulang
    # (mis. tepat sebelum meluncurkan batch), jadi ia ikut dilihat di sini.
    paksa = not _pindai["waktu"]
    if not paksa and sekarang - simpanan["waktu"] < JEDA_PINDAI:
        return simpanan["hasil"]

    try:
        hasil = _keluaran_proses(nama_skrip)
    except Exception:
        return simpanan["hasil"]      # tidak bisa memeriksa: jangan mengarang
    simpanan.update(waktu=sekarang, hasil=hasil)
    _pindai["waktu"] = sekarang       # paksaan sudah dipakai
    return hasil


def _pindai_batch() -> list[dict]:
    """
    Batch analisis yang benar-benar berjalan, dibaca dari daftar proses.

    Termasuk batch yang diluncurkan dari terminal oleh sesi lain — itu justru
    intinya: halaman ini tidak boleh menawarkan tombol Mulai kalau engine
    sudah dipakai.
    """
    return _pindai_proses("analisis_partai.py")


def _keluaran_proses(nama_skrip: str) -> list[dict]:
    """Baca daftar proses, kembalikan yang baris perintahnya cocok."""
    hasil: list[dict] = []
    if os.name == "nt":
        perintah = ("Get-CimInstance Win32_Process -Filter \"Name like 'python%'\" | "
                    "ForEach-Object { \"$($_.ProcessId)`t$($_.CreationDate)`t$($_.CommandLine)\" }")
        keluaran = subprocess.run(["powershell", "-NoProfile", "-Command", perintah],
                                  capture_output=True, text=True, encoding="utf-8",
                                  errors="replace", timeout=30,
                                  creationflags=TANPA_JENDELA).stdout
        pemisah = "	"
    else:
        keluaran = subprocess.run(["ps", "-eo", "pid=,lstart=,args="],
                                  capture_output=True, text=True, timeout=30).stdout
        pemisah = None
    for baris in keluaran.splitlines():
        bagian = baris.split(pemisah, 2) if pemisah else baris.split(None, 2)
        if len(bagian) < 3 or not bagian[0].strip().isdigit():
            continue
        pid, mulai, perintah_proses = bagian[0].strip(), bagian[1].strip(), bagian[2]
        if nama_skrip not in perintah_proses or "Get-CimInstance" in perintah_proses:
            continue
        hasil.append({"pid": int(pid), "mulai": mulai,
                      "perintah": " ".join(perintah_proses.split())[:200]})
    return hasil

def _proses_ada(pid: int) -> bool:
    """
    Apakah PID itu masih ada? Murah: tanya langsung ke Windows, tanpa
    menjalankan proses baru.
    """
    if os.name != "nt":
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False
    import ctypes
    SINKRONISASI = 0x00100000
    MASIH_HIDUP = 259
    k32 = ctypes.windll.kernel32
    h = k32.OpenProcess(SINKRONISASI | 0x0400, False, pid)   # 0x400 = QUERY_INFORMATION
    if not h:
        return False
    try:
        kode = ctypes.c_ulong()
        if k32.GetExitCodeProcess(h, ctypes.byref(kode)):
            return kode.value == MASIH_HIDUP
        return True
    finally:
        k32.CloseHandle(h)


def _matikan(pid: int, pohon: bool = True) -> None:
    """
    Hentikan satu proses beserta anaknya (Stockfish ikut mati), lalu
    TUNGGU sampai benar-benar hilang.

    Tanpa penantian itu, pemindaian berikutnya masih melihatnya dan
    jawaban jadi salah — terjadi nyata 13 Sep 2026: penjaga dimatikan,
    lalu permintaan menghidupkan dijawab "sudah jalan" dengan PID yang
    sudah mati, sehingga tidak ada penjaga yang berjalan sama sekali.
    """
    try:
        if os.name == "nt":
            # /T menyapu seluruh keturunan. Untuk BATCH itu perlu (Stockfish
            # ikut mati), tapi untuk PENJAGA justru salah: batch yang ia
            # jalankan adalah anaknya, jadi "Matikan penjaga" ikut membunuh
            # semua batch — terjadi nyata 13 Sep 2026, #1 mati tiga kali.
            argumen = ["taskkill", "/PID", str(pid)] + (["/T"] if pohon else []) + ["/F"]
            subprocess.run(argumen,
                           capture_output=True, timeout=30,
                           creationflags=TANPA_JENDELA)
        else:
            import signal
            if pohon:
                os.killpg(os.getpgid(pid), signal.SIGTERM)
            else:
                os.kill(pid, signal.SIGTERM)
    except Exception as e:      # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"Gagal menghentikan: {e}") from e

    batas = time.time() + 5
    while _proses_ada(pid) and time.time() < batas:
        time.sleep(0.2)
    _pindai["waktu"] = 0.0          # pindaian lama sudah tidak sahih


def _hidup(pid: int) -> bool:
    return any(b["pid"] == pid for b in _pindai_batch())


# ------------------------------------------------------------ catatan tugas

def _baca_tugas() -> list[dict]:
    try:
        return json.loads(PATH_TUGAS.read_text(encoding="utf-8"))
    except Exception:
        return []


def _tulis_tugas(daftar: list[dict]) -> None:
    PATH_TUGAS.parent.mkdir(parents=True, exist_ok=True)
    PATH_TUGAS.write_text(json.dumps(daftar[-MAKS_TUGAS:], indent=1, ensure_ascii=False),
                          encoding="utf-8", newline="\n")


def _status_akhir(path_log: Path) -> tuple[str, str]:
    """
    ("selesai" | "gagal", alasan) untuk batch yang prosesnya sudah tidak ada.

    Kenapa tidak cukup "prosesnya hilang berarti selesai": pada 12 Sep 2026 dua
    batch mati dengan `EngineTerminatedError ... exit code 3221225786`
    (0xC000013A = STATUS_CONTROL_C_EXIT — jendela konsolnya ditutup) tapi tetap
    dilabeli "selesai", padahal baru 2 dari 25 partai. Label yang bohong lebih
    berbahaya daripada tidak ada label: Ray mengira pekerjaannya beres.

    Penanda selesai yang benar = baris penutup `analisis_partai.py`:
    "selesai N, gagal M, ... menit  |  database: ...". Kalau baris itu tidak
    ada, batch berhenti sebelum waktunya.
    """
    try:
        baris = [b for b in path_log.read_text(encoding="utf-8", errors="replace").splitlines()
                 if b.strip()]
    except OSError:
        return "gagal", "berkas log tidak terbaca"
    if any(b.startswith("selesai ") and " menit" in b for b in baris[-6:]):
        return "selesai", ""
    for b in reversed(baris):
        # Baris galat Python terakhir paling menjelaskan sebabnya.
        if "Error" in b or "error" in b:
            return "gagal", b.strip()[:160]
    if any("Dihentikan" in b for b in baris[-4:]):
        return "dihentikan", "dihentikan di tengah jalan"
    return "gagal", "berhenti sebelum baris penutup muncul"


def _kemajuan(path_log: Path) -> dict:
    """Baca kemajuan dari ekor berkas log batch."""
    try:
        teks = path_log.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}
    baris = [b for b in teks.splitlines() if b.strip()]
    maju = {}
    for b in reversed(baris):
        m = POLA_MAJU.search(b)
        if m:
            maju["selesai"], maju["dari"] = int(m.group(1)), int(m.group(2))
            sisa = POLA_SISA.search(b)
            if sisa:
                maju["sisa_menit"] = int(sisa.group(1))
            break
    for b in baris:
        m = POLA_DIPILIH.match(b)
        if m:
            maju.setdefault("dari", int(m.group(1)))
            break
    maju["ekor"] = baris[-6:]
    return maju


# Angka database untuk halaman Mesin: berubah beberapa per menit, tapi
# halaman itu bertanya tiap 5 detik selagi batch jalan. Menghitungnya tiap kali
# berarti menyisir 266 ribu partai berulang-ulang — 0,6 detik sekali hitung,
# tepat saat mesin sedang sibuk dengan empat engine. Ditahan sebentar saja.
JEDA_ANGKA = 10.0
_angka: dict = {"waktu": 0.0, "hasil": None}


def angka_database(conn: sqlite3.Connection) -> dict:
    """Statistik + sumber + jumlah belum dianalisis, ditahan JEDA_ANGKA detik."""
    from .analisis_db import statistik_analisis        # noqa: PLC0415

    sekarang = time.time()
    if _angka["hasil"] is not None and sekarang - _angka["waktu"] < JEDA_ANGKA:
        return _angka["hasil"]

    tanda = ",".join("?" * len(SUMBER_SAYA))
    pola = f"""
        SELECT SUM(CASE WHEN g.sumber IN ({tanda}) THEN 1 ELSE 0 END) AS saya,
               SUM(CASE WHEN g.sumber NOT IN ({tanda}) THEN 1 ELSE 0 END) AS gm
    """
    p2 = list(SUMBER_SAYA) * 2
    total = conn.execute(pola + " FROM partai g WHERE g.jumlah_langkah >= 10", p2).fetchone()
    sudah = conn.execute(
        pola + " FROM analisis a JOIN partai g ON g.id = a.partai_id"
               " WHERE g.jumlah_langkah >= 10", p2).fetchone()
    hasil = {
        "statistik": statistik_analisis(conn),
        "sumber": daftar_sumber(conn),
        "belum_saya": (total["saya"] or 0) - (sudah["saya"] or 0),
        "belum_gm": (total["gm"] or 0) - (sudah["gm"] or 0),
    }
    _angka.update(waktu=sekarang, hasil=hasil)
    return hasil


def kemajuan_proses(pid: int) -> dict:
    """
    Kemajuan batch yang dijalankan dari TERMINAL.

    Halaman ini hanya tahu berkas log batch yang diluncurkannya sendiri. Batch
    dari terminal mengarahkan keluarannya ke mana saja, jadi `analisis_partai.py`
    menulis catatan kecil bernama PID-nya (lihat tulis_kemajuan di sana) dan di
    sini tinggal dibaca. Ray, 12 Sep: "saya tidak melihat persentasenya".
    """
    berkas = AKAR / "data" / "laporan" / f".kemajuan-{pid}.json"
    try:
        d = json.loads(berkas.read_text(encoding="utf-8"))
    except Exception:
        return {}
    keluar = {k: d[k] for k in ("selesai", "dari", "sisa_menit", "mulai") if k in d}
    if keluar.get("dari"):
        keluar["persen"] = round(100 * keluar.get("selesai", 0) / keluar["dari"], 1)
    return keluar


def bersihkan_kemajuan_yatim(hidup: set[int]) -> None:
    """Buang catatan kemajuan milik proses yang sudah tidak ada."""
    folder = AKAR / "data" / "laporan"
    try:
        berkas = list(folder.glob(".kemajuan-*.json"))
    except OSError:
        return
    for b in berkas:
        try:
            pid = int(b.stem.split("-")[-1])
        except ValueError:
            continue
        if pid not in hidup:
            try:
                b.unlink()
            except OSError:
                pass


def _rangkum_tugas() -> list[dict]:
    daftar = _baca_tugas()
    berubah = False
    for t in daftar:
        hidup = _hidup(t["pid"])
        if t.get("status") == "berjalan" and not hidup:
            # Prosesnya hilang BUKAN berarti pekerjaannya beres — lihat
            # _status_akhir(). Batch yang mati di tengah harus terlihat gagal.
            t["status"], alasan = _status_akhir(Path(t["log"]))
            if alasan:
                t["alasan"] = alasan
            t["sampai"] = datetime.now().strftime("%Y-%m-%d %H:%M")
            berubah = True
        t["berjalan"] = hidup
        t.update(_kemajuan(Path(t["log"])))
    if berubah:
        _tulis_tugas(daftar)
    return list(reversed(daftar))


# ------------------------------------------------------------------ rute

@router.post("/pratinjau", summary="Berapa partai terpilih dan berapa lama")
def pratinjau(s: Saringan):
    _periksa(s)
    _periksa_sumber(s)
    return _hitung(s)


@router.post("/mulai", summary="Jalankan batch analisis di latar belakang")
def mulai(s: Saringan):
    """Rute /mulai: periksa dulu, baru luncurkan."""
    _periksa(s)
    _periksa_sumber(s)
    return luncurkan(s)


def luncurkan(s: Saringan) -> dict:
    """
    Luncurkan satu batch analisis sebagai proses lepas.

    Dipisah dari rutenya supaya penjaga antrean (jaga_antrean.py) memakai
    jalur peluncuran yang SAMA — termasuk batas jumlah batch, bendera
    proses, dan pencatatan tugas.
    """
    from . import jeda_mesin                 # noqa: PLC0415
    if jeda_mesin.sedang_jeda():
        raise HTTPException(status_code=409,
                            detail="Mesin sedang dijeda. Tekan Lanjutkan dulu kalau mau menjalankan batch.")
    _pindai["waktu"] = 0.0                   # jangan pakai hasil pindaian lama
    berjalan = _pindai_batch()
    if len(berjalan) >= MAKS_BATCH:
        raise HTTPException(
            status_code=409,
            detail=f"Sudah ada {len(berjalan)} batch analisis berjalan (PID "
                   + ", ".join(str(b["pid"]) for b in berjalan) + f"), dan batasnya "
                   f"{MAKS_BATCH} sekaligus. Tunggu satu selesai, atau hentikan dari "
                   "daftar di bawah.")

    ringkas = _hitung(s)
    if ringkas["jumlah"] == 0:
        raise HTTPException(status_code=422,
                            detail="Tidak ada partai yang cocok (mungkin semuanya sudah "
                                   "dianalisis). Longgarkan saringannya, atau centang "
                                   "'analisis ulang'.")

    cap = datetime.now().strftime("%Y%m%d-%H%M%S")
    FOLDER_LOG.mkdir(parents=True, exist_ok=True)
    path_log = FOLDER_LOG / f"log-analisa-web-{cap}.txt"

    # -u: keluaran tidak ditahan buffer, jadi kemajuannya bisa dibaca hidup-hidup.
    perintah = [sys.executable, "-X", "utf8", "-u"] + _perintah(s)
    bendera = 0
    if os.name == "nt":
        # DETACHED_PROCESS, bukan CREATE_NO_WINDOW (dikembalikan 13 Sep 2026).
        #
        # CREATE_NO_WINDOW membuat batch tetap berbagi console session dengan
        # server. Waktu server dimatikan paksa untuk dimuat ulang, console itu
        # ikut dibubarkan dan Stockfish mati dengan exit code 3221225786
        # (0xC000013A = STATUS_CONTROL_C_EXIT). Terjadi nyata pada empat batch
        # Ray 12 Sep: dua mati dengan EngineTerminatedError tepat saat server
        # dimuat ulang, dua lagi berhenti di partai ke-2.
        #
        # Jendela konsol kosong yang dulu terlihat (A22) datang dari Stockfish,
        # bukan dari proses batch: induknya tidak punya console, jadi Windows
        # membuatkan satu untuk engine. Itu ditangani di sumbernya —
        # src/catur_analysis/engine.py menjalankan engine dengan CREATE_NO_WINDOW.
        #
        # IDLE_PRIORITY_CLASS (13 Sep 2026): Ray "5 terlalu banyak, komputer saya
        # tidak bisa buka apa apa". Dengan prioritas normal, engine merebut CPU
        # dari aplikasi yang sedang dipakai. Prioritas idle berarti engine hanya
        # memakai CPU yang sedang tidak dipakai siapa pun: saat Ray bekerja,
        # aplikasinya didahulukan; saat laptop ditinggal, engine tetap dapat
        # CPU penuh. Stockfish ikut mewarisi kelas ini karena dianakkan tanpa
        # bendera prioritas sendiri (lihat src/catur_analysis/engine.py).
        bendera = (subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
                   | subprocess.IDLE_PRIORITY_CLASS)
    log = path_log.open("w", encoding="utf-8")
    try:
        proses = subprocess.Popen(
            perintah, cwd=str(AKAR), stdout=log, stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL, creationflags=bendera,
            start_new_session=(os.name != "nt"), close_fds=True)
    except OSError as e:
        log.close()
        raise HTTPException(status_code=500, detail=f"Gagal menjalankan batch: {e}") from e
    finally:
        log.close()

    # Kurung di inti yang diizinkan SEBELUM batch sempat menyalakan Stockfish;
    # Stockfish mewarisi affinity induknya. Penjaga menerapkannya ulang tiap
    # putaran untuk jaga-jaga kalau balapan ini kalah.
    from . import batas_cpu                        # noqa: PLC0415
    batas_cpu.terapkan(proses.pid, batas_cpu.masker(batas_cpu.baca_persen()))

    tugas = {
        "pid": proses.pid,
        "mulai": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "status": "berjalan",
        "jumlah": ringkas["jumlah"],
        "perkiraan_menit": ringkas["perkiraan_menit"],
        "profil": s.profil,
        "perintah": ringkas["perintah"],
        "log": str(path_log),
        # Saringannya ikut disimpan supaya batch yang berhenti di tengah bisa
        # dilanjutkan tanpa Ray menyusun ulang isian formulirnya.
        "saringan": s.model_dump(),
    }
    _tulis_tugas(_baca_tugas() + [tugas])
    _pindai["waktu"] = 0.0
    return {"tugas": tugas, "pratinjau": ringkas}


@router.get("/status", summary="Statistik analisis + tugas yang berjalan")
def status():
    conn = _buka()
    try:
        angka = angka_database(conn)
    finally:
        conn.close()
    st = angka["statistik"]

    tugas = _rangkum_tugas()
    dikenal = {t["pid"] for t in tugas}
    semua_berjalan = _pindai_batch()
    bersihkan_kemajuan_yatim({b["pid"] for b in semua_berjalan})
    lain = []
    for b in semua_berjalan:
        if b["pid"] in dikenal:
            continue
        b = dict(b)
        b.update(kemajuan_proses(b["pid"]))    # kemajuan batch dari terminal
        lain.append(b)
    return {
        "partai_dianalisis": st["partai_dianalisis"],
        "partai_total": st["partai_total"],
        "langkah_dianalisis": st["langkah_dianalisis"],
        "belum_saya": angka["belum_saya"],
        "belum_gm": angka["belum_gm"],
        "sumber": angka["sumber"],
        "tugas": tugas,
        "batch_lain": lain,
        # sibuk = slot penuh, bukan sekadar "ada yang jalan": halaman boleh
        # menawarkan tombol Mulai selama masih ada slot engine yang kosong.
        "batch_berjalan": len(semua_berjalan),
        "maks_batch": MAKS_BATCH,
        "sibuk": len(semua_berjalan) >= MAKS_BATCH,
        "jeda": _status_jeda(),
    }


def _status_jeda() -> dict:
    from . import jeda_mesin                 # noqa: PLC0415
    return jeda_mesin.status()


@router.get("/pemain", summary="Pemain yang bisa dipilih, diurutkan dari yang paling banyak belum dianalisis")
def pemain(sumber: str | None = None, cari: str | None = None, batas: int = 40,
           kelompok: str | None = None):
    """
    Isi dropdown Pemain. Ikut `sumber` dan `kelompok` yang sedang dipilih,
    supaya angkanya sesuai dengan yang akan benar-benar dianalisis.

    Rute ini sempat hilang pada B19 (13 Sep 2026) padahal `daftar_pemain()`
    tetap ada, jadi dropdown Pemain di halaman Mesin diam-diam jatuh ke mode
    "ketik sendiri". Dikembalikan dengan kontrak yang sama.
    """
    if kelompok not in (None, "", "gm", "saya", "semua"):
        raise HTTPException(status_code=422,
                            detail="kelompok harus 'gm', 'saya', atau kosong")
    conn = _buka()
    try:
        if sumber:
            ada = {x["nama"] for x in daftar_sumber(conn)}
            if sumber not in ada:
                raise HTTPException(
                    status_code=422,
                    detail=f"Sumber {sumber!r} tidak ada. Yang ada: {', '.join(sorted(ada))}")
        return {"sumber": sumber, "kelompok": kelompok,
                "pemain": daftar_pemain(conn, sumber, cari, batas, kelompok)}
    finally:
        conn.close()


class TambahAntrean(BaseModel):
    saringan: Saringan
    keterangan: str = Field("", max_length=200)


def _penjaga_jalan(segar: bool = False) -> int | None:
    """PID penjaga antrean kalau sedang jalan."""
    if segar:
        _pindai["waktu"] = 0.0
    for b in _pindai_proses("jaga_antrean.py"):
        if _proses_ada(b["pid"]):        # daftar bisa basi beberapa detik
            return b["pid"]
    return None


@router.get("/antrean", summary="Isi antrean analisis")
def lihat_antrean():
    from . import antrean as antre                 # noqa: PLC0415
    r = antre.ringkas()
    r["penjaga_pid"] = _penjaga_jalan()
    r["maks_batch"] = MAKS_BATCH
    return r


@router.post("/antrean", summary="Tambahkan pekerjaan ke antrean")
def tambah_antrean(masukan: TambahAntrean):
    """
    Pekerjaan disimpan dengan perkiraannya, bukan dijalankan sekarang.
    Penjaga (`jaga_antrean.py`) yang menjalankannya begitu ada slot kosong.
    """
    from . import antrean as antre                 # noqa: PLC0415
    s = masukan.saringan
    _periksa(s)
    _periksa_sumber(s)
    ringkas = _hitung(s)
    if ringkas["jumlah"] == 0:
        raise HTTPException(status_code=422,
                            detail="Tidak ada partai yang cocok, jadi tidak ada yang perlu diantrekan.")
    kerja = antre.tambah(s.model_dump(), masukan.keterangan or ringkas["perintah"],
                         ringkas["jumlah"], ringkas["perkiraan_menit"])
    return {"kerja": kerja, "pratinjau": ringkas, "penjaga_pid": _penjaga_jalan()}


@router.delete("/antrean/{id_}", summary="Buang satu pekerjaan dari antrean")
def hapus_antrean(id_: int):
    from . import antrean as antre                 # noqa: PLC0415
    if not antre.hapus(id_):
        raise HTTPException(status_code=404,
                            detail="Pekerjaan itu tidak ada, atau sedang berjalan "
                                   "(hentikan dulu dari daftar batch).")
    return {"id": id_, "status": "dibuang"}


@router.post("/antrean/jaga", summary="Hidupkan/matikan penjaga antrean")
def jaga_antrean(jalan: bool = True, maks: int = MAKS_BATCH):
    """
    Penjaga dijalankan sebagai proses LEPAS, sama seperti batch: dia harus
    tetap hidup walau server dimuat ulang atau halaman ditutup.
    """
    pid = _penjaga_jalan(segar=True)
    if not jalan:
        if pid is None:
            return {"penjaga_pid": None, "status": "memang tidak jalan"}
        # hanya penjaganya; batch yang sedang jalan dibiarkan selesai
        _matikan(pid, pohon=False)
        return {"penjaga_pid": None, "status": "dihentikan"}
    if pid is not None:
        return {"penjaga_pid": pid, "status": "sudah jalan"}

    FOLDER_LOG.mkdir(parents=True, exist_ok=True)
    perintah = [sys.executable, "-X", "utf8", "-u", "jaga_antrean.py", "--jaga",
                "--maks", str(max(1, min(int(maks), MAKS_BATCH)))]
    bendera = (subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
               if os.name == "nt" else 0)
    # stdout dibuang: penjaga menulis lognya sendiri ke log-antrean.txt.
    # Kalau keduanya menulis ke berkas yang sama, tiap baris muncul dua kali.
    try:
        proses = subprocess.Popen(perintah, cwd=str(AKAR), stdout=subprocess.DEVNULL,
                                  stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
                                  creationflags=bendera,
                                  start_new_session=(os.name != "nt"), close_fds=True)
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"Gagal menjalankan penjaga: {e}") from e
    _pindai["waktu"] = 0.0
    return {"penjaga_pid": proses.pid, "status": "dijalankan"}


@router.get("/cpu", summary="Batas pemakaian CPU untuk engine")
def lihat_batas_cpu():
    from . import batas_cpu                        # noqa: PLC0415
    persen = batas_cpu.baca_persen()
    return {"persen": persen, "pilihan": list(batas_cpu.PERSEN_VALID),
            "inti": batas_cpu.jumlah_inti(persen), "inti_total": batas_cpu.inti_total()}


@router.post("/cpu", summary="Ubah batas CPU dan langsung terapkan ke engine yang jalan")
def ubah_batas_cpu(persen: int):
    """
    Berlaku seketika untuk batch dan Stockfish yang SUDAH berjalan, tidak
    menunggu batch berikutnya — yang dikeluhkan Ray adalah laptop yang tidak
    bisa dipakai sekarang.
    """
    from . import batas_cpu                        # noqa: PLC0415
    try:
        batas_cpu.simpan_persen(persen)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    _pindai["waktu"] = 0.0
    return batas_cpu.terapkan_semua([b["pid"] for b in _pindai_batch()], persen)


@router.post("/jeda", summary="Jeda atau lanjutkan semua engine di laptop ini")
def jeda_semua(aktif: bool = True):
    """
    aktif=true: bekukan semua Stockfish (CPU kosong seketika) dan tahan batch
    baru. aktif=false: cairkan dan izinkan lagi. Lihat app/jeda_mesin.py.
    """
    from . import jeda_mesin                 # noqa: PLC0415
    _pindai["waktu"] = 0.0
    return jeda_mesin.jeda() if aktif else jeda_mesin.lanjutkan()


@router.post("/hentikan", summary="Hentikan satu batch analisis")
def hentikan(masukan: Penghentian):
    _pindai["waktu"] = 0.0
    if not _hidup(masukan.pid):
        raise HTTPException(status_code=404,
                            detail=f"Tidak ada batch analisis dengan PID {masukan.pid} "
                                   "yang sedang berjalan.")
    _matikan(masukan.pid)

    _pindai["waktu"] = 0.0
    daftar = _baca_tugas()
    for t in daftar:
        if t["pid"] == masukan.pid and t.get("status") == "berjalan":
            t["status"] = "dihentikan"
            t["sampai"] = datetime.now().strftime("%Y-%m-%d %H:%M")
    _tulis_tugas(daftar)
    return {"pid": masukan.pid, "status": "dihentikan",
            "catatan": "Partai yang sudah selesai tetap tersimpan; partai yang sedang "
                       "dihitung akan terpilih lagi di batch berikutnya. Pakai "
                       "\"Lanjutkan\" untuk meneruskan dengan saringan yang sama."}


@router.post("/lanjutkan", summary="Jalankan ulang batch yang berhenti sebelum selesai")
def lanjutkan(masukan: Penghentian):
    """
    Teruskan batch yang gagal atau dihentikan, memakai saringan yang sama.

    Ini untuk batch yang sudah BERHENTI. Untuk sekadar mengosongkan CPU
    sementara ada /jeda (app/jeda_mesin.py). Yang dipakai di sini adalah sifat
    `analisis_partai.py` sendiri — hasil disimpan tiap partai selesai, dan
    partai yang sudah ada di tabel `analisis` tidak dipilih lagi. Jadi
    menjalankan ulang saringan yang sama = melanjutkan dari tempat berhenti.

    Catatan jujur: dengan `--terbaru N`, batch lanjutan tetap mengambil N partai
    yang BELUM dianalisis, jadi jumlah totalnya bisa sedikit melebihi N yang
    semula diminta. Itu disengaja; memaksa hitungannya persis berarti menyimpan
    daftar id dan membuat batch lanjutan mengabaikan partai baru yang masuk.
    """
    for t in _baca_tugas():
        if t["pid"] != masukan.pid:
            continue
        tersimpan = t.get("saringan")
        if not tersimpan:
            raise HTTPException(
                status_code=400,
                detail="Batch ini dijalankan sebelum saringannya ikut dicatat, jadi "
                       "tidak bisa dilanjutkan otomatis. Susun saringannya lagi di atas.")
        hasil = mulai(Saringan(**tersimpan))
        hasil["dilanjutkan_dari"] = masukan.pid
        return hasil
    raise HTTPException(status_code=404,
                        detail=f"Batch PID {masukan.pid} tidak ada di catatan.")
