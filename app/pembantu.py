"""
Pembantu analisis: tablet/HP di WiFi rumah ikut menghitung lewat browser.

Ray, 13 Sep 2026: "saya ada tab dan hp mungkin bisa membantu proses analisa
ini karena kalau laptop semua cpunya tidak kuat" — lalu "boleh" untuk versi
browser. Keputusan lama "engine tetap di server, bukan WASM di browser"
(SESI.md) diubah Ray HANYA untuk pembantu ini; analisis di laptop tetap
Stockfish 18 native.

Alurnya:
    1. Pembantu membuka http://<alamat laptop>:8000/pembantu dan memasukkan
       kode yang tampil di halaman Mesin.
    2. POST /api/pembantu/ambil  -> satu partai yang belum dianalisis,
       "disewa" SEWA_DETIK supaya tidak dikerjakan dua perangkat.
    3. Browser menjalankan Stockfish 18 lite (WASM, satu inti) di tiap posisi.
    4. POST /api/pembantu/kirim  -> evaluasi per posisi. Laporan disusun di
       sini oleh analisis_partai() yang SAMA dengan batch laptop, jadi
       akurasi, klasifikasi, dan langkah terbaik dihitung dengan rumus yang
       sama persis; browser hanya menggantikan engine-nya.

Pengamanan dua lapis (lihat `mode_pembantu_saja()`):
  * Server yang dibuka ke internet dijalankan dengan `CATUR_MODE=pembantu`
    (`jalankan-pembantu.cmd`, port 8001). Dalam mode itu HANYA JALUR_LUAR yang
    dilayani, siapa pun pemanggilnya — tidak bergantung pada header apa pun.
  * Server biasa (port 8000, mode penuh) tetap menolak jalur di luar
    JALUR_LUAR untuk alamat non-lokal atau permintaan berheader proxy.
Kedua-duanya wajib kode untuk API pembantu; `/api/pembantu/info` dan `/kode`
sengaja TIDAK ada di JALUR_LUAR, jadi kode tidak pernah bisa dibaca dari luar.

Urutan partai: partai GM dulu, lalu partai akun sendiri (Ray, 13 Sep sore:
"utamakan yang partai gm dulu"); di dalam tiap kelompok dari yang TERLAMA.
Batch laptop memilih yang terbaru (--terbaru), jadi keduanya jarang
bertabrakan; batch juga memeriksa ulang tiap partai tepat sebelum dihitung dan
melewati yang sudah dianalisis pembantu.

Engine browser tidak ikut git (biner 7 MB, sama seperti engine/):
    python -m app.pembantu --unduh-engine
"""

from __future__ import annotations

import argparse
import io
import json
import secrets
import socket
import sqlite3
import sys
import threading
from collections import deque
import time
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse
from pydantic import BaseModel, Field

AKAR = Path(__file__).resolve().parents[1]
if str(AKAR / "src") not in sys.path:
    sys.path.insert(0, str(AKAR / "src"))

import chess                                               # noqa: E402
import chess.pgn                                           # noqa: E402

from catur_analysis.engine import CEPAT, HEMAT, Stockfish         # noqa: E402
from catur_analysis.partai import SKOR_MAT, analisis_partai   # noqa: E402

from .analisa_web import SUMBER_SAYA                       # noqa: E402
from .analisis_db import simpan_laporan                    # noqa: E402
from .batas_cpu import PATH_PENGATURAN                     # noqa: E402
from .db import FOLDER_DATA, koneksi                       # noqa: E402

FOLDER_ENGINE = AKAR / "engine" / "browser"
BERKAS_ENGINE = ("stockfish-18-lite-single.js", "stockfish-18-lite-single.wasm")
URL_PAKET = "https://registry.npmjs.org/stockfish/-/stockfish-18.0.8.tgz"

NAMA_ENGINE = "Stockfish 18 lite (browser pembantu)"
PROFIL = CEPAT.nama            # depth 10, sama dengan profil cepat laptop
DEPTH = CEPAT.depth

