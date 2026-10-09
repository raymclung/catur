"""
Identitas pemain saat impor: mencocokkan nama ke baris `pemain` yang sudah ada,
atau membuat yang baru.

Dipecah dari app/impor.py (audit 25 Sep 2026). Aturan pentingnya tetap sama:
kalau ada FIDE ID, itu yang jadi identitas dan pemainnya ditandai
terverifikasi; kalau tidak ada, pencocokan lewat nama dan hasilnya TIDAK
dianggap pasti benar.
"""

from __future__ import annotations

import sqlite3

from .db import sekarang
from .impor_bantu import GELAR, KOSONG, bersih, ke_int, normalisasi_nama
from .repositori import pola_like


def _belakang_depan(nama: str):
    """
    ('Moroni, Luca Jr') -> ('moroni', ['luca', 'jr']).

    Batas nama belakang diambil dari **koma**, bukan dari posisi kata. Menebak
    lewat posisi salah untuk nama depan lebih dari satu kata: 'Moroni, Luca Jr'
    sempat terbaca sebagai nama belakang 'moroni luca'. Tanpa koma -> None,
    karena urutan kata tidak bisa dipastikan (username, 'Wei Yi').
    """
    if "," not in nama:
        return None
    belakang, depan = nama.split(",", 1)
    b = normalisasi_nama(belakang)
    d = normalisasi_nama(depan).split()
    if not b or not d:
        return None
    return b, d


def _depan_cocok(pendek: list[str], panjang: list[str]) -> bool:
    """
    Benar kalau `pendek` adalah singkatan dari `panjang`.

    Tiga bentuk singkatan yang dipakai TWIC, semuanya dari data nyata:

      'l'  -> ['luca', 'jr']            awalan kata pertama
      'hj' -> ['hjorvar', 'steinn']     awalan kata pertama, dua huruf
      'tl' -> ['tigran', 'l']           deret inisial

    Singkatan bernomor ('vl3', dipakai TWIC untuk membedakan dua orang senama)
    tidak diterima: melepas angkanya justru menyatukan yang sengaja dibedakan.
    """
    if pendek == panjang:
        return True
    if len(pendek) != 1:
        return False
    s = pendek[0]
    if not s.isalpha() or len(s) > 3:
        return False
    if panjang[0].startswith(s):
        return True
    if len(s) <= len(panjang) and all(panjang[i].startswith(s[i]) for i in range(len(s))):
        return True
    return False


def _cocok_lewat_inisial(conn: sqlite3.Connection, nama: str,
                         n_normal: str) -> int | None:
    """
    Cocokkan nama yang depannya disingkat dengan nama lengkap, atau sebaliknya.

    'Aronian,L' dan 'Aronian, Levon' adalah orang yang sama, tapi bentuk
    normalnya beda ('aronian l' vs 'aronian levon') sehingga pencocokan biasa
    gagal dan lahir pemain ganda. Diukur sebelum impor PGN Mentor: 499 nama
    akan menjadi duplikat, menyangkut 50 ribu kemunculan partai.

    Aturan sengaja ketat, karena menggabungkan dua orang berbeda jauh lebih
    merusak daripada membiarkan satu duplikat:

      * hanya untuk nama berkoma ('Belakang, Depan') di kedua sisi;
      * nama belakang sama persis setelah normalisasi;
      * satu sisi harus singkatan dari sisi lain (lihat _depan_cocok);
      * hanya diterima kalau calonnya **tepat satu**. 'Smith,J' yang bisa
        berarti John atau Jane dibiarkan jadi pemain baru.

    Kembalikan id pemain yang cocok, atau None.
    """
    # Partai konsultasi ('Anand,Viswanathan:Borgialli,Mihail') bukan satu
    # orang; dulu cocok lewat inisial ke 'Anand,V' (audit 27 Sep 2026).
    if ":" in nama or "/" in nama:
        return None
    pisah = _belakang_depan(nama)
    if pisah is None:
        return None
    belakang, depan = pisah

    calon = []
    lengkap_lain = []     # nama depan LENGKAP lain di marga yang sama
    for baris in conn.execute(
        "SELECT id, nama, nama_normal FROM pemain WHERE nama_normal LIKE ? ESCAPE '\\'",
        (pola_like(belakang + " ", awal=False),),
    ):
        pisah_lain = _belakang_depan(baris["nama"])
        if pisah_lain is None:
            continue
        belakang_lain, depan_lain = pisah_lain
        if belakang_lain != belakang:
            continue
        # yang lebih pendek hurufnya adalah calon singkatan
        a, b = (depan, depan_lain) if len(" ".join(depan)) <= len(" ".join(depan_lain))             else (depan_lain, depan)
        cocok = _depan_cocok(a, b)
        if cocok and depan != depan_lain:
            calon.append(baris["id"])
        elif not cocok and len(depan_lain[0]) > 3:
            lengkap_lain.append(depan_lain[0])

    if len(calon) != 1:
        return None
    # Nama depan lengkap yang hanya cocok dengan SINGKATAN ('Anand,Vignesh' ->
    # 'Anand,V'): singkatannya ambigu kalau marga ini juga dipakai nama lengkap
    # lain berawalan sama — sebagai baris sendiri ('Anand, Viswanathan') atau
    # di PGN partai baris singkatan itu. Dulu 49 partai Vignesh (Elo ~2030)
    # masuk ke 'Anand,V', yang kemudian digabung ke Vishy (audit 27 Sep 2026).
    if len(depan[0]) > 3:
        if any(l[0] == depan[0][0] and l != depan[0] for l in lengkap_lain):
            return None
        if any(_bentrok_depan(depan[0], l)
               for l in nama_depan_di_partai(conn, calon[0], belakang)):
            return None
    return calon[0]


