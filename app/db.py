"""
Koneksi database dan pembuatan skema.

Seluruh pengetahuan tentang bentuk database ada di file ini. Modul lain tidak
boleh membuat tabel sendiri — kalau skemanya berubah, cuma ada satu tempat
yang perlu diperiksa.

Jalankan langsung untuk membuat/memeriksa database:
    python -m app.db
    python -m app.db --info
"""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

AKAR = Path(__file__).resolve().parents[1]

# Bawaannya database Ray. CATUR_DB dipakai untuk menjalankan server terhadap
# database lain tanpa menyentuh yang asli — dipakai uji halaman headless
# (tests/test_halaman_headless.py), supaya tes tidak pernah membaca atau
# menulis data sungguhan.
PATH_DB = Path(os.environ["CATUR_DB"]) if os.environ.get("CATUR_DB") else AKAR / "data" / "catur.db"

# Berkas KEADAAN yang menyertai database — kunci penulis, antrean, pengaturan
# mesin, catatan tugas, log, klaim analisis, cadangan — tinggal di folder
# database yang sedang dipakai. Dulu semuanya dikeraskan ke data/ proyek, jadi
# server uji dengan CATUR_DB lain tetap memegang kunci penulis Ray dan
# mengantrekan pekerjaan ke antrean asli (audit 28 Sep 2026). Berkas SUMBER
# (data/pgn, data/twic, cache FIDE) tetap di data/ proyek.
FOLDER_DATA = PATH_DB.parent

# Dinaikkan tiap kali skema berubah, supaya nanti bisa dideteksi kalau ada
# database lama yang perlu dimigrasi. Disimpan lewat PRAGMA user_version
# bawaan SQLite, jadi tidak perlu tabel tambahan.
VERSI_SKEMA = 13  # v2 analisis; v3 sumber_impor; v4 posisi; v5 posisi_tersimpan;
                  # v6 akun_pribadi + format; v7 indeks penutup posisi;
                  # v8 indeks (sumber, tanggal) untuk daftar partai;
                  # v9 latihan_jawaban; v10 pemain.jenis (mesin/konsultasi);
                  # v11 CHECK akun_pribadi untuk database hasil migrasi (trigger);
                  # v12 indeks penutup (sumber, hasil, jumlah_langkah) halaman Mesin;
                  # v13 indeks ekspresi REPLACE(tanggal) untuk urutan daftar partai


