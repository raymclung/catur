"""
Ekspor repertoire jadi berkas PGN (A41).

Gunanya: garis yang di halaman Repertoire cuma bisa dilihat di laptop, sekarang
bisa diunduh dan dibuka di Lichess Study atau aplikasi catur di HP — jadi
pembukaan bisa dihafal tanpa membuka aplikasi ini.

Isinya BUKAN repertoire ideal menurut teori, melainkan yang BENAR-BENAR
dimainkan: tiap langkah diambil dari partai sendiri (tabel `posisi`), dan tiap
langkah sendiri diberi catatan berapa kali dimainkan, skornya, serta langkah
utama GM di posisi yang sama. Dengan begitu, yang perlu diperbaiki kelihatan
sewaktu garisnya dibaca ulang.

Bentuk keluaran: satu partai PGN dengan variasi bersarang (kurung), seperti
berkas repertoire pada umumnya.
"""

from __future__ import annotations

import io
import sqlite3

import chess
import chess.pgn
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import PlainTextResponse

from .db import koneksi
from .posisi import (agregasi_langkah, baris_posisi_partai, cari_langkah, fen_kunci,
                     langkah_dari_peta, peta_partai, peta_partai_gm)

router = APIRouter(prefix="/api", tags=["repertoire"])

MAKS_PLY = 24          # 12 langkah penuh; lebih dalam PGN-nya jadi sulit dibaca
MAKS_SIMPUL = 400      # penjaga supaya pohonnya tidak meledak


def _skor_pemain(l: dict, warna: str) -> float | None:
    """Skor langkah dari sudut pemain sendiri (cari_langkah memberi sudut Putih)."""
    selesai = l["menang_putih"] + l["remis"] + l["menang_hitam"]
    if not selesai:
        return None
    poin_putih = l["menang_putih"] + l["remis"] / 2
    poin = poin_putih if warna == "putih" else selesai - poin_putih
    return round(100 * poin / selesai, 1)


def langkah_sendiri(conn: sqlite3.Connection, papan: chess.Board,
                    pemain_ids: list[int], warna: str, simpanan: dict | None = None,
                    peta: dict | None = None) -> list[dict]:
    """Langkah yang muncul di posisi ini, dari partai pemain sendiri saja.

    Dipakai untuk giliran sendiri MAUPUN giliran lawan: di giliran lawan, yang
    terkumpul adalah jawaban yang benar-benar dihadapi, bukan teori.

    `simpanan` menyimpan hasil per posisi: transposisi membuat posisi yang sama
    ditanyakan berkali-kali (terukur: 41 detik tanpa simpanan)."""
    kunci = fen_kunci(papan)
    if simpanan is not None and kunci in simpanan:
        return simpanan[kunci]
    gabung: dict[str, dict] = {}
    for pid in pemain_ids:
        # peta (lihat bangun_pohon): partai pemain ini sudah disaring sekali,
        # hasilnya sama persis dengan cari_langkah (app/posisi.py).
        daftar = (agregasi_langkah(peta[pid][1].get(kunci, ()), peta[pid][0]) if peta is not None else
                  cari_langkah(conn, kunci, sumber="semua", pemain_id=pid, warna=warna,
                               jumlah_contoh=0)["langkah"])
        for l in daftar:
            g = gabung.setdefault(l["uci"], {"uci": l["uci"], "san": l["san"], "n": 0,
                                             "menang_putih": 0, "remis": 0, "menang_hitam": 0})
            for k in ("n", "menang_putih", "remis", "menang_hitam"):
                g[k] += l[k] or 0
    hasil = sorted(gabung.values(), key=lambda l: -l["n"])
    if simpanan is not None:
        simpanan[kunci] = hasil
    return hasil


