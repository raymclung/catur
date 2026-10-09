"""
Impor partai GM dari TWIC (The Week in Chess) ke database.

TWIC: https://theweekinchess.com/twic — terbit mingguan sejak 1994, memuat
partai OTB dari turnamen elite dunia, dengan FIDE ID pada ~99,8% partai.
Syaratnya: "free for personal use only, all rights reserved". Karena itu:

  - zip disimpan di data/twic/, folder yang TIDAK masuk git;
  - unduhan berurutan dengan jeda, dan file yang sudah ada tidak diunduh ulang;
  - tidak ada penyebaran ulang.

Satu edisi berisi ~7.700 partai. Menyimpan semuanya tidak ada gunanya, jadi
hanya partai yang melibatkan pemain sasaran yang masuk: FIDE ID di
data/gm_target.txt (default), atau --elo-min N (salah satu pemain).

Cakupan arsip: zip hanya tersedia dari edisi 920 (Juni 2012); edisi 1-919
(1994-2012) tidak bisa diunduh satuan, hanya lewat donasi ke TWIC.

Catatan penting: backfill memakai saringan LAIN — kedua pemain Elo >= 2500,
online dibuang — lewat `perbarui.py --hanya twic --edisi 920-1505`, yang juga
mencatat ke tabel sumber_impor. Skrip ini tidak menulis sumber_impor;
pengulangan mengandalkan cache zip dan dedup hash. Lihat ARSITEKTUR.md (Phase 3).

Contoh:
    python impor_twic.py 1661                    # satu edisi
    python impor_twic.py 920-1661                # seluruh arsip yang tersedia
    python impor_twic.py 1661 --elo-min 2700     # semua partai dengan pemain 2700+
    python impor_twic.py 1661 --tanpa-online     # buang Titled Tuesday dkk.
    python impor_twic.py 1661 --coba             # hitung saja, jangan simpan
"""

from __future__ import annotations

import argparse
import io
import sys
import time
import urllib.request
import zipfile
from collections import Counter
from pathlib import Path

import chess.pgn

from app.db import PATH_DB, buat_skema, info, koneksi
from app.impor import impor_satu, ke_elo, perbarui_elo_terakhir
from app.kunci_penulis import KunciPenulis, KunciTerpakai, penulis_berjalan

AKAR = Path(__file__).resolve().parent
FOLDER_TWIC = AKAR / "data" / "twic"
FILE_TARGET = AKAR / "data" / "gm_target.txt"
URL_ZIP = "https://theweekinchess.com/zips/twic{n}g.zip"
URL_EDISI = "https://theweekinchess.com/html/twic{n}.html"
JEDA_DETIK = 3          # sopan ke server yang hidup dari donasi
SUMBER = "twic"

# Situs yang menandakan partai online di TWIC (mis. Titled Tuesday:
# 'chess.com INT'). Partai OTB memakai nama kota: 'Bengaluru IND'.
PENANDA_ONLINE = ("chess.com", "lichess", "chess24", "online", "internet", " int")


# --------------------------------------------------------------------------
# Daftar sasaran
# --------------------------------------------------------------------------


def baca_target(path: Path) -> dict[str, str]:
    """
    Format tiap baris: <fide_id>  <nama bebas>   # komentar
    Baris kosong dan baris berawalan # diabaikan.
    """
    target = {}
    if not path.is_file():
        return target
    for baris in path.read_text(encoding="utf-8").splitlines():
        isi = baris.split("#", 1)[0].strip()
        if not isi:
            continue
        bagian = isi.split(None, 1)
        target[bagian[0]] = bagian[1] if len(bagian) > 1 else ""
    return target


# --------------------------------------------------------------------------
# Unduh dan baca
# --------------------------------------------------------------------------


def rentang(teks: str) -> list[int]:
    """'1661' -> [1661]; '1505-1661' -> [1505, ..., 1661]."""
    if "-" in teks:
        a, b = teks.split("-", 1)
        return list(range(int(a), int(b) + 1))
    return [int(teks)]