SKEMA = """
-- ------------------------------------------------------------------ PEMAIN
CREATE TABLE IF NOT EXISTS pemain (
    id            INTEGER PRIMARY KEY,

    -- Nama apa adanya seperti tertulis di PGN. Tidak diubah, supaya kalau
    -- terjadi kesalahan pencocokan, sumber aslinya masih bisa ditelusuri.
    nama          TEXT    NOT NULL,

    -- Nama yang sudah dinormalisasi (huruf kecil, tanpa gelar dan tanda baca).
    -- Hanya dipakai untuk mencocokkan pemain yang PGN-nya tidak menyertakan
    -- FIDE ID.
    nama_normal   TEXT    NOT NULL,

    -- Kunci identitas yang sebenarnya. NULL kalau PGN tidak menyebutkannya.
    -- UNIQUE memastikan satu FIDE ID tidak pernah jadi dua baris pemain.
    fide_id       TEXT    UNIQUE,

    negara        TEXT,
    gelar         TEXT,
    elo_terakhir  INTEGER,

    -- 1 hanya kalau identitasnya dipastikan lewat fide_id.
    -- 0 berarti pemain ini dicocokkan dari nama saja, jadi masih mungkin
    -- keliru. Kolom ini ada supaya ketidakpastian itu tercatat, bukan
    -- disembunyikan.
    terverifikasi INTEGER NOT NULL DEFAULT 0 CHECK (terverifikasi IN (0, 1)),

    -- 1 = akun milik pemilik aplikasi (username yang diunduh ambil_partai.py).
    -- Diisi impor_pgn.py, dipakai halaman latihan/ringkasan/profil supaya
    -- tidak perlu menebak dari keragaman lawan (permintaan frontend #7).
    akun_pribadi  INTEGER NOT NULL DEFAULT 0 CHECK (akun_pribadi IN (0, 1)),

    -- NULL = satu manusia. 'mesin' = program catur (Comp Deep Blue, DEEP
    -- FRITZ, HYDRA), 'konsultasi' = dua pemain atau lebih yang ditulis jadi
    -- satu nama (Nikitin/Sakarov, 'Anand,V / FRITZ7'). Keduanya ikut masuk
    -- dari koleksi PGN Mentor dan terhitung sebagai partai GM, padahal
    -- CLAUDE.md bagian 11 memfokuskan data GM pada turnamen OTB dan
    -- broadcast antar-manusia. Ditandai, bukan dihapus: Kasparov melawan
    -- Deep Blue 1997 tetap data yang tidak bisa dibuat ulang. Pengisinya
    -- app/jenis_pemain.py; CHECK memastikan nilai lain tidak pernah masuk.
    jenis         TEXT    CHECK (jenis IS NULL OR jenis IN ('mesin', 'konsultasi')),

    dibuat_pada   TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_pemain_nama_normal ON pemain(nama_normal);


-- ---------------------------------------------------------------- TURNAMEN
CREATE TABLE IF NOT EXISTS turnamen (
    id          INTEGER PRIMARY KEY,
    nama        TEXT    NOT NULL,
    tahun       INTEGER,
    lokasi      TEXT,
    sumber_url  TEXT,
    dibuat_pada TEXT    NOT NULL,

    -- Turnamen yang sama di tahun berbeda adalah dua turnamen.
    UNIQUE(nama, tahun)
);


-- ------------------------------------------------------------------ PARTAI
CREATE TABLE IF NOT EXISTS partai (
    id             INTEGER PRIMARY KEY,

    turnamen_id    INTEGER REFERENCES turnamen(id),
    putih_id       INTEGER NOT NULL REFERENCES pemain(id),
    hitam_id       INTEGER NOT NULL REFERENCES pemain(id),

    -- Tanggal disimpan apa adanya dari PGN ('YYYY.MM.DD', dan PGN memang
    -- membolehkan bagian yang tidak diketahui ditulis '????'). Tidak dipaksa
    -- jadi tanggal asli supaya data yang tidak lengkap tidak hilang.
    tanggal        TEXT,
    babak          TEXT,

    -- '*' berarti partainya belum selesai. Ini penting untuk broadcast
    -- turnamen yang masih berlangsung.
    hasil          TEXT    NOT NULL,

    eco            TEXT,
    pembukaan      TEXT,
    elo_putih      INTEGER,
    elo_hitam      INTEGER,
    kontrol_waktu  TEXT,
    -- Turunan dari kontrol_waktu: bullet / blitz / rapid / klasik / tanpa
    -- (lihat app/format_partai.py). Disimpan supaya bisa disaring & di-index,
    -- bukan dihitung ulang tiap query (permintaan frontend #8).
    format         TEXT,
    jumlah_langkah INTEGER,

    -- PGN utuh. Terlihat boros, tapi ini jaring pengaman: kalau ada metadata
    -- yang ternyata lupa diambil, datanya masih lengkap dan tinggal diproses
    -- ulang tanpa mengunduh apa pun.
    pgn            TEXT    NOT NULL,

    -- Dari mana partai ini berasal, dan tautan ke sumbernya kalau ada.
    sumber         TEXT    NOT NULL,
    sumber_url     TEXT,

    -- Penjaga duplikat. Dihitung dari isi partai, lalu ditegakkan database
    -- lewat UNIQUE. Impor boleh dijalankan berkali-kali dengan aman.
    hash_unik      TEXT    NOT NULL UNIQUE,

    diimpor_pada   TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_partai_putih    ON partai(putih_id);
CREATE INDEX IF NOT EXISTS idx_partai_hitam    ON partai(hitam_id);
CREATE INDEX IF NOT EXISTS idx_partai_turnamen ON partai(turnamen_id);
CREATE INDEX IF NOT EXISTS idx_partai_eco      ON partai(eco);
CREATE INDEX IF NOT EXISTS idx_partai_tanggal  ON partai(tanggal);
-- Daftar partai hampir selalu disaring sumber lalu diurutkan tanggal
-- terbaru. Indeks gabungan membuat itu satu jalan lurus di indeks: diukur
-- 13 Sep 2026 pada 266 ribu partai, `sumber=twic` 0,45 detik -> 0,01 detik.
-- (Saringan "gm" memakai IN tiga nilai dan masih ~0,6 detik: SQLite tidak
-- bisa menggabungkan tiga rentang indeks sambil menjaga urutan.)
CREATE INDEX IF NOT EXISTS idx_partai_sumber_tanggal ON partai(sumber, tanggal DESC, id DESC);
-- v12: semua kolom yang dihitung halaman Mesin (jumlah per sumber, yang bisa
-- dipilih & belum dianalisis) — baris partai memuat PGN, memindainya = membaca
-- gigabyte hanya untuk menghitung.
CREATE INDEX IF NOT EXISTS idx_partai_bisa_dipilih ON partai(sumber, hasil, jumlah_langkah);
-- v13: urutan terbaru/terlama daftar partai memakai REPLACE(tanggal,'?','0')
-- (tanggal "1996.??.??" dibaca awal tahun, lihat app/repositori.py), dan
-- indeks kolom biasa tidak bisa dipakai untuk ekspresi itu: tiap halaman
-- daftar mengurutkan ±275 ribu partai di B-tree sementara (10 Okt 2026:
-- tanpa saringan 1,4 s hangat / 3-5 s dingin, sumber=gm 2,8 s). Ekspresinya
-- harus SAMA PERSIS dengan _TANGGAL_URUT di repositori.py, kalau tidak
-- SQLite diam-diam kembali ke B-tree sementara (dijaga tests/test_db.py).
CREATE INDEX IF NOT EXISTS idx_partai_tanggal_urut
    ON partai(REPLACE(tanggal, '?', '0'), id);
CREATE INDEX IF NOT EXISTS idx_partai_sumber_tanggal_urut
    ON partai(sumber, REPLACE(tanggal, '?', '0'), id);


-- ================================================================ PHASE 2
-- Hasil analisis engine. Dua tabel ini TAMBAHAN: tabel Phase 1 tidak diubah.
-- Diisi oleh CLI analisis (bukan oleh server web), dibaca oleh server web.

-- ---------------------------------------------------------------- ANALISIS
-- Satu baris per partai yang sudah dianalisis. UNIQUE pada partai_id berarti
-- menganalisis ulang = hapus baris lama, tulis baru — tidak pernah dobel.
CREATE TABLE IF NOT EXISTS analisis (
    id                 INTEGER PRIMARY KEY,
    partai_id          INTEGER NOT NULL UNIQUE REFERENCES partai(id) ON DELETE CASCADE,
    engine             TEXT    NOT NULL,   -- mis. 'Stockfish 18'
    depth              INTEGER NOT NULL,
    profil             TEXT    NOT NULL,   -- hemat / normal / dalam
    akurasi_putih      REAL,
    akurasi_hitam      REAL,
    blunder_putih      INTEGER NOT NULL DEFAULT 0,
    mistake_putih      INTEGER NOT NULL DEFAULT 0,
    inaccuracy_putih   INTEGER NOT NULL DEFAULT 0,
    blunder_hitam      INTEGER NOT NULL DEFAULT 0,
    mistake_hitam      INTEGER NOT NULL DEFAULT 0,
    inaccuracy_hitam   INTEGER NOT NULL DEFAULT 0,
    turning_point_ply  INTEGER,            -- NULL = tidak ada titik balik jelas
    durasi_detik       REAL,
    dianalisis_pada    TEXT    NOT NULL
);

-- ---------------------------------------------------------- ANALISIS_LANGKAH
-- Satu baris per ply. Evaluasi disimpan dua kali dengan sengaja:
--   eval_*_cp     dari sudut pandang pemain yang jalan (untuk hitung loss)
--   eval_putih_cp dari sudut pandang Putih (untuk grafik, tandanya stabil)
--
-- PERHATIKAN: mat_sesudah bertanda dari sudut pandang PUTIH (sama dengan
-- eval_putih_cp), BUKAN dari sudut pemain yang jalan. Mencampur keduanya sudah
-- menghasilkan tampilan salah sungguhan — halaman Kekalahan sempat menulis
-- "mat dalam 2 untuk saya" untuk partai yang justru kena mat (13 Sep 2026).
-- Kalau butuh dari sudut pemain, pakai analisis_db.mat_dari_sudut_pemain();
-- /api/partai/{id}/analisis sudah menyertakannya sebagai mat_sesudah_pemain.
-- Nilai 0 = mat sudah terjadi.
CREATE TABLE IF NOT EXISTS analisis_langkah (
    partai_id            INTEGER NOT NULL REFERENCES partai(id) ON DELETE CASCADE,
    ply                  INTEGER NOT NULL,
    nomor_langkah        INTEGER NOT NULL,
    warna                TEXT    NOT NULL CHECK (warna IN ('putih', 'hitam')),
    san                  TEXT    NOT NULL,
    uci                  TEXT    NOT NULL,
    eval_sebelum_cp      INTEGER NOT NULL,
    eval_sesudah_cp      INTEGER NOT NULL,
    eval_putih_cp        INTEGER NOT NULL,
    mat_sesudah          INTEGER,
    kehilangan_cp        INTEGER NOT NULL,
    klasifikasi          TEXT    NOT NULL,
    langkah_terbaik_san  TEXT,
    langkah_terbaik_uci  TEXT,
    variasi_terbaik      TEXT,             -- SAN dipisah spasi
    PRIMARY KEY (partai_id, ply)
);

CREATE INDEX IF NOT EXISTS idx_analisis_langkah_klas
    ON analisis_langkah(klasifikasi);


-- ================================================================ PHASE 3
-- Catatan sumber yang sudah diimpor. Gunanya: backfill ratusan zip TWIC atau
-- puluhan ronde broadcast bisa dihentikan dan dilanjutkan tanpa mengunduh
-- ulang yang sudah selesai. Dedup PARTAI tetap urusan partai.hash_unik;
-- tabel ini hanya menghemat unduhan.
CREATE TABLE IF NOT EXISTS sumber_impor (
    id                     INTEGER PRIMARY KEY,
    jenis                  TEXT    NOT NULL,   -- 'twic' | 'lichess-broadcast'
    kunci                  TEXT    NOT NULL,   -- nomor TWIC / id ronde
    nama                   TEXT,
    url                    TEXT,
    jumlah_partai          INTEGER NOT NULL DEFAULT 0,  -- di sumber
    jumlah_lolos_saringan  INTEGER NOT NULL DEFAULT 0,  -- yang dicoba impor
    jumlah_baru            INTEGER NOT NULL DEFAULT 0,
    selesai                INTEGER NOT NULL DEFAULT 1 CHECK (selesai IN (0, 1)),
    diimpor_pada           TEXT    NOT NULL,
    UNIQUE(jenis, kunci)
);


-- ================================================================ PHASE 4
-- Indeks posisi untuk penjelajah pembukaan. Satu baris = "dari posisi X,
-- partai Y memainkan langkah Z". Dibangun oleh indeks_posisi.py, bukan oleh
-- server web. fen_kunci tanpa penghitung langkah supaya transposisi dikenali.
CREATE TABLE IF NOT EXISTS posisi (
    fen_kunci    TEXT    NOT NULL,
    partai_id    INTEGER NOT NULL REFERENCES partai(id) ON DELETE CASCADE,
    ply          INTEGER NOT NULL,
    langkah_uci  TEXT    NOT NULL,
    langkah_san  TEXT    NOT NULL,
    PRIMARY KEY (partai_id, ply)
) WITHOUT ROWID;

-- Indeks PENUTUP: memuat kolom langkah, jadi penjelajah pembukaan cukup
-- membaca indeks tanpa mencari balik ke tabel untuk tiap baris. Diukur 12 Sep
-- 2026 pada 9,4 juta baris: posisi awal (253 ribu partai) 30 detik -> 1,1
-- detik, posisi repertoire biasa 0,03-0,18 detik. fen_kunci jadi kolom
-- pertama, sehingga indeks ini juga menggantikan idx_posisi_fen yang lama.
CREATE INDEX IF NOT EXISTS idx_posisi_fen_langkah
    ON posisi(fen_kunci, langkah_san, langkah_uci, partai_id);


-- ================================================================ PHASE 6
-- Posisi yang disimpan pengguna dari papan (permintaan frontend #6).
-- Aplikasi lokal pribadi: tanpa pemilik/autentikasi. partai_id boleh NULL
-- (posisi hasil susun sendiri); kalau partainya dihapus, tautannya dilepas,
-- posisinya tetap ada.
CREATE TABLE IF NOT EXISTS posisi_tersimpan (
    id           INTEGER PRIMARY KEY,
    fen          TEXT    NOT NULL,
    judul        TEXT    NOT NULL DEFAULT '',
    catatan      TEXT    NOT NULL DEFAULT '',
    asal         TEXT    NOT NULL DEFAULT '',   -- mis. 'jelajah', 'analisis', 'partai'
    partai_id    INTEGER REFERENCES partai(id) ON DELETE SET NULL,
    dibuat_pada  TEXT    NOT NULL
);


-- ================================================================ PHASE 7
-- Jawaban latihan (A38). Satu baris = satu jawaban, jadi riwayatnya utuh dan
-- bisa dihitung per minggu. Status sebuah soal = baris AKTIF terbarunya.
-- "Ulang semua" hanya membuat aktif = 0, riwayat tidak dihapus.
--   mode     'kesalahan' (posisi sebelum blunder) | 'pembukaan' (repertoire)
--   kunci    kunci soal dari halaman: 'partai_id:ply' atau 'buka:<fen_kunci>'
--   hasil    'benar' | 'salah' | 'menyerah' | 'pilihan' ("ini memang pilihanku")
--   kelompok nama pembukaan (mode pembukaan) atau babak (mode kesalahan)
CREATE TABLE IF NOT EXISTS latihan_jawaban (
    id           INTEGER PRIMARY KEY,
    mode         TEXT    NOT NULL CHECK (mode IN ('kesalahan', 'pembukaan')),
    kunci        TEXT    NOT NULL,
    hasil        TEXT    NOT NULL CHECK (hasil IN ('benar', 'salah', 'menyerah', 'pilihan')),
    langkah_uci  TEXT,
    kelompok     TEXT,
    aktif        INTEGER NOT NULL DEFAULT 1 CHECK (aktif IN (0, 1)),
    dijawab_pada TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_latihan_jawaban_kunci
    ON latihan_jawaban(mode, kunci, id);
"""


