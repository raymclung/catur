"""
Ambil partai dari Lichess dan Chess.com lewat API resmi keduanya.

Keduanya menyediakan data partai secara publik tanpa perlu login, jadi tidak
ada password atau token yang disimpan di sini.

Sopan santun yang dijaga skrip ini:
  - User-Agent diisi jelas berikut kontak, sesuai permintaan Chess.com.
  - Ada jeda antar permintaan, tidak menghantam server bertubi-tubi.
  - Kalau server menjawab 429 (terlalu sering), skrip menunggu lalu mengulang.
  - Bulan yang sudah pernah diunduh tidak diunduh lagi.

Contoh pemakaian:
    python ambil_partai.py --daftar
    python ambil_partai.py --kecepatan rapid classical
    python ambil_partai.py --akun McLung_07 --kecepatan rapid
    python ambil_partai.py --semua-kecepatan --maks 500
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

AKAR = Path(__file__).resolve().parent
FOLDER_PGN = AKAR / "data" / "pgn"

# Kontak dicantumkan supaya kalau skrip ini bermasalah, admin platform bisa
# menghubungi pemiliknya. Ini praktik yang diminta Chess.com di dokumentasinya.
USER_AGENT = "catur-analysis/0.1 (belajar catur pribadi; +https://github.com/raymclung/catur)"

JEDA_ANTAR_PERMINTAAN = 1.5   # detik
JEDA_SETELAH_429 = 65.0       # detik, kalau kena rate limit


@dataclass(frozen=True)
class Akun:
    platform: str                       # "lichess" atau "chesscom"
    username: str
    kecepatan_dikecualikan: tuple = ()  # kontrol waktu yang TIDAK boleh diambil
    alasan_pengecualian: str = ""
    # Hanya untuk Chess.com: kalau diisi, cuma partai yang URL turnamennya
    # mengandung teks ini yang diambil. Dipakai untuk akun yang sebagian
    # partainya dimainkan orang lain.
    saring_turnamen: str = ""
    alasan_saringan: str = ""


# Catatan soal pengecualian:
#
# Sebagian partai di akun tertentu bukan dimainkan Ray. Partai seperti itu
# harus dibuang, bukan sekadar diberi tanda — kalau ikut terhitung, statistik
# akurasi dan diagnosis kelemahan jadi salah, dan itu justru menyesatkan
# latihan. Lebih baik datanya sedikit tapi benar.
AKUN = [
    Akun("lichess", "McLung_07"),
    Akun("lichess", "Mikaysr"),
    Akun("lichess", "Sakamoto_PMan"),
    Akun("chesscom", "rayylung"),
    Akun(
        "chesscom",
        "ryumclungg",
        saring_turnamen="arena-kings",
        alasan_saringan="akun ini hanya dipakai Ray untuk turnamen Arena Kings; "
                        "partai lain di akun ini dimainkan orang lain",
    ),
    Akun("chesscom", "stanley-squarepants"),
]

# Nama kecepatan disamakan antara kedua platform supaya bisa dipakai bersama.
KECEPATAN_LICHESS = {
    "bullet": "bullet",
    "blitz": "blitz",
    "rapid": "rapid",
    "classical": "classical",
    "correspondence": "correspondence",
}
KECEPATAN_CHESSCOM = {
    "bullet": "bullet",
    "blitz": "blitz",
    "rapid": "rapid",
    "classical": "daily",   # Chess.com menyebut partai lambat sebagai "daily"
    "correspondence": "daily",
}

# Default sengaja rapid + classical saja.
#
# Alasannya: tujuan utama proyek ini adalah persiapan catur klasik. Partai
# bullet dan blitz jumlahnya puluhan kali lipat, tapi kesalahan di sana
# didominasi tekanan waktu, bukan pemahaman posisi. Menganalisisnya dengan
# engine menghabiskan berjam-jam untuk kesimpulan yang tidak bisa ditindak.
KECEPATAN_DEFAULT = ["rapid", "classical"]


class AmbilError(RuntimeError):
    pass


def minta(url: str, sebagai_teks: bool = False, percobaan: int = 3):
    """Satu permintaan HTTP dengan penanganan rate limit."""
    headers = {"User-Agent": USER_AGENT}
    if sebagai_teks:
        headers["Accept"] = "application/x-chess-pgn"

    for ke in range(percobaan):
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                data = r.read()
            return data.decode("utf-8", errors="replace") if sebagai_teks else json.loads(data)
        except urllib.error.HTTPError as e:
            if e.code == 429:
                print(f"    kena rate limit, tunggu {JEDA_SETELAH_429:.0f} detik...")
                time.sleep(JEDA_SETELAH_429)
                continue
            if e.code == 404:
                raise AmbilError(f"tidak ditemukan: {url}") from e
            if ke == percobaan - 1:
                raise AmbilError(f"HTTP {e.code} untuk {url}") from e
            time.sleep(3 * (ke + 1))
        except Exception as e:
            if ke == percobaan - 1:
                raise AmbilError(f"{type(e).__name__}: {e}") from e
            time.sleep(3 * (ke + 1))
    raise AmbilError(f"gagal setelah {percobaan} percobaan: {url}")


def hitung_partai(teks_pgn: str) -> int:
    """Hitung jumlah partai dalam teks PGN lewat tag [Event."""
    return teks_pgn.count("[Event ")


# ---------------------------------------------------------------------------
# Lichess
# ---------------------------------------------------------------------------


def ambil_lichess(username: str, kecepatan: list[str], maks: int,
                  hanya_rated: bool, sejak_ms: int | None = None) -> str:
    """
    Ambil PGN dari Lichess.

    Endpoint /api/games/user mengalirkan PGN langsung, jadi satu permintaan
    sudah cukup untuk semua partai yang diminta.

    sejak_ms : epoch milidetik. Hanya partai SESUDAH waktu itu yang ditarik —
               dipakai tombol "tarik partai baru" di web supaya penarikan
               harian tidak mengunduh ulang 8.596 partai.
    """
    perf = ",".join(KECEPATAN_LICHESS[k] for k in kecepatan if k in KECEPATAN_LICHESS)
    params = [
        f"perfType={perf}",
        "clocks=true",
        "evals=false",
        "opening=true",
        "sort=dateDesc",
    ]
    if hanya_rated:
        params.append("rated=true")
    if maks:
        params.append(f"max={maks}")
    if sejak_ms:
        params.append(f"since={int(sejak_ms)}")

    url = f"https://lichess.org/api/games/user/{username}?" + "&".join(params)
    print(f"    mengunduh (bisa lama untuk partai banyak)...")
    return minta(url, sebagai_teks=True)


# ---------------------------------------------------------------------------
# Chess.com
# ---------------------------------------------------------------------------


def ambil_chesscom(username: str, kecepatan: list[str], maks: int,
                   hanya_rated: bool, saring_turnamen: str = "",
                   sejak_bulan: str | None = None) -> str:
    """
    Ambil PGN dari Chess.com.

    Chess.com membagi arsip per bulan, jadi harus diambil satu per satu.

    saring_turnamen : kalau diisi, hanya partai yang URL turnamennya mengandung
                      teks ini yang diambil. Penyaringan HARUS lewat field
                      "tournament" di JSON, bukan lewat tag [Event] di PGN:
                      semua partai live Chess.com bertag [Event "Live Chess"],
                      jadi nama turnamennya tidak ada di PGN sama sekali.
    """
    kelas = {KECEPATAN_CHESSCOM[k] for k in kecepatan if k in KECEPATAN_CHESSCOM}

    arsip = minta(f"https://api.chess.com/pub/player/{username}/games/archives")["archives"]
    if sejak_bulan:
        # URL arsip berakhiran "/YYYY/MM", jadi perbandingan teks sudah benar
        # secara kronologis. Bulan partai terakhir ikut ditarik ulang — partai
        # yang sudah ada ditolak hash, dan partai baru di bulan itu tidak hilang.
        arsip = [u for u in arsip if u[-7:].replace("/", "-") >= sejak_bulan]
    print(f"    {len(arsip)} bulan arsip" + (f" sejak {sejak_bulan}" if sejak_bulan else ""))

    potongan: list[str] = []
    jumlah = 0

    # Mulai dari bulan terbaru supaya kalau dibatasi --maks, yang didapat
    # adalah partai paling baru.
    for url_bulan in reversed(arsip):
        if maks and jumlah >= maks:
            break
        time.sleep(JEDA_ANTAR_PERMINTAAN)
        try:
            data = minta(url_bulan)
        except AmbilError as e:
            print(f"    lewati {url_bulan[-7:]}: {e}")
            continue

        for g in data.get("games", []):
            if maks and jumlah >= maks:
                break
            if g.get("time_class") not in kelas:
                continue
            if hanya_rated and not g.get("rated", False):
                continue

            turnamen = g.get("tournament", "")
            if saring_turnamen and saring_turnamen not in turnamen:
                continue

            pgn = g.get("pgn")
            if not pgn:
                continue

            # Simpan asal turnamennya sebagai tag tambahan. Tag [Event] aslinya
            # tidak diubah supaya PGN tetap sesuai sumbernya; ini cuma
            # menambahkan jejak dari mana partai ini berasal.
            if turnamen:
                pgn = pgn.strip()
                sisip = f'[ChessComTournament "{turnamen}"]'
                baris = pgn.splitlines()
                akhir_tag = 0
                for i, b in enumerate(baris):
                    if b.startswith("["):
                        akhir_tag = i + 1
                    elif b.strip():
                        break
                baris.insert(akhir_tag, sisip)
                pgn = "\n".join(baris)

            potongan.append(pgn.strip())
            jumlah += 1

        print(f"    {url_bulan[-7:]}: total terkumpul {jumlah}")

    return "\n\n".join(potongan) + ("\n" if potongan else "")


# ---------------------------------------------------------------------------
# Utama
# ---------------------------------------------------------------------------


def main() -> int:
    p = argparse.ArgumentParser(description="Ambil partai dari Lichess & Chess.com")
    p.add_argument("--akun", nargs="*", default=None,
                   help="username tertentu saja (default: semua)")
    p.add_argument("--kecepatan", nargs="*", default=KECEPATAN_DEFAULT,
                   choices=sorted(KECEPATAN_LICHESS),
                   help=f"kontrol waktu (default: {' '.join(KECEPATAN_DEFAULT)})")
    p.add_argument("--semua-kecepatan", action="store_true",
                   help="ambil semua kontrol waktu termasuk bullet & blitz")
    p.add_argument("--maks", type=int, default=0,
                   help="batas jumlah partai per akun (0 = tanpa batas)")
    p.add_argument("--termasuk-casual", action="store_true",
                   help="ikutkan partai tidak rated")
    p.add_argument("--timpa", action="store_true",
                   help="unduh ulang walau filenya sudah ada")
    p.add_argument("--daftar", action="store_true",
                   help="tampilkan daftar akun lalu keluar")
    args = p.parse_args()

    if args.daftar:
        print("Akun yang terdaftar:")
        for a in AKUN:
            print(f"  {a.platform:9} {a.username}")
        return 0

    kecepatan = sorted(KECEPATAN_LICHESS) if args.semua_kecepatan else args.kecepatan
    hanya_rated = not args.termasuk_casual

    dipilih = AKUN
    if args.akun:
        diminta = {u.lower() for u in args.akun}
        dipilih = [a for a in AKUN if a.username.lower() in diminta]
        if not dipilih:
            print(f"Tidak ada akun yang cocok: {args.akun}", file=sys.stderr)
            return 1

    FOLDER_PGN.mkdir(parents=True, exist_ok=True)

    print(f"Kecepatan : {', '.join(kecepatan)}")
    print(f"Rated saja: {'ya' if hanya_rated else 'tidak'}")
    print(f"Batas     : {args.maks if args.maks else 'tanpa batas'} partai per akun")
    print()

    total = 0
    for akun in dipilih:
        nama_file = FOLDER_PGN / f"{akun.platform}-{akun.username}.pgn"
        print(f"[{akun.platform}] {akun.username}")

        kecepatan_akun = [k for k in kecepatan if k not in akun.kecepatan_dikecualikan]
        dibuang = [k for k in kecepatan if k in akun.kecepatan_dikecualikan]
        if dibuang:
            print(f"    dikecualikan: {', '.join(dibuang)} "
                  f"({akun.alasan_pengecualian})")
        if akun.saring_turnamen:
            print(f"    saringan turnamen: '{akun.saring_turnamen}' "
                  f"({akun.alasan_saringan})")
        if not kecepatan_akun:
            print(f"    tidak ada kecepatan tersisa untuk akun ini - lewati")
            print()
            continue

        if nama_file.exists() and not args.timpa:
            sudah = hitung_partai(nama_file.read_text(encoding="utf-8", errors="replace"))
            print(f"    sudah ada ({sudah} partai) - lewati. Pakai --timpa untuk unduh ulang.")
            total += sudah
            print()
            continue

        try:
            if akun.platform == "lichess":
                teks = ambil_lichess(akun.username, kecepatan_akun, args.maks, hanya_rated)
            else:
                teks = ambil_chesscom(akun.username, kecepatan_akun, args.maks,
                                      hanya_rated, akun.saring_turnamen)
        except AmbilError as e:
            print(f"    GAGAL: {e}")
            print()
            continue

        n = hitung_partai(teks)
        if n == 0:
            print(f"    tidak ada partai yang cocok dengan filter ini")
        else:
            nama_file.write_text(teks, encoding="utf-8")
            print(f"    {n} partai -> {nama_file.relative_to(AKAR)}")
        total += n
        print()
        time.sleep(JEDA_ANTAR_PERMINTAAN)

    print("=" * 60)
    print(f"Total: {total} partai di {FOLDER_PGN.relative_to(AKAR)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