def _catatan(conn: sqlite3.Connection, papan: chess.Board, l: dict, warna: str,
             simpanan_gm: dict | None = None, peta_gm=None) -> str:
    """Catatan untuk satu langkah sendiri: pemakaian, skor, dan pembanding GM."""
    bagian = [f"kamu {l['n']}x"]
    skor = _skor_pemain(l, warna)
    if skor is not None:
        bagian.append(f"skor {skor}%".replace(".", ","))
    kunci = fen_kunci(papan)
    if simpanan_gm is not None and kunci in simpanan_gm:
        gm = simpanan_gm[kunci]
    else:
        if peta_gm is not None:
            langkah = langkah_dari_peta(conn, kunci, peta_gm())
            gm = {"total": sum(x["n"] for x in langkah), "langkah": langkah}
        else:
            gm = cari_langkah(conn, kunci, sumber="gm", jumlah_contoh=0)
        if simpanan_gm is not None:
            simpanan_gm[kunci] = gm
    total_gm = gm["total"]
    if total_gm >= 20:
        n_gm = next((x["n"] for x in gm["langkah"] if x["uci"] == l["uci"]), 0)
        porsi = n_gm / total_gm
        utama = max(gm["langkah"], key=lambda x: x["n"])
        if not n_gm:
            bagian.append(f"tidak pernah dimainkan GM (dari {total_gm} partai)")
        elif porsi >= 0.05:
            bagian.append(f"GM {round(100 * porsi)}%")
        else:
            bagian.append(f"jarang di GM ({round(100 * porsi, 1)}%)".replace(".", ","))
        if utama["uci"] != l["uci"]:
            bagian.append(f"GM paling sering {utama['san']} "
                          f"({round(100 * utama['n'] / total_gm)}%)")
    return "; ".join(bagian)


def bangun_pohon(conn: sqlite3.Connection, pemain_ids: list[int], warna: str,
                 kedalaman: int = 8, cabang: int = 2, minimal: int = 10) -> chess.pgn.Game:
    game = chess.pgn.Game()
    jumlah = [0]
    simpanan: dict = {}
    simpanan_gm: dict = {}
    # Partai disaring SEKALI per unduhan, bukan di tiap posisi: dulu 118 posisi
    # x JOIN partai = 7 detik per warna, 25 detik untuk satu unduhan (diukur
    # 27-29 Sep 2026). Peta GM baru dibuat kalau memang ada catatan yang perlu.
    kolom = "g.putih_id = ?" if warna == "putih" else "g.hitam_id = ?"
    peta = {}
    for pid in pemain_ids:
        milik = peta_partai(conn, kolom, [pid])
        peta[pid] = (milik, baris_posisi_partai(conn, milik))
    _gm: list = []

    def peta_gm():
        if not _gm:
            _gm.append(peta_partai_gm(conn))
        return _gm[0]

    def tumbuh(simpul, papan: chess.Board, ply: int) -> None:
        if ply >= kedalaman or jumlah[0] >= MAKS_SIMPUL:
            return
        giliran_sendiri = (papan.turn == chess.WHITE) == (warna == "putih")
        daftar = [l for l in langkah_sendiri(conn, papan, pemain_ids, warna, simpanan, peta)
                  if l["n"] >= minimal]
        if not daftar:
            return
        # Giliran sendiri: cabang yang ditelusuri sebanyak `cabang`. Giliran lawan
        # juga dibatasi `cabang`, kalau tidak PGN-nya penuh jawaban langka.
        for l in daftar[:cabang]:
            langkah = chess.Move.from_uci(l["uci"])
            if langkah not in papan.legal_moves:
                continue                     # posisi transposisi yang tidak cocok
            anak = simpul.add_variation(langkah)
            jumlah[0] += 1
            if giliran_sendiri:
                anak.comment = _catatan(conn, papan, l, warna, simpanan_gm, peta_gm)
            papan.push(langkah)
            tumbuh(anak, papan, ply + 1)
            papan.pop()

    tumbuh(game, chess.Board(), 0)
    return game


def nama_tag(nama: str) -> str:
    """Nilai aman untuk tag PGN: tanpa kutip ganda, garis miring terbalik, atau baris baru."""
    bersih = nama.replace('"', "'").replace("\\", "/")
    return " ".join(bersih.split()) or "Saya"


