"""
Isi analisis dari evaluasi yang sudah disertakan Lichess, tanpa engine.

Ray, 13 Sep 2026: "kalau dibantu analisa dengan lichess gimana? ... disini
kan sudah ada analisanya". Partai siaran Lichess memuat [%eval ...] di tiap
langkah, dan PGN yang tersimpan di database ikut menyimpannya (378 dari 379
partai siaran). Tanpa modul ini evaluasi itu tidak pernah dipakai, dan partai
siaran ikut antre di engine laptop.

Laporan disusun `analisis_dari_eval()` dengan rumus yang sama dengan analisis
engine. Dibandingkan pada 71 partai yang sudah pernah dianalisis Stockfish 18
depth 16 (13 Sep 2026): selisih akurasi per pemain median 0,6 poin, 137 dari
142 pemain selisihnya <= 2 poin, tidak ada yang > 5 poin; jumlah blunder sama
persis di 39 dari 71 partai — label blunder lebih peka terhadap perbedaan
engine daripada akurasi.

Hasilnya ditandai engine "Lichess (evaluasi siaran)" dan profil "lichess",
jadi selalu bisa dibedakan dari hasil laptop, dan tidak ikut dipakai untuk
memperkirakan kecepatan engine (perkiraan memilih per profil).

    python -m app.eval_lichess            # isi semua yang bisa
    python -m app.eval_lichess --lihat    # hitung saja
"""

from __future__ import annotations

import argparse
import io
import sqlite3
import sys
from pathlib import Path

AKAR = Path(__file__).resolve().parents[1]
if str(AKAR / "src") not in sys.path:
    sys.path.insert(0, str(AKAR / "src"))

import chess.pgn                                          # noqa: E402

from catur_analysis.partai import analisis_dari_daftar, analisis_dari_eval   # noqa: E402

from .analisis_db import simpan_laporan                    # noqa: E402

NAMA_ENGINE = "Lichess (evaluasi siaran)"
NAMA_ENGINE_SERVER = "Lichess (analisis server)"
PROFIL = "lichess"

USER_AGENT = "catur-analysis/0.1 (belajar catur pribadi; +https://github.com/raymclung/catur)"
URL_EKSPOR = ("https://lichess.org/api/games/export/_ids"
              "?evals=true&moves=false&clocks=false&opening=false")
PER_PERMINTAAN = 300        # batas Lichess untuk ekspor per id
JEDA_DETIK = 1.0            # sopan terhadap API
JEDA_429 = 65.0


def calon(conn: sqlite3.Connection, partai_ids: list[int] | None = None) -> list[sqlite3.Row]:
    """Partai yang belum dianalisis dan PGN-nya memuat evaluasi Lichess."""
    sql = """SELECT g.id, g.pgn FROM partai g
             LEFT JOIN analisis a ON a.partai_id = g.id
             WHERE a.partai_id IS NULL AND g.pgn LIKE '%[%eval %'"""
    params: list = []
    if partai_ids:
        sql += f" AND g.id IN ({','.join('?' * len(partai_ids))})"
        params = list(partai_ids)
    return conn.execute(sql + " ORDER BY g.id", params).fetchall()


def isi(conn: sqlite3.Connection, partai_ids: list[int] | None = None,
        lihat_saja: bool = False) -> dict:
    """
    Buat analisis untuk tiap calon. Partai yang evaluasinya tidak lengkap
    dilewati — laporan setengah jadi lebih menyesatkan daripada tidak ada.
    """
    daftar = calon(conn, partai_ids)
    hasil = {"calon": len(daftar), "dianalisis": 0, "dilewati": 0}
    for r in daftar:
        try:
            game = chess.pgn.read_game(io.StringIO(r["pgn"] or ""))
            lap = analisis_dari_eval(game) if game is not None else None
        except Exception:                       # noqa: BLE001 - satu PGN aneh tidak menghentikan sisanya
            lap = None
        if lap is None:
            hasil["dilewati"] += 1
            continue
        if not lihat_saja:
            simpan_laporan(conn, r["id"], lap, NAMA_ENGINE, PROFIL, 0.0)
        hasil["dianalisis"] += 1
    return hasil


def _ekspor_analisis(id_lichess: list[str]) -> dict[str, list[dict]]:
    """{id partai Lichess: daftar analisis per ply} untuk partai yang punya analisis server."""
    import json
    import time
    import urllib.error
    import urllib.request

    for percobaan in range(2):
        req = urllib.request.Request(URL_EKSPOR, data=",".join(id_lichess).encode(), method="POST",
                                     headers={"User-Agent": USER_AGENT,
                                              "Accept": "application/x-ndjson",
                                              "Content-Type": "text/plain"})
        try:
            with urllib.request.urlopen(req, timeout=180) as r:
                teks = r.read().decode("utf-8", "replace")
            break
        except urllib.error.HTTPError as e:
            if e.code == 429 and percobaan == 0:
                time.sleep(JEDA_429)
                continue
            raise
    hasil = {}
    for baris in teks.splitlines():
        if not baris.strip():
            continue
        g = json.loads(baris)
        if g.get("analysis"):
            hasil[g["id"]] = g["analysis"]
    return hasil


