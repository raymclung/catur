"""
Pola taktik di balik kesalahan sendiri (RENCANA §14, pustaka pola).

Halaman Kelemahan dan Kekalahan sudah menjawab DI MANA kesalahan terjadi
(pembukaan, fase, warna). Yang belum terjawab: kesalahan JENIS apa. Data
10 Okt 2026: ±58% kekalahan Sicilian/English melawan lawan kuat jatuh di
tengah permainan, jadi "lebih teliti" saja bukan saran yang bisa dilatih;
"kamu sering kena garpu kuda" bisa.

Motif dibaca dari data analisis yang SUDAH ada, tanpa engine dan tanpa tabel
baru:

  membiarkan   garis sanggahan lawan sesudah kesalahan = langkah terbaik +
               variasi_terbaik baris ply+1 (engine yang sama, posisi sesudah
               kesalahan). Ini menjawab "apa yang kubiarkan".
  melewatkan   variasi_terbaik baris kesalahan itu sendiri: taktik apa yang
               sebenarnya tersedia untukku. Dipakai kalau sanggahannya tidak
               taktis (mis. kesalahan yang melewatkan garpu sendiri).

Risiko yang diterima — ini heuristik, BUKAN engine:
  * Garis variasi pendek (biasanya 6 ply). Motif yang baru tampak lebih
    dalam tidak terlihat; kesalahannya jatuh ke "posisional" atau "taktik
    lain". Lebih baik salah ke arah "tidak tahu" daripada mengarang motif.
  * Motif material (garpu, pin, ...) hanya diberi label kalau garisnya
    sungguh memenangkan materi (>= 2 pion pada posisi yang tenang), supaya
    "kuda menyerang dua buah" yang tidak menghasilkan apa-apa tidak dihitung.
  * Satu kesalahan bisa punya beberapa motif; yang ditampilkan satu, menurut
    URUTAN_MOTIF. Daftar lengkapnya tetap dikirim.
"""

from __future__ import annotations

import re
import sqlite3

import chess
from fastapi import APIRouter, HTTPException, Query

from .db import koneksi

router = APIRouter(prefix="/api", tags=["pola"])

NILAI = {chess.PAWN: 1, chess.KNIGHT: 3, chess.BISHOP: 3,
         chess.ROOK: 5, chess.QUEEN: 9, chess.KING: 0}
UNTUNG_MIN = 2          # pion; di bawah ini garis dianggap tidak memenangkan materi
LANGKAH_PENYERANG = 3   # motif hanya dicari di tiga langkah pertama pihak penyerang
JENIS_KESALAHAN = ("blunder", "mistake")

# Urutan = prioritas label utama untuk motif di langkah yang SAMA (langkah
# yang lebih awal selalu menang, lihat motif_garis). Mat paling menentukan;
# buah tergantung sebelum garpu/tusuk sate karena memakan buah gratis yang
# kebetulan juga skak adalah pelajaran "buahku tak terjaga", bukan "skewer".
# "posisional" paling akhir karena artinya "tidak ada motif yang terbaca".
URUTAN_MOTIF = ("mat_baris_belakang", "mat", "buah_tergantung", "garpu", "tusuk_sate",
                "pin", "serangan_tersingkap", "promosi",
                "menang_materi", "posisional", "tanpa_data")
NAMA_MOTIF = {
    "mat_baris_belakang": "Mat baris belakang",
    "mat": "Mat / serangan raja",
    "garpu": "Garpu",
    "tusuk_sate": "Tusuk sate (skewer)",
    "pin": "Pin",
    "serangan_tersingkap": "Serangan tersingkap",
    "buah_tergantung": "Buah tergantung",
    "promosi": "Promosi",
    "menang_materi": "Taktik lain (materi hilang)",
    "posisional": "Posisional (tanpa kehilangan materi)",
    "tanpa_data": "Tanpa garis engine",
}
MOTIF_TAKTIS = set(URUTAN_MOTIF) - {"posisional", "tanpa_data"}

