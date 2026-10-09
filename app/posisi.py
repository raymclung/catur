"""
Indeks posisi dan query penjelajah pembukaan.

Dua bagian:
  - indeks_partai(): dipanggil CLI, menulis baris posisi untuk satu partai
  - cari_langkah():  dipanggil server web, hanya membaca
"""

from __future__ import annotations

import copy
import io
import math
import sqlite3
import threading
import time

import chess
import chess.pgn

MAKS_PLY_DEFAULT = 36

SUMBER_GM = ("twic", "lichess-broadcast", "pgnmentor")

# Partai GM berarti partai antar-MANUSIA. Baris pemain yang ditandai
# 'mesin' atau 'konsultasi' (app/jenis_pemain.py) dikeluarkan: koleksi PGN
# Mentor memuat Kasparov melawan Deep Blue dan partai konsultasi yang dua
# pemainnya ditulis jadi satu nama, dan keduanya ikut terhitung sebagai
# partai GM sampai 25 Sep 2026. Partainya TIDAK dihapus — hanya tidak
# dihitung sebagai statistik pembukaan antar-manusia.
SYARAT_ANTAR_MANUSIA = (
    "NOT EXISTS (SELECT 1 FROM pemain mp WHERE mp.id IN (g.putih_id, g.hitam_id)"
    "            AND mp.jenis IS NOT NULL)"
)


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


# ------------------------------------------------------------------ cache
# Posisi awal dicocokkan ~259 ribu partai, jadi tiap kali dibuka makan 1-3
# detik (27 Sep 2026; di server yang sedang menjalankan batch jauh lebih
# lama), dan justru posisi-posisi itulah yang paling sering diminta:
# /jelajah dibuka dari sana, repertoire menelusurinya puluhan kali.
#
# Risiko yang diterima (keputusan Ray 27 Sep): hasil yang disimpan bisa
# tertinggal paling lama CACHE_DETIK. Dipersempit dengan tiga cara:
#   * hanya hasil MAHAL yang disimpan (total >= CACHE_AMBANG partai);
#     posisi yang lebih dalam selalu dihitung langsung;
#   * kuncinya memuat MAX(partai.id), jadi impor dari terminal pun langsung
#     membuat simpanan lama tidak terpakai;
#   * impor lewat web memanggil kosongkan_cache().
# Yang bisa tertinggal: perubahan tanpa partai baru (gabung pemain, hasil
# siaran yang diperbarui, penandaan pemain mesin).
CACHE_DETIK = 600
CACHE_AMBANG = 1000
CACHE_MAKS = 500
_cache: dict[tuple, tuple[float, dict]] = {}
# Endpoint sinkron FastAPI jalan di threadpool: tanpa kunci, `kc in _cache`
# lalu `_cache[kc]` bisa KeyError kalau thread lain membuang entrinya di
# antaranya, dan min() atas dict yang sedang diisi thread lain bisa
# RuntimeError (temuan audit 27 Sep). Kueri mahalnya sengaja di LUAR kunci:
# dua permintaan serentak paling buruk menghitung posisi yang sama dua kali.
_kunci_cache_lock = threading.Lock()


def kosongkan_cache() -> None:
    with _kunci_cache_lock:
        _cache.clear()


def _kunci_cache(conn: sqlite3.Connection, argumen: tuple) -> tuple | None:
    """None = jangan pakai cache (database di memori: tidak punya identitas)."""
    berkas = conn.execute("PRAGMA database_list").fetchone()[2]
    if not berkas:
        return None
    terakhir = conn.execute("SELECT MAX(id) FROM partai").fetchone()[0]
    return (berkas, terakhir) + argumen


