"""
Isi gelar lawan dari akun online, yang tidak pernah datang lewat PGN.

Kenapa perlu (temuan Ray, 25 Sep 2026): `CLAUDE.md` bagian 11 menetapkan
partai pribadi dipakai untuk analisis dan latihan kalau **lawan ber-rating di
atas 2200 — atau lawannya bergelar, berapa pun ratingnya**. Bagian kedua itu
hampir tidak pernah menyala, dan diamnya tidak menimbulkan error apa pun.

Sebabnya BUKAN penangkapan gelar yang rusak. Itu sudah benar: diukur 25 Sep
2026, **nol** pemain tanpa gelar yang partainya memuat tag gelar.

Dan setelah diukur, masalahnya ternyata hanya di SATU sumber:

  * **Lichess sudah lengkap.** Ekspor PGN-nya membawa `[WhiteTitle]`, dan
    ke-212 lawan bergelar punya tag itu di **100%** partainya (21/21, 10/10,
    7/7, ...). Dari **300 lawan dengan rating tertinggi** yang gelarnya kosong,
    `POST /api/users` menjawab 300 pemain dan **nol** di antaranya bergelar —
    termasuk yang bullet-nya 2964 dan 2859. Mereka memang tidak bergelar.
  * **Chess.com buta total.** **0 dari 1.508** partainya punya tag gelar,
    padahal dari 40 lawan teratas, **20 bergelar** (IM, FM, CM, NM). Di sinilah
    aturan "lawan bergelar tetap ikut" benar-benar tidak menyala.

Jadi yang perlu ditanya ke API hanya Chess.com. Sisa 6.142 lawan Lichess yang
belum ditanya dibiarkan, dan itu batas yang dicatat, bukan disembunyikan:
`python -m app.gelar_akun --sumber lichess --jalan` menyelesaikannya kalau
Ray mau kepastian penuh (±22 permintaan, hati-hati batas laju).

    python -m app.gelar_akun                 # lihat saja
    python -m app.gelar_akun --jalan
    python -m app.gelar_akun --sumber lichess --jalan

Dua hal yang dijaga, dan keduanya soal kebenaran, bukan kerapian:

  * **Hanya gelar FIDE yang disimpan.** Lichess juga mengembalikan `LM`
    (Lichess Master — penghargaan situs, bukan gelar FIDE) dan `BOT`. Kalau
    `BOT` ikut tersimpan sebagai gelar, partai lawan komputer akan terhitung
    "lawan bergelar" dan ikut jadi bahan latihan — persis kebalikan dari yang
    diminta aturannya.
  * **Gelar yang sudah ada tidak pernah ditimpa.** Yang datang dari TWIC dan
    broadcast lebih dekat ke sumber resmi daripada profil akun online.
"""

from __future__ import annotations

import json
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request

# Gelar FIDE yang sah. Sengaja daftar putih, bukan daftar hitam: nilai baru
# dari API (situs bisa menambah penanda kapan saja) ditolak secara bawaan,
# bukan diam-diam masuk sebagai gelar.
GELAR_FIDE = ("GM", "IM", "FM", "CM", "NM", "WGM", "WIM", "WFM", "WCM", "WNM")

# Ditolak walau API menyebutnya "title": LM penghargaan Lichess, BOT justru
# penanda bahwa lawannya bukan manusia.
GELAR_DITOLAK = ("LM", "BOT")

USER_AGENT = "catur-analysis/0.1 (belajar catur pribadi; +https://github.com/raymclung/catur)"

# Lichess menerima sampai 300 username per permintaan pada /api/users.
BATAS_BATCH_LICHESS = 300

# Jeda antar batch. 1,5 detik ternyata terlalu rapat: /api/users menjawab 429
# pada permintaan pertama setelah beberapa panggilan beruntun, lalu menahan
# lebih dari tiga menit (diukur 25 Sep 2026). 22 batch x 4 detik hanya 1,5
# menit, jadi tidak ada gunanya mepet.
JEDA_LICHESS = 4.0
JEDA_CHESSCOM = 0.8     # detik antar pemain (tidak ada endpoint massal)
JEDA_429 = 65.0

# Percobaan ulang khusus 429 dihitung terpisah dari kegagalan biasa: kena batas
# laju bukan kerusakan, cuma perlu sabar. Tanpa pemisahan ini satu 429 memakan
# jatah percobaan yang seharusnya untuk gangguan jaringan.
PERCOBAAN_429 = 5


class GelarError(RuntimeError):
    pass