def _bentrok_depan(a: str, b: str) -> bool:
    """Dua nama depan LENGKAP (> 3 huruf) yang tak satu pun awalan yang lain."""
    return len(a) > 3 and len(b) > 3 and not (a.startswith(b) or b.startswith(a))


def nama_depan_di_partai(conn: sqlite3.Connection, pemain_id: int, marga: str,
                         batas: int = 300) -> dict[str, str]:
    """
    Nama depan (pertama, dinormalisasi) -> nama asli, dari tag PGN partai
    `pemain_id` yang bermarga `marga`. Nama konsultasi (':' atau '/') dilewati.

    Baris bersingkatan ('Anand,V') bisa menampung partai beberapa orang; nama
    aslinya hanya tersisa di tag White/Black PGN-nya.
    """
    import re                                           # noqa: PLC0415
    hasil: dict[str, str] = {}
    for putih_id, awal in conn.execute(
            "SELECT putih_id, substr(pgn, 1, 600) FROM partai "
            "WHERE putih_id = ? OR hitam_id = ? LIMIT ?", (pemain_id, pemain_id, batas)):
        tag = "White" if putih_id == pemain_id else "Black"
        m = re.search(rf'\[{tag} "([^"]*)"\]', awal)
        if not m or ":" in m.group(1) or "/" in m.group(1):
            continue
        pisah = _belakang_depan(m.group(1))
        if pisah and pisah[0] == marga:
            hasil.setdefault(pisah[1][0], m.group(1))
    return hasil


def dapatkan_atau_buat_pemain(
    conn: sqlite3.Connection,
    nama: str,
    fide_id: str | None = None,
    gelar: str | None = None,
    negara: str | None = None,
    elo: int | None = None,
) -> int:
    """
    Cari pemain, buat kalau belum ada. Kembalikan id-nya.

    Urutan pencarian:
      1. Kalau ada FIDE ID -> cari berdasarkan itu. Identitas pasti.
      2. Kalau tidak -> cari berdasarkan nama yang dinormalisasi.

    Catatan tentang risiko yang disengaja: pencocokan lewat nama bisa keliru
    kalau ada dua orang berbeda dengan nama sama. Risiko itu diterima karena
    alternatifnya (membuat pemain baru tiap kali nama berulang) menghasilkan
    ratusan duplikat yang jauh lebih merepotkan. Yang penting, pemain hasil
    pencocokan nama TIDAK ditandai terverifikasi.
    """
    nama = nama.strip()
    n_normal = normalisasi_nama(nama)
    fid = bersih(fide_id)

    if fid:
        baris = conn.execute("SELECT id FROM pemain WHERE fide_id = ?", (fid,)).fetchone()
        if baris:
            _perbarui_pemain(conn, baris["id"], gelar, negara, elo)
            return baris["id"]

        # Mungkin pemain ini sudah pernah masuk lewat PGN tanpa FIDE ID.
        # Kalau ketemu berdasarkan nama, lengkapi datanya dan naikkan statusnya
        # jadi terverifikasi.
        baris = conn.execute(
            "SELECT id FROM pemain WHERE nama_normal = ? AND fide_id IS NULL",
            (n_normal,),
        ).fetchone()
        if baris:
            conn.execute(
                "UPDATE pemain SET fide_id = ?, terverifikasi = 1 WHERE id = ?",
                (fid, baris["id"]),
            )
            _perbarui_pemain(conn, baris["id"], gelar, negara, elo)
            return baris["id"]
    else:
        baris = conn.execute(
            "SELECT id FROM pemain WHERE nama_normal = ? ORDER BY id LIMIT 1",
            (n_normal,),
        ).fetchone()
        if baris:
            _perbarui_pemain(conn, baris["id"], gelar, negara, elo)
            return baris["id"]

        # Nama depan disingkat di satu sumber, lengkap di sumber lain:
        # PGN Mentor menulis 'Aronian,L', database punya 'Aronian, Levon'.
        id_inisial = _cocok_lewat_inisial(conn, nama, n_normal)
        if id_inisial is not None:
            _perbarui_pemain(conn, id_inisial, gelar, negara, elo)
            return id_inisial

    cur = conn.execute(
        "INSERT INTO pemain(nama, nama_normal, fide_id, negara, gelar, "
        "elo_terakhir, terverifikasi, dibuat_pada) VALUES (?,?,?,?,?,?,?,?)",
        (nama, n_normal, fid, bersih(negara), bersih(gelar), elo,
         1 if fid else 0, sekarang()),
    )
    return cur.lastrowid


