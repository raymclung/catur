"""
Simpan dan baca hasil analisis engine di database.

Jembatan antara dua dunia yang sengaja dipisah:

    src/catur_analysis/   menjalankan Stockfish, menghasilkan LaporanPartai
    app/                  server web, hanya membaca database

Modul ini satu-satunya yang tahu bentuk kedua sisi. Server web tidak pernah
mengimpor catur_analysis, dan catur_analysis tidak pernah tahu ada database.
"""

from __future__ import annotations

import sqlite3
import time

from .db import sekarang


def sudah_dianalisis(conn: sqlite3.Connection, partai_id: int) -> bool:
    return conn.execute(
        "SELECT 1 FROM analisis WHERE partai_id = ?", (partai_id,)
    ).fetchone() is not None


# Kalau penulis lain sedang memegang database (impor TWIC bisa menahan kunci
# lebih lama dari busy_timeout 5 detik), penyimpanan dicoba ulang dengan jeda
# menaik, bukan langsung gagal: satu analisis = ±20 detik kerja engine, dan
# sebelum ini satu kunci membuat batch 266 partai mati di partai ke-70.
ULANG_MAKS = 6
JEDA_AWAL = 2.0


def _terkunci(e: Exception) -> bool:
    return isinstance(e, sqlite3.OperationalError) and "locked" in str(e).lower()


def simpan_laporan(
    conn: sqlite3.Connection,
    partai_id: int,
    laporan,
    engine: str,
    profil: str,
    durasi_detik: float | None = None,
    lapor=None,
) -> None:
    """
    Simpan satu LaporanPartai. Kalau partai ini sudah pernah dianalisis,
    hasil lama dihapus dulu — jadi menganalisis ulang dengan depth berbeda
    tidak menghasilkan dua versi.

    Semua dalam satu transaksi: kalau gagal di tengah, tidak ada hasil
    setengah jadi yang tersimpan. Database terkunci dicoba ulang sampai
    ULANG_MAKS kali; `lapor` (opsional) dipanggil dengan pesan tiap percobaan.
    """
    jeda = JEDA_AWAL
    for percobaan in range(1, ULANG_MAKS + 1):
        try:
            _tulis_laporan(conn, partai_id, laporan, engine, profil, durasi_detik)
            return
        except Exception as e:
            if not _terkunci(e) or percobaan == ULANG_MAKS:
                raise
            if lapor:
                lapor(f"database terkunci, coba lagi {percobaan}/{ULANG_MAKS - 1} "
                      f"setelah {jeda:.0f} detik")
            time.sleep(jeda)
            jeda *= 2