POLA_FEN = re.compile(r'\[FEN\s+"([^"]+)"\]')


# ---------------------------------------------------------------- dasar

def _materi(board: chess.Board, warna: bool) -> int:
    """Selisih materi dari sudut `warna` (raja tidak dihitung)."""
    s = 0
    for kotak, buah in board.piece_map().items():
        nilai = NILAI[buah.piece_type]
        s += nilai if buah.color == warna else -nilai
    return s


def _garis_ke_langkah(board: chess.Board, garis_san: list[str]) -> list[chess.Move]:
    """SAN -> Move dari posisi `board`; berhenti di SAN pertama yang tidak sah."""
    b = board.copy(stack=False)
    keluar = []
    for san in garis_san:
        try:
            m = b.parse_san(san)
        except ValueError:
            break
        keluar.append(m)
        b.push(m)
    return keluar


def _dijaga(board: chess.Board, kotak: int, warna: bool) -> bool:
    return board.is_attacked_by(warna, kotak)


def _sasaran_berharga(board: chess.Board, kotak: int, penyerang_nilai: int, bertahan: bool) -> bool:
    """Buah lawan di `kotak` layak disebut sasaran: raja, lebih mahal, atau tak terjaga."""
    buah = board.piece_at(kotak)
    if buah is None or buah.color != bertahan:
        return False
    if buah.piece_type == chess.KING:
        return True
    nilai = NILAI[buah.piece_type]
    if nilai > penyerang_nilai:
        return True
    return nilai >= 3 and not _dijaga(board, kotak, bertahan)


# ---------------------------------------------------------------- motif per langkah

def _garpu(b: chess.Board, ke: int, penyerang: bool) -> bool:
    buah = b.piece_at(ke)
    if buah is None:
        return False
    nilai = NILAI[buah.piece_type] or 100
    sasaran = [k for k in b.attacks(ke) if _sasaran_berharga(b, k, nilai, not penyerang)]
    return len(sasaran) >= 2


def _pin(sebelum: chess.Board, b: chess.Board, ke: int, penyerang: bool) -> bool:
    """Langkah ke `ke` memaku buah lawan (>= 3) ke rajanya; sebelumnya belum terpaku."""
    bertahan = not penyerang
    for kotak, buah in b.piece_map().items():
        if buah.color != bertahan or buah.piece_type == chess.KING or NILAI[buah.piece_type] < 3:
            continue
        if b.is_pinned(bertahan, kotak) and ke in b.pin(bertahan, kotak) \
                and not sebelum.is_pinned(bertahan, kotak):
            return True
    return False


def _tusuk_sate(b: chess.Board, ke: int, penyerang: bool) -> bool:
    """Buah jarak jauh menyerang raja/menteri lawan, dan di belakangnya ada buah >= 3."""
    buah = b.piece_at(ke)
    if buah is None or buah.piece_type not in (chess.BISHOP, chess.ROOK, chess.QUEEN):
        return False
    bertahan = not penyerang
    for depan in b.attacks(ke):
        target = b.piece_at(depan)
        if target is None or target.color != bertahan:
            continue
        if target.piece_type not in (chess.KING, chess.QUEEN) or \
                (target.piece_type == chess.QUEEN and buah.piece_type == chess.QUEEN):
            continue
        # Langkahi kotak-kotak di belakang `depan` pada garis yang sama.
        df = chess.square_file(depan) - chess.square_file(ke)
        dr = chess.square_rank(depan) - chess.square_rank(ke)
        langkah_f = (df > 0) - (df < 0)
        langkah_r = (dr > 0) - (dr < 0)
        f, r = chess.square_file(depan) + langkah_f, chess.square_rank(depan) + langkah_r
        while 0 <= f < 8 and 0 <= r < 8:
            belakang = b.piece_at(chess.square(f, r))
            if belakang is not None:
                if belakang.color == bertahan and NILAI[belakang.piece_type] >= 3:
                    return True
                break
            f, r = f + langkah_f, r + langkah_r
    return False