def unduh(n: int) -> Path | None:
    """Kembalikan path zip edisi n, unduh kalau belum ada. None kalau gagal."""
    FOLDER_TWIC.mkdir(parents=True, exist_ok=True)
    tujuan = FOLDER_TWIC / f"twic{n}g.zip"
    if tujuan.is_file() and tujuan.stat().st_size > 0:
        return tujuan

    req = urllib.request.Request(
        URL_ZIP.format(n=n),
        headers={"User-Agent": "Mozilla/5.0 (proyek belajar catur pribadi; impor_twic.py)"},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = resp.read()
    except Exception as e:
        print(f"    gagal mengunduh edisi {n}: {e}", file=sys.stderr)
        return None

    sementara = tujuan.with_suffix(".part")
    sementara.write_bytes(data)
    sementara.replace(tujuan)       # baru diganti nama setelah utuh
    time.sleep(JEDA_DETIK)
    return tujuan


def baca_pgn(zip_path: Path) -> str:
    with zipfile.ZipFile(zip_path) as z:
        nama = next(n for n in z.namelist() if n.lower().endswith(".pgn"))
        return z.read(nama).decode("utf-8", errors="replace")


# --------------------------------------------------------------------------
# Saringan
# --------------------------------------------------------------------------


def online(h) -> bool:
    """TWIC menandai partai online lewat Site: 'chess.com INT', 'lichess.org INT'."""
    site = str(h.get("Site", "")).lower()
    return any(k in site for k in PENANDA_ONLINE)


def cocok(h, target: dict[str, str], elo_min: int) -> bool:
    """Partai diambil kalau salah satu pemain ada di daftar sasaran, atau
    (kalau elo_min diberikan) salah satu pemain ber-Elo >= elo_min.
    Tanpa Elo -> tidak ditebak."""
    if target:
        if h.get("WhiteFideId") in target or h.get("BlackFideId") in target:
            return True
    if elo_min:
        for w in ("WhiteElo", "BlackElo"):
            e = ke_elo(h.get(w))
            if e is not None and e >= elo_min:
                return True
    return False


# --------------------------------------------------------------------------
# Utama
# --------------------------------------------------------------------------


def main() -> int:
    """Impor = penulis besar: ambil kunci dulu (CLAUDE.md bagian 7). --coba
    tidak menulis, jadi boleh jalan walau ada penulis lain."""
    if "--coba" in sys.argv[1:]:
        return _impor()
    lain = penulis_berjalan()
    if lain:
        print("Penulis besar lain sedang berjalan, impor tidak dijalankan:", file=sys.stderr)
        for b in lain:
            print("  ..." + b, file=sys.stderr)
        return 1
    try:
        with KunciPenulis("impor_twic.py " + " ".join(sys.argv[1:])[:80]):
            return _impor()
    except KunciTerpakai as e:
        print(e, file=sys.stderr)
        return 1


def _impor() -> int:
    p = argparse.ArgumentParser(description="Impor partai GM dari TWIC")
    p.add_argument("edisi", help="nomor edisi, mis. 1661 atau 1505-1661")
    p.add_argument("--db", type=Path, default=PATH_DB)
    p.add_argument("--target", type=Path, default=FILE_TARGET,
                   help="file daftar FIDE ID sasaran")
    p.add_argument("--elo-min", type=int, default=0,
                   help="ikutkan juga partai dengan salah satu pemain ber-Elo >= N")
    p.add_argument("--tanpa-online", action="store_true",
                   help="lewati partai online (Titled Tuesday, dsb.)")
    p.add_argument("--coba", action="store_true",
                   help="hitung saja, jangan simpan ke database")
    args = p.parse_args()

    target = baca_target(args.target)
    if not target and not args.elo_min:
        print(f"Tidak ada saringan: {args.target} kosong/tidak ada dan --elo-min "
              f"tidak diberikan. Menolak mengimpor semuanya.", file=sys.stderr)
        return 1

    print(f"Database : {args.db}{'  (--coba: tidak menyimpan)' if args.coba else ''}")
    print(f"Sasaran  : {len(target)} FIDE ID dari {args.target.name}"
          + (f", plus Elo >= {args.elo_min}" if args.elo_min else ""))
    print(f"Online   : {'dilewati' if args.tanpa_online else 'ikut'}")
    print()

    conn = koneksi(args.db)
    buat_skema(conn)
    sebelum = info(conn)

    total = Counter()
    terlihat: dict[str, Counter] = {}     # fide_id -> nama-nama yang muncul
    mulai = time.time()

    try:
        for n in rentang(args.edisi):
            zp = unduh(n)
            if zp is None:
                total["edisi gagal"] += 1
                continue

            fh = io.StringIO(baca_pgn(zp))
            edisi = Counter()
            while True:
                try:
                    game = chess.pgn.read_game(fh)
                except Exception:
                    edisi["rusak"] += 1
                    continue
                if game is None:
                    break
                h = game.headers
                edisi["dibaca"] += 1
                # Langkah ilegal: python-chess memotong sisa partai tanpa
                # galat. Sama dengan app/impor.py: jangan simpan seolah utuh.
                if game.errors:
                    edisi["rusak"] += 1
                    continue

                if not cocok(h, target, args.elo_min):
                    continue
                if args.tanpa_online and online(h):
                    edisi["online dilewati"] += 1
                    continue

                for w in ("White", "Black"):
                    fid = h.get(w + "FideId")
                    if fid in target:
                        terlihat.setdefault(fid, Counter())[h.get(w, "?")] += 1

                edisi["cocok"] += 1
                if args.coba:
                    continue

                status = impor_satu(conn, game, SUMBER, URL_EDISI.format(n=n))
                kunci = status.split(":", 1)[0]
                edisi[kunci] += 1
                if edisi["cocok"] % 200 == 0:
                    conn.commit()

            conn.commit()
            total.update(edisi)
            print(f"  twic{n}: {edisi['dibaca']:5} dibaca, {edisi['cocok']:4} cocok"
                  + (f" -> {edisi['baru']} baru, {edisi['duplikat']} duplikat, "
                     f"{edisi['diperbarui']} diperbarui, {edisi['lewati']} dilewati"
                     if not args.coba else "")
                  + (f", {edisi['online dilewati']} online dilewati"
                     if edisi["online dilewati"] else ""))
    finally:
        conn.commit()
        if total["baru"] and not args.coba:
            perbarui_elo_terakhir(conn)
        sesudah = info(conn)
        conn.close()

    print()
    print("=" * 64)
    print(f"  Dibaca   : {total['dibaca']} partai dalam {time.time() - mulai:.0f} detik")
    print(f"  Cocok    : {total['cocok']}")
    if not args.coba:
        print(f"  Baru     : {total['baru']}")
        print(f"  Duplikat : {total['duplikat']}")
        print(f"  Diperbarui: {total['diperbarui']}")
        print(f"  Dilewati : {total['lewati']}")
        print(f"  Database : {sesudah['partai']} partai "
              f"(+{sesudah['partai'] - sebelum['partai']}), "
              f"{sesudah['pemain']} pemain, {sesudah['turnamen']} turnamen")
    if total["edisi gagal"]:
        print(f"  Edisi gagal diunduh: {total['edisi gagal']}")

    # Bukti bahwa FIDE ID di daftar sasaran memang orang yang dimaksud.
    # ID yang salah akan memunculkan nama yang tidak dikenal, atau tidak
    # muncul sama sekali.
    if target:
        print()
        print("  Sasaran yang muncul di data:")
        for fid, nama in target.items():
            if fid in terlihat:
                varian = ", ".join(f"{nm} ({c})" for nm, c in terlihat[fid].most_common(3))
                print(f"    {fid:>10}  {nama:24} -> {varian}")
            else:
                print(f"    {fid:>10}  {nama:24} -> tidak muncul di edisi ini")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
