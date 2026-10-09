"""
Impor lewat halaman web: unggah PGN, atau tempel tautan broadcast Lichess.

Tujuannya supaya Ray bisa menambah partai sendiri tanpa membuka terminal.
Dua cara, keduanya mengakhiri pekerjaan dengan partai yang langsung bisa
dijelajahi:

    POST /api/impor/pgn         { pgn, sumber, nama }
    POST /api/impor/broadcast   { url }
    POST /api/impor/akun        { akun?, kecepatan?, maks?, semua_riwayat? }

Router terpisah dari `app/main.py` supaya berkas itu tidak perlu diubah
banyak; `main.py` hanya menyertakannya satu baris.

Tiga hal yang dijaga:

  * **Satu penulis besar pada satu waktu.** Kalau impor TWIC atau analisis
    batch sedang jalan, permintaan ditolak dengan **409** dan menyebut siapa
    pemegang kuncinya — bukan mati dengan 'database is locked'.
  * **Indeks posisi ikut dibangun** untuk partai yang baru masuk, jadi partai
    itu langsung muncul di Jelajah dan Repertoire. Tanpa ini, partai baru ada
    di daftar tapi tidak pernah muncul di statistik pembukaan.
  * **Batas ukuran.** Unggahan dibatasi supaya satu permintaan tidak menahan
    server bermenit-menit; berkas raksasa tetap lewat `impor_pgn.py`.
"""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .db import koneksi
from .impor import impor_teks
from .kunci_penulis import KunciPenulis, KunciTerpakai
from .posisi import id_belum_terindeks, indeks_partai, kosongkan_cache
from .tarik_akun import tarik_semua
from .sumber_impor import catat, sudah_selesai

router = APIRouter(prefix="/api/impor", tags=["impor"])

# Unggahan lebih besar dari ini ditolak dengan saran memakai impor_pgn.py.
# 8 MB kira-kira 20 ribu partai — jauh di atas satu turnamen.
MAKS_AKSARA = 8_000_000

# Indeks dibangun untuk paling banyak sekian partai per permintaan, supaya
# satu unggahan tidak menahan server terlalu lama.
MAKS_INDEKS = 5_000


class UnggahPgn(BaseModel):
    pgn: str = Field(..., description="isi berkas PGN")
    sumber: str = Field("unggahan", pattern=r"^[a-z0-9-]{2,20}$",
                        description="label sumber, mis. 'unggahan' atau 'pgnmentor'")
    nama: str | None = Field(None, description="nama berkas, untuk catatan")


class TarikAkun(BaseModel):
    """
    Tarik partai dari akun Lichess & Chess.com milik pemilik aplikasi.

    Bawaannya **semua kecepatan** — beda dari `ambil_partai.py` yang bawaannya
    rapid+classical. Alasannya: 97% partai Ray bullet/blitz, jadi bawaan lama
    membuat tombol ini seolah tidak menarik apa-apa.
    """
    akun: list[str] | None = Field(None, description="username tertentu; kosong = semua akun")
    kecepatan: list[str] | None = Field(
        None, description="bullet/blitz/rapid/classical/correspondence; kosong = semua")
    maks: int = Field(0, ge=0, le=5000, description="batas partai per akun, 0 = tanpa batas")
    semua_riwayat: bool = Field(
        False, description="abaikan partai terakhir di database dan tarik seluruh riwayat")
    hanya_rated: bool = True


class UnggahBroadcast(BaseModel):
    url: str = Field(..., description="tautan ronde atau turnamen broadcast Lichess")
    seluruh_turnamen: bool = Field(
        True, description="kalau tautannya satu ronde, ambil juga semua ronde lain di turnamennya")
    pakai_evaluasi_lichess: bool = Field(
        True, description="isi analisis dari [%eval] yang disertakan Lichess, tanpa engine")


