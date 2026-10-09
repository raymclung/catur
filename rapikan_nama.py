"""
Rapikan nama pemain memakai nama resmi FIDE, dicocokkan lewat FIDE ID.

Masalahnya: TWIC menyingkat nama depan — `Caruana,F`, `Nakamura,Hi`,
`Fedoseev,Vl3` — dan itulah bentuk yang tersimpan untuk sebagian besar
pemain. Sulit dibaca, dan tidak seragam dengan nama dari broadcast Lichess
yang lengkap (`Caruana, Fabiano`).

Sumbernya API resmi Lichess `GET /api/fide/player/{fide_id}`, yang
mengembalikan nama FIDE resmi, federasi, gelar, dan tahun lahir. Pencocokan
memakai **FIDE ID**, bukan tebakan nama, jadi tidak ada risiko menyatukan dua
orang berbeda. Nama yang tidak punya FIDE ID tidak disentuh.

Yang diubah: kolom `nama` (ke bentuk "Belakang, Depan" dari FIDE),
`nama_normal` (dihitung ulang), serta `negara` dan `gelar` kalau masih kosong.
Partai tidak disentuh sama sekali.

Pengaman:

  * hasil API disimpan di data/cache-fide.json — menjalankan ulang tidak
    mengunduh ulang;
  * nama baru ditolak kalau `nama_normal`-nya bentrok dengan pemain lain
    (dua baris bernama sama akan mengacaukan pencocokan saat impor);
  * nama baru ditolak kalau nama belakangnya berubah — itu tanda FIDE ID di
    sumber keliru, bukan sekadar singkatan (lihat gabung_pemain.py);
  * bawaannya hanya melihat; perlu --jalan untuk benar-benar menulis.

    python rapikan_nama.py --batas 20        # coba 20 dulu, lihat saja
    python rapikan_nama.py --jalan           # rapikan semua yang bisa
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from app.db import koneksi
from app.kunci_penulis import KunciPenulis, KunciTerpakai
from app.impor import normalisasi_nama

AKAR = Path(__file__).resolve().parent
CACHE = AKAR / "data" / "cache-fide.json"

USER_AGENT = "catur-analysis/0.1 (belajar catur pribadi; +https://github.com/raymclung/catur)"
JEDA = 1.0          # detik antar permintaan; Lichess mengizinkan jauh lebih cepat
JEDA_429 = 65.0
TUNGGU_KUNCI_MS = 60_000


def muat_cache() -> dict:
    if CACHE.exists():
        try:
            return json.loads(CACHE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def simpan_cache(cache: dict) -> None:
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")


def ambil_fide(fide_id: str, cache: dict) -> dict | None:
    """Data FIDE satu pemain. None = tidak ada di FIDE (disimpan juga di cache)."""
    if fide_id in cache:
        return cache[fide_id]
    url = f"https://lichess.org/api/fide/player/{fide_id}"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    for ke in range(3):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                data = json.loads(r.read())
            cache[fide_id] = data
            return data
        except urllib.error.HTTPError as e:
            if e.code == 429:
                print(f"    kena rate limit, tunggu {JEDA_429:.0f} detik...")
                time.sleep(JEDA_429)
                continue
            if e.code == 404:
                cache[fide_id] = None
                return None
            if ke == 2:
                raise
            time.sleep(3 * (ke + 1))
        except Exception:
            if ke == 2:
                raise
            time.sleep(3 * (ke + 1))
    return None


def nama_belakang_dari(nama: str) -> str:
    n = nama.split(",")[0] if "," in nama else nama
    kata = normalisasi_nama(n).split()
    return kata[0] if kata else ""


def perlu_dirapikan(nama: str) -> bool:
    """Nama depan disingkat: 'Caruana,F', 'Nakamura,Hi', 'Fedoseev,Vl3'."""
    if "," not in nama:
        return False
    depan = nama.split(",", 1)[1].strip().rstrip(".")
    if not depan or len(depan) > 3:
        return False
    return depan[0].isalpha()


def nama_dari_berkas_pgn(folder: str, nama_lama: str) -> str | None:
    """
    Nama lengkap dari berkas PGN lokal, untuk pemain yang tidak ada di FIDE.

    FIDE menghapus pemain yang sudah wafat dari basis datanya — Korchnoi,
    Sveshnikov, Kurnosov, dan 18 lainnya tidak bisa dicari lewat ID maupun
    nama. Satu-satunya sumber nama lengkap yang tersisa adalah berkas PGN yang
    sudah ada di disk.

    Sumber ini TIDAK tepercaya seperti FIDE — PGN Mentor menulis 'Eliseev,
    Alexey' untuk partai Urii Eliseev, orang yang berbeda. Karena itu:

      * nama belakang harus sama persis;
      * huruf depan nama depan harus sama dengan singkatannya;
      * hanya diterima kalau tersisa **satu** bentuk lengkap.
    """
    import glob
    import os
    import re as _re
    if "," not in nama_lama:
        return None
    belakang, singkat = [x.strip() for x in nama_lama.split(",", 1)]
    if not singkat:
        return None
    calon = {}
    pola = _re.compile(r'\[(?:White|Black) "([^"]*)"\]')
    for berkas in sorted(glob.glob(os.path.join(folder, "*.pgn"))):
        with open(berkas, encoding="utf-8", errors="replace") as f:
            for baris in f:
                if not (baris.startswith("[White ") or baris.startswith("[Black ")):
                    continue
                m = pola.match(baris)
                if not m or "," not in m.group(1):
                    continue
                b, d = [x.strip() for x in m.group(1).split(",", 1)]
                if b.lower() != belakang.lower() or len(d) <= 3:
                    continue
                if d[0].lower() != singkat[0].lower():
                    continue
                calon[b + ", " + d] = calon.get(b + ", " + d, 0) + 1
    return list(calon)[0] if len(calon) == 1 else None


def putuskan(nama_lama: str, data: dict | None, cari_bentrok=None,
             abaikan_beda_belakang: bool = False):
    """
    Tentukan nama baru untuk satu pemain.

    Kembalikan (nama_baru, alasan_ditolak). Salah satunya selalu None.
    `cari_bentrok(nama_normal)` mengembalikan pemain lain yang sudah memakai
    bentuk normal itu, atau None.
    """
    if not data or not data.get("name"):
        return None, "tidak ada di FIDE"
    nama_baru = data["name"].strip()
    if not nama_baru:
        return None, "nama FIDE kosong"
    if nama_baru == nama_lama:
        return None, "sudah sama"
    if (not abaikan_beda_belakang
            and nama_belakang_dari(nama_baru) != nama_belakang_dari(nama_lama)):
        return None, f"nama belakang berbeda ({nama_baru!r}) — FIDE ID mungkin keliru"
    if cari_bentrok:
        bentrok = cari_bentrok(normalisasi_nama(nama_baru))
        if bentrok:
            return None, f"bentrok dengan {bentrok}"
    return nama_baru, None


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--jalan", action="store_true", help="benar-benar tulis ke database")
    p.add_argument("--batas", type=int, default=0, help="olah maksimal sekian pemain")
    p.add_argument("--semua", action="store_true",
                   help="termasuk pemain yang namanya sudah lengkap (untuk mengisi negara/gelar)")
    p.add_argument("--nama-resmi", action="store_true",
                   help="pakai nama resmi FIDE apa adanya, walau nama belakangnya "
                        "berbeda dari yang tersimpan (ejaan alih aksara, urutan "
                        "nama, atau baris yang masih memakai username). Identitas "
                        "tetap dipastikan lewat FIDE ID")
    p.add_argument("--cadangan-pgn", metavar="FOLDER",
                   help="untuk pemain yang tidak ada di FIDE (sudah wafat), cari nama "
                        "lengkap di berkas PGN folder ini")
    p.add_argument("--db")
    a = p.parse_args()
    # Log yang diarahkan ke berkas harus tetap terbaca sesi lain saat proses
    # berjalan; tanpa ini stdout ditahan sampai proses keluar (25 Sep 2026:
    # 30 menit tanpa satu baris pun, dikira menggantung).
    sys.stdout.reconfigure(line_buffering=True)

    conn = koneksi(a.db)
    conn.row_factory = sqlite3.Row
    conn.execute(f"PRAGMA busy_timeout = {TUNGGU_KUNCI_MS}")

    baris = conn.execute(
        "SELECT id, nama, nama_normal, fide_id, gelar, negara FROM pemain "
        "WHERE fide_id IS NOT NULL ORDER BY id").fetchall()
    sasaran = [r for r in baris if a.semua or a.nama_resmi or perlu_dirapikan(r["nama"])]
    if a.batas:
        sasaran = sasaran[:a.batas]

    print(f"{len(sasaran)} pemain akan diperiksa"
          f"{'' if a.jalan else ' (lihat saja; tambahkan --jalan untuk menulis)'}.")
    if not sasaran:
        return 0

    cache = muat_cache()
    diubah = dilewati = gagal = tidak_ada = 0
    unduh_baru = 0

    # Tahap 1 — unduh dulu, TANPA kunci penulis. Inilah bagian yang lama
    # (ribuan permintaan, jeda 1 detik, menunggu jaringan) dan ia tidak
    # menyentuh database sama sekali. Sebelumnya kunci dipegang sepanjang
    # unduhan: 25 Sep 2026 kunci tertahan 51 menit dan sesi lain tidak bisa
    # menulis, padahal yang berjalan cuma menunggu Lichess.
    gagal_ambil: set[str] = set()
    try:
        for i, r in enumerate(sasaran, 1):
            fid = r["fide_id"]
            if fid in cache:
                continue
            try:
                ambil_fide(fid, cache)
            except Exception as e:
                gagal += 1
                gagal_ambil.add(fid)
                print(f"  [{i}/{len(sasaran)}] {r['nama']!r} fide {fid}: GAGAL {type(e).__name__}: {e}")
                continue
            unduh_baru += 1
            time.sleep(JEDA)
            if unduh_baru % 25 == 0:
                simpan_cache(cache)
            if unduh_baru % 100 == 0:
                print(f"  ... {i}/{len(sasaran)} diperiksa, {unduh_baru} diunduh")
    except KeyboardInterrupt:
        simpan_cache(cache)
        print("\ndihentikan pengguna saat mengunduh; yang sudah diunduh tersimpan di cache.")
        return 1
    simpan_cache(cache)
    print(f"  unduhan selesai: {unduh_baru} baru, {gagal} gagal, sisanya dari cache.")

    # Tahap 2 — putuskan dan tulis. Satu penulis besar pada satu waktu
    # (CLAUDE.md bagian 7): impor atau analisis yang sedang jalan tidak boleh
    # diadu dengan perubahan massal ini. Kunci diambil di sini, bukan di atas.
    penjaga = None
    if a.jalan:
        try:
            penjaga = KunciPenulis("rapikan nama dari FIDE").ambil()
        except KunciTerpakai as e:
            print(e)
            return 1
    try:
        for i, r in enumerate(sasaran, 1):
            fid = r["fide_id"]
            if fid in gagal_ambil:
                continue
            data = cache.get(fid)
            if not data or not data.get("name"):
                tidak_ada += 1
                if not a.cadangan_pgn or not perlu_dirapikan(r["nama"]):
                    continue
                usul = nama_dari_berkas_pgn(a.cadangan_pgn, r["nama"])
                if not usul:
                    continue
                data = {"name": usul}
                print(f"  [{i}/{len(sasaran)}] {r['nama']!r}: tidak ada di FIDE, "
                      f"memakai nama dari berkas PGN")

            def cari_bentrok(normal, _id=r["id"]):
                b = conn.execute(
                    "SELECT id, nama FROM pemain WHERE nama_normal = ? AND id <> ?",
                    (normal, _id)).fetchone()
                return f"id {b['id']} ({b['nama']!r})" if b else None

            nama_baru, alasan = putuskan(r["nama"], data, cari_bentrok, a.nama_resmi)
            normal_baru = normalisasi_nama(nama_baru) if nama_baru else None

            if alasan:
                if alasan != "sudah sama":
                    dilewati += 1
                    print(f"  [{i}/{len(sasaran)}] {r['nama']!r}: dilewati — {alasan}")
            else:
                diubah += 1
                print(f"  [{i}/{len(sasaran)}] {r['nama']!r} -> {nama_baru!r}"
                      f"{'' if a.jalan else ' (belum ditulis)'}")
                if a.jalan:
                    conn.execute("UPDATE pemain SET nama = ?, nama_normal = ? WHERE id = ?",
                                 (nama_baru, normal_baru, r["id"]))

            if a.jalan:
                if data.get("federation") and not r["negara"]:
                    conn.execute("UPDATE pemain SET negara = ? WHERE id = ?",
                                 (data["federation"], r["id"]))
                if data.get("title") and not r["gelar"]:
                    conn.execute("UPDATE pemain SET gelar = ? WHERE id = ?",
                                 (data["title"], r["id"]))
            if a.jalan and i % 25 == 0:
                conn.commit()
                simpan_cache(cache)
    except KeyboardInterrupt:
        print("\ndihentikan pengguna.")
    finally:
        if a.jalan:
            conn.commit()
        simpan_cache(cache)
        if penjaga:
            penjaga.lepas()   # berkas .info tidak tertinggal basi setelah keluar

    print(f"\n{diubah} nama {'dirapikan' if a.jalan else 'akan dirapikan'}, "
          f"{dilewati} dilewati, {tidak_ada} tidak ada di FIDE, {gagal} gagal diunduh. "
          f"{unduh_baru} permintaan baru ke Lichess.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
