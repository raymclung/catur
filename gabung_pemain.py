"""
Gabungkan baris pemain ganda yang lahir dari FIDE ID keliru di sumber.

Kenapa perlu: `dapatkan_atau_buat_pemain()` memperlakukan FIDE ID sebagai
kunci identitas — benar, karena itu satu-satunya penanda yang pasti. Tapi
sumbernya bisa salah. TWIC edisi 1650 (FIDE World Rapid Team 2026) memberi
`Carlsen,M` FIDE ID 6015549 dan `Ivanchuk,V` 373257, padahal keduanya bukan
ID orang tersebut. Akibatnya lahir baris pemain kedua, dan partainya hilang
dari profil pemain yang asli.

Deteksi (sengaja hanya mengusulkan, tidak menebak sendiri):

  * nama belakang sama persis setelah normalisasi;
  * nama depan cocok — sama, atau satu sisi singkatan dari yang lain;
  * kedua baris punya FIDE ID, tapi berbeda;
  * rentang Elo keduanya tumpang-tindih di tahun yang sama.

Syarat terakhir yang memisahkan "sumbernya salah" dari "dua orang berbeda
yang kebetulan senama": dua orang berbeda jarang punya Elo yang sama persis
di pertandingan yang sama.

Baris yang dipertahankan = yang partainya lebih banyak. Partai, dan kolom
yang masih kosong, dipindahkan ke sana; baris yang kalah dihapus.

    python gabung_pemain.py                # hanya melihat (bawaan)
    python gabung_pemain.py --jalan        # benar-benar menggabungkan
    python gabung_pemain.py --id 7933 7151 # gabungkan pasangan tertentu
"""

from __future__ import annotations

import argparse
import re
import sqlite3
import sys
from collections import defaultdict

from app.db import koneksi
from app.kunci_penulis import KunciPenulis, KunciTerpakai
from app.impor import normalisasi_nama

# Elo dua baris dianggap "orang yang sama" kalau selisihnya tidak lebih dari
# ini pada tahun yang sama. Rating seseorang bergerak pelan; dua orang senama
# yang berbeda biasanya terpaut jauh lebih besar.
SELISIH_ELO = 60

# Berapa lama menunggu kalau database sedang dipakai penulis lain (impor
# TWIC, analisis batch). Satu pasangan yang gagal tidak membatalkan sisanya.
TUNGGU_KUNCI_MS = 60_000


def _pisah(nama_normal: str) -> tuple[str, str] | None:
    kata = nama_normal.split()
    if len(kata) < 2:
        return None
    return " ".join(kata[:-1]), kata[-1]


def _depan_cocok(a: str, b: str) -> bool:
    if a == b:
        return True
    if a[0] != b[0]:
        return False
    return len(a) == 1 or len(b) == 1


def _tanpa_penanda_twic(depan: str) -> str:
    """
    Nama depan tanpa angka penanda TWIC: 'vl3' -> 'vl', 'a1' -> 'a'.

    TWIC menambah angka di belakang singkatan untuk membedakan pemain senama
    ('Fedoseev,Vl3', 'Moiseenko,A1'). Angka itu membuat 'vl3' bukan awalan
    'vladimir', sehingga 379 partai Vladimir Fedoseev terdampar di baris
    terpisah. Angka dibuang HANYA untuk mencari calon; bukti identitasnya tetap
    pengaman lain di cari_pasangan_tanpa_fide(). Dan justru karena angka itu
    berarti "orang berbeda", baris yang sama-sama ber-FIDE ID tidak pernah
    disentuh di sini ('Sokolov,And2' FIDE 11600306 bukan 'Sokolov, Andrei'
    627089) — calon ganda seperti itu membuat kasusnya ragu dan dilewati.
    """
    return re.sub(r"\d+$", "", depan)