def _tersingkap(sebelum: chess.Board, b: chess.Board, m: chess.Move, penyerang: bool) -> bool:
    """Buah LAIN (bukan yang melangkah) kini menyerang raja/benteng/menteri lawan."""
    bertahan = not penyerang
    for kotak, buah in b.piece_map().items():
        if buah.color != penyerang or kotak == m.to_square or \
                buah.piece_type not in (chess.BISHOP, chess.ROOK, chess.QUEEN):
            continue
        baru = b.attacks(kotak) & ~sebelum.attacks(kotak)
        for k in baru:
            t = b.piece_at(k)
            if t is not None and t.color == bertahan and \
                    (t.piece_type == chess.KING or NILAI[t.piece_type] >= 5):
                return True
    return False


def _mat_baris_belakang(b: chess.Board, m: chess.Move) -> bool:
    buah = b.piece_at(m.to_square)
    raja = b.king(b.turn)
    if buah is None or raja is None or buah.piece_type not in (chess.ROOK, chess.QUEEN):
        return False
    baris_belakang = 0 if b.turn == chess.WHITE else 7
    return chess.square_rank(raja) == baris_belakang == chess.square_rank(m.to_square)


def _untung(b: chess.Board, penyerang: bool, awal: int, terakhir: chess.Move) -> int:
    """
    Materi yang dimenangkan penyerang sejauh ini. Kalau langkah terakhir
    adalah tangkapan penyerang dan buah penangkapnya masih bisa dimakan
    balik, anggap dimakan balik: garis engine sering terpotong di tengah
    pertukaran.
    """
    untung = _materi(b, penyerang) - awal
    if b.turn != penyerang and b.is_attacked_by(not penyerang, terakhir.to_square):
        penangkap = b.piece_at(terakhir.to_square)
        if penangkap is not None:
            untung -= NILAI[penangkap.piece_type]
    return untung


def _motif_langkah(sebelum: chess.Board, b: chess.Board, m: chess.Move, penyerang: bool) -> set[str]:
    """Motif material yang tampak pada satu langkah penyerang."""
    temuan = set()
    if _garpu(b, m.to_square, penyerang):
        temuan.add("garpu")
    if _pin(sebelum, b, m.to_square, penyerang):
        temuan.add("pin")
    if _tusuk_sate(b, m.to_square, penyerang):
        temuan.add("tusuk_sate")
    if _tersingkap(sebelum, b, m, penyerang):
        temuan.add("serangan_tersingkap")
    if m.promotion:
        temuan.add("promosi")
    tangkap = sebelum.piece_at(m.to_square)
    if tangkap is not None and tangkap.color != penyerang and NILAI[tangkap.piece_type] >= 3             and not _dijaga(sebelum, m.to_square, not penyerang):
        temuan.add("buah_tergantung")
    return temuan


def motif_garis(board: chess.Board, garis: list[chess.Move], ada_mat: bool = False) -> list[str]:
    """
    Motif yang dipakai pihak yang jalan di `board` sepanjang `garis`.

    `ada_mat`: engine melaporkan mat paksa untuk pihak ini walau garisnya
    terlalu pendek untuk sampai ke mat itu.

    Urutan hasil: mat dulu, lalu motif dari langkah penyerang yang PALING
    AWAL (seri -> URUTAN_MOTIF). Pencarian berhenti begitu materi sudah
    dimenangkan, supaya bentuk kebetulan sesudahnya (mis. pin di langkah
    ketiga sesudah benteng gratis dimakan di langkah kedua) tidak ikut.
    """
    if not garis:
        return ["mat"] if ada_mat else ["tanpa_data"]

    penyerang = board.turn
    b = board.copy(stack=False)
    awal = _materi(b, penyerang)
    mat: set[str] = set()
    per_langkah: list[set[str]] = []
    selesai = False

    for m in garis:
        giliran_penyerang = b.turn == penyerang
        sebelum = b.copy(stack=False)
        b.push(m)
        if b.is_checkmate():
            mat.add("mat_baris_belakang" if _mat_baris_belakang(b, m) else "mat")
        if giliran_penyerang and not selesai and len(per_langkah) < LANGKAH_PENYERANG:
            per_langkah.append(_motif_langkah(sebelum, b, m, penyerang))
            selesai = _untung(b, penyerang, awal, m) >= UNTUNG_MIN

    if ada_mat and not mat:
        mat.add("mat")
    urut = [k for k in URUTAN_MOTIF if k in mat]
    if _untung(b, penyerang, awal, garis[-1]) >= UNTUNG_MIN:
        for temuan in per_langkah:
            urut += [k for k in URUTAN_MOTIF if k in temuan and k not in urut]
        if len(urut) == len([k for k in urut if k in mat]):
            urut.append("menang_materi")
    # Motif material tanpa hasil material bukan motif, hanya kebetulan bentuk.
    return urut or ["posisional"]