# Tahap kedua: analisis mendalam. Ray, 13 Sep 2026 malam: "kalau ini sudah
# cepat suruh mereka untuk analisa mendalam lagi ya ketika sudah beres semua".
# Begitu tidak ada lagi partai tanpa analisis, pembantu mengulang partai yang
# analisisnya masih kasar — profil cepat, atau evaluasi siaran Lichess yang
# tidak punya langkah terbaik — dengan depth profil hemat. Analisis server
# Lichess dan hasil hemat/normal/dalam laptop tidak diulang.
PROFIL_PER_DEPTH = {CEPAT.depth: CEPAT.nama, HEMAT.depth: HEMAT.nama}
DEPTH_MENDALAM = HEMAT.depth
ENGINE_KASAR = ("Lichess (evaluasi siaran)",)
SEWA_DETIK = 20 * 60           # partai dikembalikan ke antrean kalau pembantu diam selama ini
AKTIF_DETIK = 3 * 60           # pembantu dianggap masih aktif kalau mengirim kabar dalam rentang ini

# Jalur yang boleh dibuka dari perangkat lain. Semuanya selain ini hanya dari laptop.
JALUR_LUAR = ("/pembantu", "/static/pembantu.html", "/static/pembantu.js",
              "/api/pembantu/ambil", "/api/pembantu/kirim", "/api/pembantu/lepas",
              "/pembantu/engine/")
ALAMAT_LOKAL = {"127.0.0.1", "::1", "localhost", "testclient"}

# Mode server. Audit 21 Sep 2026: penjagaan lama FAIL-OPEN — permintaan
# dianggap tepercaya KECUALI membawa salah satu header proxy. Artinya
# keamanannya bergantung pada tunnel yang rajin menambahkan header; tunnel yang
# tidak mengirim satu pun dari keempatnya akan membuka seluruh API (impor,
# hentikan batch, ganti kode) ke internet tanpa tanda apa pun.
#
# Sekarang dibalik: server yang ditunnel dijalankan dengan
# CATUR_MODE=pembantu, dan dalam mode itu HANYA jalur pembantu yang dilayani —
# tidak peduli permintaannya datang dari mana atau membawa header apa.
# Bawaannya "penuh" (untuk 127.0.0.1 di laptop sendiri), dan pemeriksaan lama
# tetap jalan sebagai lapis kedua.
ENV_MODE = "CATUR_MODE"


def mode_pembantu_saja() -> bool:
    """Server ini sedang dijalankan khusus untuk pembantu (dibuka lewat tunnel)?"""
    import os
    return os.environ.get(ENV_MODE, "penuh").strip().lower() in ("pembantu", "pembantu-saja")


router = APIRouter()

_kunci = threading.Lock()
_sewa: dict[int, dict] = {}          # partai_id -> {"nama", "sampai"}
_perangkat: dict[str, dict] = {}     # nama -> {"terakhir", "selesai", "posisi", "detik", "partai_id"}
_ditolak: set[int] = set()           # kirimannya tidak cocok: jangan dibagikan lagi sampai server dinyalakan ulang

# Antrean partai yang disiapkan sekaligus. 17 Sep 2026: dengan 25 tab pembantu,
# satu permintaan "ambil" makan 9-48 detik, padahal kuerinya sendiri 0,3 detik
# -- semua tab mengantre di _kunci sementara tiap permintaan mencari ulang di
# database yang sebagian besar sudah dianalisis. Throughput turun dari +-300
# ke +-32 partai per menit. Sekarang ISI_ANTREAN id diambil sekali, lalu
# dibagikan dari memori; tiap id diperiksa ulang (satu kueri titik) sebelum
# diserahkan, karena batch laptop bisa sudah mengerjakannya.
ISI_ANTREAN = 400
_antrean: deque[tuple[int, int]] = deque()   # (partai_id, depth)


# ------------------------------------------------------------------ kode

def baca_kode(path: Path | None = None, buat: bool = True) -> str | None:
    """Kode 6 angka untuk pembantu. Dibuat sekali lalu disimpan di pengaturan mesin."""
    p = path or PATH_PENGATURAN
    try:
        isi = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(isi, dict):
            isi = {}
    except (OSError, ValueError):
        isi = {}
    kode = isi.get("kode_pembantu")
    if isinstance(kode, str) and len(kode) == 6 and kode.isdigit():
        return kode
    return ganti_kode(p) if buat else None