def _depan_awalan(a: str, b: str) -> bool:
    """
    Satu nama depan adalah awalan dari yang lain: 'hi' ⊂ 'hikaru', 'f' ⊂
    'fabiano', 'maxime' = 'maxime'.

    Lebih longgar dari _depan_cocok (yang hanya menerima singkatan SATU huruf)
    karena PGN Mentor memakai singkatan dua huruf — 'Nakamura,Hi', 'Fedoseev,Vl'
    — dan itu yang membuat 6.990 partai Nakamura terdampar di baris terpisah.
    Kelonggaran ini aman hanya karena dipakai bersama dua syarat lain di
    cari_pasangan_tanpa_fide(): calon harus TEPAT SATU, dan Elo keduanya harus
    berdekatan di tahun yang sama.
    """
    if not a or not b:
        return False
    return a.startswith(b) or b.startswith(a)


def _elo_per_tahun(conn: sqlite3.Connection, pemain_id: int) -> dict[str, tuple[int, int]]:
    """{tahun: (elo terendah, elo tertinggi)} dari partai pemain ini."""
    q = """
        SELECT substr(tanggal, 1, 4) AS th,
               MIN(CASE WHEN putih_id = ? THEN elo_putih ELSE elo_hitam END) AS emin,
               MAX(CASE WHEN putih_id = ? THEN elo_putih ELSE elo_hitam END) AS emax
        FROM partai
        WHERE (putih_id = ? OR hitam_id = ?) AND tanggal IS NOT NULL
        GROUP BY th
    """
    keluar = {}
    for r in conn.execute(q, (pemain_id, pemain_id, pemain_id, pemain_id)):
        if r["emin"] is not None and r["emax"] is not None:
            keluar[r["th"]] = (r["emin"], r["emax"])
    return keluar


def _elo_tumpang_tindih(a: dict, b: dict) -> str | None:
    """Tahun pertama di mana rentang Elo keduanya berdekatan, atau None."""
    for th in sorted(set(a) & set(b)):
        amin, amax = a[th]
        bmin, bmax = b[th]
        if amin - SELISIH_ELO <= bmax and bmin - SELISIH_ELO <= amax:
            return th
    return None


def jumlah_partai(conn: sqlite3.Connection, pemain_id: int) -> int:
    return conn.execute(
        "SELECT COUNT(*) FROM partai WHERE putih_id = ? OR hitam_id = ?",
        (pemain_id, pemain_id),
    ).fetchone()[0]


def cari_pasangan(conn: sqlite3.Connection) -> list[tuple[sqlite3.Row, sqlite3.Row, str]]:
    """Pasangan (dipertahankan, dihapus, alasan) yang layak digabung."""
    baris = conn.execute(
        "SELECT id, nama, nama_normal, fide_id, gelar, negara, elo_terakhir "
        "FROM pemain WHERE fide_id IS NOT NULL"
    ).fetchall()

    kel = defaultdict(list)
    for r in baris:
        p = _pisah(r["nama_normal"])
        if p:
            kel[p[0]].append((r, p[1]))

    hasil = []
    for _, anggota in kel.items():
        for i in range(len(anggota)):
            for j in range(i + 1, len(anggota)):
                a, depan_a = anggota[i]
                b, depan_b = anggota[j]
                if a["fide_id"] == b["fide_id"]:
                    continue
                if not _depan_cocok(depan_a, depan_b):
                    continue
                th = _elo_tumpang_tindih(_elo_per_tahun(conn, a["id"]),
                                         _elo_per_tahun(conn, b["id"]))
                if th is None:
                    continue
                na, nb = jumlah_partai(conn, a["id"]), jumlah_partai(conn, b["id"])
                simpan, buang = (a, b) if na >= nb else (b, a)
                hasil.append((simpan, buang, f"Elo berdekatan pada {th}"))
    return hasil


