"""
Semua query SQL ada di file ini.

Alasan dikumpulkan: kalau SQL tersebar di rute-rute HTTP, mengubah skema
berarti berburu query ke seluruh proyek. Di sini rute cuma memanggil fungsi,
dan tidak tahu-menahu soal bentuk tabel.
"""

from __future__ import annotations

import sqlite3

from .format_partai import kontrol_normal


def pola_like(teks: str, awal: bool = True, akhir: bool = True) -> str:
    r"""
    Pola LIKE dari teks pengguna, dengan % _ \ di-escape. Pakai bersama
    `LIKE ? ESCAPE '\'` (di kode Python: `ESCAPE '\\'`).

    Audit 27 Sep 2026: tanpa escape, mencari "%" atau "_" mengembalikan
    SEMUA pemain, dan nama akun berisi _ (mis. McLung_07) ikut mencocokkan
    huruf apa pun di posisi itu.
    """
    aman = teks.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return ("%" if awal else "") + aman + ("%" if akhir else "")

# Kolom yang selalu diambil untuk baris daftar partai. Ditulis sekali supaya
# daftar dan detail tidak pernah beda isinya.
KOLOM_DAFTAR = """
    g.id,
    g.tanggal,
    g.babak,
    g.hasil,
    g.eco,
    g.pembukaan,
    g.elo_putih,
    g.elo_hitam,
    g.jumlah_langkah,
    g.sumber,
    g.sumber_url,
    g.format,
    g.kontrol_waktu,
    pw.id   AS putih_id,
    pw.nama AS putih,
    pw.gelar AS putih_gelar,
    pw.terverifikasi AS putih_terverifikasi,
    pb.id   AS hitam_id,
    pb.nama AS hitam,
    pb.gelar AS hitam_gelar,
    pb.terverifikasi AS hitam_terverifikasi,
    t.id    AS turnamen_id,
    t.nama  AS turnamen,
    (a.partai_id IS NOT NULL) AS dianalisis,
    a.akurasi_putih,
    a.akurasi_hitam
"""

DARI_DAFTAR = """
    FROM partai g
    JOIN pemain pw    ON pw.id = g.putih_id
    JOIN pemain pb    ON pb.id = g.hitam_id
    LEFT JOIN turnamen t ON t.id = g.turnamen_id
    LEFT JOIN analisis a ON a.partai_id = g.id
"""

# Tanggal PGN yang tidak diketahui ditulis '?' ("1996.??.??", 12.821 partai).
# '?' lebih besar dari angka di ASCII, jadi dulu "1996.??.??" terurut DI ATAS
# "1996.12.31" (audit 27 Sep 2026). Untuk URUTAN saja '?' dibaca '0' = awal
# tahun/bulan; data tanggalnya tetap '??', tidak ditebak. Biayanya indeks
# tanggal tidak dipakai untuk urutan: terukur <= 0,15 dtk pada 273 ribu partai.
_TANGGAL_URUT = "REPLACE(g.tanggal, '?', '0')"

URUTAN = {
    "terbaru": f"{_TANGGAL_URUT} DESC, g.id DESC",
    "terlama": f"{_TANGGAL_URUT} ASC, g.id ASC",
    "terpanjang": "g.jumlah_langkah DESC, g.id DESC",
    "elo": "MAX(COALESCE(g.elo_putih,0), COALESCE(g.elo_hitam,0)) DESC, g.id DESC",
}


