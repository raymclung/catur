"""
Sidik jari partai: penjaga duplikat impor.

Dipecah dari app/impor.py (audit 25 Sep 2026). Tiap partai dihitung sidik
jarinya dari ISI partai itu sendiri, lalu database yang menolak kalau sidik
jarinya sudah ada — jadi impor boleh dijalankan berulang kali, termasuk untuk
turnamen yang masih berlangsung. Di sini juga ada penghitungan ulang hash
(kalau rumusnya berubah) dan penelusuran jejak asal berkas PGN.
"""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

import chess
import chess.pgn

from .impor_bantu import bersih, ke_int, normalisasi_nama
from .sumber_impor import catat as catat_sumber, sudah_selesai


def nama_belakang(nama: str) -> str:
    """
    Nama belakang yang dinormalisasi, untuk sidik jari partai.

    Sumber berbeda menulis orang yang sama dengan panjang berbeda:
    Lichess 'Anand, Viswanathan', TWIC 'Anand,V'. Nama lengkap yang
    dinormalisasi tetap beda ('anand viswanathan' vs 'anand v'), tapi nama
    belakangnya sama. Kalau ada koma, nama belakang = bagian sebelum koma;
    kalau tidak (username, atau 'Pranesh M'), = kata pertama.
    """
    n = nama.split(",")[0] if "," in nama else nama
    kata = normalisasi_nama(n).split()
    return kata[0] if kata else ""


def hash_partai(putih: str, hitam: str, tanggal: str | None,
                hasil: str, langkah_uci: str) -> str:
    """
    Sidik jari sebuah partai.

    Dihitung dari isi partainya — nama belakang kedua pemain, tanggal, hasil,
    dan seluruh rangkaian langkah. Sengaja TIDAK memakai teks PGN mentah
    (spasi/komentar/urutan tag bisa beda untuk partai yang sama) dan TIDAK
    memakai nama lengkap (lihat nama_belakang: TWIC menyingkat nama depan,
    sehingga 14 partai GCL 2026 sempat lolos sebagai "baru" padahal sudah ada
    dari broadcast Lichess).

    Kalau fungsi ini berubah, semua hash lama harus dihitung ulang lewat
    hitung_ulang_hash() — kalau tidak, partai lama tidak lagi terdeteksi
    sebagai duplikat.
    """
    bahan = "|".join([
        nama_belakang(putih),
        nama_belakang(hitam),
        bersih(tanggal) or "",
        hasil,
        langkah_uci,
    ])
    return hashlib.sha256(bahan.encode("utf-8")).hexdigest()



# Kembar lintas sumber. hash_partai() memuat TANGGAL, dan sumber berbeda
# menulis tanggal berbeda untuk partai yang sama: siaran Lichess memberi semua
# ronde Candidates 2026 tanggal mulai turnamen (2 Mar), TWIC tanggal rondenya
# (sampai 15 Apr). Audit 27 Sep 2026: 500-an partai tersimpan dua kali.
# Tanpa batas tanggal pun salah: So-Vachier-Lagrave memainkan 25 langkah yang
# persis sama di blitz 2023, 2024, dan 2026 — tiga partai berbeda.
KEMBAR_HARI = 3             # selisih yang selalu dianggap partai yang sama
KEMBAR_HARI_PANJANG = 45    # ... untuk partai panjang (lihat KEMBAR_MIN_LANGKAH)
KEMBAR_MIN_LANGKAH = 16     # garis pendek bisa berulang; > 15 langkah identik tidak


def _tanggal(s: str | None):
    import datetime                                       # noqa: PLC0415
    try:
        return datetime.date(*map(int, (s or "").split(".")))
    except (ValueError, TypeError):
        return None


def tanggal_kembar(a: str | None, b: str | None, jumlah_langkah: int) -> bool:
    """Apakah dua tanggal cukup dekat untuk partai identik yang sama?"""
    ta, tb = _tanggal(a), _tanggal(b)
    if ta is None or tb is None:
        return False            # tanggal kabur: jangan menebak
    selisih = abs((ta - tb).days)
    return selisih <= KEMBAR_HARI or (jumlah_langkah >= KEMBAR_MIN_LANGKAH
                                      and selisih <= KEMBAR_HARI_PANJANG)