def cari_pasangan_tanpa_fide(conn: sqlite3.Connection) -> list[tuple[sqlite3.Row, sqlite3.Row, str]]:
    """
    Baris TANPA FIDE ID yang sebenarnya orang yang sama dengan baris
    terverifikasi. Ini kasus impor PGN Mentor: sumbernya tidak memuat FIDE ID
    sama sekali, dan nama depannya disingkat, sehingga lahir baris kedua.

    Beda dari cari_pasangan(): di sana kedua baris punya FIDE ID yang berbeda
    (sumber salah). Di sini yang dibuang tidak punya FIDE ID sama sekali.

    Yang DIPERTAHANKAN selalu baris ber-FIDE ID, walau partainya lebih sedikit —
    FIDE ID satu-satunya penanda identitas yang pasti (Nakamura: baris
    terverifikasi 2.803 partai, baris singkatan 6.990).

    Tiga pengaman, dan ketiganya harus lolos:
      * nama belakang sama persis setelah normalisasi (tanda hubung sudah jadi
        spasi, jadi 'Vachier-Lagrave' = 'Vachier Lagrave');
      * nama depan: satu awalan dari yang lain;
      * calon ber-FIDE ID harus TEPAT SATU, dan Elo keduanya berdekatan di
        tahun yang sama — dua orang berbeda jarang begitu.
    """
    semua = conn.execute(
        "SELECT id, nama, nama_normal, fide_id, gelar, negara, elo_terakhir FROM pemain"
    ).fetchall()

    berfide = defaultdict(list)
    tanpa = defaultdict(list)
    for r in semua:
        p = _pisah(r["nama_normal"])
        if not p:
            continue
        (berfide if r["fide_id"] else tanpa)[p[0]].append((r, p[1]))

    hasil = []
    for belakang, anggota in tanpa.items():
        for baris, depan in anggota:
            inti = _tanpa_penanda_twic(depan)
            calon = [(t, d) for t, d in berfide.get(belakang, [])
                     if _depan_awalan(inti, _tanpa_penanda_twic(d))]
            if len(calon) != 1:
                continue          # ragu = jangan tebak, biarkan terpisah
            target = calon[0][0]
            th = _elo_tumpang_tindih(_elo_per_tahun(conn, target["id"]),
                                     _elo_per_tahun(conn, baris["id"]))
            if th is None:
                continue
            hasil.append((target, baris, f"tanpa FIDE ID, Elo berdekatan pada {th}"))
    hasil.sort(key=lambda x: -jumlah_partai(conn, x[1]["id"]))
    return hasil


def perbaiki_fide_id_pgn(conn, id_pemain: int, fide_salah: str, fide_benar):
    """
    Perbaiki tag WhiteFideId/BlackFideId yang keliru di PGN tersimpan.

    Kalau hanya baris pemain yang digabung, PGN-nya tetap memuat ID keliru dari
    sumber, dan impor ulang akan melahirkan pemain ganda yang sama lagi. Yang
    diubah hanya tag di sisi pemain ini, dan hanya kalau isinya persis ID keliru
    itu — langkah, hasil, dan tag lain tidak disentuh.
    """
    if not fide_benar or not fide_salah:
        return 0
    diperbaiki = 0
    q = "SELECT id, pgn, putih_id FROM partai WHERE putih_id = ? OR hitam_id = ?"
    for r in conn.execute(q, (id_pemain, id_pemain)).fetchall():
        tag = "WhiteFideId" if r["putih_id"] == id_pemain else "BlackFideId"
        lama_teks = '[' + tag + ' "' + fide_salah + '"]'
        if lama_teks not in (r["pgn"] or ""):
            continue
        baru_teks = '[' + tag + ' "' + str(fide_benar) + '"]'
        conn.execute("UPDATE partai SET pgn = ? WHERE id = ?",
                     (r["pgn"].replace(lama_teks, baru_teks), r["id"]))
        diperbaiki += 1
    return diperbaiki


