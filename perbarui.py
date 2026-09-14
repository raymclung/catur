"""
Pembaruan mingguan dalam satu perintah.

Urutan tahap (masing-masing bisa dilewati dengan --hanya):
  1. cadangan   cadangkan_db.py, supaya ada jalan mundur sebelum menulis
  2. twic       edisi TWIC yang belum tercatat di sumber_impor, dari edisi
                terakhir + 1 sampai perkiraan edisi terbaru (terbit tiap Senin);
                edisi yang belum terbit (404) menghentikan tahap ini tanpa error
  3. broadcast  impor_broadcast.py --resmi --tier N (turnamen resmi Lichess)
  4. indeks     indeks_posisi.py untuk partai baru (partai lama dilewati sendiri)

Saringan TWIC sama untuk backfill maupun mingguan: kedua pemain Elo >= 2500,
partai online (Site "... INT") dibuang. Lihat SESI.md bagian 6.

Cakupan: SELURUH arsip TWIC yang tersedia, bukan 3 tahun (keputusan Ray,
11 Sep 2026). Zip hanya ada dari edisi 920 (Juni 2012) ke atas; edisi 1-919
tidak bisa diunduh satuan.

Contoh:
    python perbarui.py                       # semua tahap
    python perbarui.py --coba                # hitung saja, tidak menulis database
    python perbarui.py --hanya twic --hanya indeks
    python perbarui.py --hanya twic --edisi 920-1505   # backfill arsip lama
    python perbarui.py --hanya twic --edisi 920-1505 --unduh-saja
    python perbarui.py --min-elo 2600 --tier 4

Satu penulis besar pada satu waktu (CLAUDE.md bagian 7) ditegakkan berkas
kunci: perintah kedua yang menulis database ditolak dengan pesan yang menyebut
siapa pemegang kuncinya, bukan mati di tengah jalan. `--coba` tidak mengambil
kunci karena hanya membaca.
"""

from __future__ import annotations

import argparse
import io
import subprocess
import sys
import time
from collections import Counter
from datetime import date, datetime
from pathlib import Path

import chess.pgn

from app.db import PATH_DB, buat_skema, info, koneksi
from app.impor import impor_satu, ke_int, perbarui_elo_terakhir
from app.kunci_penulis import KunciPenulis, KunciTerpakai, lingkungan_anak
from app.sumber_impor import catat, sudah_selesai
from cadangkan_db import cadangkan
from impor_twic import URL_EDISI, baca_pgn, online, rentang, unduh

AKAR = Path(__file__).resolve().parent
FOLDER_LAPORAN = AKAR / "data" / "laporan"
TAHAP = ("cadangan", "twic", "broadcast", "indeks")
MIN_ELO_DEFAULT = 2500
TIER_DEFAULT = 5

# TWIC terbit tiap Senin; jangkar untuk menghitung nomor edisi dari tanggal.
EDISI_JANGKAR = 1661
TANGGAL_JANGKAR = date(2026, 9, 7)


# --------------------------------------------------------------------------
# Fungsi murni (diuji di tests/test_perbarui.py)
# --------------------------------------------------------------------------


def edisi_terbaru(hari_ini: date | None = None) -> int:
    """Perkiraan nomor edisi TWIC terbaru. Kalau ternyata belum terbit,
    unduhan 404 dan tahap TWIC berhenti dengan tenang."""
    selisih = (hari_ini or date.today()) - TANGGAL_JANGKAR
    return EDISI_JANGKAR + selisih.days // 7


def lolos_mingguan(h, min_elo: int) -> bool:
    """Saringan backfill: kedua pemain ber-Elo >= min_elo dan bukan online.
    Tanpa Elo -> dibuang, bukan ditebak."""
    if online(h):
        return False
    for kunci in ("WhiteElo", "BlackElo"):
        elo = ke_int(h.get(kunci))
        if elo is None or elo < min_elo:
            return False
    return True


def edisi_belum(conn, terbaru: int) -> list[int]:
    """Nomor edisi setelah yang terakhir tercatat, sampai `terbaru`."""
    baris = conn.execute(
        "SELECT MAX(CAST(kunci AS INTEGER)) FROM sumber_impor WHERE jenis = 'twic'"
    ).fetchone()
    terakhir = baris[0] if baris and baris[0] is not None else EDISI_JANGKAR
    return [n for n in range(terakhir + 1, terbaru + 1) if not sudah_selesai(conn, "twic", str(n))]


# --------------------------------------------------------------------------
# Tahap
# --------------------------------------------------------------------------


