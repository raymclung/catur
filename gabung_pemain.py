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
Mode kedua `--tanpa-fide` menangani kasus PGN Mentor, yang sumbernya tidak
memuat FIDE ID sama sekali dan menyingkat nama depan ('Nakamura,Hi').
Di sana **bukti yang memutuskan, bukan jumlah calon** (25 Sep 2026): aturan
lama "calon harus tepat satu" justru gugur pada kasus terbesar, karena
calon keduanya sendiri duplikat ('Larsen,B' punya enam calon bernama
Larsen) sehingga 646 partai dilewati DIAM-DIAM. Sekarang tiap calon diuji
Elo lebih dulu, dan hanya digabung kalau tepat satu calon yang lolos.

Sisanya tidak disembunyikan: baris yang punya calon tapi tidak terbukti
dicetak berikut perintah siap tempel. Mayoritasnya partai lama tanpa tag
Elo, jadi memang tidak ada yang bisa diuji.

    python gabung_pemain.py --tanpa-fide            # lihat usul + yang ragu
    python gabung_pemain.py --tanpa-fide --ragu 0   # daftar ragu lengkap
"""

from __future__ import annotations

import argparse
import re
import sqlite3
import sys
from collections import defaultdict

from app.db import koneksi
from app.kunci_penulis import KunciPenulis, KunciTerpakai
from app.impor import (normalisasi_nama, _belakang_depan,
                       _depan_cocok as _depan_kata_cocok)

# Elo dua baris dianggap "orang yang sama" kalau selisihnya tidak lebih dari
# ini pada tahun yang sama. Rating seseorang bergerak pelan; dua orang senama
# yang berbeda biasanya terpaut jauh lebih besar.
SELISIH_ELO = 60

# Sumber partai yang berasal dari akun online pribadi. Baris pemain yang
# punya partai dari sini tidak pernah digabung tanpa FIDE ID: di situlah
# username hidup ('Cabe-pedas' vs 'Cabe-pedas165'), dan nama mirip di sana
# bukan bukti apa-apa. Partai OTB (twic/pgnmentor/lichess-broadcast) aman.
SUMBER_AKUN = ("lichess-user", "chesscom")

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


def _hanya_otb(conn: sqlite3.Connection, pemain_id: int) -> bool:
    """
    Semua partai baris ini dari sumber OTB (TWIC, PGN Mentor, broadcast)?

    Pemisah inilah yang membuat sesama baris tanpa FIDE ID boleh digabung.
    Audit 13 Sep menolaknya menyeluruh karena bukti Elo meloloskan
    'Cabe-pedas' + 'Cabe-pedas165' — dua username Lichess yang berbeda. Tapi
    username selalu datang dari partai akun pribadi (`lichess-user`,
    `chesscom`), sedangkan 'Larsen,B' dan 'Larsen, Bent' 100% `pgnmentor`.
    Jadi yang dilarang bukan "tanpa FIDE ID", melainkan "pernah muncul sebagai
    akun online" — dan 2.268 partai Bent Larsen tidak lagi jadi korban aturan
    yang sebenarnya mengincar username.
    """
    tanda = ",".join("?" * len(SUMBER_AKUN))
    baris = conn.execute(
        "SELECT COUNT(*) n, "
        "       SUM(CASE WHEN sumber IN (" + tanda + ") THEN 1 ELSE 0 END) akun "
        "FROM partai WHERE putih_id = ? OR hitam_id = ?",
        (*SUMBER_AKUN, pemain_id, pemain_id),
    ).fetchone()
    return bool(baris["n"]) and not baris["akun"]


def _pernah_berhadapan(conn: sqlite3.Connection, a: int, b: int) -> bool:
    """
    Dua baris yang pernah saling berhadapan pasti dua orang berbeda.

    Pengaman negatif yang murah: satu partai cukup membatalkan pencalonan,
    tanpa perlu menimbang Elo atau nama sama sekali.
    """
    return conn.execute(
        "SELECT 1 FROM partai WHERE (putih_id = ? AND hitam_id = ?) "
        "OR (putih_id = ? AND hitam_id = ?) LIMIT 1", (a, b, b, a)).fetchone() is not None


def _tafsir_nama(nama: str, nama_normal: str) -> set:
    """
    Semua tafsir (nama belakang, nama depan) yang masuk akal untuk satu baris.

    Kenapa lebih dari satu: batas nama belakang tidak selalu bisa dipastikan.
    `_pisah()` menebaknya dari posisi kata, dan itu salah untuk nama depan yang
    lebih dari satu kata — 'Short, Nigel D' terbaca bermarga 'short nigel',
    sehingga tidak pernah bertemu 'Short,N' dan 83 partainya terdampar. Koma
    memberi jawaban pasti kalau ada; kalau tidak ada ('Narayanan S L'), kedua
    tebakan posisi dipakai sekaligus. Salah tafsir tidak berbahaya di sini:
    tafsir hanya melahirkan CALON, dan yang memutuskan tetap bukti Elo.
    """
    hasil = set()
    bd = _belakang_depan(nama)
    if bd:
        hasil.add((bd[0], tuple(bd[1])))
    kata = nama_normal.split()
    if len(kata) >= 2:
        hasil.add((" ".join(kata[:-1]), (kata[-1],)))
        hasil.add((kata[0], tuple(kata[1:])))
    return hasil


def _bernomor_twic(depan: tuple) -> bool:
    """Singkatan nama depan yang diberi angka penanda: ('vl3',), ('and1',)."""
    return len(depan) == 1 and bool(re.search(r"\d+$", depan[0]))


def _depan_mungkin_sama(a: tuple, b: tuple) -> bool:
    """
    Satu sisi singkatan dari sisi lain; sama persis tidak dihitung.

    Sesama singkatan bernomor ('and1' terhadap 'and2') TETAP dicalonkan di
    sini, walau tidak boleh digabung: kehadirannya sebagai calon itulah yang
    membuat 'Sokolov,And1' terbaca ambigu dan karena itu ditolak. Larangan
    menggabungkannya ada di `_boleh_bertahan()` — menyingkirkannya dari
    pencalonan justru MENYEMBUNYIKAN keraguan, bukan menyelesaikannya.
    """
    if a == b:
        return False
    if _depan_kata_cocok(list(a), list(b)) or _depan_kata_cocok(list(b), list(a)):
        return True
    # Singkatan dua huruf PGN Mentor ('Nakamura,Hi') dan penanda angka TWIC
    # ('Fedoseev,Vl3'): keduanya satu kata, jadi cukup dibandingkan sebagai
    # awalan setelah angkanya dilepas.
    if len(a) == 1 and len(b) >= 1:
        ia, ib = _tanpa_penanda_twic(a[0]), _tanpa_penanda_twic(b[0])
        return bool(ia and ib and (ib.startswith(ia) or ia.startswith(ib)))
    return False


def _keduanya_bernomor(a, b) -> bool:
    """Kedua baris punya singkatan nama depan bernomor, pada tafsir mana pun."""
    def bernomor(baris):
        return any(_bernomor_twic(depan)
                   for _, depan in _tafsir_nama(baris["nama"], baris["nama_normal"]))
    return bernomor(a) and bernomor(b)


def _boleh_bertahan(conn: sqlite3.Connection, buang, simpan) -> bool:
    """
    Boleh `simpan` yang bertahan dan `buang` yang dihapus?

    FIDE ID selalu menang. Tanpa itu, yang bertahan harus jelas lebih lengkap
    DAN lebih banyak partainya — kalau tidak, penggabungan bisa berjalan ke
    arah yang salah ('Larsen, Bent' dilebur ke 'Larsen,B') atau membentuk
    rantai dua arah yang saling menghapus.
    """
    # Angka penanda TWIC berarti "dua orang senama yang BERBEDA". Jadi dua baris
    # yang KEDUANYA bernomor tidak pernah boleh dilebur satu ke yang lain, walau
    # Elo-nya berdekatan: 'Biolek,R1' dan 'Biolek,R2' memang dua orang. Diukur
    # 25 Sep 2026: 6 pasangan seperti ini saling jadi calon, dan semuanya lolos
    # hanya karena belum ada tag Elo yang membuktikan.
    if _keduanya_bernomor(buang, simpan):
        return False
    if simpan["fide_id"]:
        return True
    return (len(simpan["nama_normal"]) > len(buang["nama_normal"])
            and jumlah_partai(conn, simpan["id"]) > jumlah_partai(conn, buang["id"]))


def cari_pasangan_tanpa_fide(conn: sqlite3.Connection, ragu: list | None = None):
    """
    Baris tanpa FIDE ID yang sebenarnya orang yang sama dengan baris lain.
    Ini kasus impor PGN Mentor: sumbernya tidak memuat FIDE ID sama sekali dan
    nama depannya disingkat, sehingga lahir baris kedua.

    Beda dari cari_pasangan(): di sana kedua baris punya FIDE ID yang berbeda
    (sumber salah). Di sini yang dibuang tidak punya FIDE ID sama sekali.

    Yang DIPERTAHANKAN selalu baris ber-FIDE ID kalau ada, walau partainya
    lebih sedikit — FIDE ID satu-satunya penanda identitas yang pasti. Kalau
    tidak ada yang ber-FIDE ID, baris yang namanya lebih lengkap dipertahankan,
    dan itu hanya diizinkan untuk baris yang 100% OTB (lihat `_hanya_otb`).

    **Yang memutuskan adalah bukti, bukan jumlah calon** (diubah 25 Sep 2026).
    Aturan lama "calon harus tepat satu" gugur justru pada kasus terbesar:
    'Larsen,B' punya enam calon bernama Larsen, 'Short,N' punya dua (satu di
    antaranya nama rusak 'Short,Nigel D:Baptie,Justin'), jadi keduanya
    dilewati diam-diam walau Elo-nya jelas menunjuk satu orang. Sekarang tiap
    calon diuji Elo lebih dulu, dan penggabungan diusulkan hanya kalau **tepat
    satu calon yang lolos bukti**. Dua calon berbukti ('Sokolov,And1' terhadap
    'Sokolov, Andrei' dan 'Sokolov,And2') tetap ditolak.

    Pengaman yang harus lolos semua:
      * nama belakang sama pada salah satu tafsir (lihat `_tafsir_nama`);
      * nama depan: satu singkatan/awalan dari yang lain;
      * tidak pernah saling berhadapan di papan;
      * calon tanpa FIDE ID hanya boleh kalau kedua baris 100% OTB;
      * Elo keduanya berdekatan di tahun yang sama, dan hanya satu calon yang
        memenuhinya.

    `ragu`, kalau diberikan, diisi `(baris, calon, jumlah_berbukti)` untuk
    setiap baris yang punya calon tapi tidak diputuskan. Batas yang tercetak
    bukan batas yang disembunyikan (CLAUDE.md bagian 1): mayoritas sisa ini
    adalah baris PGN Mentor lama yang tidak memuat Elo sama sekali, sehingga
    tidak ada bukti yang bisa diuji.
    """
    semua = conn.execute(
        "SELECT id, nama, nama_normal, fide_id, gelar, negara, elo_terakhir FROM pemain"
    ).fetchall()

    indeks = defaultdict(list)
    tafsir = {}
    for r in semua:
        tafsir[r["id"]] = _tafsir_nama(r["nama"], r["nama_normal"])
        for belakang, depan in tafsir[r["id"]]:
            indeks[belakang].append((r, depan))

    elo_cache: dict = {}

    def elo(pid: int) -> dict:
        if pid not in elo_cache:
            elo_cache[pid] = _elo_per_tahun(conn, pid)
        return elo_cache[pid]

    hasil = []
    tertunda: list = []
    for r in semua:
        if r["fide_id"] or not jumlah_partai(conn, r["id"]):
            continue
        calon = {}
        for belakang, depan in tafsir[r["id"]]:
            for lain, depan_lain in indeks.get(belakang, []):
                if lain["id"] == r["id"] or lain["id"] in calon:
                    continue
                if not _depan_mungkin_sama(depan, depan_lain):
                    continue
                if _pernah_berhadapan(conn, r["id"], lain["id"]):
                    continue
                if not lain["fide_id"] and not (_hanya_otb(conn, r["id"])
                                                and _hanya_otb(conn, lain["id"])):
                    continue          # username online: jangan pernah digabung
                calon[lain["id"]] = lain
        if not calon:
            continue
        berbukti = [(lain, _elo_tumpang_tindih(elo(lain["id"]), elo(r["id"])))
                    for lain in calon.values()]
        berbukti = [(lain, th) for lain, th in berbukti if th]
        if len(berbukti) == 1 and _boleh_bertahan(conn, r, berbukti[0][0]):
            lain, th = berbukti[0]
            alasan = ("tanpa FIDE ID, Elo berdekatan pada " + th) if lain["fide_id"] else (
                "keduanya tanpa FIDE ID tapi 100% OTB, Elo berdekatan pada " + th)
            hasil.append((lain, r, alasan))
        elif ragu is not None:
            tertunda.append((r, sorted(calon.values(),
                                       key=lambda x: -jumlah_partai(conn, x["id"])),
                             len(berbukti)))

    hasil.sort(key=lambda x: -jumlah_partai(conn, x[1]["id"]))
    if ragu is not None:
        # Pasangan yang sudah diusulkan tidak boleh muncul lagi sebagai
        # "ragu" dari arah sebaliknya: 'Larsen, Bent' sempat tercetak
        # seolah dialah yang perlu dilebur ke 'Larsen,B'.
        sudah = {frozenset((s["id"], b["id"])) for s, b, _ in hasil}
        # Baris yang sudah pasti dilebur tidak boleh ditawarkan lagi:
        # sarannya akan menunjuk id yang keburu dihapus.
        lenyap = {b["id"] for _, b, _ in hasil}
        for baris, calon_baris, jumlah_bukti in tertunda:
            if baris["id"] in lenyap:
                continue
            sisa = [c for c in calon_baris
                    if frozenset((baris["id"], c["id"])) not in sudah
                    and c["id"] not in lenyap]
            if sisa:
                ragu.append((baris, sisa, jumlah_bukti))
        ragu.sort(key=lambda x: -jumlah_partai(conn, x[0]["id"]))
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


def nama_bentrok(conn: sqlite3.Connection, id_simpan: int, id_buang: int) -> list[str]:
    """
    Nama di PGN partai `id_buang` yang jelas orang lain dari `id_simpan`.

    Baris bersingkatan ('Anand,V') bisa menampung partai beberapa orang lewat
    pencocokan inisial saat impor. Menggabungkannya ke nama lengkap membawa
    orang lain ikut: 49 partai 'Anand,Vignesh' (Elo ~2030) jadi milik Vishy
    Anand (audit 27 Sep 2026). Bentrok = marga sama, kedua nama depan lengkap
    (> 3 huruf), dan tidak satu pun awalan yang lain. Ejaan lain orang yang
    sama ('Vassily'/'Vasyl') tetap bentrok menurut aturan ini, jadi hasilnya
    menolak, bukan menggabungkan diam-diam: yang ragu dicek manusia.
    """
    from app.impor_pemain import (_belakang_depan, _bentrok_depan,   # noqa: PLC0415
                                  nama_depan_di_partai)
    simpan = conn.execute("SELECT nama FROM pemain WHERE id = ?", (id_simpan,)).fetchone()
    pisah_simpan = _belakang_depan(simpan["nama"]) if simpan else None
    if not pisah_simpan:
        return []
    marga, depan = pisah_simpan[0], pisah_simpan[1][0]
    return sorted(asli for d, asli in nama_depan_di_partai(conn, id_buang, marga, batas=100000).items()
                  if _bentrok_depan(d, depan))


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


def cetak_ragu(conn: sqlite3.Connection, ragu: list, batas: int = 15) -> None:
    """
    Cetak baris yang punya calon tapi tidak bisa dibuktikan.

    Sengaja selalu dicetak, walau tidak ada satu pun pasangan yang lolos:
    sebelum ini tool menjawab "Tidak ada pemain ganda yang terdeteksi" pada
    database yang memuat ratusan baris kembar, dan diam itu terbaca sebagai
    "sudah bersih". Tiap calon diikuti perintah siap tempel, supaya Ray bisa
    memutuskan satu per satu tanpa melonggarkan pengamannya.
    """
    if not ragu:
        return
    nyasar = sum(jumlah_partai(conn, r["id"]) for r, _, _ in ragu)
    print("\n" + f"{len(ragu)} baris punya calon tapi TIDAK dibuktikan "
          f"({nyasar} partai masih terpisah).")
    print("Sebagian besar partai PGN Mentor lama tidak memuat Elo, jadi tidak "
          "ada yang bisa diuji.")
    tampil = ragu if batas <= 0 else ragu[:batas]
    for baris, calon, jumlah_bukti in tampil:
        n = jumlah_partai(conn, baris["id"])
        if jumlah_bukti == 0:
            sebab = "tidak ada calon yang Elo-nya membuktikan"
        elif jumlah_bukti == 1:
            sebab = "satu calon berbukti, tapi arah penggabungannya tidak sah"
        else:
            sebab = f"{jumlah_bukti} calon sama-sama berbukti"
        print("\n" + f"  {baris['nama']!r} (id {baris['id']}, {n} partai) — {sebab}:")
        for c in calon[:4]:
            print(f"      id {c['id']:6} {c['nama']!r} "
                  f"({jumlah_partai(conn, c['id'])} partai, fide {c['fide_id'] or '-'})")
            # Arah ditentukan aturan yang sama dengan penggabungan otomatis:
            # FIDE ID menang, kalau tidak ada yang partainya lebih banyak.
            # Perintah terbalik akan menghapus baris yang justru penuh.
            if _boleh_bertahan(conn, baris, c):
                buang, simpan = baris, c
            elif _boleh_bertahan(conn, c, baris):
                buang, simpan = c, baris
            else:
                print("        (arah tidak jelas — periksa sendiri dulu)")
                continue
            print(f"        python gabung_pemain.py --id {buang['id']} "
                  f"{simpan['id']} --jalan"
                  f"   # {buang['nama']!r} -> {simpan['nama']!r}")
    if batas > 0 and len(ragu) > batas:
        print("\n" + f"  ... {len(ragu) - batas} baris lagi; "
              "tambahkan --ragu 0 untuk semua.")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--jalan", action="store_true", help="benar-benar gabungkan (bawaan: lihat saja)")
    p.add_argument("--id", nargs=2, type=int, metavar=("BUANG", "SIMPAN"),
                   help="gabungkan pasangan ini tanpa deteksi")
    p.add_argument("--tanpa-fide", action="store_true",
                   help="mode kedua: baris TANPA FIDE ID yang sama orangnya dengan "
                        "baris terverifikasi (kasus impor PGN Mentor)")
    p.add_argument("--ragu", type=int, default=15, metavar="N",
                   help="berapa baris ragu yang dicetak (0 = semua, bawaan 15)")
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

    ragu: list = []
    pasangan = (cari_pasangan_tanpa_fide(conn, ragu) if a.tanpa_fide
                else cari_pasangan(conn))
    if not pasangan and not ragu:
        print("Tidak ada pemain ganda yang terdeteksi.")
        return 0
    if not pasangan:
        print("Tidak ada pasangan yang bisa dibuktikan.")
        cetak_ragu(conn, ragu, a.ragu)
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
        bentrok = nama_bentrok(conn, simpan["id"], buang["id"])
        if bentrok:
            print(f"    DITOLAK: partainya memuat nama depan lain ({', '.join(bentrok[:3])}); "
                  "pisahkan dulu, atau gabungkan manual dengan --id kalau memang orang yang sama.")
            total -= n
            continue
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
    cetak_ragu(conn, ragu, a.ragu)
    return 0


if __name__ == "__main__":
    sys.exit(main())