def cari_langkah(conn: sqlite3.Connection, kunci: str, sumber: str = "gm",
                 min_elo: int | None = None, jumlah_contoh: int = 10,
                 pemain_id: int | None = None, warna: str | None = None) -> dict:
    """cari_langkah_langsung() dengan cache untuk posisi yang mahal (lihat di atas).

    Hasil selalu salinan, supaya pemanggil yang mengubahnya tidak mencemari
    simpanan."""
    kc = _kunci_cache(conn, (kunci, sumber, min_elo, jumlah_contoh, pemain_id, warna))
    sekarang = time.monotonic()
    if kc is not None:
        with _kunci_cache_lock:
            ada = _cache.get(kc)
            if ada is not None and sekarang - ada[0] >= CACHE_DETIK:
                del _cache[kc]
                ada = None
        if ada is not None:
            return copy.deepcopy(ada[1])     # simpanan tidak pernah diubah di tempat
    hasil = cari_langkah_langsung(conn, kunci, sumber, min_elo, jumlah_contoh,
                                  pemain_id, warna)
    if kc is not None and hasil["total"] >= CACHE_AMBANG:
        salinan = copy.deepcopy(hasil)
        with _kunci_cache_lock:
            if kc not in _cache and len(_cache) >= CACHE_MAKS:
                _cache.pop(min(_cache, key=lambda k: _cache[k][0]))
            _cache[kc] = (sekarang, salinan)
    return hasil


def peta_partai(conn: sqlite3.Connection, syarat: str, params: list) -> dict[int, tuple]:
    """{partai_id: (hasil, elo_putih, elo_hitam)} untuk partai yang memenuhi
    `syarat` (SQL atas alias g). Pasangan langkah_dari_peta()."""
    return {r[0]: (r[1], r[2], r[3]) for r in conn.execute(
        f"SELECT g.id, g.hasil, g.elo_putih, g.elo_hitam FROM partai g WHERE {syarat}", params)}


def peta_partai_gm(conn: sqlite3.Connection) -> dict[int, tuple]:
    """Partai yang dihitung `cari_langkah(sumber="gm")`, tanpa saringan Elo."""
    return peta_partai(conn, f"g.sumber IN ({','.join('?' * len(SUMBER_GM))}) "
                             f"AND {SYARAT_ANTAR_MANUSIA}", list(SUMBER_GM))


def _bulat(x: float) -> int:
    """ROUND() SQLite: setengah menjauhi nol, bukan pembulatan bankir Python."""
    return int(math.floor(x + 0.5)) if x >= 0 else -int(math.floor(-x + 0.5))


def langkah_dari_peta(conn: sqlite3.Connection, kunci: str, peta: dict[int, tuple]) -> list[dict]:
    """
    Daftar `langkah` seperti cari_langkah_langsung(), tapi partainya sudah
    disaring sekali di `peta` (lihat peta_partai) — posisi dibaca dari indeks
    penutup idx_posisi_fen_langkah saja, tanpa JOIN per posisi.

    Untuk pemanggil yang menanyakan RATUSAN posisi dengan saringan yang sama
    (unduh repertoire: 118 posisi, 7 detik per warna, diukur 29 Sep 2026).
    Urutan sama dengan SQL-nya: n turun, elo_rata turun (NULL terakhir), lalu
    urutan uci. Tanpa kolom `terakhir` dan partai contoh.
    """
    return agregasi_langkah(conn.execute(
        "SELECT DISTINCT langkah_san, langkah_uci, partai_id FROM posisi WHERE fen_kunci = ?",
        (kunci,)), peta)


def baris_posisi_partai(conn: sqlite3.Connection, partai_ids) -> dict[str, list[tuple]]:
    """{fen_kunci: [(san, uci, partai_id), ...]} untuk SEMUA posisi partai-partai
    ini (DISTINCT seperti langkah_dari_peta). Untuk himpunan partai yang kecil
    (partai sendiri): memuat semuanya sekali lewat kunci utama (partai_id, ply)
    jauh lebih murah daripada memindai baris GM di tiap posisi yang populer."""
    hasil: dict[str, set] = {}
    ids = list(partai_ids)
    for i in range(0, len(ids), 900):
        potong = ids[i:i + 900]
        for fen, san, uci, pid in conn.execute(
                "SELECT fen_kunci, langkah_san, langkah_uci, partai_id FROM posisi "
                f"WHERE partai_id IN ({','.join('?' * len(potong))})", potong):
            hasil.setdefault(fen, set()).add((san, uci, pid))
    return {k: list(v) for k, v in hasil.items()}