def _indeks_yang_baru(conn: sqlite3.Connection) -> int:
    """Bangun indeks posisi untuk partai yang belum terindeks. Kembalikan jumlahnya."""
    ids = id_belum_terindeks(conn, batas=MAKS_INDEKS)
    n = 0
    for pid in ids:
        baris = conn.execute("SELECT pgn FROM partai WHERE id = ?", (pid,)).fetchone()
        if not baris or not baris["pgn"]:
            continue
        try:
            indeks_partai(conn, pid, baris["pgn"])
            n += 1
        except Exception:
            continue        # satu partai rusak tidak boleh membatalkan sisanya
        # Commit bertahap: satu transaksi untuk ribuan partai mengunci
        # database puluhan detik, dan penulis lain (jawaban latihan, posisi
        # tersimpan) menyerah setelah 5 detik -> 500 (audit 28 Sep 2026).
        if n % 25 == 0:
            conn.commit()
    conn.commit()
    # Dipanggil sesudah tiap impor web yang mengubah data (termasuk hasil
    # yang diperbarui tanpa partai baru, yang tidak menggeser MAX(id)).
    kosongkan_cache()
    return n


def _dengan_kunci(keterangan: str):
    """Ambil kunci penulis, atau lempar 409 yang menyebut pemegangnya."""
    try:
        return KunciPenulis(keterangan).ambil()
    except KunciTerpakai as e:
        raise HTTPException(status_code=409, detail=str(e)) from e


@router.post("/pgn", summary="Unggah berkas PGN")
def impor_pgn(masukan: UnggahPgn):
    teks = masukan.pgn
    if len(teks) > MAKS_AKSARA:
        raise HTTPException(
            status_code=413,
            detail=f"Berkas terlalu besar ({len(teks):,} aksara, batas {MAKS_AKSARA:,}). "
                   "Untuk berkas sebesar ini pakai: python impor_pgn.py <berkas> --sumber <label>")
    if "[Event " not in teks:
        raise HTTPException(status_code=422, detail="Isinya tidak terlihat seperti PGN: tidak ada tag [Event].")

    penjaga = _dengan_kunci("impor PGN lewat web" + (f" ({masukan.nama})" if masukan.nama else ""))
    conn = koneksi()
    try:
        hasil = impor_teks(conn, teks, sumber=masukan.sumber)
        conn.commit()
        terindeks = _indeks_yang_baru(conn)
    finally:
        conn.close()
        penjaga.lepas()

    diproses = hasil.baru + hasil.duplikat + hasil.diperbarui + hasil.dilewati
    return {
        "berkas": masukan.nama,
        "sumber": masukan.sumber,
        "diproses": diproses,
        "baru": hasil.baru,
        "duplikat": hasil.duplikat,
        "diperbarui": hasil.diperbarui,
        "dilewati": hasil.dilewati,
        # Sudah dihitung tapi dulu tidak dikirim: "dilewati: 1" tanpa alasan
        # tidak membedakan Chess960, langkah ilegal, atau header rusak.
        "alasan_dilewati": hasil.alasan_dilewati,
        # Termasuk partai lama yang belum terindeks, bukan hanya unggahan ini.
        "diindeks": terindeks,
    }


# Pemain dicari dulu (tabel kecil), baru partainya lewat idx_partai_putih/
# idx_partai_hitam. Bentuk lama `JOIN pemain p ON p.id IN (g.putih_id,
# g.hitam_id)` memaksa SQLite memindai SELURUH tabel partai sekali per akun:
# 27 Sep 2026 terukur 18 detik untuk lima akun, kini 0,03 detik per akun
# dengan hasil yang sama (McLung_07: 8.605 partai, terakhir 2026.09.17).
KUERI_JUMLAH_AKUN = """
    WITH p AS (SELECT id FROM pemain WHERE LOWER(nama) = LOWER(?))
    SELECT COUNT(*) AS jumlah, MAX(tanggal) AS terakhir
    FROM partai
    WHERE putih_id IN (SELECT id FROM p) OR hitam_id IN (SELECT id FROM p)
"""


@router.get("/akun", summary="Daftar akun sendiri yang bisa ditarik")
def daftar_akun():
    """
    Dipakai halaman Impor untuk membuat satu tombol per akun.

    Perlu karena menarik semua akun sekaligus bisa makan beberapa menit
    (Chess.com dibaca per bulan), jadi halaman memanggil `/akun` sekali per
    akun supaya hasilnya muncul bertahap. Daftarnya diambil dari
    `ambil_partai.AKUN` — satu-satunya tempat akun Ray ditulis — bukan
    disalin ke berkas web.
    """
    from ambil_partai import AKUN                      # noqa: PLC0415

    conn = koneksi()
    try:
        keluar = []
        for a in AKUN:
            baris = conn.execute(KUERI_JUMLAH_AKUN, (a.username,)).fetchone()
            keluar.append({
                "akun": a.username,
                "platform": a.platform,
                "partai": baris["jumlah"] or 0,
                "terakhir": baris["terakhir"],
                "saring_turnamen": a.saring_turnamen,
                "alasan_saringan": a.alasan_saringan,
            })
    finally:
        conn.close()
    return {"akun": keluar}