def _syarat(cari: str | None, sumber: str | None, eco: str | None,
            hasil: str | None, gelar: str | None = None,
            dianalisis: bool | None = None, pemain_id: int | None = None,
            warna: str | None = None, eco_dari: str | None = None,
            eco_sampai: str | None = None,
            fen_kunci: str | None = None,
            format_: str | None = None) -> tuple[str, list]:
    """
    Bangun bagian WHERE beserta parameternya.

    Nilai selalu dikirim sebagai parameter (tanda tanya), tidak pernah
    disambung ke teks SQL. Ini yang mencegah SQL injection — pencarian
    berisi tanda kutip pun aman.
    """
    syarat, params = [], []

    if cari:
        pola = pola_like(cari.strip())
        syarat.append(
            "(pw.nama LIKE ? ESCAPE '\\' OR pb.nama LIKE ? ESCAPE '\\' OR t.nama LIKE ? ESCAPE '\\' "
            " OR g.pembukaan LIKE ? ESCAPE '\\' OR g.eco LIKE ? ESCAPE '\\')"
        )
        params += [pola] * 5

    if sumber == "gm":
        # Sama dengan penjelajah (/api/posisi): partai GM = TWIC + broadcast.
        # Diambil dari app.posisi supaya definisinya cuma ada di satu tempat.
        # Partai GM = antar-MANUSIA, sama dengan penjelajah: Kasparov-Deep Blue
        # dan partai konsultasi (pemain.jenis) dulu ikut terhitung di sini.
        from .posisi import SUMBER_GM, SYARAT_ANTAR_MANUSIA
        syarat.append(f"g.sumber IN ({','.join('?' * len(SUMBER_GM))})")
        syarat.append(SYARAT_ANTAR_MANUSIA)
        params += list(SUMBER_GM)
    elif sumber:
        syarat.append("g.sumber = ?")
        params.append(sumber)

    if eco:
        syarat.append("g.eco LIKE ? ESCAPE '\\'")
        params.append(pola_like(eco.strip().upper(), awal=False))

    if hasil == "menang-kalah":
        # Keputusan Ray (SESI.md bagian 6): remis TIDAK dihapus dari database,
        # hanya disembunyikan di tampilan lewat saringan ini.
        syarat.append("g.hasil IN ('1-0', '0-1')")
    elif hasil:
        syarat.append("g.hasil = ?")
        params.append(hasil)

    if gelar:
        # Salah satu pemain bergelar ini. Digabung dengan cari=<username Ray>
        # hasilnya "partai Ray melawan GM", karena akun Ray sendiri tidak
        # bergelar. Untuk database GM: gelar=GM saja = partai antar-GM.
        syarat.append("(pw.gelar = ? OR pb.gelar = ?)")
        params += [gelar.upper(), gelar.upper()]

    if dianalisis is True:
        syarat.append("a.partai_id IS NOT NULL")
    elif dianalisis is False:
        syarat.append("a.partai_id IS NULL")

    if pemain_id is not None:
        if warna == "putih":
            syarat.append("g.putih_id = ?")
            params.append(pemain_id)
        elif warna == "hitam":
            syarat.append("g.hitam_id = ?")
            params.append(pemain_id)
        else:
            syarat.append("(g.putih_id = ? OR g.hitam_id = ?)")
            params += [pemain_id, pemain_id]

    # Rentang ECO untuk saringan repertoire (mis. Sicilian = B20..B99).
    if eco_dari and eco_sampai:
        syarat.append("substr(g.eco, 1, 3) BETWEEN ? AND ?")
        params += [eco_dari.upper()[:3], eco_sampai.upper()[:3]]

    # Partai yang pernah mencapai sebuah posisi (permintaan frontend #3).
    # Subquery IN memakai idx_posisi_fen sekali, lalu dicocokkan ke partai;
    # EXISTS per baris akan menyentuh indeks 47 ribu kali.
    if fen_kunci:
        syarat.append("g.id IN (SELECT p.partai_id FROM posisi p WHERE p.fen_kunci = ?)")
        params.append(fen_kunci)

    # Format (bullet/blitz/rapid/klasik/tanpa), permintaan frontend #8.
    if format_:
        syarat.append("g.format = ?")
        params.append(format_)

    return ("WHERE " + " AND ".join(syarat) if syarat else ""), params


def _butuh_join(cari: str | None, gelar: str | None,
                dianalisis: bool | None) -> bool:
    """
    Apakah penghitungan total perlu tabel selain `partai`?

    Hanya tiga saringan yang menyentuh tabel lain: `cari` (nama pemain dan
    turnamen), `gelar` (tabel pemain), dan `dianalisis` (tabel analisis).
    Sisanya — sumber, eco, hasil, pemain_id, format, fen — semuanya kolom
    `partai`.

    Kenapa dibedakan: `SELECT COUNT(*)` lewat empat sambungan tabel memakan
    **1,0 detik** pada 266 ribu partai, sedangkan menghitung dari `partai` saja
    0,01 detik. Barisnya sendiri sudah cepat karena dibatasi LIMIT; yang mahal
    justru totalnya, dan total itu muncul di tiap halaman daftar.
    """
    return bool(cari) or bool(gelar) or dianalisis is not None


def _baris_partai(baris: sqlite3.Row) -> dict:
    """Baris partai apa adanya + `kontrol_normal` ("180+0" -> "3+0").

    Dihitung di sini, bukan disimpan sebagai kolom: turunan murni dari
    kontrol_waktu + sumber, jadi kolom baru hanya akan bisa basi. Aturannya
    satu tempat di app/format_partai.py — halaman web memakai field ini,
    tidak menghitung sendiri.
    """
    d = dict(baris)
    d["kontrol_normal"] = kontrol_normal(d.get("kontrol_waktu"), d.get("sumber"))
    return d