class Log:
    def __init__(self, path: Path | None):
        self.path = path
        self.fh = open(path, "a", encoding="utf-8") if path else None

    def __call__(self, teks: str = "") -> None:
        print(teks)
        if self.fh:
            self.fh.write(teks + "\n")
            self.fh.flush()

    def tutup(self) -> None:
        if self.fh:
            self.fh.close()


def tahap_cadangan(db: Path, coba: bool, log: Log) -> None:
    if coba:
        log("  dilewati (--coba)")
        return
    tujuan = cadangkan(db)
    log(f"  {tujuan.name}  {tujuan.stat().st_size / 1048576:.1f} MB, integrity ok")


def tahap_twic(conn, min_elo: int, coba: bool, log: Log,
               edisi: str | None = None, unduh_saja: bool = False) -> Counter:
    total = Counter()
    if edisi:
        # Backfill arsip lama: rentang ditentukan sendiri. Edisi yang sudah
        # tercatat di sumber_impor tetap dilewati, jadi aman diulang.
        daftar = [n for n in rentang(edisi) if coba or not sudah_selesai(conn, "twic", str(n))]
    else:
        daftar = edisi_belum(conn, edisi_terbaru())
    if not daftar:
        log("  tidak ada edisi baru")
        return total
    log(f"  edisi yang dicoba: {daftar[0]}..{daftar[-1]} ({len(daftar)} zip)"
        + ("  [unduh saja]" if unduh_saja else ""))
    for n in daftar:
        zp = unduh(n)
        if zp is None:
            # Edisi mingguan yang belum terbit = berhenti. Lubang di arsip lama
            # = lewati saja dan lanjut.
            if edisi:
                log(f"  twic{n}: tidak ada di arsip, dilewati")
                total["hilang"] += 1
                continue
            log(f"  twic{n}: belum terbit atau gagal diunduh, berhenti di sini")
            break
        if unduh_saja:
            total["diunduh"] += 1
            if total["diunduh"] % 25 == 0:
                log(f"  {total['diunduh']}/{len(daftar)} zip tersimpan (terakhir twic{n})")
            continue
        fh = io.StringIO(baca_pgn(zp))
        hitung = Counter()          # JANGAN dinamai `edisi`: itu parameter rentang
        while True:
            try:
                game = chess.pgn.read_game(fh)
            except Exception:
                hitung["rusak"] += 1
                continue
            if game is None:
                break
            hitung["dibaca"] += 1
            if not lolos_mingguan(game.headers, min_elo):
                continue
            hitung["lolos"] += 1
            if coba:
                continue
            status = impor_satu(conn, game, "twic", URL_EDISI.format(n=n))
            hitung[status.split(":", 1)[0]] += 1
            if hitung["lolos"] % 200 == 0:
                conn.commit()
        conn.commit()
        if not coba:
            catat(conn, "twic", str(n), f"TWIC {n}", URL_EDISI.format(n=n),
                  hitung["dibaca"], hitung["lolos"], hitung["baru"])
        total.update(hitung)
        total["edisi"] += 1
        log(f"  twic{n}: {hitung['dibaca']} partai, {hitung['lolos']} lolos"
            + (f", {hitung['baru']} baru, {hitung['duplikat']} duplikat, {hitung['diperbarui']} diperbarui"
               if not coba else ""))
    if total["baru"]:
        perbarui_elo_terakhir(conn)
    return total


def jalankan(skrip: str, argumen: list[str], log: Log) -> int:
    """Jalankan skrip lain sebagai proses terpisah dan salin keluarannya ke log."""
    perintah = [sys.executable, "-X", "utf8", str(AKAR / skrip)] + argumen
    log(f"  $ python {skrip} {' '.join(argumen)}")
    # lingkungan_anak(): skrip anak memakai kunci penulis yang dipegang proses
    # ini, bukan ditolak olehnya (tahap indeks dulu selalu GAGAL karena itu).
    proses = subprocess.run(perintah, cwd=AKAR, capture_output=True, text=True,
                            encoding="utf-8", errors="replace", env=lingkungan_anak())
    for baris in (proses.stdout + proses.stderr).splitlines():
        if baris.strip():
            log("    " + baris)
    return proses.returncode


def tahap_broadcast(tier: int, coba: bool, log: Log) -> int:
    return jalankan("impor_broadcast.py", ["--resmi", "--tier", str(tier)] + (["--lihat-saja"] if coba else []), log)


def tahap_indeks(coba: bool, log: Log) -> int:
    if coba:
        log("  dilewati (--coba)")
        return 0
    return jalankan("indeks_posisi.py", [], log)


# --------------------------------------------------------------------------
# Utama
# --------------------------------------------------------------------------