def tarik_analisis_akun(conn: sqlite3.Connection, partai_ids: list[int] | None = None,
                        lapor=None, batas: int = 0) -> dict:
    """
    Ambil analisis server Lichess untuk partai akun sendiri yang belum dianalisis.

    Ray, 13 Sep 2026: "game partai saya di lichess juga apakah bisa ikut diambil
    langsung dari analisa lichess?" Analisis itu hanya ada untuk partai yang
    pernah dimintakan "Request computer analysis" di Lichess — di partai kalah
    lawan >2200 yang belum dianalisis, terukur 9% — tapi yang ada memberi
    langkah terbaik di tiap kesalahan, jadi halaman Latihan ikut bisa memakainya.

    Urutan langkah di database dan di Lichess sudah dicocokkan (60 langkah = 60
    langkah = 60 entri analisis pada partai uZTuKoec); kalau jumlahnya tidak
    cocok, partainya dilewati, bukan ditebak.
    """
    import re
    import time

    sql = """SELECT g.id, g.pgn, g.sumber_url FROM partai g
             LEFT JOIN analisis a ON a.partai_id = g.id
             WHERE a.partai_id IS NULL AND g.sumber = 'lichess-user'
               AND g.sumber_url LIKE '%lichess.org/%'"""
    params: list = []
    if partai_ids:
        sql += f" AND g.id IN ({','.join('?' * len(partai_ids))})"
        params = list(partai_ids)
    peta: dict[str, sqlite3.Row] = {}
    # terbaru dulu: partai yang baru ditarik paling mungkin baru dimintakan analisisnya
    sql += " ORDER BY g.id DESC" + (f" LIMIT {int(batas)}" if batas else "")
    for r in conn.execute(sql, params):
        m = re.search(r"lichess\.org/(\w{8})", r["sumber_url"] or "")
        if m:
            peta[m.group(1)] = r

    hasil = {"calon": len(peta), "punya_analisis": 0, "dianalisis": 0, "dilewati": 0}
    semua_id = list(peta)
    for i in range(0, len(semua_id), PER_PERMINTAAN):
        potong = semua_id[i:i + PER_PERMINTAAN]
        analisis = _ekspor_analisis(potong)
        hasil["punya_analisis"] += len(analisis)
        for id_li, daftar in analisis.items():
            r = peta.get(id_li)
            if r is None or id_li not in potong:     # jawaban yang tidak diminta: abaikan
                continue
            try:
                game = chess.pgn.read_game(io.StringIO(r["pgn"] or ""))
                langkah = len(list(game.mainline_moves())) if game else 0
                # boleh kurang satu: posisi mat terakhir kadang tanpa evaluasi
                lap = (analisis_dari_daftar(game, daftar)
                       if game and langkah - 1 <= len(daftar) <= langkah else None)
            except Exception:                   # noqa: BLE001
                lap = None
            if lap is None:
                hasil["dilewati"] += 1
                continue
            simpan_laporan(conn, r["id"], lap, NAMA_ENGINE_SERVER, PROFIL, 0.0)
            hasil["dianalisis"] += 1
        if lapor:
            lapor(f"{min(i + PER_PERMINTAAN, len(semua_id))}/{len(semua_id)} diperiksa, "
                  f"{hasil['dianalisis']} dianalisis")
        if i + PER_PERMINTAAN < len(semua_id):
            time.sleep(JEDA_DETIK)
    return hasil


def main() -> int:
    from .db import koneksi
    from .kunci_penulis import KunciPenulis, KunciTerpakai

    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--lihat", action="store_true", help="hitung saja, jangan simpan")
    p.add_argument("--akun", action="store_true",
                   help="ambil juga analisis server Lichess untuk partai akun sendiri")
    a = p.parse_args()

    penjaga = None
    if not a.lihat:
        try:
            penjaga = KunciPenulis("isi analisis dari evaluasi Lichess").ambil()
        except KunciTerpakai as e:
            print(e)
            return 1
    conn = koneksi()
    try:
        h = isi(conn, lihat_saja=a.lihat)
        if a.akun and not a.lihat:
            ha = tarik_analisis_akun(conn, lapor=print)
            print(f"akun: {ha['calon']} calon, {ha['punya_analisis']} punya analisis Lichess, "
                  f"{ha['dianalisis']} dianalisis, {ha['dilewati']} dilewati")
    finally:
        conn.close()
        if penjaga:
            penjaga.lepas()
    kata = "bisa dianalisis" if a.lihat else "dianalisis"
    print(f"{h['calon']} calon, {h['dianalisis']} {kata}, {h['dilewati']} dilewati (evaluasi tidak lengkap)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