# ---------------------------------------------------------------- per partai

def fen_awal(pgn: str | None) -> str | None:
    """FEN dari tag [FEN] kalau partai tidak dimulai dari posisi awal."""
    if not pgn:
        return None
    m = POLA_FEN.search(pgn)
    return m.group(1) if m else None


def _mat_untuk_lawan(mat_sesudah: int | None, warna: str) -> bool:
    """mat_sesudah dari sudut Putih: positif = Putih yang memberi mat."""
    if not mat_sesudah:
        return False
    return (mat_sesudah < 0) if warna == "putih" else (mat_sesudah > 0)


def _mat_untuk_saya(mat_sebelum: int | None, warna: str) -> bool:
    if not mat_sebelum:
        return False
    return (mat_sebelum > 0) if warna == "putih" else (mat_sebelum < 0)


def _sebagai_list(variasi) -> list[str]:
    if not variasi:
        return []
    return variasi.split() if isinstance(variasi, str) else list(variasi)


def motif_kesalahan(sebelum: chess.Board, langkah: dict, berikut: dict | None,
                    mat_sebelum: int | None = None) -> dict:
    """
    Motif satu kesalahan. `langkah` = baris analisis kesalahan itu, `berikut`
    = baris ply+1 (sanggahan lawan) atau None kalau partainya selesai.
    """
    sesudah = sebelum.copy(stack=False)
    sesudah.push(chess.Move.from_uci(langkah["uci"]))

    if berikut is not None:
        garis = _sebagai_list(berikut.get("variasi_terbaik"))
        if not garis and berikut.get("langkah_terbaik_san"):
            garis = [berikut["langkah_terbaik_san"]]
        membiarkan = motif_garis(sesudah, _garis_ke_langkah(sesudah, garis),
                                 _mat_untuk_lawan(langkah.get("mat_sesudah"), langkah["warna"]))
    elif sesudah.is_game_over():
        membiarkan = ["posisional"]
    else:
        membiarkan = motif_garis(sesudah, [], _mat_untuk_lawan(langkah.get("mat_sesudah"),
                                                               langkah["warna"]))

    melewatkan = motif_garis(sebelum, _garis_ke_langkah(sebelum, _sebagai_list(langkah.get("variasi_terbaik"))),
                             _mat_untuk_saya(mat_sebelum, langkah["warna"]))

    utama, sisi = membiarkan[0], "membiarkan"
    if utama not in MOTIF_TAKTIS and melewatkan[0] in MOTIF_TAKTIS:
        utama, sisi = melewatkan[0], "melewatkan"
    return {"utama": utama, "nama": NAMA_MOTIF[utama], "sisi": sisi,
            "membiarkan": membiarkan, "melewatkan": melewatkan}