def _bersihkan_gelar(mentah) -> str | None:
    """Gelar FIDE yang sah, atau None. Lihat GELAR_DITOLAK."""
    g = (mentah or "").strip().upper()
    if not g or g in GELAR_DITOLAK:
        return None
    return g if g in GELAR_FIDE else None


def lawan_tanpa_gelar(conn: sqlite3.Connection, sumber: str) -> list[tuple[int, str]]:
    """
    (id, nama) lawan Ray di `sumber` yang gelarnya masih kosong.

    Lawan saja, bukan semua pemain: nama di baris ini adalah username akun,
    dan hanya username yang bisa ditanyakan ke API situsnya.

    Diurutkan dari rating TERTINGGI. Permintaan ke API dibatasi laju, jadi
    urutan menentukan seberapa cepat jawabannya berguna: pemain bergelar
    hampir selalu ada di puncak daftar rating, sehingga `--batas 300` menjawab
    "adakah gelar yang terlewat" dengan satu permintaan, bukan dua puluh dua.
    """
    q = """
        SELECT lawan.id, lawan.nama,
               MAX(CASE WHEN g.putih_id = saya.id THEN g.elo_hitam ELSE g.elo_putih END) elo
        FROM partai g
        JOIN pemain saya  ON saya.id IN (g.putih_id, g.hitam_id) AND saya.akun_pribadi = 1
        JOIN pemain lawan ON lawan.id = CASE WHEN g.putih_id = saya.id
                                             THEN g.hitam_id ELSE g.putih_id END
        WHERE g.sumber = ?
          AND (lawan.gelar IS NULL OR lawan.gelar = '')
          AND lawan.akun_pribadi = 0
        GROUP BY lawan.id
        ORDER BY elo DESC NULLS LAST, lawan.id
    """
    return [(r["id"], r["nama"]) for r in conn.execute(q, (sumber,))]


def _minta(url: str, data: bytes | None = None, percobaan: int = 3,
           tidur=time.sleep):
    """
    Satu permintaan JSON, dengan penanganan 429 seperti ambil_partai.py.

    Sebab kegagalan terakhir ikut dibawa ke pesan error. Versi pertama hanya
    menulis "gagal setelah 3 percobaan", dan saat Lichess menjawab 429 pesan
    itu tidak memberi petunjuk apa pun — padahal jawabannya cuma "tunggu".
    """
    sebab = "tidak diketahui"
    sisa_429 = PERCOBAAN_429
    ke = 0
    while ke < percobaan:
        req = urllib.request.Request(
            url, data=data,
            headers={"User-Agent": USER_AGENT,
                     **({"Content-Type": "text/plain"} if data else {})})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code == 429:
                sebab = "HTTP 429 (batas laju)"
                sisa_429 -= 1
                if sisa_429 <= 0:
                    break
                tidur(JEDA_429)
                continue          # 429 tidak memakan jatah percobaan biasa
            if e.code == 404:
                return None            # pemain tidak ada / akun ditutup
            sebab = f"HTTP {e.code}"
            ke += 1
            if ke >= percobaan:
                break
            tidur(3 * ke)
        except Exception as e:
            sebab = f"{type(e).__name__}: {e}"
            ke += 1
            if ke >= percobaan:
                break
            tidur(3 * ke)
    raise GelarError(f"{url}: menyerah setelah {sebab}")


def gelar_lichess(username: list[str], jeda: float = JEDA_LICHESS,
                  minta=_minta, pada_temuan=None, lapor=None) -> dict[str, str]:
    """
    {username_kecil: gelar} untuk yang bergelar FIDE. Yang tidak bergelar
    sengaja TIDAK muncul di hasil, supaya pemanggil tidak menulis string kosong.

    `POST /api/users` menerima 300 username sekaligus, jadi 6.442 lawan cukup
    ±22 permintaan — bukan 6.442. Lihat catatan JEDA_LICHESS soal batas laju.
    """
    hasil: dict[str, str] = {}
    for i in range(0, len(username), BATAS_BATCH_LICHESS):
        potongan = username[i:i + BATAS_BATCH_LICHESS]
        jawab = minta("https://lichess.org/api/users",
                      ",".join(potongan).encode("utf-8"))
        for u in jawab or []:
            g = _bersihkan_gelar(u.get("title"))
            if g:
                nama = (u.get("id") or u.get("username") or "").lower()
                hasil[nama] = g
                if pada_temuan:
                    pada_temuan(nama, g)
        if lapor:
            lapor(min(i + BATAS_BATCH_LICHESS, len(username)), len(username))
        if i + BATAS_BATCH_LICHESS < len(username):
            time.sleep(jeda)
    return hasil