@router.post("/akun", summary="Tarik partai baru dari akun Lichess & Chess.com sendiri")
def impor_akun(masukan: TarikAkun):
    """
    Menarik hanya partai SESUDAH partai terakhir yang sudah ada per akun, jadi
    aman ditekan tiap hari: penarikan harian hanya beberapa partai, bukan
    mengunduh ulang seluruh riwayat. `semua_riwayat=true` untuk penarikan
    pertama atau kalau ada yang terlewat.
    """
    semua_kecepatan = ["bullet", "blitz", "rapid", "classical", "correspondence"]
    kecepatan = masukan.kecepatan or semua_kecepatan
    tak_dikenal = [k for k in kecepatan if k not in semua_kecepatan]
    if tak_dikenal:
        raise HTTPException(status_code=422,
                            detail=f"Kecepatan tidak dikenal: {', '.join(tak_dikenal)}")

    penjaga = _dengan_kunci("tarik partai akun lewat web")
    conn = koneksi()
    try:
        try:
            hasil = tarik_semua(conn, username=masukan.akun, kecepatan=kecepatan,
                                maks=masukan.maks, hanya_rated=masukan.hanya_rated,
                                semua_riwayat=masukan.semua_riwayat)
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e)) from e
        conn.commit()
        hasil["diindeks"] = _indeks_yang_baru(conn) if hasil["total_baru"] else 0
    finally:
        conn.close()
        penjaga.lepas()
    return hasil


class TarikAnalisisLichess(BaseModel):
    batas: int = Field(900, ge=0, le=20000,
                       description="berapa partai terbaru yang diperiksa; 0 = semua (bisa belasan menit)")


@router.post("/analisis-lichess", summary="Ambil analisis server Lichess untuk partai akun sendiri")
def tarik_analisis_lichess(masukan: TarikAnalisisLichess):
    """
    Partai Lichess milik Ray yang pernah dimintakan "Request computer analysis"
    langsung terisi analisisnya, tanpa engine laptop. Bawaannya hanya 900
    partai terbaru (±3 permintaan ke Lichess, kurang dari semenit); memeriksa
    semua ±9.900 partai terukur 7 menit.
    """
    from .eval_lichess import tarik_analisis_akun            # noqa: PLC0415
    penjaga = _dengan_kunci("ambil analisis Lichess lewat web")
    conn = koneksi()
    try:
        try:
            hasil = tarik_analisis_akun(conn, batas=masukan.batas)
        except Exception as e:                                # noqa: BLE001 - jaringan / 429
            raise HTTPException(status_code=502, detail=f"Lichess tidak bisa dihubungi: {e}") from e
    finally:
        conn.close()
        penjaga.lepas()
    return hasil


