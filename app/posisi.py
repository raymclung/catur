"""
Indeks posisi dan query penjelajah pembukaan.

Dua bagian:
  - indeks_partai(): dipanggil CLI, menulis baris posisi untuk satu partai
  - cari_langkah():  dipanggil server web, hanya membaca
"""

from __future__ import annotations

import io
import sqlite3

import chess
import chess.pgn

MAKS_PLY_DEFAULT = 36

SUMBER_GM = ("twic", "lichess-broadcast", "pgnmentor")


def fen_kunci(board_atau_fen) -> str:
    """
    FEN tanpa halfmove clock dan fullmove number.

    Dua jalur langkah yang sampai ke posisi sama (transposisi) menghasilkan
    kunci yang sama. Field en passant dibiarkan: python-chess hanya
    menuliskannya kalau tangkapan en passant memang legal, jadi sudah
    ternormalisasi.
    """
    fen = board_atau_fen.fen() if isinstance(board_atau_fen, chess.Board) else str(board_atau_fen)
    bagian = fen.strip().split()
    if len(bagian) < 4:
        raise ValueError(f"FEN tidak lengkap: {fen!r}")
    return " ".join(bagian[:4])


def kunci_dari_fen_pengguna(fen: str) -> str:
    """Validasi FEN dari luar (query string) lalu kembalikan kuncinya.
    Melempar ValueError kalau bukan posisi yang sah."""
    board = chess.Board(fen)  # ValueError kalau rusak
    if not board.is_valid():
        raise ValueError("posisi tidak sah")
    return fen_kunci(board)


def baris_posisi(pgn: str, maks_ply: int = MAKS_PLY_DEFAULT) -> list[tuple[str, int, str, str]]:
    """(fen_kunci, ply, uci, san) untuk tiap langkah sampai maks_ply."""
    game = chess.pgn.read_game(io.StringIO(pgn))
    if game is None or game.errors:
        return []
    board = game.board()
    keluar = []
    for ply, move in enumerate(game.mainline_moves()):
        if ply >= maks_ply:
            break
        keluar.append((fen_kunci(board), ply, move.uci(), board.san(move)))
        board.push(move)
    return keluar


def indeks_partai(conn: sqlite3.Connection, partai_id: int, pgn: str,
                  maks_ply: int = MAKS_PLY_DEFAULT) -> int:
    baris = baris_posisi(pgn, maks_ply)
    if not baris:
        return 0
    conn.executemany(
        "INSERT OR REPLACE INTO posisi(fen_kunci, partai_id, ply, langkah_uci, langkah_san) "
        "VALUES (?,?,?,?,?)",
        [(f, partai_id, p, u, s) for f, p, u, s in baris],
    )
    return len(baris)


def id_belum_terindeks(conn: sqlite3.Connection, batas: int = 0) -> list[int]:
    sql = ("SELECT g.id FROM partai g WHERE NOT EXISTS "
           "(SELECT 1 FROM posisi p WHERE p.partai_id = g.id) ORDER BY g.id")
    if batas:
        sql += f" LIMIT {int(batas)}"
    return [r[0] for r in conn.execute(sql)]


def statistik_indeks(conn: sqlite3.Connection) -> dict:
    satu = lambda s: conn.execute(s).fetchone()[0]
    return {
        "baris_posisi": satu("SELECT COUNT(*) FROM posisi"),
        "partai_terindeks": satu("SELECT COUNT(DISTINCT partai_id) FROM posisi"),
        "partai_total": satu("SELECT COUNT(*) FROM partai"),
    }


def cari_langkah(conn: sqlite3.Connection, kunci: str, sumber: str = "gm",
                 min_elo: int | None = None, jumlah_contoh: int = 10,
                 pemain_id: int | None = None, warna: str | None = None) -> dict:
    """
    Langkah-langkah yang pernah dimainkan dari posisi ini, beserta hasilnya.

    Skor selalu dari sudut pandang PUTIH (persen). Kolom elo_rata adalah
    rata-rata Elo kedua pemain, bukan Elo pemain yang jalan.

    pemain_id + warna (permintaan frontend #5) membatasi ke partai satu pemain:
    "apa yang Carlsen mainkan di posisi ini sebagai Hitam". Tanpa warna =
    kedua warna. Berlaku untuk tabel langkah DAN partai contoh, karena keduanya
    memakai syarat yang sama.
    """
    syarat = ["p.fen_kunci = ?"]
    params: list = [kunci]
    if pemain_id is not None:
        if warna == "putih":
            syarat.append("g.putih_id = ?")
            params.append(pemain_id)
        elif warna == "hitam":
            syarat.append("g.hitam_id = ?")
            params.append(pemain_id)
        else:
            syarat.append("(g.putih_id = ? OR g.hitam_id = ?)")
            params += [pemain_id, pemain_id]
    if sumber == "gm":
        syarat.append(f"g.sumber IN ({','.join('?' * len(SUMBER_GM))})")
        params += list(SUMBER_GM)
    elif sumber not in ("semua", ""):
        syarat.append("g.sumber = ?")
        params.append(sumber)
    if min_elo:
        syarat.append("g.elo_putih >= ? AND g.elo_hitam >= ?")
        params += [min_elo, min_elo]
    where = " AND ".join(syarat)

    langkah = [dict(r) for r in conn.execute(
        f"""
        SELECT p.langkah_san AS san, p.langkah_uci AS uci,
               COUNT(*)                        AS n,
               SUM(g.hasil = '1-0')            AS menang_putih,
               SUM(g.hasil = '1/2-1/2')        AS remis,
               SUM(g.hasil = '0-1')            AS menang_hitam,
               ROUND(AVG((g.elo_putih + g.elo_hitam) / 2.0)) AS elo_rata,
               MAX(g.tanggal)                  AS terakhir
        FROM posisi p JOIN partai g ON g.id = p.partai_id
        WHERE {where}
        GROUP BY p.langkah_uci
        ORDER BY n DESC, elo_rata DESC
        """,
        params,
    )]
    for l in langkah:
        selesai = l["menang_putih"] + l["remis"] + l["menang_hitam"]
        l["skor_putih"] = round(100 * (l["menang_putih"] + 0.5 * l["remis"]) / selesai, 1) if selesai else None
        l["belum_selesai"] = l["n"] - selesai

    contoh = [dict(r) for r in conn.execute(
        f"""
        SELECT g.id, g.tanggal, g.hasil, g.eco, p.langkah_san AS langkah,
               pw.nama AS putih, pw.gelar AS putih_gelar, g.elo_putih,
               pb.nama AS hitam, pb.gelar AS hitam_gelar, g.elo_hitam,
               t.nama AS turnamen
        FROM posisi p
        JOIN partai g ON g.id = p.partai_id
        JOIN pemain pw ON pw.id = g.putih_id
        JOIN pemain pb ON pb.id = g.hitam_id
        LEFT JOIN turnamen t ON t.id = g.turnamen_id
        WHERE {where}
        ORDER BY (COALESCE(g.elo_putih, 0) + COALESCE(g.elo_hitam, 0)) DESC, g.tanggal DESC
        LIMIT ?
        """,
        params + [jumlah_contoh],
    )]

    return {"kunci": kunci, "total": sum(l["n"] for l in langkah),
            "langkah": langkah, "partai": contoh}