def pgn_repertoire(conn: sqlite3.Connection, pemain_ids: list[int], warna: str,
                   kedalaman: int = 8, cabang: int = 2, minimal: int = 10,
                   nama: str = "Saya") -> str:
    game = bangun_pohon(conn, pemain_ids, warna, kedalaman, cabang, minimal)
    # python-chess menulis nilai tag apa adanya: nama `a"b` jadi
    # [White "a"b"], tag rusak yang ditolak Lichess (audit 27 Sep 2026).
    nama = nama_tag(nama)
    game.headers["Event"] = f"Repertoire {nama} sebagai {warna.capitalize()}"
    game.headers["Site"] = "McLung ChessBase"
    game.headers["White"] = nama if warna == "putih" else "Lawan"
    game.headers["Black"] = "Lawan" if warna == "putih" else nama
    game.headers["Result"] = "*"
    game.headers["Annotator"] = "McLung ChessBase"
    for k in ("Date", "Round"):
        game.headers[k] = "?"
    game.comment = (f"Garis yang benar-benar dimainkan: minimal {minimal} partai per cabang, "
                    f"{cabang} cabang terbanyak, sampai {kedalaman // 2} langkah. "
                    "Catatan tiap langkah sendiri: berapa kali dipakai, skornya, "
                    "dan langkah utama GM di posisi yang sama.")
    keluar = io.StringIO()
    print(game, file=keluar, end="\n\n")
    return keluar.getvalue()


def header_unduhan(berkas: str) -> str:
    """
    Content-Disposition yang aman untuk nama apa pun (RFC 6266 + 5987).

    Header HTTP harus latin-1: nama dengan apostrof HP (’) atau huruf non-latin
    dulu membuat endpoint ini 500 (audit 27 Sep 2026), dan tanda kutip di nama
    merusak header. `filename` = cadangan ASCII, `filename*` = nama aslinya.
    """
    import re                                           # noqa: PLC0415
    from urllib.parse import quote                      # noqa: PLC0415
    cadangan = re.sub(r"[^A-Za-z0-9._-]", "_", berkas)
    return f"attachment; filename=\"{cadangan}\"; filename*=UTF-8''{quote(berkas, safe='')}"


@router.get("/repertoire/pgn", summary="Unduh repertoire yang dimainkan sebagai berkas PGN")
def api_repertoire_pgn(
    pemain_id: list[int] = Query(..., description="id akun sendiri, boleh beberapa"),
    warna: str = Query("putih", pattern="^(putih|hitam)$"),
    kedalaman: int = Query(8, ge=2, le=MAKS_PLY, description="ply; 8 = 4 langkah penuh"),
    cabang: int = Query(2, ge=1, le=4),
    minimal: int = Query(10, ge=1, le=1000, description="cabang dengan partai lebih sedikit dilewati"),
    nama: str = Query("Saya", max_length=60),
):
    if len(pemain_id) > 20:
        raise HTTPException(status_code=422, detail="Maksimal 20 akun sekaligus")
    conn = koneksi()
    try:
        # Pemain yang tidak ada dulu dijawab 200 dengan PGN kosong, seolah
        # repertoirenya memang kosong (audit 27 Sep 2026).
        ada = {r[0] for r in conn.execute(
            f"SELECT id FROM pemain WHERE id IN ({','.join('?' * len(pemain_id))})", pemain_id)}
        hilang = sorted(set(pemain_id) - ada)
        if hilang:
            raise HTTPException(status_code=404,
                                detail=f"Pemain tidak ada: {', '.join(map(str, hilang))}")
        teks = pgn_repertoire(conn, pemain_id, warna, kedalaman, cabang, minimal, nama)
    finally:
        conn.close()
    berkas = f"repertoire-{nama}-{warna}.pgn".replace(" ", "_")
    return PlainTextResponse(teks, media_type="application/x-chess-pgn",
                             headers={"Content-Disposition": header_unduhan(berkas)})
