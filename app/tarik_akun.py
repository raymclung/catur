"""
Tarik partai terbaru dari akun Lichess & Chess.com Ray, langsung ke database.

Bedanya dengan `ambil_partai.py` (yang menyimpan PGN ke berkas lalu diimpor
terpisah): di sini unduhan langsung masuk database, dan yang ditarik hanya
partai **setelah partai terakhir yang sudah ada**. Tanpa itu, menekan tombol
"tarik partai baru" berarti mengunduh ulang 8.596 partai McLung_07 tiap kali.

Cara menentukan "sejak kapan":
  * Lichess menerima `since` dalam epoch milidetik -> dipakai langsung.
  * Chess.com membagi arsip per bulan -> bulan partai terakhir ikut ditarik
    ulang (partai lama ditolak sidik jari, partai baru di bulan itu tidak
    hilang).

Batas aman: partai terakhir dihitung per akun dari kolom `tanggal`, yang
formatnya 'YYYY.MM.DD'. Kalau akun belum punya partai sama sekali, seluruh
riwayat ditarik — itu memang yang diinginkan pada penarikan pertama.
"""

from __future__ import annotations

import datetime as _dt
import sqlite3

from .impor import HasilImpor, impor_teks, normalisasi_nama

# Akun dan pengunduhnya ada di skrip akar; modul ini hanya mengatur "sejak
# kapan" dan menaruh hasilnya ke database.
from ambil_partai import (AKUN, KECEPATAN_DEFAULT, Akun, ambil_chesscom,
                          ambil_lichess, hitung_partai)

# Mundur satu hari dari partai terakhir. Zona waktu server Lichess dan kolom
# tanggal kita tidak persis sama, dan partai yang terlewat jauh lebih merugikan
# daripada beberapa partai yang diunduh ulang lalu ditolak sidik jari.
MUNDUR_HARI = 1


def tanggal_partai_terakhir(conn: sqlite3.Connection, username: str) -> str | None:
    """'YYYY.MM.DD' partai terakhir akun ini, atau None kalau belum ada."""
    baris = conn.execute(
        """
        SELECT MAX(g.tanggal) AS t
        FROM partai g
        JOIN pemain p ON p.id IN (g.putih_id, g.hitam_id)
        WHERE p.nama_normal = ?
        """,
        (normalisasi_nama(username),),
    ).fetchone()
    return baris["t"] if baris and baris["t"] else None


def _sejak(tanggal: str | None) -> tuple[int | None, str | None]:
    """(epoch milidetik untuk Lichess, 'YYYY-MM' untuk Chess.com)."""
    if not tanggal:
        return None, None
    try:
        t = _dt.datetime.strptime(tanggal[:10], "%Y.%m.%d")
    except ValueError:
        return None, None
    t -= _dt.timedelta(days=MUNDUR_HARI)
    ms = int(t.replace(tzinfo=_dt.timezone.utc).timestamp() * 1000)
    return ms, f"{t.year:04d}-{t.month:02d}"


def tarik_satu(conn: sqlite3.Connection, akun: Akun, kecepatan: list[str],
               maks: int = 0, hanya_rated: bool = True,
               semua_riwayat: bool = False) -> dict:
    """
    Tarik satu akun dan masukkan ke database. Tidak melempar kalau jaringan
    gagal — kegagalan satu akun tidak boleh membatalkan akun lain; pesannya
    ikut dikembalikan supaya terlihat di web.
    """
    terakhir = None if semua_riwayat else tanggal_partai_terakhir(conn, akun.username)
    sejak_ms, sejak_bulan = _sejak(terakhir)
    hasil = {
        "akun": akun.username,
        "platform": akun.platform,
        "sejak": terakhir,
        "diunduh": 0,
        "baru": 0,
        "duplikat": 0,
        "dilewati": 0,
        "galat": None,
    }
    try:
        if akun.platform == "lichess":
            pgn = ambil_lichess(akun.username, kecepatan, maks, hanya_rated,
                                sejak_ms=sejak_ms)
        else:
            pgn = ambil_chesscom(akun.username, kecepatan, maks, hanya_rated,
                                 saring_turnamen=akun.saring_turnamen,
                                 sejak_bulan=sejak_bulan)
    except Exception as e:                       # jaringan, 429, format aneh
        hasil["galat"] = str(e)[:200]
        return hasil

    pgn = (pgn or "").strip()
    if not pgn:
        return hasil

    hasil["diunduh"] = hitung_partai(pgn)
    sumber = "lichess-user" if akun.platform == "lichess" else "chesscom"
    h: HasilImpor = impor_teks(conn, pgn, sumber)
    hasil["baru"] = h.baru
    hasil["duplikat"] = h.duplikat
    hasil["dilewati"] = h.dilewati
    return hasil


def tarik_semua(conn: sqlite3.Connection, username: list[str] | None = None,
                kecepatan: list[str] | None = None, maks: int = 0,
                hanya_rated: bool = True, semua_riwayat: bool = False) -> dict:
    """Tarik beberapa akun sekaligus. Kembalikan ringkasan siap ditampilkan."""
    kecepatan = kecepatan or list(KECEPATAN_DEFAULT)
    pilih = AKUN
    if username:
        diminta = {u.lower() for u in username}
        pilih = [a for a in AKUN if a.username.lower() in diminta]
        if not pilih:
            raise ValueError(f"Tidak ada akun yang cocok: {', '.join(username)}")

    rincian = [tarik_satu(conn, a, kecepatan, maks, hanya_rated, semua_riwayat)
               for a in pilih]
    return {
        "kecepatan": kecepatan,
        "akun": rincian,
        "total_baru": sum(r["baru"] for r in rincian),
        "total_duplikat": sum(r["duplikat"] for r in rincian),
        "gagal": [r["akun"] for r in rincian if r["galat"]],
    }
