"""
Pembantu pembacaan PGN yang dipakai bersama modul impor.

Dipecah dari app/impor.py (900 baris, modul terbesar setelah analisa_web.py
dipecah — audit 25 Sep 2026). Isinya fungsi murni: membersihkan nilai tag,
menormalisasi nama, dan membaca nama turnamen dari header. Tidak menyentuh
database sama sekali, jadi bisa diuji tanpa menyiapkan apa pun.
"""

from __future__ import annotations

import re

# Gelar yang sering menempel di nama pemain dan harus dibuang saat
# menormalisasi, supaya "GM Carlsen, Magnus" dan "Carlsen, Magnus" dikenali
# sebagai orang yang sama.
GELAR = {"gm", "im", "fm", "cm", "wgm", "wim", "wfm", "wcm", "nm", "lm"}

# Nilai yang artinya "tidak diketahui" di PGN. Diperlakukan sebagai kosong,
# bukan disimpan apa adanya sebagai teks '?'.
KOSONG = {"", "?", "??", "-", "unknown", "n/a"}

# Event yang BUKAN turnamen: partai lepas di Lichess ("rated bullet game") dan
# Chess.com ("Live Chess"). Kalau dijadikan turnamen, tabel turnamen berisi
# baris seperti "rated bullet game / 2025" dengan ribuan partai yang tidak
# berarti apa-apa. Informasinya tidak hilang: kecepatan ada di kontrol_waktu,
# dan PGN utuh tetap tersimpan.
EVENT_GENERIK = re.compile(r"^(rated|casual)\s+\w+\s+game$|^live chess$", re.I)

# Event yang isinya label PAPAN atau placeholder, bukan nama turnamen.
# PGN siaran Lichess kadang menulis "Round 1: Vrolijk, Liam - Ernst, Sipke"
# di tag Event untuk sebagian papan dalam ronde yang SAMA, dan sebagian
# penyelenggara lupa mengganti "new tournament description". Ditemukan
# 25 Sep 2026: satu ronde Dutch Meesterklasse pecah jadi 18 baris turnamen,
# dan di seluruh database 3.362 baris turnamen berisi nama seperti ini,
# menyangkut 3.431 partai. Nama turnamen yang benar diberikan pemanggil
# (impor_broadcast.py tahu nama turnamennya dari API Lichess).
EVENT_PAPAN = re.compile(
    r"^round\s+\d+\s*(\|[^:]*)?\s*[:.]\s*\S.*\s-\s\S"
    r"|^new tournament description$", re.I)


def bersih(nilai: str | None) -> str | None:
    """Kembalikan None untuk nilai yang artinya 'tidak diketahui'."""
    if nilai is None:
        return None
    n = nilai.strip()
    return None if n.lower() in KOSONG else n


def eco_baku(nilai: str | None) -> str | None:
    """Kode ECO dalam huruf besar ('b90' -> 'B90'); None kalau tidak diketahui.

    Kode ECO memang huruf besar, tapi PGN menyimpannya apa adanya. Saringan
    ECO di daftar partai memakai rentang `eco >= ? AND eco < ?` (bisa memakai
    indeks; LIKE tidak) yang membedakan huruf besar-kecil, jadi `b90` akan
    diam-diam terlewat. Sejak skema v14 database juga menolak ECO yang tidak
    huruf besar (trigger), jadi jalur impor yang lupa memanggil fungsi ini
    gagal keras, bukan menyimpan diam-diam.
    """
    n = bersih(nilai)
    return n.upper() if n else n


def ke_int(nilai: str | None) -> int | None:
    n = bersih(nilai)
    if n is None:
        return None
    # Semua digit: \d{1,4} dulu memotong "12345" jadi 1234, angka yang tampak
    # masuk akal padahal karangan (audit 27 Sep 2026).
    m = re.match(r"^\s*(\d+)", n)
    return int(m.group(1)) if m else None


ELO_MAKS = 3500     # tidak ada rating FIDE maupun online setinggi ini


def ke_elo(nilai: str | None) -> int | None:
    """
    Elo dari tag PGN; 0 berarti TIDAK DIKETAHUI, bukan rating nol.

    Siaran Lichess menulis `[WhiteElo "0"]` untuk pemain tanpa rating. Disimpan
    sebagai 0, ia ikut merusak saringan Elo dan rata-rata Elo (audit 27 Sep:
    51 partai). Tidak ada rating FIDE atau online yang sungguhan 0.
    """
    n = ke_int(nilai)
    return n if n and n <= ELO_MAKS else None


def normalisasi_nama(nama: str) -> str:
    """
    Bentuk nama yang dipakai untuk mencocokkan pemain tanpa FIDE ID.

    'GM Carlsen, Magnus'  ->  'carlsen magnus'
    'Carlsen,M.'          ->  'carlsen m'
    """
    n = nama.lower()
    n = re.sub(r"[^\w\s]", " ", n)            # tanda baca jadi spasi
    kata = [k for k in n.split() if k not in GELAR]
    return " ".join(kata)


def turnamen_dari_header(h) -> tuple[str | None, str | None]:
    """
    Tentukan (nama turnamen, URL turnamen) dari header PGN.

    Tag ChessComTournament (ditambahkan ambil_partai.py, isinya URL API
    Chess.com) didahulukan, karena Event di Chess.com selalu "Live Chess".
    Namanya diambil dari ujung URL, mis. 'arena-kings-31036071'.

    Event generik (lihat EVENT_GENERIK) tidak dianggap turnamen.
    """
    url = bersih(h.get("ChessComTournament"))
    if url:
        return url.rstrip("/").rsplit("/", 1)[-1], url
    event = bersih(h.get("Event"))
    if not event or EVENT_GENERIK.match(event) or EVENT_PAPAN.match(event):
        return None, None
    return event, None


def tahun_dari_tanggal(tanggal: str | None) -> int | None:
    """Ambil tahun dari 'YYYY.MM.DD'. PGN membolehkan bagian tak diketahui
    ditulis '????', jadi kegagalan di sini normal dan bukan error."""
    t = bersih(tanggal)
    if not t:
        return None
    m = re.match(r"^(\d{4})", t)
    return int(m.group(1)) if m else None
