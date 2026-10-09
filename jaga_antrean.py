"""
Penjaga antrean analisis: menjalankan pekerjaan berikutnya begitu slot kosong.

Ray, 13 Sep 2026: "bisa buatkan fitur untuk aktif terus?" Tanpa ini, satu
batch selesai lalu engine menganggur sampai ada yang menekan Mulai.

    python jaga_antrean.py --jaga          # jaga terus (lepas dari terminal)
    python jaga_antrean.py --sekali        # satu kali periksa lalu keluar
    python jaga_antrean.py --lihat         # isi antrean saja

Yang PERLU diluruskan: tidak ada yang berjalan saat komputer dimatikan —
Windows mematikan semua proses. Yang dijamin penjaga ini: antreannya tersimpan
di berkas, jadi begitu komputer hidup lagi dan penjaga dijalankan (bisa lewat
folder Startup lewat `--daftarkan`), sisa pekerjaan dilanjutkan sendiri.

Batas jumlah batch tetap aturan yang sama dengan halaman Mesin (CLAUDE.md
bagian 7): `MAKS_BATCH` di app/analisa_web.py, sekarang 2. Penjaga memakai
batas yang sama (bawaan 2); `--maks N` untuk mengubahnya.
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime
from pathlib import Path

AKAR = Path(__file__).resolve().parent
sys.path.insert(0, str(AKAR))

from app import antrean
from app.db import FOLDER_DATA
from app.proses import masih_hidup
from app.analisa_web import (MAKS_BATCH, Saringan, _pindai, _pindai_batch,
                             batch_berjalan,
                             _pindai_proses, _status_akhir, luncurkan)

# Batas jumlah batch yang boleh berjalan bersamaan MENURUT PENJAGA. Dihitung
# dari seluruh batch di mesin, bukan cuma milik penjaga, jadi penjaga tidak
# pernah membuat mesin melewati aturan bersama. Bawaannya 2 = batas mesin
# (13 Sep sore sempat 3 atas permintaan Ray, malamnya kembali 2 karena RAM
# penuh — CLAUDE.md bagian 7). Aman
# karena batch berprioritas idle dan dikurung batas CPU (app/batas_cpu.py).
MAKS_PENJAGA_BAWAAN = 2

# Jeda antar pemeriksaan. Satu partai ±1 menit, jadi tidak perlu rapat-rapat.
JEDA_BAWAAN = 60.0

PATH_LOG = FOLDER_DATA / "laporan" / "log-antrean.txt"

# Pengisi slot kosong. Ray, 13 Sep 2026 malam: "kenapa ini cuma 2 sayang
# waktunya harusnya berjalan 4 terus terusan". Kalau antrean habis tapi slot
# masih kosong, penjaga membuat sendiri pekerjaan "semua partai yang belum
# dianalisis", profil cepat (keputusan Ray: cepat dulu, teliti belakangan),
# partai GM dulu lalu partai sendiri. Tiap pengisi memakai --bagian K/N yang
# berbeda, jadi dua pengisi tidak pernah memilih partai yang sama.
ISI_SUMBER = ("gm", "lichess-user", "chesscom")
ISI_JUMLAH = 2000

# Nama tugas Windows yang menghidupkan penjaga tiap Ray masuk (logon). Sengaja
# LOGON, bukan STARTUP: tidak perlu hak administrator.
NAMA_TUGAS = "CaturPenjagaAntrean"


def _catat(pesan: str) -> None:
    baris = f"{datetime.now():%Y-%m-%d %H:%M:%S}  {pesan}"
    print(baris, flush=True)
    try:
        PATH_LOG.parent.mkdir(parents=True, exist_ok=True)
        with PATH_LOG.open("a", encoding="utf-8") as f:
            f.write(baris + "\n")
    except OSError:
        pass


def _berkas_startup() -> Path:
    """
    Berkas .cmd di folder Startup pengguna.

    Task Scheduler ditolak di mesin ini ("Access is denied" walau ONLOGON +
    /RL LIMITED), dan menaikkan hak administrator hanya untuk ini tidak
    sepadan. Folder Startup melakukan hal yang sama untuk pengguna ini,
    tanpa hak khusus, dan isinya bisa dilihat/dihapus sendiri oleh Ray.
    """
    import os
    dasar = Path(os.environ.get("APPDATA", Path.home())) /         "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
    return dasar / f"{NAMA_TUGAS}.cmd"


def tugas_terdaftar() -> bool:
    return _berkas_startup().exists()


def daftarkan_tugas(maks: int) -> bool:
    """
    Hidupkan penjaga sendiri tiap Ray masuk ke Windows.

    Perintahnya memakai --cabut-kalau-habis, jadi pendaftaran ini menghapus
    dirinya sendiri begitu antrean habis — permintaan Ray: "matikan lagi
    setelah sudah semua partai dimasukan". Tidak ada sisa yang menggantung
    setelah pekerjaannya rampung.

    pythonw.exe dipakai kalau ada supaya tidak ada jendela konsol yang muncul
    saat komputer dinyalakan.
    """
    berkas = _berkas_startup()
    juru = Path(sys.executable)
    tanpa_jendela = juru.with_name("pythonw.exe")
    if tanpa_jendela.exists():
        juru = tanpa_jendela
    # Baris .cmd dipisah CRLF (chr(13)+chr(10)) supaya tidak ada urusan
    # escape di dalam sumber ini.
    crlf = chr(13) + chr(10)
    isi = crlf.join([
        "@echo off",
        "rem Penjaga antrean analisis catur - dibuat jaga_antrean.py --daftarkan.",
        "rem Berkas ini menghapus dirinya sendiri begitu antrean habis.",
        f'cd /d "{AKAR}"',
        f'start "" "{juru}" -X utf8 -u "{AKAR / "jaga_antrean.py"}"'
        f" --jaga --maks {maks} --cabut-kalau-habis",
        "",
    ])
    try:
        berkas.parent.mkdir(parents=True, exist_ok=True)
        berkas.write_text(isi, encoding="utf-8", newline="")
    except OSError as e:
        _catat(f"daftarkan penjaga: GAGAL ({e})")
        return False
    _catat(f"penjaga didaftarkan di Startup: {berkas}")
    return True


def cabut_tugas(diam: bool = False) -> bool:
    berkas = _berkas_startup()
    if not berkas.exists():
        if not diam:
            _catat("penjaga memang tidak terdaftar di Startup")
        return False
    try:
        berkas.unlink()
    except OSError as e:
        _catat(f"cabut penjaga: GAGAL ({e})")
        return False
    _catat(f"penjaga dicabut dari Startup: {berkas}")
    return True


def penjaga_lain_jalan() -> int | None:
    """
    PID penjaga lain yang sudah berjalan.

    Perlu karena tugas logon bisa menyalakan penjaga kedua di atas yang sudah
    dihidupkan dari halaman Mesin; dua penjaga akan berebut antrean yang sama.

    Hanya penjaga yang LEBIH TUA yang dihitung. Dulu siapa pun dihitung, jadi
    dua penjaga yang menyala di detik yang sama (pintasan Nyalakan Server +
    tombol di halaman Mesin) saling melihat dan KEDUANYA berhenti: 29 Sep
    18:49:33, PID 4272 dan 15276, tidak ada penjaga sama sekali. Sekarang
    yang lebih muda mengalah (waktu lahir proses; PID kalau sama).
    """
    import os
    from app.proses import waktu_lahir_proses
    saya = os.getpid()

    def umur(pid):
        lahir = waktu_lahir_proses(pid)
        return (lahir if lahir is not None else float("inf"), pid)

    for b in _pindai_proses("jaga_antrean.py"):
        if b["pid"] == saya or "--jaga" not in (b.get("perintah") or ""):
            continue                     # diri sendiri, atau `--lihat`/`--sekali`
        if umur(b["pid"]) < umur(saya):
            return b["pid"]
    return None


# Pekerjaan yang terputus (mis. komputer dimatikan di tengah batch) diantrekan
# ulang sampai sekian kali. Aman: analisis_partai.py melewati partai yang sudah
# dianalisis, jadi ulangan hanya melanjutkan sisanya. Batasnya ada supaya
# pekerjaan yang memang selalu mogok tidak berputar selamanya.
MAKS_ULANG = 3


def segarkan_status() -> None:
    """
    Pekerjaan yang prosesnya sudah tidak ada ditandai selesai — atau, kalau
    berhenti sebelum selesai, diantrekan ulang (paling banyak MAKS_ULANG kali).

    Sebelumnya pekerjaan yang terputus langsung dicap gagal: 13 Sep 2026
    pekerjaan #1 (62 partai rapid) terhenti di partai ke-3 waktu komputer
    dimatikan, lalu dibiarkan begitu saja walau 59 partainya belum dikerjakan.
    """
    hidup = {b["pid"] for b in _pindai_batch()}
    for kerja in antrean.baca():
        if kerja.get("status") != "jalan":
            continue
        if kerja.get("pid") in hidup:
            continue
        # Tidak ada di pindaian belum tentu mati: pindaian bisa gagal sesaat.
        # 27 Sep 17:03 batch 22436 yang masih hidup dicap terputus di sini dan
        # penjaga meluncurkan kembarannya (29292). Butuh bukti kedua.
        if kerja.get("pid") and masih_hidup(kerja["pid"], kerja.get("mulai")):
            continue
        status, alasan = _status_akhir(Path(kerja.get("log", "")))
        ulang = kerja.get("ulang", 0)
        if status == "gagal" and ulang < MAKS_ULANG:
            antrean.perbarui(kerja["id"], status="menunggu", ulang=ulang + 1,
                             alasan=f"terputus ({alasan}); diantrekan ulang ke-{ulang + 1}")
            _catat(f"#{kerja['id']} terputus — diantrekan ulang ({ulang + 1}/{MAKS_ULANG})")
            continue
        antrean.perbarui(kerja["id"], status=status, alasan=alasan,
                         sampai=datetime.now().strftime("%Y-%m-%d %H:%M"))
        _catat(f"#{kerja['id']} {status}" + (f" — {alasan}" if alasan else ""))


def masih_ada_kerja() -> bool:
    """
    Antrean belum rampung selama ada yang MENUNGGU atau masih JALAN.

    "Tidak ada yang menunggu" bukan berarti selesai: 13 Sep 2026 penjaga
    berhenti dan mencabut pendaftaran Startup-nya padahal empat pekerjaan
    masih berjalan — kalau komputer mati saat itu, tidak ada yang melanjutkan.
    """
    return any(k.get("status") in ("menunggu", "jalan") for k in antrean.baca())


def _ada_yang_belum(sumber: str, bagian: str) -> bool:
    """
    Masih ada partai sumber ini di bagian ini yang belum dianalisis?

    Syaratnya HARUS sama dengan yang dipilih analisis_partai.pilih_partai(),
    termasuk `hasil <> '*'`. Audit 27 Sep 2026: tanpa itu, bagian yang tinggal
    berisi partai '*' dianggap "masih ada", pekerjaannya selesai dengan "Tidak
    ada partai", lalu ditambahkan lagi — tanpa henti, tiap putaran.
    """
    from app.db import koneksi
    k, n = (int(x) for x in bagian.split("/"))
    if sumber == "gm":
        from app.posisi import SUMBER_GM
        daftar = list(SUMBER_GM)
    else:
        daftar = [sumber]
    conn = koneksi()
    try:
        return conn.execute(
            f"""SELECT 1 FROM partai g WHERE g.sumber IN ({','.join('?' * len(daftar))})
                AND g.id % ? = ? AND g.jumlah_langkah >= 10 AND g.hasil <> '*'
                AND NOT EXISTS (SELECT 1 FROM analisis a WHERE a.partai_id = g.id)
                LIMIT 1""", daftar + [n, k]).fetchone() is not None
    finally:
        conn.close()


def isi_slot_kosong(maks: int, berjalan: int) -> dict | None:
    """
    Antrean habis tapi slot masih kosong: tambahkan satu pekerjaan pengisi.
    Kembalikan pekerjaan yang ditambahkan, atau None kalau semua sudah dianalisis.
    """
    n = MAKS_BATCH
    if berjalan >= min(maks, n):
        return None
    dipakai = {k["saringan"].get("bagian") for k in antrean.baca()
               if k.get("otomatis") and k.get("status") in ("menunggu", "jalan")}
    for sumber in ISI_SUMBER:
        for k in range(n):
            bagian = f"{k}/{n}"
            if bagian in dipakai or not _ada_yang_belum(sumber, bagian):
                continue
            s = Saringan(sumber=sumber, terbaru=ISI_JUMLAH, profil="cepat", bagian=bagian)
            nama = "partai GM" if sumber == "gm" else f"partai {sumber}"
            kerja = antrean.tambah(s.model_dump(), f"otomatis: {nama} yang belum dianalisis, "
                                                   f"bagian {bagian}, profil cepat", ISI_JUMLAH)
            antrean.perbarui(kerja["id"], otomatis=True)
            _catat(f"#{kerja['id']} ditambahkan otomatis untuk mengisi slot kosong: {nama}, bagian {bagian}")
            return kerja
    return None


def satu_putaran(maks: int, isi_otomatis: bool = True) -> bool:
    """
    Satu kali periksa. Kembalikan True selama pekerjaannya belum rampung
    (masih ada yang menunggu atau masih berjalan).

    Batasnya dihitung dari SELURUH batch yang berjalan (termasuk yang Ray
    jalankan sendiri), supaya penjaga tidak pernah membuat mesin melewati
    aturan bersama.
    """
    _pindai["waktu"] = 0.0
    segarkan_status()

    # Batas CPU diterapkan ulang tiap putaran: batch dari terminal dan
    # Stockfish yang terlanjur jalan sebelum dikurung ikut tertangkap.
    try:
        from app import batas_cpu
        batas_cpu.terapkan_semua([b["pid"] for b in _pindai_batch()])
    except Exception as e:               # noqa: BLE001 - jangan hentikan penjaga karena ini
        _catat(f"batas CPU tidak bisa diterapkan: {e}")

    # Dijeda dari halaman Mesin: jangan menyalakan apa pun, dan bekukan
    # Stockfish yang menyala sesudah tombol Jeda ditekan.
    from app import jeda_mesin
    if jeda_mesin.sedang_jeda():
        try:
            jeda_mesin.bekukan_yang_lolos()
        except Exception as e:           # noqa: BLE001
            _catat(f"pembekuan gagal: {e}")
        return True

    kerja = antrean.berikutnya()
    if kerja is None:
        if isi_otomatis:
            try:
                kerja = isi_slot_kosong(maks, len(batch_berjalan()))
            except Exception as e:           # noqa: BLE001 - mis. database terkunci; coba putaran berikutnya
                _catat(f"pengisi slot gagal: {e}")
        if kerja is None:
            return masih_ada_kerja()      # yang jalan tetap ditunggu sampai selesai

    berjalan = batch_berjalan()
    if len(berjalan) >= min(maks, MAKS_BATCH):
        return True                      # slot penuh; coba lagi nanti

    try:
        s = Saringan(**kerja["saringan"])
    except Exception as e:               # noqa: BLE001 - antrean bisa disunting tangan
        antrean.perbarui(kerja["id"], status="gagal", alasan=f"saringan tidak sah: {e}")
        _catat(f"#{kerja['id']} gagal: saringan tidak sah ({e})")
        return True

    try:
        hasil = luncurkan(s)
    except Exception as e:               # noqa: BLE001 - mis. 409/422 dari pemeriksaan
        pesan = getattr(e, "detail", str(e))
        # Saringan kembar dengan batch yang sedang jalan: bukan gagal, cuma
        # belum waktunya. Kalau ditandai gagal, pekerjaan yang sah hilang dari
        # antrean hanya karena kebetulan bentrok (25 Sep 2026).
        if "saringan yang sama persis" in str(pesan):
            _catat(f"#{kerja['id']} ditunda: {str(pesan)[:120]}")
            return True
        # 0 partai = tidak ada yang perlu dikerjakan; itu bukan kegagalan.
        status = "selesai" if "Tidak ada partai" in str(pesan) else "gagal"
        antrean.perbarui(kerja["id"], status=status, alasan=str(pesan)[:200])
        _catat(f"#{kerja['id']} {status}: {str(pesan)[:120]}")
        return True

    t = hasil["tugas"]
    antrean.perbarui(kerja["id"], status="jalan", pid=t["pid"], log=t["log"],
                     mulai=t["mulai"], jumlah=t["jumlah"],
                     perkiraan_menit=t["perkiraan_menit"])
    _catat(f"#{kerja['id']} jalan — PID {t['pid']}, {t['jumlah']} partai, "
           f"~{t['perkiraan_menit']} menit: {t['perintah']}")
    return True


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--jaga", action="store_true", help="jaga terus sampai antrean habis")
    p.add_argument("--sekali", action="store_true", help="satu kali periksa lalu keluar")
    p.add_argument("--lihat", action="store_true", help="tampilkan isi antrean")
    p.add_argument("--maks", type=int, default=MAKS_PENJAGA_BAWAAN,
                   help=f"maksimal batch berjalan bersamaan (bawaan {MAKS_PENJAGA_BAWAAN})")
    p.add_argument("--jeda", type=float, default=JEDA_BAWAAN,
                   help=f"detik antar pemeriksaan (bawaan {JEDA_BAWAAN:.0f})")
    p.add_argument("--daftarkan", action="store_true",
                   help="daftarkan penjaga ke folder Startup (hidup tiap logon)")
    p.add_argument("--cabut", action="store_true",
                   help="cabut pendaftaran itu")
    p.add_argument("--cabut-kalau-habis", action="store_true", dest="cabut_habis",
                   help="cabut pendaftarannya sendiri begitu antrean habis")
    a = p.parse_args()

    if a.daftarkan:
        return 0 if daftarkan_tugas(a.maks) else 1
    if a.cabut:
        cabut_tugas()
        return 0

    if a.lihat or not (a.jaga or a.sekali):
        r = antrean.ringkas()
        if not r["antrean"]:
            print("Antrean kosong.")
            return 0
        for k in r["antrean"]:
            ket = k.get("keterangan") or (k.get("saringan") or {}).get("sumber", "")
            print(f"  #{k['id']:<3} {k['status']:<9} {str(k.get('jumlah') or '?'):>5} partai  {ket}")
        print(f"\n{r['hitung']['menunggu']} menunggu, perkiraan {r['perkiraan_menit_sisa']} menit.")
        return 0

    if a.sekali:
        satu_putaran(a.maks)
        return 0

    lain = penjaga_lain_jalan()
    if lain is not None:
        _catat(f"penjaga lain sudah jalan (PID {lain}); yang ini berhenti supaya "
               "tidak dua-duanya berebut antrean")
        return 0

    _catat(f"penjaga mulai (maks {a.maks} batch, periksa tiap {a.jeda:.0f} detik)")
    try:
        while satu_putaran(a.maks):
            time.sleep(a.jeda)
    except KeyboardInterrupt:
        _catat("penjaga dihentikan dari papan ketik")
        return 0
    _catat("antrean habis; penjaga berhenti")
    if a.cabut_habis:
        # Permintaan Ray: jangan tinggalkan pengaturan sistem yang menggantung
        # setelah pekerjaannya rampung.
        cabut_tugas(diam=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