def ganti_kode(path: Path | None = None) -> str:
    p = path or PATH_PENGATURAN
    try:
        isi = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(isi, dict):
            isi = {}
    except (OSError, ValueError):
        isi = {}
    isi["kode_pembantu"] = f"{secrets.randbelow(1_000_000):06d}"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(isi, indent=1), encoding="utf-8", newline="\n")
    return isi["kode_pembantu"]


# Batas tebakan kode. Kode 6 angka = sejuta kemungkinan; tanpa batas, orang
# yang menemukan alamat publik bisa mencobanya satu per satu. Dengan batas ini
# satu alamat hanya boleh salah GAGAL_PER_ASAL kali per JENDELA_GAGAL detik, dan
# semua alamat bersama-sama GAGAL_TOTAL kali (penyerang yang berganti alamat).
JENDELA_GAGAL = 10 * 60
GAGAL_PER_ASAL = 10
GAGAL_TOTAL = 100
_gagal: dict[str, list[float]] = {}
HEADER_PROXY = ("cf-connecting-ip", "x-forwarded-for", "x-real-ip", "forwarded")


def asal_permintaan(request: Request) -> str:
    """
    Alamat pengirim sebenarnya, untuk batas tebakan kode.

    Header proxy hanya dipercaya kalau koneksinya datang dari laptop sendiri
    (proses cloudflared); perangkat di WiFi yang mengirim header itu sendiri
    tetap dicatat dengan alamat aslinya. Dari header, hanya CF-Connecting-IP
    yang dipakai: Cloudflare selalu MENIMPANYA dengan IP pengirim, sedangkan
    X-Forwarded-For (elemen pertama) dan X-Real-IP diteruskan apa adanya dari
    pengirim. Audit 27 Sep 2026: dengan X-Forwarded-For acak di tiap tebakan,
    batas per alamat tidak pernah tercapai, dan 100 tebakan palsu mengunci
    SEMUA pembantu selama 10 menit.
    """
    langsung = request.client.host if request.client else "?"
    if langsung in ALAMAT_LOKAL and request.headers.get("cf-connecting-ip"):
        return request.headers["cf-connecting-ip"].strip()
    return langsung


def _periksa_kode(kode: str, asal: str = "?") -> None:
    sekarang = time.time()
    with _kunci:
        for a in list(_gagal):
            _gagal[a] = [t for t in _gagal[a] if sekarang - t < JENDELA_GAGAL]
            if not _gagal[a]:
                del _gagal[a]
        if (len(_gagal.get(asal, [])) >= GAGAL_PER_ASAL
                or sum(len(v) for v in _gagal.values()) >= GAGAL_TOTAL):
            raise HTTPException(status_code=429, detail="Terlalu banyak kode salah. Coba lagi 10 menit lagi.")
    benar = baca_kode()
    if not benar or not secrets.compare_digest(str(kode), benar):
        with _kunci:
            _gagal.setdefault(asal, []).append(sekarang)
        raise HTTPException(status_code=403, detail="Kode pembantu salah. Lihat kodenya di halaman Mesin di laptop.")


# ------------------------------------------------------------- akses luar

def boleh_dari_luar(host: str | None, jalur: str, lewat_proxy: bool = False) -> bool:
    """
    False kalau permintaan ini tidak boleh dilayani.

    Dua lapis, yang pertama tidak bergantung pada apa pun yang dikirim pemanggil:

    1. **Mode pembantu** (CATUR_MODE=pembantu, dipakai server yang ditunnel):
       hanya JALUR_LUAR yang dilayani, siapa pun pemanggilnya. Ini yang
       menutup lubang fail-open: tunnel tanpa header proxy pun tidak bisa
       menembus, karena servernya memang tidak melayani jalur lain.
    2. **Mode penuh** (bawaan, untuk laptop sendiri): permintaan dari luar —
       alamat bukan lokal, ATAU membawa header proxy — dibatasi ke JALUR_LUAR.
    """
    if mode_pembantu_saja():
        return _jalur_pembantu(jalur)
    if not lewat_proxy and (host is None or host in ALAMAT_LOKAL):
        return True
    return _jalur_pembantu(jalur)


