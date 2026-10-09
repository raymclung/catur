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


def hapus_analisis(conn: sqlite3.Connection, partai_id: int, commit: bool = True) -> bool:
    """`commit=False` untuk pemanggil yang sedang di dalam transaksi/SAVEPOINT
    sendiri (impor per partai): commit di sini akan melepas savepoint-nya."""
    cur = conn.execute("DELETE FROM analisis WHERE partai_id = ?", (partai_id,))
    conn.execute("DELETE FROM analisis_langkah WHERE partai_id = ?", (partai_id,))
    if commit:
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


# COUNT(*) analisis_langkah memindai 23 juta entri indeks (0,25 detik hangat,
# jauh lebih lama saat disk dingin) di tiap /api/statistik dan halaman Mesin,
# padahal angkanya hanya berubah kalau ada analisis yang disimpan/dihapus.
# Disimpan per database selama (jumlah, id terbesar) tabel analisis sama.
_langkah_tersimpan: dict = {}


def statistik_analisis(conn: sqlite3.Connection) -> dict:
    satu = lambda sql: conn.execute(sql).fetchone()[0]
    dianalisis, id_akhir = conn.execute("SELECT COUNT(*), MAX(id) FROM analisis").fetchone()
    berkas = conn.execute("PRAGMA database_list").fetchone()[2]
    kunci = (berkas, dianalisis, id_akhir)
    langkah = _langkah_tersimpan.get(kunci) if berkas else None
    if langkah is None:
        langkah = satu("SELECT COUNT(*) FROM analisis_langkah")
        if berkas:
            _langkah_tersimpan.clear()
            _langkah_tersimpan[kunci] = langkah
    return {
        "partai_dianalisis": dianalisis,
        "partai_total": satu("SELECT COUNT(*) FROM partai"),
        "langkah_dianalisis": langkah,
    }


# ---------------------------------------------------------------------------
# Hitung ulang klasifikasi & akurasi analisis yang sudah tersimpan
# ---------------------------------------------------------------------------

ROKADE_LICHESS = ("e1h1", "e1a1", "e8h8", "e8a8")    # raja-makan-benteng


def klasifikasi_ulang_satu(conn: sqlite3.Connection, partai_id: int) -> dict | None:
    """
    Terapkan aturan klasifikasi & akurasi yang BERLAKU SEKARANG
    (catur_analysis.partai) ke satu analisis tersimpan, tanpa engine.

    Eval tiap langkah sudah tersimpan, jadi yang dihitung ulang hanya
    turunannya: klasifikasi, kehilangan langkah terbaik/paksa (= 0), rokade
    Lichess `e8h8` -> `e8g8`, akurasi, dan jumlah blunder/mistake/inaccuracy.
    Langkah paksa dikenali dengan memutar ulang PGN. Idempoten: dijalankan dua
    kali hasilnya sama. Kembalikan ringkasan perubahan, None kalau tak ada
    analisis atau PGN tak terbaca.
    """
    import io                                                   # noqa: PLC0415
    from types import SimpleNamespace                           # noqa: PLC0415
    import chess                                                # noqa: PLC0415
    import chess.pgn                                            # noqa: PLC0415
    from catur_analysis.partai import (CP_AWAL, akurasi_partai,   # noqa: PLC0415
                                       klasifikasikan)

    baris_pgn = conn.execute("SELECT pgn FROM partai WHERE id = ?", (partai_id,)).fetchone()
    rows = conn.execute(
        "SELECT ply, warna, eval_sebelum_cp, eval_sesudah_cp, eval_putih_cp, kehilangan_cp, "
        "klasifikasi, langkah_terbaik_uci FROM analisis_langkah WHERE partai_id = ? ORDER BY ply",
        (partai_id,)).fetchall()
    if baris_pgn is None or not rows:
        return None
    game = chess.pgn.read_game(io.StringIO(baris_pgn[0]))
    if game is None:
        return None

    # papan sebelum tiap ply: langkah paksa + ubah rokade Lichess ke UCI standar
    board = game.board()
    paksa, rokade = {}, {}
    per_ply = {r[0]: r for r in rows}
    for ply, mv in enumerate(game.mainline_moves()):
        paksa[ply] = board.legal_moves.count() == 1
        r = per_ply.get(ply)
        if r is not None and (r[7] or "") in ROKADE_LICHESS:
            try:
                gerak = chess.Move.from_uci(r[7])
                standar = board.parse_san(board.san(gerak)).uci() if gerak in board.legal_moves else r[7]
                if standar != r[7]:          # e1h1 bisa juga Rh1 biasa: itu dibiarkan
                    rokade[ply] = standar
            except ValueError:
                pass
        board.push(mv)

    ubah = []
    baru = []
    hitung = {w: {"blunder": 0, "mistake": 0, "inaccuracy": 0} for w in ("putih", "hitam")}
    for ply, warna, sebelum, sesudah, putih_cp, hilang, klas, terbaik_uci in rows:
        if klas == "buku":
            klas_baru, hilang_baru = klas, hilang
        else:
            terbaik = klas == "terbaik" or paksa.get(ply, False)
            klas_baru = klasifikasikan(sebelum, sesudah, terbaik)
            hilang_baru = 0 if terbaik else hilang
            if klas_baru in hitung[warna]:
                hitung[warna][klas_baru] += 1
        uci_baru = rokade.get(ply, terbaik_uci)
        if (klas_baru, hilang_baru, uci_baru) != (klas, hilang, terbaik_uci):
            ubah.append((klas_baru, hilang_baru, uci_baru, partai_id, ply))
        baru.append(SimpleNamespace(warna=warna, eval_putih_cp=putih_cp, klasifikasi=klas_baru))

    pertama = rows[0]
    awal = CP_AWAL if game.board().fen() == chess.STARTING_FEN else (
        pertama[2] if pertama[1] == "putih" else -pertama[2])
    akurasi = akurasi_partai(baru, awal)
    if ubah:
        conn.executemany(
            "UPDATE analisis_langkah SET klasifikasi = ?, kehilangan_cp = ?, langkah_terbaik_uci = ? "
            "WHERE partai_id = ? AND ply = ?", ubah)
    conn.execute(
        "UPDATE analisis SET akurasi_putih = ?, akurasi_hitam = ?, "
        "blunder_putih = ?, mistake_putih = ?, inaccuracy_putih = ?, "
        "blunder_hitam = ?, mistake_hitam = ?, inaccuracy_hitam = ? WHERE partai_id = ?",
        (akurasi["putih"], akurasi["hitam"],
         hitung["putih"]["blunder"], hitung["putih"]["mistake"], hitung["putih"]["inaccuracy"],
         hitung["hitam"]["blunder"], hitung["hitam"]["mistake"], hitung["hitam"]["inaccuracy"],
         partai_id))
    return {"langkah_berubah": len(ubah), "rokade": len(rokade)}