def cari_kembar_lintas_sumber(conn: sqlite3.Connection, putih: str, hitam: str,
                              tanggal: str | None, hasil: str, langkah_uci: str,
                              sumber: str, jumlah_langkah: int) -> int | None:
    """
    Id partai yang sama dari SUMBER LAIN dengan tanggal berbeda, atau None.

    Sama = nama belakang kedua pemain, hasil, dan SELURUH langkah identik, dan
    tanggalnya dekat (tanggal_kembar). Kalau tanggalnya sama persis, hash
    biasa sudah menangkapnya.
    """
    import datetime                                       # noqa: PLC0415
    import io                                             # noqa: PLC0415
    t = _tanggal(tanggal)
    if t is None:
        return None
    jendela = datetime.timedelta(days=KEMBAR_HARI_PANJANG)
    dari, sampai = (t - jendela).strftime("%Y.%m.%d"), (t + jendela).strftime("%Y.%m.%d")
    nb_putih, nb_hitam = nama_belakang(putih), nama_belakang(hitam)
    for r in conn.execute(
            """SELECT g.id, g.pgn, g.tanggal, pw.nama AS pw, pb.nama AS pb
               FROM partai g JOIN pemain pw ON pw.id = g.putih_id
                             JOIN pemain pb ON pb.id = g.hitam_id
               WHERE g.tanggal BETWEEN ? AND ? AND g.jumlah_langkah = ?
                 AND g.hasil = ? AND g.sumber <> ?""",
            (dari, sampai, jumlah_langkah, hasil, sumber)):
        if (nama_belakang(r["pw"]), nama_belakang(r["pb"])) != (nb_putih, nb_hitam):
            continue
        if not tanggal_kembar(tanggal, r["tanggal"], jumlah_langkah):
            continue
        g = chess.pgn.read_game(io.StringIO(r["pgn"]))
        if g is not None and " ".join(m.uci() for m in g.mainline_moves()) == langkah_uci:
            return r["id"]
    return None


def bahan_hash(game: chess.pgn.Game) -> dict | None:
    """
    Field yang masuk ke hash_partai(), diambil dari satu tempat.

    Dipakai impor_satu() **dan** periksa_jejak(): kalau dua-duanya mengambil
    sendiri dari header, satu hari nanti audit akan menghitung hash yang beda
    tipis dari impor (mis. lupa UTCDate) dan melaporkan partai yang sebenarnya
    ada sebagai "tidak ada di database". None = partai tidak bisa dipakai.
    """
    h = game.headers
    putih, hitam = bersih(h.get("White")), bersih(h.get("Black"))
    if not putih or not hitam:
        return None
    langkah = list(game.mainline_moves())
    if not langkah:
        return None
    return {
        "putih": putih,
        "hitam": hitam,
        "tanggal": bersih(h.get("Date")) or bersih(h.get("UTCDate")),
        "hasil": bersih(h.get("Result")) or "*",
        "langkah": langkah,
        "langkah_uci": " ".join(m.uci() for m in langkah),
    }


def sidik_partai(game: chess.pgn.Game) -> str | None:
    """hash_unik sebuah partai PGN, persis seperti impor menghitungnya."""
    b = bahan_hash(game)
    if b is None:
        return None
    return hash_partai(b["putih"], b["hitam"], b["tanggal"], b["hasil"], b["langkah_uci"])


def hitung_ulang_hash(conn: sqlite3.Connection, lapor_tiap: int = 0) -> dict:
    """
    Hitung ulang hash_unik semua partai dengan rumus hash_partai() yang
    sekarang. Dipanggil setelah rumusnya berubah.

    Kalau dua baris ternyata punya hash baru yang sama (duplikat yang dulu
    lolos), satu dihapus. Yang dipertahankan: yang hasilnya bukan '*', lalu
    yang langkahnya lebih banyak, lalu id terkecil. Penghapusan ikut membuang
    analisisnya (ON DELETE CASCADE) — tidak ada analisis yatim.
    """
    import io

    baris = conn.execute(
        "SELECT id, pgn, tanggal, hasil, jumlah_langkah FROM partai ORDER BY id"
    ).fetchall()

    hash_baru: dict[str, dict] = {}
    perubahan = 0
    dihapus = 0

    for i, r in enumerate(baris, start=1):
        game = chess.pgn.read_game(io.StringIO(r["pgn"]))
        if game is None:
            continue
        h = game.headers
        langkah_uci = " ".join(m.uci() for m in game.mainline_moves())
        sidik = hash_partai(h.get("White", ""), h.get("Black", ""),
                            r["tanggal"], r["hasil"], langkah_uci)
        kandidat = {"id": r["id"], "hasil": r["hasil"],
                    "langkah": r["jumlah_langkah"] or 0}

        lama = hash_baru.get(sidik)
        if lama is None:
            hash_baru[sidik] = kandidat
            continue

        # Tabrakan: pilih yang dipertahankan.
        def peringkat(x):
            return (x["hasil"] != "*", x["langkah"], -x["id"])
        menang, kalah = (kandidat, lama) if peringkat(kandidat) > peringkat(lama) else (lama, kandidat)
        hash_baru[sidik] = menang
        conn.execute("DELETE FROM partai WHERE id = ?", (kalah["id"],))
        dihapus += 1
        if lapor_tiap and i % lapor_tiap == 0:
            print(f"    {i}/{len(baris)} diperiksa, {dihapus} duplikat dihapus")

    # Dua tahap supaya UNIQUE tidak menabrak hash lama milik baris lain
    # yang belum sempat diganti.
    conn.execute("UPDATE partai SET hash_unik = 'sementara-' || id")
    for sidik, x in hash_baru.items():
        cur = conn.execute(
            "UPDATE partai SET hash_unik = ? WHERE id = ? AND hash_unik != ?",
            (sidik, x["id"], sidik),
        )
        perubahan += cur.rowcount
    conn.commit()
    return {"diperiksa": len(baris), "dihapus": dihapus, "hash_diperbarui": perubahan}