def _jalur_pembantu(jalur: str) -> bool:
    return any(jalur == j or (j.endswith("/") and jalur.startswith(j)) for j in JALUR_LUAR)


def alamat_wifi() -> list[str]:
    """Alamat IPv4 laptop di jaringan lokal (bukan 127.x)."""
    alamat: set[str] = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            alamat.add(info[4][0])
    except OSError:
        pass
    try:                                    # alamat yang benar-benar dipakai keluar
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.0.2.1", 9))     # tidak mengirim apa pun (UDP tanpa data)
            alamat.add(s.getsockname()[0])
    except OSError:
        pass

    def lokal(a: str) -> bool:
        b = [int(x) for x in a.split(".")]
        return b[0] == 10 or (b[0] == 192 and b[1] == 168) or (b[0] == 172 and 16 <= b[1] <= 31)

    return sorted(a for a in alamat if lokal(a))


def terbuka_untuk_wifi(alamat: str, port: int) -> bool:
    """Apakah server bisa dihubungi lewat alamat WiFi (bukan cuma 127.0.0.1)."""
    try:
        with socket.create_connection((alamat, port), timeout=1.0):
            return True
    except OSError:
        return False


# --------------------------------------------------------------- tugas

def _bersihkan_sewa(sekarang: float) -> None:
    for pid in [p for p, s in _sewa.items() if s["sampai"] < sekarang]:
        del _sewa[pid]


def pilih_partai(conn: sqlite3.Connection, dikecualikan: list[int],
                 dukung960: bool = True) -> tuple[sqlite3.Row, int] | None:
    """(partai, depth) berikutnya -- satu saja; lihat pilih_banyak()."""
    daftar = pilih_banyak(conn, dikecualikan, dukung960, 1)
    if not daftar:
        return None
    pid, depth = daftar[0]
    return conn.execute("SELECT id, pgn FROM partai WHERE id = ?", (pid,)).fetchone(), depth


def pilih_banyak(conn: sqlite3.Connection, dikecualikan: list[int],
                 dukung960: bool = True, batas: int = ISI_ANTREAN) -> list[tuple[int, int]]:
    """
    Sampai `batas` (partai_id, depth) berikutnya untuk pembantu.

    Tahap 1, depth 10: partai GM yang belum dianalisis, lalu partai akun
    sendiri — masing-masing terlama dulu.
    Tahap 2, depth 16 (hanya kalau tahap 1 habis): partai yang analisisnya
    masih kasar, partai sendiri dulu (yang paling dipakai halaman latihan),
    lalu partai GM.
    """
    tanda = ",".join("?" * len(SUMBER_SAYA))
    kecuali = ""
    if dikecualikan:
        kecuali = f" AND g.id NOT IN ({','.join('?' * len(dikecualikan))})"
    if not dukung960:
        # tab yang dibuka sebelum perbaikan Chess960 tidak bisa menghitungnya dengan benar
        kecuali += " AND g.pgn NOT LIKE '%[Variant \"Chess960\"]%'"
    dasar = ("SELECT g.id FROM partai g WHERE g.jumlah_langkah >= 10"
             " AND NOT EXISTS (SELECT 1 FROM analisis a WHERE a.partai_id = g.id)" + kecuali)
    hasil: list[tuple[int, int]] = []
    for syarat, urut in ((f" AND g.sumber NOT IN ({tanda})", " ORDER BY g.id ASC"),
                         (f" AND g.sumber IN ({tanda})", " ORDER BY g.id ASC")):
        sisa = batas - len(hasil)
        if sisa <= 0:
            break
        hasil += [(r[0], DEPTH) for r in conn.execute(
            dasar + syarat + urut + f" LIMIT {int(sisa)}", list(dikecualikan) + list(SUMBER_SAYA))]
    if hasil:
        return hasil

    kasar = (f"SELECT g.id FROM analisis a JOIN partai g ON g.id = a.partai_id"
             f" WHERE (a.profil = ? OR a.engine IN ({','.join('?' * len(ENGINE_KASAR))}))" + kecuali)
    for syarat in (f" AND g.sumber IN ({tanda})", f" AND g.sumber NOT IN ({tanda})"):
        sisa = batas - len(hasil)
        if sisa <= 0:
            break
        hasil += [(r[0], DEPTH_MENDALAM) for r in conn.execute(
            kasar + syarat + f" ORDER BY g.id DESC LIMIT {int(sisa)}",
            [CEPAT.nama, *ENGINE_KASAR, *dikecualikan, *SUMBER_SAYA])]
    return hasil