def klasifikasi_ulang(conn: sqlite3.Connection, per_commit: int = 500,
                      lapor=print, batas: int | None = None) -> dict:
    """klasifikasi_ulang_satu() untuk semua analisis, commit per `per_commit` partai."""
    ids = [r[0] for r in conn.execute("SELECT partai_id FROM analisis ORDER BY partai_id")]
    if batas:
        ids = ids[:batas]
    total = {"partai": 0, "langkah_berubah": 0, "rokade": 0, "dilewati": 0}
    mulai = time.time()
    for i, pid in enumerate(ids, start=1):
        h = klasifikasi_ulang_satu(conn, pid)
        if h is None:
            total["dilewati"] += 1
        else:
            total["partai"] += 1
            total["langkah_berubah"] += h["langkah_berubah"]
            total["rokade"] += h["rokade"]
        if i % per_commit == 0 or i == len(ids):
            conn.commit()
            if lapor:
                sisa = (time.time() - mulai) / i * (len(ids) - i) / 60
                lapor(f"  {i}/{len(ids)} partai, {total['langkah_berubah']} langkah berubah, "
                      f"sisa ~{sisa:.0f} menit")
    return total


if __name__ == "__main__":
    import argparse                                             # noqa: PLC0415
    import sys                                                  # noqa: PLC0415
    from pathlib import Path                                    # noqa: PLC0415

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
    from .db import koneksi                                     # noqa: PLC0415
    from .kunci_penulis import KunciPenulis, KunciTerpakai      # noqa: PLC0415

    p = argparse.ArgumentParser(description="Hitung ulang klasifikasi & akurasi analisis "
                                            "tersimpan dengan aturan sekarang (tanpa engine).")
    p.add_argument("--klasifikasi-ulang", action="store_true", required=True)
    p.add_argument("--batas", type=int, help="hanya N analisis pertama (uji coba)")
    p.add_argument("--db")
    a = p.parse_args()
    try:
        kunci = KunciPenulis("hitung ulang klasifikasi analisis").ambil()
    except KunciTerpakai as e:
        print(e)
        raise SystemExit(1)
    try:
        conn = koneksi(a.db) if a.db else koneksi()
        conn.execute("PRAGMA busy_timeout = 120000")
        print(klasifikasi_ulang(conn, batas=a.batas, lapor=lambda s: print(s, flush=True)))
        conn.close()
    finally:
        kunci.lepas()

