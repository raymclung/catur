"""Catatan sumber yang sudah diimpor (zip TWIC, ronde broadcast)."""

from __future__ import annotations

import sqlite3

from .db import sekarang


def sudah_selesai(conn: sqlite3.Connection, jenis: str, kunci: str) -> bool:
    """True kalau sumber ini sudah diimpor DAN sumbernya sudah final."""
    r = conn.execute(
        "SELECT selesai FROM sumber_impor WHERE jenis = ? AND kunci = ?",
        (jenis, str(kunci)),
    ).fetchone()
    return bool(r and r["selesai"])


def catat(conn: sqlite3.Connection, jenis: str, kunci: str, nama: str | None,
          url: str | None, jumlah_partai: int, jumlah_lolos: int,
          jumlah_baru: int, selesai: bool = True) -> None:
    conn.execute(
        """
        INSERT INTO sumber_impor(jenis, kunci, nama, url, jumlah_partai,
                                 jumlah_lolos_saringan, jumlah_baru, selesai,
                                 diimpor_pada)
        VALUES (?,?,?,?,?,?,?,?,?)
        ON CONFLICT(jenis, kunci) DO UPDATE SET
            nama = excluded.nama, url = excluded.url,
            jumlah_partai = excluded.jumlah_partai,
            jumlah_lolos_saringan = excluded.jumlah_lolos_saringan,
            jumlah_baru = jumlah_baru + excluded.jumlah_baru,
            selesai = excluded.selesai,
            diimpor_pada = excluded.diimpor_pada
        """,
        (jenis, str(kunci), nama, url, jumlah_partai, jumlah_lolos,
         jumlah_baru, 1 if selesai else 0, sekarang()),
    )
    conn.commit()


def ringkasan(conn: sqlite3.Connection) -> list[dict]:
    return [dict(r) for r in conn.execute(
        "SELECT jenis, COUNT(*) AS sumber, SUM(jumlah_partai) AS partai_di_sumber, "
        "SUM(jumlah_lolos_saringan) AS lolos, SUM(jumlah_baru) AS baru, "
        "SUM(selesai = 0) AS belum_final FROM sumber_impor GROUP BY jenis"
    )]