def periksa_jejak(conn: sqlite3.Connection, berkas: list[Path],
                  url_per_berkas: dict[str, str | None] | None = None,
                  sumber: str = "pgnmentor", tulis: bool = False,
                  lapor=None) -> list[dict]:
    """
    Cocokkan ulang berkas PGN ke baris database lewat hash, dan (kalau tulis)
    lengkapi jejak asalnya.

    Kenapa ada: koleksi pgnmentor diimpor tanpa `sumber_url` dan tanpa baris
    `sumber_impor`, jadi pertanyaan wajib "partai ini dari mana?" (CLAUDE.md
    bagian 7) tidak bisa dijawab, dan tidak ada bukti impornya tuntas.
    Jejaknya masih bisa dipulihkan karena hash partai dihitung dari isi
    partai, bukan dari nama berkas: parse ulang berkasnya, hitung hash yang
    sama dengan impor, lalu cocokkan.

    Per berkas dilaporkan: `partai` di berkas, `cocok` (baris bersumber ini
    yang baru dihitung di berkas ini), `juga_koleksi_lain` (barisnya sudah
    dihitung/dilabeli koleksi sebelumnya — partai Carlsen-Anand ada di dua
    koleksi, dan tanpa hitungan ini jumlah `cocok` melebihi jumlah barisnya
    sendiri), `duplikat_sumber_lain` (hash-nya sudah ada dari TWIC dsb — memang
    ditolak saat impor), `tidak_ada` (TIDAK ada di database: ini yang berarti
    impornya belum tuntas), `tanpa_langkah` (tidak bisa dihitung).

    Yang ditulis hanya kalau `tulis` dan URL-nya ada: `sumber_url` untuk baris
    yang masih NULL, plus satu baris `sumber_impor` per berkas. URL tidak
    pernah dikarang — pemanggil yang memeriksanya (impor_pgn.py --jejak
    memakai HEAD), dan berkas tanpa URL sah tetap NULL. Baris `sumber_impor`
    yang sudah ada tidak ditulis ulang, supaya angkanya tidak berlipat kalau
    audit dijalankan dua kali.
    """
    url_per_berkas = url_per_berkas or {}
    peta: dict[str, tuple[int, str, str | None]] = {
        r["hash_unik"]: (r["id"], r["sumber"], r["sumber_url"])
        for r in conn.execute("SELECT hash_unik, id, sumber, sumber_url FROM partai")
    }
    terlihat: set[int] = set()      # id yang sudah dihitung di jalannya ini
    hasil = []

    for f in berkas:
        url = url_per_berkas.get(f.name)
        s = {"berkas": f.name, "url": url, "partai": 0, "tanpa_langkah": 0,
             "cocok": 0, "juga_koleksi_lain": 0, "duplikat_sumber_lain": 0,
             "tidak_ada": 0, "url_ditulis": 0, "sumber_impor": "tidak ditulis"}
        perlu_url: list[int] = []

        with f.open(encoding="utf-8", errors="replace") as fh:
            while True:
                game = chess.pgn.read_game(fh)
                if game is None:
                    break
                s["partai"] += 1
                sidik = sidik_partai(game)
                if sidik is None:
                    s["tanpa_langkah"] += 1
                    continue
                baris = peta.get(sidik)
                if baris is None:
                    s["tidak_ada"] += 1
                    continue
                partai_id, sumber_baris, url_baris = baris
                if sumber_baris != sumber:
                    s["duplikat_sumber_lain"] += 1
                    continue
                if partai_id in terlihat or url_baris:
                    s["juga_koleksi_lain"] += 1   # partai yang sama ada di dua koleksi
                    continue
                s["cocok"] += 1
                terlihat.add(partai_id)
                if url:
                    perlu_url.append(partai_id)

        if tulis and url and perlu_url:
            conn.executemany(
                "UPDATE partai SET sumber_url = ? WHERE id = ? AND sumber_url IS NULL",
                [(url, i) for i in perlu_url],
            )
            s["url_ditulis"] = len(perlu_url)
        if tulis:
            if sudah_selesai(conn, sumber, f.name):
                s["sumber_impor"] = "sudah ada, tidak ditimpa"
            else:
                catat_sumber(conn, sumber, f.name, f.stem, url,
                             jumlah_partai=s["partai"],
                             jumlah_lolos=s["partai"] - s["tanpa_langkah"],
                             jumlah_baru=s["cocok"])
                s["sumber_impor"] = "dicatat"
            conn.commit()

        hasil.append(s)
        if lapor:
            lapor(s)

    return hasil
