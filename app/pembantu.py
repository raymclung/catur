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

Pengamanan: server yang dibuka ke WiFi memperlihatkan seluruh API. Karena itu
middleware `hanya_pembantu_dari_luar` (dipasang di main.py) menolak semua
alamat selain JALUR_LUAR untuk perangkat lain, dan jalur itu pun wajib kode.
Laptop sendiri (127.0.0.1) tetap bisa membuka semuanya.

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
from .db import koneksi                                    # noqa: E402

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

router = APIRouter()

_kunci = threading.Lock()
_sewa: dict[int, dict] = {}          # partai_id -> {"nama", "sampai"}
_perangkat: dict[str, dict] = {}     # nama -> {"terakhir", "selesai", "posisi", "detik", "partai_id"}
_ditolak: set[int] = set()           # kirimannya tidak cocok: jangan dibagikan lagi sampai server dinyalakan ulang


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
    """Alamat pengirim sebenarnya, termasuk kalau datang lewat tunnel/proxy."""
    for h in ("cf-connecting-ip", "x-real-ip"):
        if request.headers.get(h):
            return request.headers[h].strip()
    if request.headers.get("x-forwarded-for"):
        return request.headers["x-forwarded-for"].split(",")[0].strip()
    return request.client.host if request.client else "?"


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
    False kalau permintaan dari perangkat lain menuju jalur di luar JALUR_LUAR.

    `lewat_proxy`: permintaan membawa header proxy/tunnel. Tunnel (mis.
    Cloudflare) meneruskan permintaan internet dari 127.0.0.1, jadi tanpa
    pemeriksaan ini seluruh API akan terbuka ke internet.
    """
    if not lewat_proxy and (host is None or host in ALAMAT_LOKAL):
        return True
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
    """
    (partai, depth) berikutnya untuk pembantu.

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
    dasar = ("SELECT g.id, g.pgn FROM partai g WHERE g.jumlah_langkah >= 10"
             " AND NOT EXISTS (SELECT 1 FROM analisis a WHERE a.partai_id = g.id)" + kecuali)
    for syarat, urut in ((f" AND g.sumber NOT IN ({tanda})", " ORDER BY g.id ASC"),
                         (f" AND g.sumber IN ({tanda})", " ORDER BY g.id ASC")):
        r = conn.execute(dasar + syarat + urut + " LIMIT 1",
                         list(dikecualikan) + list(SUMBER_SAYA)).fetchone()
        if r is not None:
            return r, DEPTH

    kasar = (f"SELECT g.id, g.pgn FROM analisis a JOIN partai g ON g.id = a.partai_id"
             f" WHERE (a.profil = ? OR a.engine IN ({','.join('?' * len(ENGINE_KASAR))}))" + kecuali)
    for syarat in (f" AND g.sumber IN ({tanda})", f" AND g.sumber NOT IN ({tanda})"):
        r = conn.execute(kasar + syarat + " ORDER BY g.id DESC LIMIT 1",
                         [CEPAT.nama, *ENGINE_KASAR, *dikecualikan, *SUMBER_SAYA]).fetchone()
        if r is not None:
            return r, DEPTH_MENDALAM
    return None


def _catat_perangkat(nama: str, **ubah) -> dict:
    d = _perangkat.setdefault(nama, {"terakhir": 0.0, "selesai": 0, "posisi": 0,
                                     "detik": 0.0, "partai_id": None})
    d.update(ubah, terakhir=time.time())
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
        sekarang = time.time()
        _bersihkan_sewa(sekarang)
        # partai lama milik perangkat ini (misalnya halaman dimuat ulang) dilepas dulu
        for pid in [p for p, s in _sewa.items() if s["nama"] == badan.nama]:
            del _sewa[pid]
        pilihan = pilih_partai(conn, list(_sewa) + sorted(_ditolak), badan.dukung960)
        if pilihan is None:
            _catat_perangkat(badan.nama, partai_id=None)
            return {"partai_id": None, "pesan": "Semua partai sudah dianalisis mendalam."}
        r, depth = pilihan
        _sewa[r["id"]] = {"nama": badan.nama, "sampai": sekarang + SEWA_DETIK, "depth": depth}
        _catat_perangkat(badan.nama, partai_id=r["id"])
    game = chess.pgn.read_game(io.StringIO(r["pgn"] or ""))
    papan = game.board()
    h = game.headers
    return {"partai_id": r["id"], "fen_awal": papan.fen(),
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
            del _sewa[pid]
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
        daftar = [{"nama": n, "aktif": sekarang - d["terakhir"] < AKTIF_DETIK,
                   "detik_lalu": round(sekarang - d["terakhir"]),
                   "selesai": d["selesai"], "partai_id": d["partai_id"],
                   "posisi_per_detik": round(d["posisi"] / d["detik"], 2) if d["detik"] else None}
                  for n, d in sorted(_perangkat.items())]
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