def _masih_perlu(conn: sqlite3.Connection, pid: int, depth: int) -> sqlite3.Row | None:
    """PGN partai kalau partai itu masih perlu dikerjakan dengan depth ini, selain itu None."""
    r = conn.execute(
        "SELECT g.pgn, a.profil, a.engine FROM partai g LEFT JOIN analisis a ON a.partai_id = g.id"
        " WHERE g.id = ?", (pid,)).fetchone()
    if r is None:
        return None
    if depth == DEPTH:
        return r if r["profil"] is None else None
    return r if (r["profil"] == CEPAT.nama or r["engine"] in ENGINE_KASAR) else None


# Server pembantu (mode pembantu, port 8001) adalah PROSES LAIN dari server
# utama, jadi kartu "Pembantu" di halaman Mesin tidak bisa melihat isi
# memorinya. Statusnya dititipkan lewat berkas ini — bukan lewat jalur HTTP
# baru, karena `/api/pembantu/info` memuat kode dan sengaja tidak pernah
# dilayani ke luar.
PATH_STATUS = FOLDER_DATA / "pembantu-status.json"
JEDA_TULIS_STATUS = 5.0
_status_ditulis = [0.0]


def _tulis_status(paksa: bool = False) -> None:
    sekarang = time.time()
    if not paksa and sekarang - _status_ditulis[0] < JEDA_TULIS_STATUS:
        return
    _status_ditulis[0] = sekarang
    try:
        PATH_STATUS.parent.mkdir(parents=True, exist_ok=True)
        PATH_STATUS.write_text(json.dumps({"waktu": sekarang, "perangkat": _perangkat}),
                               encoding="utf-8", newline="\n")
    except OSError:
        pass          # status hanya bantuan tampilan, jangan menggagalkan pembantu


def _baca_status() -> dict:
    """Perangkat yang dicatat proses pembantu lain (kalau ada)."""
    try:
        isi = json.loads(PATH_STATUS.read_text(encoding="utf-8"))
        return isi.get("perangkat", {}) if isinstance(isi, dict) else {}
    except (OSError, ValueError):
        return {}


def _catat_perangkat(nama: str, **ubah) -> dict:
    d = _perangkat.setdefault(nama, {"terakhir": 0.0, "selesai": 0, "posisi": 0,
                                     "detik": 0.0, "partai_id": None})
    d.update(ubah, terakhir=time.time())
    _tulis_status()
    return d


class _EvaluasiSiap:
    """
    Pengganti Stockfish untuk analisis_partai(): mengembalikan evaluasi yang
    sudah dihitung browser, dicari dari jumlah langkah yang sudah dimainkan.
    """

    def __init__(self, posisi: list[dict | None], depth: int):
        self.posisi = posisi
        self.profil = HEMAT if depth >= HEMAT.depth else CEPAT
        self.depth = depth

    def evaluasi(self, board: chess.Board, depth=None, multipv: int = 1, waktu=None) -> list[dict]:
        i = len(board.move_stack)
        e = self.posisi[i] if i < len(self.posisi) else None
        if not e:
            raise ValueError(f"evaluasi posisi ke-{i} tidak dikirim")
        pv: list[chess.Move] = []
        papan = board.copy(stack=False)
        for uci in (e.get("pv") or [])[:12]:
            try:
                m = chess.Move.from_uci(uci)
            except ValueError:
                break
            if m not in papan.legal_moves:
                break
            pv.append(m)
            papan.push(m)
        if not pv:
            raise ValueError(f"langkah terbaik posisi ke-{i} tidak sah")
        return [{"cp": e.get("cp"), "mate": e.get("mate"),
                 "pv": Stockfish._pv_ke_san(board, pv), "depth": e.get("depth")}]