def _perbarui_pemain(conn, pemain_id: int, gelar, negara, elo) -> None:
    """
    Lengkapi kolom yang masih kosong. Data yang sudah ada tidak ditimpa.

    Catatan: elo sengaja TIDAK diperbarui di sini. Kalau diperbarui tiap partai,
    nilai akhirnya berasal dari partai yang kebetulan diproses terakhir, bukan
    dari partai terbaru — dan urutan pemrosesan file tidak selalu urut waktu.
    Elo diisi belakangan lewat perbarui_elo_terakhir().
    """
    conn.execute(
        "UPDATE pemain SET "
        "  gelar = COALESCE(gelar, ?), "
        "  negara = COALESCE(negara, ?) "
        "WHERE id = ?",
        (bersih(gelar), bersih(negara), pemain_id),
    )


def tandai_akun_pribadi(conn: sqlite3.Connection, usernames: list[str],
                        kecualikan: list[str] = ()) -> int:
    """
    Tandai pemain yang namanya = username akun pemilik (permintaan #7).

    Dicocokkan lewat nama_normal dan hanya pemain TANPA FIDE ID — akun online
    tidak punya FIDE ID; kalau ada GM yang kebetulan bernama sama dan sudah
    terverifikasi, dia tidak ikut tertandai. `kecualikan` = username yang
    tandanya harus DICABUT (keputusan SESI.md bagian 6: RyuMcLungG bukan akun
    Ray walau partainya diunduh). Kembalikan jumlah pemain yang tertandai.
    """
    if kecualikan:
        normal = [normalisasi_nama(u) for u in kecualikan]
        conn.execute(
            f"UPDATE pemain SET akun_pribadi = 0 "
            f"WHERE nama_normal IN ({','.join('?' * len(normal))})",
            normal,
        )
    if usernames:
        normal = [normalisasi_nama(u) for u in usernames]
        conn.execute(
            f"UPDATE pemain SET akun_pribadi = 1 "
            f"WHERE nama_normal IN ({','.join('?' * len(normal))}) AND fide_id IS NULL",
            normal,
        )
    conn.commit()
    return conn.execute("SELECT COUNT(*) FROM pemain WHERE akun_pribadi = 1").fetchone()[0]


def perbarui_elo_terakhir(conn: sqlite3.Connection) -> int:
    """
    Isi kolom elo_terakhir dari partai TERBARU tiap pemain.

    Dijalankan sekali setelah impor selesai, bukan per partai.

    Yang perlu disadari saat membaca angkanya: satu pemain punya rating berbeda
    untuk tiap kontrol waktu (bullet, blitz, rapid). Kolom ini hanya menyimpan
    elo pada partai terakhir yang tercatat, apa pun kontrol waktunya. Jadi ini
    "elo di partai terakhir", bukan "rating pemain saat ini".
    """
    cur = conn.execute(
        """
        UPDATE pemain SET elo_terakhir = (
            SELECT elo FROM (
                SELECT putih_id AS pid, elo_putih AS elo, tanggal
                  FROM partai WHERE elo_putih IS NOT NULL
                UNION ALL
                SELECT hitam_id, elo_hitam, tanggal
                  FROM partai WHERE elo_hitam IS NOT NULL
            )
            WHERE pid = pemain.id
            ORDER BY tanggal DESC
            LIMIT 1
        )
        """
    )
    conn.commit()
    return cur.rowcount