def main() -> int:
    p = argparse.ArgumentParser(description="Pembaruan mingguan: cadangan, TWIC, broadcast, indeks")
    p.add_argument("--coba", action="store_true", help="hitung saja, jangan menulis database")
    p.add_argument("--hanya", action="append", choices=TAHAP, help="jalankan tahap ini saja (boleh diulang)")
    p.add_argument("--min-elo", type=int, default=MIN_ELO_DEFAULT,
                   help=f"TWIC: kedua pemain harus >= N (default {MIN_ELO_DEFAULT})")
    p.add_argument("--edisi", metavar="RENTANG",
                   help="backfill arsip lama, mis. 920-1505 (tanpa ini: hanya edisi baru)")
    p.add_argument("--unduh-saja", action="store_true",
                   help="tahap TWIC hanya mengunduh zip ke data/twic/, tidak menulis database")
    p.add_argument("--tier", type=int, default=TIER_DEFAULT, help=f"broadcast resmi tier >= N (default {TIER_DEFAULT})")
    p.add_argument("--db", type=Path, default=PATH_DB)
    args = p.parse_args()

    tahap = tuple(args.hanya) if args.hanya else TAHAP
    FOLDER_LAPORAN.mkdir(parents=True, exist_ok=True)
    log = Log(None if args.coba else FOLDER_LAPORAN / f"log-perbarui-{datetime.now():%Y%m%d-%H%M}.txt")
    mulai = time.time()
    log(f"=== perbarui {datetime.now():%Y-%m-%d %H:%M} | tahap: {', '.join(tahap)} | "
        f"twic: kedua Elo >= {args.min_elo}, online dibuang | broadcast tier >= {args.tier}"
        f"{' | COBA (tidak menulis)' if args.coba else ''} ===")

    # Kunci diambil SEBELUM menyentuh database. Yang menolak adalah sistem
    # operasi, bukan pemeriksaan daftar proses yang bisa kalah balapan.
    penjaga = None
    if not args.coba:
        try:
            penjaga = KunciPenulis("perbarui.py " + " ".join(sys.argv[1:])).ambil()
        except KunciTerpakai as e:
            log("")
            log(str(e))
            log.tutup()
            return 1

    conn = koneksi(args.db)
    buat_skema(conn)
    sebelum = info(conn)
    hasil = {}
    try:
        if "cadangan" in tahap:
            log("\n[1/4] cadangan")
            try:
                tahap_cadangan(args.db, args.coba, log)
            except Exception as e:
                log(f"  GAGAL: {e}")
                hasil["cadangan"] = "gagal"
        if "twic" in tahap:
            log("\n[2/4] TWIC")
            hasil["twic"] = tahap_twic(conn, args.min_elo, args.coba, log,
                                       edisi=args.edisi, unduh_saja=args.unduh_saja)
        if "broadcast" in tahap:
            log("\n[3/4] broadcast Lichess")
            hasil["broadcast"] = tahap_broadcast(args.tier, args.coba, log)
        if "indeks" in tahap:
            log("\n[4/4] indeks posisi")
            hasil["indeks"] = tahap_indeks(args.coba, log)
    finally:
        conn.commit()
        sesudah = info(conn)
        conn.close()
        if penjaga:
            penjaga.lepas()

    log("\n" + "=" * 60)
    t = hasil.get("twic")
    if t is not None and args.unduh_saja:
        log(f"  TWIC      : {t['diunduh']} zip tersimpan, {t['hilang']} tidak ada di arsip")
    elif t is not None:
        log(f"  TWIC      : {t['edisi']} edisi, {t['dibaca']} partai dibaca, {t['lolos']} lolos"
            + (f", {t['baru']} baru" if not args.coba else "")
            + (f", {t['hilang']} tidak ada di arsip" if t["hilang"] else ""))
    if "broadcast" in hasil:
        log(f"  broadcast : {'ok' if hasil['broadcast'] == 0 else 'GAGAL (kode ' + str(hasil['broadcast']) + ')'}")
    if "indeks" in hasil:
        log(f"  indeks    : {'ok' if hasil['indeks'] == 0 else 'GAGAL (kode ' + str(hasil['indeks']) + ')'}")
    log(f"  database  : {sesudah['partai']} partai (+{sesudah['partai'] - sebelum['partai']}), "
        f"{sesudah['pemain']} pemain, {sesudah['turnamen']} turnamen")
    log(f"  waktu     : {(time.time() - mulai) / 60:.1f} menit")
    if log.path:
        log(f"  log       : {log.path}")
    log.tutup()
    gagal = hasil.get("cadangan") == "gagal" or any(hasil.get(k) not in (None, 0) for k in ("broadcast", "indeks"))
    return 1 if gagal else 0


if __name__ == "__main__":
    raise SystemExit(main())