def susun_laporan(pgn: str, posisi: list[dict | None], depth: int = DEPTH):
    """LaporanPartai dari evaluasi browser. ValueError kalau kirimannya tidak cocok dengan partai."""
    game = chess.pgn.read_game(io.StringIO(pgn or ""))
    if game is None:
        raise ValueError("PGN partai tidak terbaca")
    jumlah = len(list(game.mainline_moves()))
    if len(posisi) != jumlah + 1:
        raise ValueError(f"partai ini punya {jumlah + 1} posisi, yang dikirim {len(posisi)}")
    for e in posisi:
        if not e:
            continue
        if e.get("mate") is not None:
            if not isinstance(e["mate"], int) or abs(e["mate"]) > 500:
                raise ValueError("nilai mat tidak sah")
        elif not isinstance(e.get("cp"), int) or abs(e["cp"]) >= SKOR_MAT:
            raise ValueError("nilai evaluasi tidak sah")
    return analisis_partai(game, _EvaluasiSiap(posisi, depth), depth=depth)


# --------------------------------------------------------------- rute

def db():
    conn = koneksi()
    conn.execute("PRAGMA busy_timeout = 30000")
    try:
        yield conn
    finally:
        conn.close()


class Ambil(BaseModel):
    kode: str = Field(..., max_length=12)
    nama: str = Field(..., min_length=1, max_length=40)
    dukung960: bool = Field(False, description="pembantu.js versi baru mengirim true")


class Kirim(Ambil):
    partai_id: int
    depth: int = Field(DEPTH, ge=1, le=30, description="depth yang diminta server saat partai diambil")
    posisi: list[dict | None] = Field(..., max_length=2000)
    detik: float = Field(0.0, ge=0)


class Lepas(Ambil):
    partai_id: int | None = None


@router.get("/pembantu", include_in_schema=False)
def halaman_pembantu():
    return RedirectResponse("/static/pembantu.html")


@router.get("/pembantu/engine/{nama}", include_in_schema=False)
def berkas_engine(nama: str):
    if nama not in BERKAS_ENGINE or not (FOLDER_ENGINE / nama).is_file():
        raise HTTPException(status_code=404,
                            detail="Engine browser belum diunduh: python -m app.pembantu --unduh-engine")
    jenis = "application/wasm" if nama.endswith(".wasm") else "text/javascript"
    return FileResponse(FOLDER_ENGINE / nama, media_type=jenis)


@router.post("/api/pembantu/ambil", summary="Pembantu: minta satu partai untuk dianalisis")
def api_ambil(badan: Ambil, conn: sqlite3.Connection = Depends(db),
              asal: str = Depends(asal_permintaan)):
    _periksa_kode(badan.kode, str(asal))
    with _kunci:
        # partai lama milik perangkat ini (misalnya halaman dimuat ulang) dilepas dulu
        for p_lama in [p for p, s in _sewa.items() if s["nama"] == badan.nama]:
            _antrean.appendleft((p_lama, _sewa.pop(p_lama).get("depth", DEPTH)))
    lewati_960: list[tuple[int, int]] = []
    r = None
    pid = depth = None
    for _ in range(50):
        with _kunci:
            sekarang = time.time()
            _bersihkan_sewa(sekarang)
            berikut = None
            for coba_isi in range(2):
                while _antrean:
                    kandidat = _antrean.popleft()
                    if kandidat[0] not in _sewa and kandidat[0] not in _ditolak:
                        berikut = kandidat
                        break
                if berikut or coba_isi:
                    break
                _antrean.extend(pilih_banyak(conn, list(_sewa) + sorted(_ditolak)))
            if berikut is None:
                _antrean.extendleft(reversed(lewati_960))
                _catat_perangkat(badan.nama, partai_id=None)
                return {"partai_id": None, "pesan": "Semua partai sudah dianalisis mendalam."}
            pid, depth = berikut
            _sewa[pid] = {"nama": badan.nama, "sampai": sekarang + SEWA_DETIK, "depth": depth}
        r = _masih_perlu(conn, pid, depth)
        if r is not None and not badan.dukung960 and '[Variant "Chess960"]' in (r["pgn"] or ""):
            lewati_960.append(berikut)       # tab lama tanpa dukungan 960: biarkan untuk tab lain
            r = None
        if r is not None:
            break
        with _kunci:
            _sewa.pop(pid, None)             # sudah dikerjakan di tempat lain, atau dilewati
    with _kunci:
        _antrean.extendleft(reversed(lewati_960))
        if r is None:
            _catat_perangkat(badan.nama, partai_id=None)
    if r is None:
        # bukan "habis": pembantu.js akan mencoba lagi 15 detik kemudian
        raise HTTPException(status_code=503, detail="Belum ada partai yang bisa dibagikan, dicoba lagi.")
    with _kunci:
        _catat_perangkat(badan.nama, partai_id=pid)
    game = chess.pgn.read_game(io.StringIO(r["pgn"] or ""))
    papan = game.board()
    h = game.headers
    return {"partai_id": pid, "fen_awal": papan.fen(),
            "langkah": [m.uci() for m in game.mainline_moves()],
            "depth": depth, "mendalam": depth > DEPTH,
            # Chess960 (mis. TWIC "Biel GM2-960"): rokade UCI-nya raja-makan-benteng,
            # dan engine browser harus diberi UCI_Chess960 supaya pv-nya sah.
            # Tanpa ini 66 dari 514 kiriman pertama ditolak (13 Sep 2026).
            "chess960": bool(papan.chess960),
            "putih": h.get("White", "?"), "hitam": h.get("Black", "?")}