def motif_partai(langkah: list[dict], fen: str | None = None) -> dict[int, dict]:
    """
    {ply: motif} untuk tiap blunder/mistake dalam satu partai.

    `langkah` = baris analisis_langkah urut ply (bentuk dict, variasi boleh
    string atau list). Papan disusun ulang dari kolom `uci`, jadi PGN tidak
    perlu di-parse. Partai yang langkahnya tidak bisa diputar ulang
    (data rusak) menghasilkan {} alih-alih menebak.
    """
    try:
        board = chess.Board(fen) if fen else chess.Board()
    except ValueError:
        return {}
    keluar: dict[int, dict] = {}
    mat_sebelum = None
    for i, l in enumerate(langkah):
        if l["klasifikasi"] in JENIS_KESALAHAN:
            berikut = langkah[i + 1] if i + 1 < len(langkah) and langkah[i + 1]["ply"] == l["ply"] + 1 else None
            try:
                keluar[l["ply"]] = motif_kesalahan(board, l, berikut, mat_sebelum)
            except (ValueError, AssertionError):
                pass
        try:
            m = chess.Move.from_uci(l["uci"])
        except ValueError:
            return keluar
        if not board.is_legal(m):
            return keluar
        board.push(m)
        mat_sebelum = l.get("mat_sesudah")
    return keluar


def tandai_motif(hasil: dict, pgn: str | None) -> dict:
    """Tambahkan field `motif` ke blunder/mistake di hasil `ambil_analisis()`."""
    peta = motif_partai(hasil.get("langkah", []), fen_awal(pgn))
    for l in hasil.get("langkah", []):
        if l["ply"] in peta:
            l["motif"] = peta[l["ply"]]
    return hasil


# ---------------------------------------------------------------- ringkasan lintas partai

BATAS_PARTAI = (1, 500)
CONTOH_PER_MOTIF = 6
JENIS_VALID = ("semua", "blunder", "mistake")


def _baris_langkah(conn: sqlite3.Connection, ids: list[int]) -> dict[int, list[dict]]:
    """Baris analisis yang dibutuhkan motif_partai, per partai, urut ply."""
    from .sebab_kalah import POTONGAN_ID
    per_partai: dict[int, list[dict]] = {}
    for i in range(0, len(ids), POTONGAN_ID):
        potong = ids[i:i + POTONGAN_ID]
        q = f"""SELECT partai_id, ply, nomor_langkah, warna, san, uci, klasifikasi,
                       kehilangan_cp, mat_sesudah, langkah_terbaik_san, variasi_terbaik
                FROM analisis_langkah WHERE partai_id IN ({",".join("?" * len(potong))})
                ORDER BY partai_id, ply"""
        for l in conn.execute(q, potong):
            per_partai.setdefault(l["partai_id"], []).append(dict(l))
    return per_partai