def sekarang() -> str:
    """Waktu UTC dalam format ISO, dipakai untuk semua kolom waktu."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def koneksi(path: Path | None = None) -> sqlite3.Connection:
    """
    Buka koneksi ke database.

    Tiga PRAGMA yang selalu diset:

    foreign_keys  SQLite mematikan pengecekan foreign key secara default.
                  Tanpa ini, partai bisa menunjuk pemain yang tidak ada dan
                  database tetap menerimanya.

    journal_mode  WAL membuat pembacaan tidak terblokir saat ada penulisan.
                  Berguna karena nanti impor broadcast bisa berjalan sementara
                  halaman web sedang dibuka.

    busy_timeout  Kalau database sedang dipakai proses lain, tunggu sebentar
                  daripada langsung gagal.

    check_same_thread=False — ini perbaikan bug #2 (SESI.md bagian 3, 10 Sep
    2026): server menjawab 500 acak untuk permintaan paralel. Dugaan awal
    (urutan PRAGMA saat database terkunci) ternyata SALAH: di mode WAL pembaca
    tidak pernah menunggu penulis. Traceback aslinya:
    "SQLite objects created in a thread can only be used in that same thread"
    di app/main.py db(). FastAPI menjalankan dependency generator di thread
    worker, tapi bagian finally-nya (conn.close()) bisa jatuh di thread lain.
    Mematikan pemeriksaan thread aman di sini karena satu koneksi hanya
    dipakai oleh satu permintaan dan tidak pernah dari dua thread sekaligus.
    """
    p = Path(path or PATH_DB)
    p.parent.mkdir(parents=True, exist_ok=True)

    # timeout=5 berlaku untuk SEMUA pernyataan, termasuk PRAGMA pertama.
    conn = sqlite3.connect(p, timeout=5, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def _tambah_kolom_jika_belum(conn: sqlite3.Connection, tabel: str, kolom: str,
                             definisi: str) -> bool:
    """
    Migrasi aditif untuk database yang tabelnya sudah ada: CREATE TABLE IF NOT
    EXISTS tidak menambah kolom baru ke tabel lama, jadi kolom v6 ditambahkan
    lewat ALTER TABLE. Kembalikan True kalau kolomnya baru ditambahkan.
    """
    ada = {r[1] for r in conn.execute(f"PRAGMA table_info({tabel})")}
    if kolom in ada:
        return False
    conn.execute(f"ALTER TABLE {tabel} ADD COLUMN {kolom} {definisi}")
    return True


# Kolom yang ditambahkan setelah tabelnya sudah beredar. Definisi di sini harus
# sama maknanya dengan yang ada di SKEMA (untuk database baru).
KOLOM_MIGRASI = (
    ("pemain", "akun_pribadi", "INTEGER NOT NULL DEFAULT 0 CHECK (akun_pribadi IN (0, 1))"),
    ("partai", "format", "TEXT"),
    ("pemain", "jenis",
     "TEXT CHECK (jenis IS NULL OR jenis IN ('mesin', 'konsultasi'))"),
)


def buat_skema(conn: sqlite3.Connection) -> None:
    """Buat tabel dan index kalau belum ada, tambah kolom migrasi kalau kurang.
    Aman dijalankan berulang kali."""
    conn.executescript(SKEMA)
    for tabel, kolom, definisi in KOLOM_MIGRASI:
        _tambah_kolom_jika_belum(conn, tabel, kolom, definisi)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_partai_format ON partai(format)")
    # v7: idx_posisi_fen lama hanya berisi fen_kunci, jadi sepenuhnya termuat
    # di awalan idx_posisi_fen_langkah. Menyimpan keduanya = 500 MB sia-sia.
    conn.execute("DROP INDEX IF EXISTS idx_posisi_fen")
    # v8: idx_partai_sumber lama hanya berisi kolom sumber, jadi termuat penuh
    # di awalan idx_partai_sumber_tanggal.
    conn.execute("DROP INDEX IF EXISTS idx_partai_sumber")
    # v11: kolom akun_pribadi yang ditambahkan lewat ALTER TABLE sebelum v11
    # tidak punya CHECK (0/1) — database asli termasuk (audit 28 Sep 2026:
    # nilai 5 diterima). SQLite tidak bisa menambah CHECK ke kolom yang sudah
    # ada tanpa membangun ulang tabel, jadi aturannya ditegakkan trigger:
    # tetap database yang menolak, bukan kode yang berjanji.
    for kapan in ("INSERT", "UPDATE OF akun_pribadi"):
        nama = "cek_akun_pribadi_" + kapan.split()[0].lower()
        conn.execute(
            f"CREATE TRIGGER IF NOT EXISTS {nama} BEFORE {kapan} ON pemain "
            "WHEN NEW.akun_pribadi NOT IN (0, 1) "
            "BEGIN SELECT RAISE(ABORT, 'CHECK constraint failed: akun_pribadi'); END")
    conn.execute(f"PRAGMA user_version = {VERSI_SKEMA}")
    conn.commit()


def versi_skema(conn: sqlite3.Connection) -> int:
    return conn.execute("PRAGMA user_version").fetchone()[0]


def info(conn: sqlite3.Connection) -> dict:
    """Ringkasan isi database, untuk pengecekan cepat."""
    hasil = {"versi_skema": versi_skema(conn)}
    for tabel in ("pemain", "turnamen", "partai", "analisis"):
        try:
            hasil[tabel] = conn.execute(f"SELECT COUNT(*) FROM {tabel}").fetchone()[0]
        except sqlite3.OperationalError:
            hasil[tabel] = None
    return hasil


def main() -> int:
    import argparse

    p = argparse.ArgumentParser(description="Buat atau periksa database")
    p.add_argument("--db", type=Path, default=PATH_DB)
    p.add_argument("--info", action="store_true", help="tampilkan isi saja")
    args = p.parse_args()

    baru = not args.db.exists()
    conn = koneksi(args.db)
    try:
        if not args.info:
            buat_skema(conn)

        print(f"Database : {args.db}")
        print(f"Status   : {'baru dibuat' if baru else 'sudah ada'}")

        d = info(conn)
        print(f"Versi    : skema v{d['versi_skema']}")
        print()
        print(f"  {'Tabel':<12}{'Baris':>8}")
        print(f"  {'-' * 20}")
        for tabel in ("pemain", "turnamen", "partai", "analisis"):
            jumlah = d[tabel]
            print(f"  {tabel:<12}{'-' if jumlah is None else jumlah:>8}")

        # Tampilkan struktur supaya bisa diperiksa mata, bukan cuma dipercaya.
        print()
        print("  Index yang terpasang:")
        for baris in conn.execute(
            "SELECT name, tbl_name FROM sqlite_master "
            "WHERE type='index' AND name LIKE 'idx_%' ORDER BY tbl_name, name"
        ):
            print(f"    {baris['tbl_name']:<10} {baris['name']}")

        ukuran = args.db.stat().st_size / 1024
        print()
        print(f"  Ukuran file: {ukuran:.1f} KB")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