def gabungkan(conn: sqlite3.Connection, id_simpan: int, id_buang: int):
    """
    Pindahkan partai, lengkapi kolom kosong, hapus baris yang kalah.

    Kembalikan (jumlah partai dipindahkan, jumlah PGN yang tagnya diperbaiki).
    """
    if id_simpan == id_buang:
        raise ValueError("id sama")
    n = jumlah_partai(conn, id_buang)
    baris_simpan = conn.execute("SELECT fide_id FROM pemain WHERE id = ?", (id_simpan,)).fetchone()
    baris_buang = conn.execute("SELECT fide_id FROM pemain WHERE id = ?", (id_buang,)).fetchone()
    pgn_diperbaiki = 0
    if baris_simpan and baris_buang and baris_simpan["fide_id"] and baris_buang["fide_id"]:
        pgn_diperbaiki = perbaiki_fide_id_pgn(conn, id_buang, baris_buang["fide_id"],
                                              baris_simpan["fide_id"])
    conn.execute("UPDATE partai SET putih_id = ? WHERE putih_id = ?", (id_simpan, id_buang))
    conn.execute("UPDATE partai SET hitam_id = ? WHERE hitam_id = ?", (id_simpan, id_buang))
    buang = conn.execute("SELECT * FROM pemain WHERE id = ?", (id_buang,)).fetchone()
    if buang is not None:
        for kolom in ("gelar", "negara", "fide_id"):
            conn.execute(
                f"UPDATE pemain SET {kolom} = ? WHERE id = ? AND ({kolom} IS NULL OR {kolom} = '')",
                (buang[kolom], id_simpan),
            )
    conn.execute("DELETE FROM pemain WHERE id = ?", (id_buang,))
    conn.commit()
    return n, pgn_diperbaiki


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--jalan", action="store_true", help="benar-benar gabungkan (bawaan: lihat saja)")
    p.add_argument("--id", nargs=2, type=int, metavar=("BUANG", "SIMPAN"),
                   help="gabungkan pasangan ini tanpa deteksi")
    p.add_argument("--tanpa-fide", action="store_true",
                   help="mode kedua: baris TANPA FIDE ID yang sama orangnya dengan "
                        "baris terverifikasi (kasus impor PGN Mentor)")
    p.add_argument("--db")
    a = p.parse_args()

    # Satu penulis besar pada satu waktu (CLAUDE.md bagian 7): impor atau
    # analisis yang sedang jalan tidak boleh diadu dengan perubahan massal ini.
    penjaga = None
    if a.jalan:
        try:
            penjaga = KunciPenulis("gabungkan pemain ganda").ambil()
        except KunciTerpakai as e:
            print(e)
            return 1

    conn = koneksi(a.db)
    conn.row_factory = sqlite3.Row
    conn.execute(f"PRAGMA busy_timeout = {TUNGGU_KUNCI_MS}")

    if a.id:
        buang, simpan = a.id
        if not a.jalan:
            print(f"lihat saja: {buang} -> {simpan} ({jumlah_partai(conn, buang)} partai)")
            return 0
        n, pgn_ok = gabungkan(conn, simpan, buang)
        print(f"digabung: {buang} -> {simpan}, {n} partai dipindahkan, {pgn_ok} PGN diperbaiki")
        return 0

    pasangan = cari_pasangan_tanpa_fide(conn) if a.tanpa_fide else cari_pasangan(conn)
    if not pasangan:
        print("Tidak ada pemain ganda yang terdeteksi.")
        return 0

    print(f"{len(pasangan)} pasangan terdeteksi"
          f"{'' if a.jalan else ' (lihat saja; tambahkan --jalan untuk menggabungkan)'}:\n")
    total = 0
    total_pgn = 0
    tertunda = 0
    for simpan, buang, alasan in pasangan:
        n = jumlah_partai(conn, buang["id"])
        total += n
        print(f"  {buang['nama']!r} (id {buang['id']}, fide {buang['fide_id'] or '-'}, {n} partai)")
        print(f"    -> {simpan['nama']!r} (id {simpan['id']}, fide {simpan['fide_id']}, "
              f"{jumlah_partai(conn, simpan['id'])} partai) — {alasan}")
        if a.jalan:
            try:
                dipindah, pgn_ok = gabungkan(conn, simpan["id"], buang["id"])
            except sqlite3.OperationalError as e:
                conn.rollback()
                tertunda += 1
                print(f"    DILEWATI: {e}. Database sedang dipakai penulis lain; "
                      "jalankan lagi setelah impor/analisis selesai.")
                continue
            total_pgn += pgn_ok
            print(f"    digabung; {pgn_ok} tag FIDE ID di PGN diperbaiki.")
    print(f"\nTotal {total} partai" + ((f" dipindahkan, {total_pgn} PGN diperbaiki.") if a.jalan else " akan dipindahkan.") +
          (f" {tertunda} pasangan tertunda karena database terkunci." if tertunda else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