def gelar_chesscom(username: list[str], jeda: float = JEDA_CHESSCOM,
                   minta=_minta, pada_temuan=None, lapor=None) -> dict[str, str]:
    """
    {username_kecil: gelar}. Chess.com tidak punya endpoint massal, jadi satu
    permintaan per pemain dengan jeda — 585 lawan ≈ 8 menit.

    `pada_temuan` dipanggil begitu satu gelar ketemu, bukan di akhir. Versi
    pertama mengumpulkan semuanya dulu lalu menulis sekali: delapan menit
    permintaan jaringan hilang seluruhnya kalau prosesnya dihentikan di menit
    ketujuh. `lapor` dipanggil tiap pemain, supaya perintah selama ini tidak
    diam total.
    """
    hasil: dict[str, str] = {}
    for ke, u in enumerate(username):
        jawab = minta("https://api.chess.com/pub/player/"
                      + urllib.parse.quote(u.lower()))
        g = _bersihkan_gelar((jawab or {}).get("title"))
        if g:
            hasil[u.lower()] = g
            if pada_temuan:
                pada_temuan(u.lower(), g)
        if lapor:
            lapor(ke + 1, len(username))
        if ke + 1 < len(username):
            time.sleep(jeda)
    return hasil


SUMBER = {
    "lichess": ("lichess-user", gelar_lichess),
    "chesscom": ("chesscom", gelar_chesscom),
}


def isi_gelar(conn: sqlite3.Connection, sumber: str, jalan: bool = False,
              batas: int = 0, ambil=None, lapor=None) -> list[tuple[int, str, str]]:
    """
    Cari gelar lawan di satu sumber. Kembalikan (id, nama, gelar) yang ketemu.

    `COALESCE` di UPDATE: gelar yang sudah ada tidak pernah ditimpa, walau
    fungsi ini hanya memilih baris yang kosong — sesi lain bisa mengisinya di
    sela-sela permintaan jaringan yang memakan menit.
    """
    kunci_sumber, bawaan = SUMBER[sumber]
    ambil = ambil or bawaan
    lawan = lawan_tanpa_gelar(conn, kunci_sumber)
    if batas:
        lawan = lawan[:batas]
    if not lawan:
        return []

    per_nama = {nama.lower(): pid for pid, nama in lawan}
    hasil: list = []

    def simpan(nama_kecil, gelar):
        pid = per_nama.get(nama_kecil)
        if pid is None:
            return             # situs mengembalikan nama yang tidak kita tanya
        hasil.append((pid, nama_kecil, gelar))
        if jalan:
            # Ditulis dan di-commit SEGERA, bukan ditumpuk sampai akhir:
            # delapan menit permintaan jaringan tidak boleh hilang seluruhnya
            # kalau prosesnya dihentikan di menit ketujuh.
            conn.execute(
                "UPDATE pemain SET gelar = COALESCE(NULLIF(gelar, ''), ?) WHERE id = ?",
                (gelar, pid))
            conn.commit()

    ambil([nama for _, nama in lawan], pada_temuan=simpan, lapor=lapor)
    return hasil


def main() -> int:
    import argparse

    from .db import koneksi

    p = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    p.add_argument("--sumber", choices=sorted(SUMBER) + ["semua"], default="semua")
    p.add_argument("--jalan", action="store_true",
                   help="benar-benar tulis gelar (bawaan: lihat saja)")
    p.add_argument("--batas", type=int, default=0,
                   help="olah maksimal sekian lawan per sumber (untuk mencoba)")
    p.add_argument("--db")
    a = p.parse_args()

    conn = koneksi(a.db)
    pilihan = sorted(SUMBER) if a.sumber == "semua" else [a.sumber]
    total = 0
    for s in pilihan:
        kunci, _ = SUMBER[s]
        belum = len(lawan_tanpa_gelar(conn, kunci))
        print(f"{s}: {belum} lawan tanpa gelar"
              + (f" (dibatasi {a.batas})" if a.batas else ""))
        if not belum:
            continue
        def lapor(sudah, total):
            if sudah == total or sudah % 50 == 0:
                print(f"    {sudah}/{total}", flush=True)

        hasil = isi_gelar(conn, s, jalan=a.jalan, batas=a.batas, lapor=lapor)
        for _, nama, gelar in sorted(hasil, key=lambda x: x[2]):
            print(f"    {gelar:4} {nama}")
        total += len(hasil)
        print(f"  -> {len(hasil)} bergelar"
              + ("" if a.jalan else " (lihat saja; tambahkan --jalan untuk menulis)"))
    print(f"\nTotal {total} gelar ditemukan.")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
