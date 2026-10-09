"""
Posisi yang disimpan pengguna dari papan (permintaan frontend #6).

FEN divalidasi dengan cara yang sama seperti /api/posisi supaya yang masuk
database hanya posisi sah; yang disimpan adalah FEN lengkap hasil
normalisasi python-chess, bukan teks mentah dari browser.
"""

from __future__ import annotations

import sqlite3

import chess

from .db import sekarang

PANJANG_JUDUL_MAKS = 200
PANJANG_CATATAN_MAKS = 2000


def _fen_sah(fen: str) -> str:
    board = chess.Board(fen)          # ValueError kalau rusak
    if not board.is_valid():
        raise ValueError("posisi tidak sah")
    # Aturan 75 langkah = 150 ply; partai terpanjang yang pernah dimainkan
    # < 300 langkah. Angka 99999999999999999999 dulu tersimpan apa adanya.
    if board.halfmove_clock > 150 or board.fullmove_number > 1000:
        raise ValueError("hitungan langkah di FEN tidak masuk akal")
    return board.fen()


def simpan(conn: sqlite3.Connection, fen: str, judul: str = "", catatan: str = "",
           asal: str = "", partai_id: int | None = None) -> dict:
    """Simpan satu posisi; kembalikan barisnya. ValueError kalau FEN rusak
    atau partai_id tidak ada."""
    fen_bersih = _fen_sah(fen)
    if partai_id is not None and conn.execute(
            "SELECT 1 FROM partai WHERE id = ?", (partai_id,)).fetchone() is None:
        raise ValueError(f"partai {partai_id} tidak ada")

    cur = conn.execute(
        "INSERT INTO posisi_tersimpan(fen, judul, catatan, asal, partai_id, dibuat_pada) "
        "VALUES (?,?,?,?,?,?)",
        ((fen_bersih, (judul or "").strip()[:PANJANG_JUDUL_MAKS],
          (catatan or "").strip()[:PANJANG_CATATAN_MAKS],
          (asal or "").strip()[:50], partai_id, sekarang())),
    )
    conn.commit()
    return ambil(conn, cur.lastrowid)


def ambil(conn: sqlite3.Connection, id_: int) -> dict | None:
    r = conn.execute("SELECT * FROM posisi_tersimpan WHERE id = ?", (id_,)).fetchone()
    return dict(r) if r else None


def daftar(conn: sqlite3.Connection, batas: int = 500) -> list[dict]:
    """Terbaru dulu."""
    return [dict(r) for r in conn.execute(
        "SELECT * FROM posisi_tersimpan ORDER BY id DESC LIMIT ?", (max(1, min(batas, 5000)),)
    )]


def hapus(conn: sqlite3.Connection, id_: int) -> bool:
    cur = conn.execute("DELETE FROM posisi_tersimpan WHERE id = ?", (id_,))
    conn.commit()
    return cur.rowcount > 0