def agregasi_langkah(baris, peta: dict[int, tuple]) -> list[dict]:
    """Inti langkah_dari_peta: baris (san, uci, partai_id) yang SUDAH distinct."""
    per_uci: dict[str, list] = {}
    for san, uci, pid in baris:
        g = peta.get(pid)
        if g is None:
            continue
        hasil, ep, eh = g
        r = per_uci.setdefault(uci, [san, 0, 0, 0, 0, 0.0, 0])
        r[1] += 1
        r[2] += hasil == "1-0"
        r[3] += hasil == "1/2-1/2"
        r[4] += hasil == "0-1"
        if ep is not None and eh is not None:
            r[5] += (ep + eh) / 2.0
            r[6] += 1
    langkah = []
    for uci, r in sorted(per_uci.items()):
        selesai = r[2] + r[3] + r[4]
        langkah.append({
            "san": r[0], "uci": uci, "n": r[1],
            "menang_putih": r[2], "remis": r[3], "menang_hitam": r[4],
            "elo_rata": float(_bulat(r[5] / r[6])) if r[6] else None,
            "skor_putih": round(100 * (r[2] + 0.5 * r[3]) / selesai, 1) if selesai else None,
            "belum_selesai": r[1] - selesai,
        })
    langkah.sort(key=lambda l: (-l["n"], l["elo_rata"] is None, -(l["elo_rata"] or 0)))
    return langkah


def cari_langkah_langsung(conn: sqlite3.Connection, kunci: str, sumber: str = "gm",
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
        syarat.append(SYARAT_ANTAR_MANUSIA)
    elif sumber not in ("semua", ""):
        syarat.append("g.sumber = ?")
        params.append(sumber)
    if min_elo:
        syarat.append("g.elo_putih >= ? AND g.elo_hitam >= ?")
        params += [min_elo, min_elo]
    where = " AND ".join(syarat)

    # Satu partai dihitung SEKALI per posisi. Posisi yang berulang di partai
    # yang sama (pengulangan langkah, manuver bolak-balik) dulu menambah n
    # dan skornya dua-tiga kali — audit 27 Sep: 13.850 pasangan (partai,
    # posisi) berulang, condong ke remis karena pengulangan tiga kali.
    # Kalau partai yang sama memainkan dua langkah BERBEDA dari posisi itu,
    # ia terhitung di kedua langkah: memang dua-duanya dimainkan di sana.
    per_langkah = ("(SELECT DISTINCT fen_kunci, langkah_san, langkah_uci, partai_id"
                   " FROM posisi WHERE fen_kunci = ?)")
    # Partai contoh: kemunculan PERTAMA posisi itu di tiap partai.
    per_partai = ("(SELECT fen_kunci, partai_id, langkah_san, MIN(ply) AS ply"
                  " FROM posisi WHERE fen_kunci = ? GROUP BY partai_id)")

    langkah = [dict(r) for r in conn.execute(
        f"""
        SELECT p.langkah_san AS san, p.langkah_uci AS uci,
               COUNT(*)                        AS n,
               SUM(g.hasil = '1-0')            AS menang_putih,
               SUM(g.hasil = '1/2-1/2')        AS remis,
               SUM(g.hasil = '0-1')            AS menang_hitam,
               ROUND(AVG((g.elo_putih + g.elo_hitam) / 2.0)) AS elo_rata,
               MAX(g.tanggal)                  AS terakhir
        FROM {per_langkah} p JOIN partai g ON g.id = p.partai_id
        WHERE {where}
        GROUP BY p.langkah_uci
        ORDER BY n DESC, elo_rata DESC
        """,
        [kunci] + params,
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
        FROM {per_partai} p
        JOIN partai g ON g.id = p.partai_id
        JOIN pemain pw ON pw.id = g.putih_id
        JOIN pemain pb ON pb.id = g.hitam_id
        LEFT JOIN turnamen t ON t.id = g.turnamen_id
        WHERE {where}
        ORDER BY (COALESCE(g.elo_putih, 0) + COALESCE(g.elo_hitam, 0)) DESC, g.tanggal DESC
        LIMIT ?
        """,
        [kunci] + params + [jumlah_contoh],
    )]

    return {"kunci": kunci, "total": sum(l["n"] for l in langkah),
            "langkah": langkah, "partai": contoh}