@router.post("/api/pembantu/kirim", summary="Pembantu: kirim evaluasi satu partai")
def api_kirim(badan: Kirim, conn: sqlite3.Connection = Depends(db),
              asal: str = Depends(asal_permintaan)):
    _periksa_kode(badan.kode, str(asal))
    r = conn.execute("SELECT pgn FROM partai WHERE id = ?", (badan.partai_id,)).fetchone()
    if r is None:
        raise HTTPException(status_code=404, detail=f"Partai {badan.partai_id} tidak ada")
    with _kunci:
        sewa = _sewa.get(badan.partai_id)
    # depth dari catatan sewa kalau masih ada: tab lama yang belum memuat ulang
    # pembantu.js tidak mengirim depth, padahal sudah menghitung sesuai permintaan
    depth = sewa["depth"] if sewa else badan.depth
    if depth not in PROFIL_PER_DEPTH:
        raise HTTPException(status_code=422, detail=f"Depth {depth} tidak dikenal")
    # Hanya partai yang memang dipinjamkan ke perangkat ini. Audit 27 Sep 2026:
    # tanpa pemeriksaan ini, siapa pun yang tahu kodenya bisa menimpa analisis
    # partai mana saja. Sewa yang sudah kedaluwarsa (tablet lambat, > SEWA_DETIK)
    # masih diterima, tapi hanya kalau partainya MASIH perlu dianalisis — hasil
    # yang sudah ada tidak pernah tertimpa. 422, bukan 409: pembantu.js melewati
    # partai untuk 422 dan mengulang tanpa henti untuk kode lain.
    if sewa is not None and sewa["nama"] != badan.nama:
        raise HTTPException(status_code=422, detail="Partai ini sedang dikerjakan perangkat lain.")
    if sewa is None and _masih_perlu(conn, badan.partai_id, depth) is None:
        raise HTTPException(status_code=422, detail="Partai ini tidak sedang dipinjamkan ke perangkat "
                                                    "ini dan analisisnya sudah ada; hasil tidak dipakai.")
    try:
        laporan = susun_laporan(r["pgn"], badan.posisi, depth)
    except ValueError as e:
        print(f"pembantu: hasil partai {badan.partai_id} dari {badan.nama!r} ditolak: {e}",
              file=sys.stderr, flush=True)
        with _kunci:
            _ditolak.add(badan.partai_id)
            _sewa.pop(badan.partai_id, None)
        raise HTTPException(status_code=422, detail=f"Hasil tidak diterima: {e}")
    try:
        # durasi sengaja None: perkiraan waktu batch laptop dihitung dari
        # durasi per profil, dan kecepatan tablet tidak boleh ikut mengubahnya
        simpan_laporan(conn, badan.partai_id, laporan, NAMA_ENGINE, PROFIL_PER_DEPTH[depth], None)
    except sqlite3.OperationalError as e:
        raise HTTPException(status_code=503, detail=f"Database sedang sibuk, dicoba lagi nanti ({e})")
    with _kunci:
        _sewa.pop(badan.partai_id, None)
        d = _perangkat.get(badan.nama) or _catat_perangkat(badan.nama)
        _catat_perangkat(badan.nama, selesai=d["selesai"] + 1, partai_id=None,
                         posisi=d["posisi"] + len(badan.posisi), detik=d["detik"] + badan.detik)
    return {"tersimpan": True, "partai_id": badan.partai_id, "langkah": len(laporan.langkah)}