@router.post("/broadcast", summary="Impor dari tautan broadcast Lichess")
def impor_broadcast(masukan: UnggahBroadcast):
    # Diimpor di sini, bukan di atas: impor_broadcast.py ada di akar proyek,
    # dan server tidak boleh gagal start hanya karena berkas itu dipindah.
    import sys
    from pathlib import Path
    akar = str(Path(__file__).resolve().parents[1])
    if akar not in sys.path:
        sys.path.insert(0, akar)
    try:
        from impor_broadcast import (BroadcastError, id_ronde, info_ronde,
                                     info_turnamen, pgn_ronde)
    except ImportError as e:
        raise HTTPException(status_code=500, detail=f"impor_broadcast.py tidak bisa dimuat: {e}") from e

    try:
        rid = id_ronde(masukan.url)
    except BroadcastError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e

    # Tautan bisa menunjuk satu ronde atau satu turnamen. Coba sebagai ronde
    # dulu; kalau Lichess mengenalinya sebagai turnamen, ambil semua rondenya.
    try:
        info = info_ronde(rid)
        ronde = [{"id": rid, "name": (info.get("round") or {}).get("name", rid)}]
        turnamen = (info.get("tour") or {}).get("name", "")
        # Tautan yang ditempel dari browser hampir selalu tautan RONDE, padahal
        # yang dimaksud turnamennya: 13 Sep 2026 tautan Global Chess League
        # hanya mengambil 1 dari 30 ronde. Rondenya dicari lewat turnamen induk.
        id_tour = (info.get("tour") or {}).get("id")
        if masukan.seluruh_turnamen and id_tour:
            try:
                semua = info_turnamen(id_tour).get("rounds", [])
                if semua:
                    ronde = semua
            except BroadcastError:
                pass                              # tetap satu ronde, jangan gagal total
    except BroadcastError:
        try:
            d = info_turnamen(rid)
            ronde = d.get("rounds", [])
            turnamen = (d.get("tour") or {}).get("name", rid)
        except BroadcastError as e:
            raise HTTPException(status_code=422, detail=f"Bukan ronde maupun turnamen yang dikenal: {e}") from e
    if not ronde:
        raise HTTPException(status_code=422, detail="Turnamen ini belum punya ronde.")

    penjaga = _dengan_kunci("impor broadcast lewat web")
    conn = koneksi()
    rincian, total = [], {"baru": 0, "duplikat": 0, "diperbarui": 0, "dilewati": 0}
    try:
        for r in ronde:
            r_id, nama = r.get("id"), r.get("name", "?")
            if not r_id:
                continue
            if sudah_selesai(conn, "lichess-broadcast", r_id):
                rincian.append({"ronde": nama, "status": "sudah lengkap, dilewati"})
                continue
            try:
                teks = pgn_ronde(r_id)
            except BroadcastError as e:
                rincian.append({"ronde": nama, "status": f"gagal: {e}"})
                continue
            jumlah = teks.count("[Event ")
            final = jumlah > 0 and '[Result "*"]' not in teks
            url_ronde = f"https://lichess.org/broadcast/-/-/{r_id}"
            h = impor_teks(conn, teks, sumber="lichess-broadcast", sumber_url_bawaan=url_ronde)
            catat(conn, "lichess-broadcast", r_id, nama, url_ronde, jumlah, jumlah, h.baru,
                  selesai=final)
            for k in total:
                total[k] += getattr(h, k, 0)
            rincian.append({"ronde": nama, "partai": jumlah, "baru": h.baru,
                            "duplikat": h.duplikat,
                            "status": "final" if final else "masih berjalan"})
        conn.commit()
        terindeks = _indeks_yang_baru(conn)
        # Evaluasi yang disertakan Lichess langsung dipakai sebagai analisis,
        # jadi partai siaran tidak perlu ikut antre di engine laptop.
        dari_eval = {"calon": 0, "dianalisis": 0, "dilewati": 0}
        if masukan.pakai_evaluasi_lichess:
            from .eval_lichess import isi as isi_dari_eval      # noqa: PLC0415
            dari_eval = isi_dari_eval(conn)
    finally:
        conn.close()
        penjaga.lepas()

    return {"turnamen": turnamen, "ronde": rincian, "diindeks": terindeks,
            "dianalisis_lichess": dari_eval["dianalisis"],
            "evaluasi_tidak_lengkap": dari_eval["dilewati"], **total}


# ------------------------------------------------- pembaruan GM terkenal
#
# Ray, 15 Sep 2026: "buatkan fitur untuk membaca langsung dari broadcast,
# jadinya nanti database bisa diupdate setiap minggunya dengan memasukan game
# baru para gm dengan ketentuan harus terkenal/super gm".
#
# Sengaja lewat TOMBOL, bukan jadwal otomatis: Ray tidak mau ada Python yang
# menyala sendiri di laptopnya (13 Sep 2026). Halaman menunjukkan kapan terakhir
# diperbarui supaya ketahuan kalau sudah lewat seminggu.
#
# Pekerjaannya bisa belasan menit (puluhan turnamen, jeda 1,5 detik per
# permintaan ke Lichess), jadi dijalankan sebagai proses lepas seperti batch
# analisis, dan halaman membaca log-nya.
#
# Sejak 9 Okt 2026 tombol ini menjalankan perbarui.py LENGKAP, bukan hanya
# impor_broadcast.py. Ray: "masih 0 partai dari twic ... perlu ada fitur pull
# data dari sana" — TWIC sebulan tertinggal karena hanya bisa lewat terminal.
# Satu tombol = perintah mingguan yang sama dengan CLI: cadangan dulu (jalan
# mundur sebelum menulis), ekspor inti ke cloud, TWIC, broadcast, indeks.
# Saringan broadcast-nya tetap sama (perbarui.py meneruskan tier 4, 10 hari,
# Elo bintang 2400), dan pembaruan-broadcast.json tetap ditulis impor_broadcast.