def cari_partai(
    conn: sqlite3.Connection,
    cari: str | None = None,
    sumber: str | None = None,
    eco: str | None = None,
    hasil: str | None = None,
    gelar: str | None = None,
    dianalisis: bool | None = None,
    pemain_id: int | None = None,
    warna: str | None = None,
    eco_dari: str | None = None,
    eco_sampai: str | None = None,
    fen_kunci: str | None = None,
    format_: str | None = None,
    urut: str = "terbaru",
    halaman: int = 1,
    per_halaman: int = 25,
) -> dict:
    """Daftar partai dengan paginasi. Selalu dibatasi — tidak pernah
    mengembalikan seluruh isi tabel."""
    where, params = _syarat(cari, sumber, eco, hasil, gelar, dianalisis,
                            pemain_id, warna, eco_dari, eco_sampai, fen_kunci, format_)
    urutan = URUTAN.get(urut, URUTAN["terbaru"])

    if _butuh_join(cari, gelar, dianalisis):
        total = conn.execute(f"SELECT COUNT(*) {DARI_DAFTAR} {where}", params).fetchone()[0]
    else:
        total = conn.execute(f"SELECT COUNT(*) FROM partai g {where}", params).fetchone()[0]

    per_halaman = max(1, min(per_halaman, 100))
    halaman = max(1, halaman)
    offset = (halaman - 1) * per_halaman

    baris = conn.execute(
        f"SELECT {KOLOM_DAFTAR} {DARI_DAFTAR} {where} "
        f"ORDER BY {urutan} LIMIT ? OFFSET ?",
        params + [per_halaman, offset],
    ).fetchall()

    return {
        "total": total,
        "halaman": halaman,
        "per_halaman": per_halaman,
        "jumlah_halaman": (total + per_halaman - 1) // per_halaman,
        "partai": [_baris_partai(b) for b in baris],
    }


def ambil_partai(conn: sqlite3.Connection, partai_id: int) -> dict | None:
    """Satu partai lengkap dengan PGN-nya."""
    baris = conn.execute(
        f"SELECT {KOLOM_DAFTAR}, g.pgn, g.diimpor_pada "
        f"{DARI_DAFTAR} WHERE g.id = ?",
        (partai_id,),
    ).fetchone()
    return _baris_partai(baris) if baris else None


def pemain_ada(conn: sqlite3.Connection, pemain_id: int) -> bool:
    """
    Pemain dengan id ini ada di database?

    Dipakai rute yang hanya perlu tahu "ada atau tidak" sebelum menjawab 404.
    Ditaruh di sini supaya `app/main.py` tidak perlu menulis SQL sendiri.
    """
    return conn.execute("SELECT 1 FROM pemain WHERE id = ?", (pemain_id,)).fetchone() is not None


def cari_pemain(conn: sqlite3.Connection, cari: str | None = None,
                limit: int = 30, pribadi: bool | None = None) -> list[dict]:
    """
    Pemain beserta jumlah partainya, diurutkan dari yang terbanyak.

    pribadi=True: hanya akun pemilik aplikasi (pemain.akun_pribadi = 1), dan
    tiap baris diberi per_sumber supaya frontend tahu akun Lichess/Chess.com
    mana (permintaan #7).
    """
    syarat, params = [], []
    if cari:
        syarat.append("p.nama LIKE ? ESCAPE '\\'")
        params.append(pola_like(cari.strip()))
    if pribadi is True:
        syarat.append("p.akun_pribadi = 1")
    elif pribadi is False:
        syarat.append("p.akun_pribadi = 0")
    where = "WHERE " + " AND ".join(syarat) if syarat else ""

    baris = conn.execute(
        f"""
        SELECT p.id, p.nama, p.gelar, p.negara, p.fide_id, p.elo_terakhir,
               p.terverifikasi, p.akun_pribadi,
               (SELECT COUNT(*) FROM partai g
                 WHERE g.putih_id = p.id OR g.hitam_id = p.id) AS jumlah_partai
        FROM pemain p
        {where}
        ORDER BY jumlah_partai DESC, p.nama
        LIMIT ?
        """,
        params + [max(1, min(limit, 200))],
    ).fetchall()
    hasil = [dict(b) for b in baris]
    if pribadi is True:
        for p in hasil:
            p["per_sumber"] = [
                [r["sumber"], r["n"]] for r in conn.execute(
                    "SELECT sumber, COUNT(*) AS n FROM partai "
                    "WHERE putih_id = ? OR hitam_id = ? GROUP BY sumber ORDER BY n DESC",
                    (p["id"], p["id"]))
            ]
    return hasil


def statistik(conn: sqlite3.Connection) -> dict:
    """Angka ringkas untuk halaman depan."""
    satu = lambda sql: conn.execute(sql).fetchone()[0]
    return {
        "partai": satu("SELECT COUNT(*) FROM partai"),
        "pemain": satu("SELECT COUNT(*) FROM pemain"),
        "turnamen": satu("SELECT COUNT(*) FROM turnamen"),
        "pemain_terverifikasi": satu("SELECT COUNT(*) FROM pemain WHERE terverifikasi = 1"),
        "sumber": [
            dict(b) for b in conn.execute(
                "SELECT sumber, COUNT(*) AS jumlah FROM partai "
                "GROUP BY sumber ORDER BY jumlah DESC"
            )
        ],
    }