def _tulis_laporan(
    conn: sqlite3.Connection,
    partai_id: int,
    laporan,
    engine: str,
    profil: str,
    durasi_detik: float | None = None,
) -> None:
    rp = laporan.ringkasan_putih
    rh = laporan.ringkasan_hitam

    try:
        conn.execute("DELETE FROM analisis_langkah WHERE partai_id = ?", (partai_id,))
        conn.execute("DELETE FROM analisis WHERE partai_id = ?", (partai_id,))

        conn.execute(
            """
            INSERT INTO analisis(
                partai_id, engine, depth, profil,
                akurasi_putih, akurasi_hitam,
                blunder_putih, mistake_putih, inaccuracy_putih,
                blunder_hitam, mistake_hitam, inaccuracy_hitam,
                turning_point_ply, durasi_detik, dianalisis_pada
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                partai_id, engine, laporan.depth, profil,
                rp.akurasi if rp else None, rh.akurasi if rh else None,
                rp.blunder if rp else 0, rp.mistake if rp else 0, rp.inaccuracy if rp else 0,
                rh.blunder if rh else 0, rh.mistake if rh else 0, rh.inaccuracy if rh else 0,
                laporan.turning_point_ply, durasi_detik, sekarang(),
            ),
        )

        conn.executemany(
            """
            INSERT INTO analisis_langkah(
                partai_id, ply, nomor_langkah, warna, san, uci,
                eval_sebelum_cp, eval_sesudah_cp, eval_putih_cp, mat_sesudah,
                kehilangan_cp, klasifikasi,
                langkah_terbaik_san, langkah_terbaik_uci, variasi_terbaik
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            [
                (
                    partai_id, l.ply, l.nomor_langkah, l.warna, l.san, l.uci,
                    l.eval_sebelum_cp, l.eval_sesudah_cp, l.eval_putih_cp, l.mat_sesudah,
                    l.kehilangan_cp, l.klasifikasi,
                    l.langkah_terbaik_san or None, l.langkah_terbaik_uci or None,
                    " ".join(l.variasi_terbaik) if l.variasi_terbaik else None,
                )
                for l in laporan.langkah
            ],
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def ambil_analisis(conn: sqlite3.Connection, partai_id: int) -> dict | None:
    """
    Hasil analisis satu partai dalam bentuk siap-JSON, atau None kalau
    partai ini belum dianalisis.
    """
    ringkasan = conn.execute(
        "SELECT * FROM analisis WHERE partai_id = ?", (partai_id,)
    ).fetchone()
    if ringkasan is None:
        return None

    langkah = conn.execute(
        "SELECT * FROM analisis_langkah WHERE partai_id = ? ORDER BY ply",
        (partai_id,),
    ).fetchall()

    hasil = dict(ringkasan)
    hasil["langkah"] = []
    mat_sebelumnya = None          # mat_sesudah ply sebelumnya = mat SEBELUM ply ini
    for l in langkah:
        d = dict(l)
        d["variasi_terbaik"] = d["variasi_terbaik"].split() if d["variasi_terbaik"] else []
        d["mat_sesudah_pemain"] = mat_dari_sudut_pemain(d["mat_sesudah"], d["warna"])
        # Posisi SEBELUM langkah ini = posisi sesudah langkah sebelumnya, jadi
        # informasi matnya diambil dari baris sebelumnya lalu dilihat dari sudut
        # pemain yang sekarang jalan.
        d["mat_sebelum_pemain"] = (mat_dari_sudut_pemain(mat_sebelumnya, d["warna"])
                                   if mat_sebelumnya else None)
        d["skor_sebelum_teks"] = teks_skor(d["eval_sebelum_cp"], d["mat_sebelum_pemain"])
        d["skor_sesudah_teks"] = teks_skor(d["eval_sesudah_cp"], d["mat_sesudah_pemain"])
        mat_sebelumnya = d["mat_sesudah"]
        hasil["langkah"].append(d)
    return hasil


# Nilai yang dipakai analisis untuk "mat": lihat SKOR_MAT di
# catur_analysis/partai.py. Ditulis ulang di sini supaya modul database tidak
# perlu mengimpor engine.
BATAS_SKOR_MAT = 100_000


def teks_skor(cp: int | None, mat: int | None) -> str:
    """
    Skor siap tampil dari sudut pemain yang jalan.

    Mat ditulis sebagai mat, bukan angka: engine menyimpan mat sebagai
    +/-100000 sentipion, dan ditampilkan apa adanya jadi "+1000,00". Ray, 13 Sep:
    "kalau mate pake mate in berapa jangan +1000", lalu "ya pake format M4".
    Formatnya sama dengan papan analisis (web/papan.js formatEval), supaya satu
    posisi tidak ditulis dua cara di dua halaman. Contoh nyata: partai 8652,
    29...Nxg5 membuang M3 lalu jadi -3,76.

        mat 3        -> "M3"      (pemain ini yang memberi mat)
        mat -2       -> "-M2"     (pemain ini yang kena mat)
        mat 0        -> "#"       (mat sudah terjadi)
        cp 451       -> "+4,51"
        cp -451      -> "-4,51"
    """
    if mat is not None:
        if mat == 0:
            return "#"
        return f"M{mat}" if mat > 0 else f"-M{-mat}"
    if cp is None:
        return "?"
    # skor mat tanpa jarak mat (mis. posisi akhir): tetap jangan tampil 1000,00
    if cp >= BATAS_SKOR_MAT:
        return "M"
    if cp <= -BATAS_SKOR_MAT:
        return "-M"
    return f"{'+' if cp > 0 else ''}{cp / 100:.2f}".replace(".", ",")


def hapus_analisis(conn: sqlite3.Connection, partai_id: int) -> bool:
    cur = conn.execute("DELETE FROM analisis WHERE partai_id = ?", (partai_id,))
    conn.execute("DELETE FROM analisis_langkah WHERE partai_id = ?", (partai_id,))
    conn.commit()
    return cur.rowcount > 0


def mat_dari_sudut_pemain(mat_sesudah: int | None, warna: str) -> int | None:
    """
    `mat_sesudah` dari sudut pandang PEMAIN YANG BARU JALAN, bukan Putih.

    Perlu karena satu baris analisis_langkah memuat DUA sudut pandang:
    `eval_sebelum_cp`/`eval_sesudah_cp` dari sudut pemain yang jalan,
    sedangkan `eval_putih_cp` dan `mat_sesudah` dari sudut Putih. Mencampur
    keduanya sudah menghasilkan tampilan salah sungguhan: halaman Kekalahan
    sempat menulis "mat dalam 2 untuk saya" untuk partai yang justru KENA
    mat (13 Sep 2026). Nilai turunan ini disediakan supaya tiap halaman tidak
    perlu menggabungkan dua konvensi sendiri-sendiri.

    0 tetap 0: artinya mat SUDAH terjadi, dan yang baru jalan adalah yang
    memberi mat.
    """
    if mat_sesudah is None:
        return None
    if mat_sesudah == 0:
        return 0
    return mat_sesudah if warna == "putih" else -mat_sesudah


def statistik_analisis(conn: sqlite3.Connection) -> dict:
    satu = lambda sql: conn.execute(sql).fetchone()[0]
    return {
        "partai_dianalisis": satu("SELECT COUNT(*) FROM analisis"),
        "partai_total": satu("SELECT COUNT(*) FROM partai"),
        "langkah_dianalisis": satu("SELECT COUNT(*) FROM analisis_langkah"),
    }