PERINTAH_MINGGUAN = ["perbarui.py"]


def twic_terakhir(conn: sqlite3.Connection) -> dict | None:
    """Edisi TWIC terbaru yang sudah selesai diimpor, supaya ketinggalan kelihatan di halaman."""
    baris = conn.execute(
        "SELECT kunci, diimpor_pada, jumlah_baru FROM sumber_impor "
        "WHERE jenis = 'twic' AND selesai = 1 ORDER BY CAST(kunci AS INTEGER) DESC LIMIT 1"
    ).fetchone()
    if baris is None:
        return None
    return {"edisi": int(baris[0]), "diimpor_pada": baris[1], "baru": baris[2]}


def _berkas_mingguan():
    from pathlib import Path
    akar = Path(__file__).resolve().parents[1]
    return (akar, akar / "data" / "laporan" / "log-broadcast-mingguan.txt",
            akar / "data" / "proses-broadcast-mingguan.json")


@router.get("/mingguan", summary="Status pembaruan mingguan partai GM (TWIC + broadcast Lichess)")
def status_mingguan():
    import json
    from datetime import datetime
    from .analisa_web import _proses_ada
    akar, log, proses = _berkas_mingguan()
    try:
        p = json.loads(proses.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        p = {}
    jalan = bool(p.get("pid")) and _proses_ada(int(p["pid"]))
    try:
        terakhir = json.loads((akar / "data" / "pembaruan-broadcast.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        terakhir = None
    hari_lalu = None
    if terakhir and terakhir.get("selesai"):
        try:
            hari_lalu = (datetime.now() - datetime.strptime(terakhir["selesai"], "%Y-%m-%d %H:%M")).days
        except ValueError:
            pass
    ekor = []
    if log.is_file():
        ekor = [b for b in log.read_text(encoding="utf-8", errors="replace").splitlines() if b.strip()][-12:]
    conn = koneksi()
    try:
        twic = twic_terakhir(conn)
    finally:
        conn.close()
    return {"jalan": jalan, "mulai": p.get("mulai") if jalan else None, "terakhir": terakhir,
            "hari_lalu": hari_lalu, "ekor_log": ekor, "twic": twic,
            "aturan": "TWIC edisi baru: kedua pemain Elo >= 2500, partai online dibuang; "
                      "broadcast: salah satu pemain Elo >= 2400 atau bergelar GM/WGM, turnamen resmi "
                      "Lichess tier 4-5 yang berlangsung dalam 10 hari terakhir"}


@router.post("/mingguan", summary="Jalankan pembaruan mingguan sekarang (perbarui.py: cadangan, TWIC, broadcast, indeks)")
def mulai_mingguan():
    import json
    import os
    import subprocess
    import sys
    from datetime import datetime
    from .kunci_penulis import status as status_kunci
    if status_mingguan()["jalan"]:
        raise HTTPException(status_code=409, detail="Pembaruan sedang berjalan.")
    bebas, pemegang = status_kunci()
    if not bebas:
        raise HTTPException(status_code=409, detail=f"Database sedang dipakai penulis lain: {pemegang}")
    akar, log, proses = _berkas_mingguan()
    log.parent.mkdir(parents=True, exist_ok=True)
    bendera = (subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
               | subprocess.BELOW_NORMAL_PRIORITY_CLASS) if os.name == "nt" else 0
    with log.open("w", encoding="utf-8") as fh:
        p = subprocess.Popen([sys.executable, "-X", "utf8", "-u", *PERINTAH_MINGGUAN],
                             cwd=str(akar), stdout=fh, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                             creationflags=bendera, close_fds=True)
    proses.write_text(json.dumps({"pid": p.pid, "mulai": datetime.now().strftime("%Y-%m-%d %H:%M")}),
                      encoding="utf-8", newline="\n")
    return {"jalan": True, "pid": p.pid}