@router.post("/api/pembantu/lepas", summary="Pembantu berhenti: kembalikan partainya ke antrean")
def api_lepas(badan: Lepas, asal: str = Depends(asal_permintaan)):
    _periksa_kode(badan.kode, str(asal))
    with _kunci:
        for pid in [p for p, s in _sewa.items()
                    if s["nama"] == badan.nama and badan.partai_id in (None, p)]:
            _antrean.appendleft((pid, _sewa.pop(pid).get("depth", DEPTH)))   # kembali ke depan antrean
        if badan.nama in _perangkat:
            _perangkat[badan.nama]["partai_id"] = None
    return {"dilepas": True}


@router.get("/api/pembantu/info", summary="Kode, alamat, dan pembantu yang aktif (untuk halaman Mesin)")
def api_info(request: Request):
    port = request.url.port or 8000
    alamat = alamat_wifi()
    sekarang = time.time()
    with _kunci:
        _bersihkan_sewa(sekarang)
        # gabung dengan catatan proses pembantu lain (port 8001); yang di
        # memori sendiri menang kalau namanya sama
        gabungan = {**_baca_status(), **_perangkat}
        daftar = [{"nama": n, "aktif": sekarang - d["terakhir"] < AKTIF_DETIK,
                   "detik_lalu": round(sekarang - d["terakhir"]),
                   "selesai": d["selesai"], "partai_id": d["partai_id"],
                   "posisi_per_detik": round(d["posisi"] / d["detik"], 2) if d["detik"] else None}
                  for n, d in sorted(gabungan.items())]
    return {"kode": baca_kode(), "port": port,
            "alamat": [f"http://{a}:{port}/pembantu" for a in alamat],
            "terbuka": any(terbuka_untuk_wifi(a, port) for a in alamat),
            "engine_ada": all((FOLDER_ENGINE / b).is_file() for b in BERKAS_ENGINE),
            "pembantu": daftar}


@router.post("/api/pembantu/kode", summary="Ganti kode pembantu (perangkat lama harus memasukkan kode baru)")
def api_ganti_kode():
    return {"kode": ganti_kode()}


# ---------------------------------------------------------------- CLI

def unduh_engine() -> None:
    """Ambil Stockfish.js 18 lite satu-inti dari paket npm resminya (GPLv3)."""
    import tarfile
    import tempfile
    import urllib.request

    FOLDER_ENGINE.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        tgz = Path(tmp) / "stockfish.tgz"
        print(f"mengunduh {URL_PAKET} (±160 MB, hanya 2 berkas yang disimpan)")
        urllib.request.urlretrieve(URL_PAKET, tgz)
        with tarfile.open(tgz) as t:
            for nama in BERKAS_ENGINE + ("Copying.txt",):
                anggota = t.getmember(f"package/bin/{nama}" if nama != "Copying.txt" else "package/Copying.txt")
                with t.extractfile(anggota) as sumber:
                    (FOLDER_ENGINE / nama).write_bytes(sumber.read())
                print(f"  {FOLDER_ENGINE / nama}")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--unduh-engine", action="store_true", help="unduh Stockfish.js untuk browser")
    p.add_argument("--kode", action="store_true", help="tampilkan kode pembantu")
    a = p.parse_args()
    if a.unduh_engine:
        unduh_engine()
    if a.kode or not a.unduh_engine:
        print(f"kode pembantu: {baca_kode()}")
        for alamat in alamat_wifi():
            print(f"alamat: http://{alamat}:8000/pembantu")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