def pola_kesalahan(conn: sqlite3.Connection, pemain_ids: list[int], format_: str | None = None,
                   lawan: str = "ketentuan", batas: int = 120, jenis: str = "semua") -> dict:
    """
    Motif blunder/mistake akun-akun ini pada `batas` partai teranalisis terbaru.

    Saringan partai sama dengan halaman Kelemahan (akun, lawan, format,
    jumlah partai terbaru), dan saringan lawan memakai SQL yang sama dengan
    Kekalahan supaya berlaku sebelum LIMIT.
    """
    from .sebab_kalah import _syarat_lawan, fase_ply

    kosong = {"total_partai": 0, "total_kesalahan": 0, "motif": [],
              "lawan": lawan, "format": format_ or "semua", "batas": batas, "jenis": jenis}
    if not pemain_ids:
        return kosong

    tanda = ",".join("?" * len(pemain_ids))
    params: list = list(pemain_ids) * 4
    sql = f"""
        SELECT g.id, g.tanggal, g.pgn,
               CASE WHEN g.putih_id IN ({tanda}) THEN 'putih' ELSE 'hitam' END AS warna_saya,
               CASE WHEN g.putih_id IN ({tanda}) THEN pb.nama ELSE pw.nama END AS lawan
        FROM partai g
        JOIN analisis a ON a.partai_id = g.id
        JOIN pemain pw ON pw.id = g.putih_id
        JOIN pemain pb ON pb.id = g.hitam_id
        WHERE (g.putih_id IN ({tanda}) OR g.hitam_id IN ({tanda}))
    """
    if format_ and format_ != "semua":
        sql += " AND g.format = ?"
        params.append(format_)
    syarat_lawan = _syarat_lawan(lawan, tanda)
    if syarat_lawan:
        sql += f" AND {syarat_lawan}"
        params += list(pemain_ids) * syarat_lawan.count(f"IN ({tanda})")
    sql += " ORDER BY g.tanggal DESC, g.id DESC LIMIT ?"
    params.append(max(BATAS_PARTAI[0], min(int(batas), BATAS_PARTAI[1])))
    partai = conn.execute(sql, params).fetchall()

    per_partai = _baris_langkah(conn, [r["id"] for r in partai])
    jenis_dipakai = JENIS_KESALAHAN if jenis == "semua" else (jenis,)

    hitung: dict[str, dict] = {}
    total = 0
    for r in partai:
        langkah = per_partai.get(r["id"], [])
        warna = r["warna_saya"]
        motif = motif_partai(langkah, fen_awal(r["pgn"]))
        for l in langkah:
            if l["warna"] != warna or l["klasifikasi"] not in jenis_dipakai or l["ply"] not in motif:
                continue
            m = motif[l["ply"]]
            total += 1
            k = hitung.setdefault(m["utama"], {
                "kunci": m["utama"], "nama": NAMA_MOTIF[m["utama"]], "jumlah": 0,
                "blunder": 0, "mistake": 0, "membiarkan": 0, "melewatkan": 0,
                "fase": {"pembukaan": 0, "tengah": 0, "akhir": 0}, "contoh": []})
            k["jumlah"] += 1
            k[l["klasifikasi"]] += 1
            k[m["sisi"]] += 1
            k["fase"][fase_ply(l["ply"])] += 1
            if len(k["contoh"]) < CONTOH_PER_MOTIF:
                k["contoh"].append({
                    "partai_id": r["id"], "tanggal": r["tanggal"], "lawan": r["lawan"],
                    "ply": l["ply"], "nomor_langkah": l["nomor_langkah"], "warna": warna,
                    "san": l["san"], "terbaik_san": l["langkah_terbaik_san"],
                    "klasifikasi": l["klasifikasi"], "kehilangan_cp": l["kehilangan_cp"],
                    "sisi": m["sisi"]})

    keluar = list(hitung.values())
    # Motif taktis dulu, terbanyak di atas; posisional/tanpa_data selalu di bawah
    # supaya angka besar "tidak terbaca" tidak menutupi pola yang bisa dilatih.
    keluar.sort(key=lambda k: (k["kunci"] not in MOTIF_TAKTIS, -k["jumlah"]))
    for k in keluar:
        k["persen"] = round(100 * k["jumlah"] / total, 1) if total else 0.0
    return {**kosong, "total_partai": len(partai), "total_kesalahan": total, "motif": keluar}


@router.get("/pola", summary="Motif taktik di balik blunder/mistake sendiri (garpu, pin, buah tergantung, ...)")
def api_pola(pemain_id: list[int] = Query(..., description="id akun sendiri, boleh beberapa"),
             format: str | None = None,
             lawan: str = "ketentuan",
             batas: int = Query(120, ge=BATAS_PARTAI[0], le=BATAS_PARTAI[1]),
             jenis: str = "semua"):
    from .sebab_kalah import LAWAN_VALID
    if lawan not in LAWAN_VALID:
        raise HTTPException(status_code=422,
                            detail=f"lawan harus salah satu dari: {', '.join(LAWAN_VALID)}")
    if jenis not in JENIS_VALID:
        raise HTTPException(status_code=422,
                            detail=f"jenis harus salah satu dari: {', '.join(JENIS_VALID)}")
    conn = koneksi()
    try:
        return pola_kesalahan(conn, pemain_id, format, lawan, batas, jenis)
    finally:
        conn.close()
