"""
Salin database catur (SQLite) ke MySQL/MariaDB untuk belajar SQL.

Ray, 19 Sep 2026: "pindah pakai my sql saja dulu sekalian saya belajar untuk
materi kuliah", lalu memilih SALINAN dulu — aplikasinya sendiri TETAP memakai
SQLite (`data/catur.db`). Jadi skrip ini hanya membaca SQLite dan menulis ke
MySQL; tidak ada satu pun bagian aplikasi yang berubah.

Kuliah Ray memakai XAMPP (MariaDB) + MySQL Workbench, jadi bawaannya
127.0.0.1:3306, pengguna root, sandi kosong.

    python ke_mysql.py --lihat                 # periksa sambungan + hitung baris
    python ke_mysql.py --tabel kecil           # pemain, turnamen, partai, analisis, dll
    python ke_mysql.py --tabel semua           # termasuk 2 tabel raksasa (±30 menit)
    python ke_mysql.py --tabel analisis_langkah --lanjut   # lanjutkan yang terputus
    python ke_mysql.py --contoh                # contoh query untuk latihan

Yang perlu diketahui:
  * Boleh dijalankan berkali-kali. Dengan --lanjut, baris yang sudah ada
    dilewati (dihitung dari kunci terbesar yang sudah masuk), jadi penyalinan
    yang terputus tinggal diulang.
  * Tanpa --lanjut, tabelnya DIKOSONGKAN dulu supaya isinya persis sama
    dengan SQLite.
  * Indeks dibuat SESUDAH data masuk: jauh lebih cepat daripada membangun
    indeks baris demi baris.
  * Butuh paket PyMySQL: python -m pip install PyMySQL
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
import time
from pathlib import Path

AKAR = Path(__file__).resolve().parent
sys.path.insert(0, str(AKAR))

from app.db import PATH_DB                                  # noqa: E402

# Tipe MySQL dipilih dari isi kolomnya, bukan disamakan semua jadi TEXT:
#   * kolom waktu di SQLite berupa teks ISO ("2026-09-19T12:00:00+00:00"),
#     jadi tetap VARCHAR supaya nilainya sama persis, bukan ditebak jadi
#     DATETIME dan berubah zona waktunya;
#   * `pgn` dan `variasi_terbaik` bisa panjang -> MEDIUMTEXT/TEXT;
#   * kolom 0/1 -> TINYINT, yang di MySQL memang bentuk boolean.
SKEMA = {
    "pemain": """
        id            INT PRIMARY KEY,
        nama          VARCHAR(190) NOT NULL,
        nama_normal   VARCHAR(190) NOT NULL,
        fide_id       VARCHAR(32),
        negara        VARCHAR(16),
        gelar         VARCHAR(16),
        elo_terakhir  INT,
        terverifikasi TINYINT NOT NULL DEFAULT 0 CHECK (terverifikasi IN (0, 1)),
        dibuat_pada   VARCHAR(40) NOT NULL,
        akun_pribadi  TINYINT NOT NULL DEFAULT 0 CHECK (akun_pribadi IN (0, 1)),
        -- v10: NULL = manusia; 'mesin'/'konsultasi' lihat app/jenis_pemain.py.
        -- ENUM supaya MySQL ikut menolak nilai asing, sama seperti CHECK di SQLite.
        jenis         ENUM('mesin','konsultasi') DEFAULT NULL
    """,
    "turnamen": """
        id          INT PRIMARY KEY,
        nama        VARCHAR(250) NOT NULL,
        tahun       INT,
        lokasi      VARCHAR(190),
        sumber_url  VARCHAR(500),
        dibuat_pada VARCHAR(40) NOT NULL
    """,
    "partai": """
        id             INT PRIMARY KEY,
        turnamen_id    INT,
        putih_id       INT NOT NULL,
        hitam_id       INT NOT NULL,
        tanggal        VARCHAR(20),
        babak          VARCHAR(40),
        hasil          VARCHAR(10) NOT NULL,
        eco            VARCHAR(8),
        pembukaan      VARCHAR(190),
        elo_putih      INT,
        elo_hitam      INT,
        -- 250, bukan 40: siaran menulis TimeControl sebagai kalimat ("90 minutes
        -- for the first 40 moves..."), terpanjang 179 aksara di 117 partai.
        kontrol_waktu  VARCHAR(250),
        jumlah_langkah INT,
        pgn            MEDIUMTEXT NOT NULL,
        sumber         VARCHAR(40) NOT NULL,
        sumber_url     VARCHAR(500),
        hash_unik      VARCHAR(64) NOT NULL,
        diimpor_pada   VARCHAR(40) NOT NULL,
        format         VARCHAR(20)
    """,
    "analisis": """
        id                INT PRIMARY KEY,
        partai_id         INT NOT NULL,
        engine            VARCHAR(80) NOT NULL,
        depth             INT NOT NULL,
        profil            VARCHAR(20) NOT NULL,
        akurasi_putih     DOUBLE,
        akurasi_hitam     DOUBLE,
        blunder_putih     INT NOT NULL,
        mistake_putih     INT NOT NULL,
        inaccuracy_putih  INT NOT NULL,
        blunder_hitam     INT NOT NULL,
        mistake_hitam     INT NOT NULL,
        inaccuracy_hitam  INT NOT NULL,
        turning_point_ply INT,
        durasi_detik      DOUBLE,
        dianalisis_pada   VARCHAR(40) NOT NULL
    """,
    "sumber_impor": """
        id                    INT PRIMARY KEY,
        jenis                 VARCHAR(80) NOT NULL,
        kunci                 VARCHAR(120) NOT NULL,
        nama                  VARCHAR(250),
        url                   VARCHAR(500),
        jumlah_partai         INT NOT NULL,
        jumlah_lolos_saringan INT NOT NULL,
        jumlah_baru           INT NOT NULL,
        selesai               TINYINT NOT NULL,
        diimpor_pada          VARCHAR(40) NOT NULL
    """,
    "posisi_tersimpan": """
        id          INT PRIMARY KEY,
        fen         VARCHAR(120) NOT NULL,
        judul       VARCHAR(250) NOT NULL,
        catatan     TEXT NOT NULL,
        asal        VARCHAR(190) NOT NULL,
        partai_id   INT,
        dibuat_pada VARCHAR(40) NOT NULL
    """,
    "analisis_langkah": """
        partai_id           INT NOT NULL,
        ply                 INT NOT NULL,
        nomor_langkah       INT NOT NULL,
        warna               VARCHAR(6) NOT NULL,
        san                 VARCHAR(16) NOT NULL,
        uci                 VARCHAR(8) NOT NULL,
        eval_sebelum_cp     INT NOT NULL,
        eval_sesudah_cp     INT NOT NULL,
        eval_putih_cp       INT NOT NULL,
        mat_sesudah         INT,
        kehilangan_cp       INT NOT NULL,
        klasifikasi         VARCHAR(16) NOT NULL,
        langkah_terbaik_san VARCHAR(16),
        langkah_terbaik_uci VARCHAR(8),
        variasi_terbaik     VARCHAR(250),
        PRIMARY KEY (partai_id, ply)
    """,
    "posisi": """
        fen_kunci   VARCHAR(100) NOT NULL,
        partai_id   INT NOT NULL,
        ply         INT NOT NULL,
        langkah_uci VARCHAR(8) NOT NULL,
        langkah_san VARCHAR(16) NOT NULL,
        PRIMARY KEY (partai_id, ply)
    """,
    # v9: jawaban latihan. Dulu tidak ikut disalin (audit 28 Sep 2026).
    "latihan_jawaban": """
        id           INT PRIMARY KEY,
        mode         ENUM('kesalahan','pembukaan') NOT NULL,
        kunci        VARCHAR(200) NOT NULL,
        hasil        ENUM('benar','salah','menyerah','pilihan') NOT NULL,
        langkah_uci  VARCHAR(8),
        kelompok     VARCHAR(60),
        aktif        TINYINT NOT NULL DEFAULT 1 CHECK (aktif IN (0, 1)),
        dijawab_pada VARCHAR(40) NOT NULL
    """,
}

# Indeks dibuat setelah data masuk. Sama dengan indeks SQLite yang benar-benar
# dipakai halaman web, jadi latihan query di Workbench terasa sama cepatnya.
INDEKS = {
    "pemain": ["CREATE INDEX idx_pemain_nama_normal ON pemain(nama_normal)",
               "CREATE UNIQUE INDEX idx_pemain_fide ON pemain(fide_id)"],
    "turnamen": ["CREATE UNIQUE INDEX idx_turnamen_nama_tahun ON turnamen(nama, tahun)"],
    "partai": ["CREATE UNIQUE INDEX idx_partai_hash ON partai(hash_unik)",
               "CREATE INDEX idx_partai_putih ON partai(putih_id)",
               "CREATE INDEX idx_partai_hitam ON partai(hitam_id)",
               "CREATE INDEX idx_partai_turnamen ON partai(turnamen_id)",
               "CREATE INDEX idx_partai_eco ON partai(eco)",
               "CREATE INDEX idx_partai_tanggal ON partai(tanggal)",
               "CREATE INDEX idx_partai_format ON partai(format)",
               "CREATE INDEX idx_partai_sumber_tanggal ON partai(sumber, tanggal, id)"],
    "analisis": ["CREATE UNIQUE INDEX idx_analisis_partai ON analisis(partai_id)"],
    "sumber_impor": ["CREATE UNIQUE INDEX idx_sumber_impor ON sumber_impor(jenis, kunci)"],
    "analisis_langkah": ["CREATE INDEX idx_analisis_langkah_klas ON analisis_langkah(klasifikasi)"],
    "posisi": ["CREATE INDEX idx_posisi_fen ON posisi(fen_kunci, langkah_san, langkah_uci, partai_id)"],
    "latihan_jawaban": ["CREATE INDEX idx_latihan_jawaban_kunci ON latihan_jawaban(mode, kunci, id)"],
}

KECIL = ("pemain", "turnamen", "partai", "analisis", "sumber_impor", "posisi_tersimpan",
         "latihan_jawaban")
BESAR = ("analisis_langkah", "posisi")
SEMUA = KECIL + BESAR

# Kunci untuk melanjutkan penyalinan yang terputus.
KUNCI_LANJUT = {t: ("id",) for t in KECIL}
KUNCI_LANJUT["analisis_langkah"] = ("partai_id", "ply")
KUNCI_LANJUT["posisi"] = ("partai_id", "ply")

CONTOH = """
-- Contoh query latihan (tempel di MySQL Workbench).

-- 1. Sepuluh pemain dengan partai terbanyak
SELECT p.nama, COUNT(*) AS partai
FROM partai g
JOIN pemain p ON p.id IN (g.putih_id, g.hitam_id)
GROUP BY p.nama
ORDER BY partai DESC
LIMIT 10;

-- 2. Pembukaan yang paling sering kamu mainkan (akun sendiri)
SELECT g.eco, g.pembukaan, COUNT(*) AS jumlah
FROM partai g
JOIN pemain p ON p.id IN (g.putih_id, g.hitam_id) AND p.akun_pribadi = 1
WHERE g.eco IS NOT NULL
GROUP BY g.eco, g.pembukaan
ORDER BY jumlah DESC
LIMIT 15;

-- 3. Rata-rata akurasi per profil engine
SELECT profil, COUNT(*) AS partai,
       ROUND(AVG(akurasi_putih), 1) AS akurasi_putih,
       ROUND(AVG(akurasi_hitam), 1) AS akurasi_hitam
FROM analisis
GROUP BY profil
ORDER BY partai DESC;

-- 4. Blunder terbesar di partaimu sendiri (JOIN tiga tabel)
SELECT g.id, pw.nama AS putih, pb.nama AS hitam, l.nomor_langkah, l.san,
       l.kehilangan_cp
FROM analisis_langkah l
JOIN partai g  ON g.id = l.partai_id
JOIN pemain pw ON pw.id = g.putih_id
JOIN pemain pb ON pb.id = g.hitam_id
WHERE l.klasifikasi = 'blunder'
  AND (pw.akun_pribadi = 1 OR pb.akun_pribadi = 1)
ORDER BY l.kehilangan_cp DESC
LIMIT 20;

-- 5. Subquery: partai yang belum dianalisis
SELECT COUNT(*) FROM partai g
WHERE NOT EXISTS (SELECT 1 FROM analisis a WHERE a.partai_id = g.id);

-- 6. Langkah terpopuler dari posisi awal (indeks pembukaan)
SELECT langkah_san, COUNT(*) AS jumlah
FROM posisi
WHERE ply = 0
GROUP BY langkah_san
ORDER BY jumlah DESC
LIMIT 10;
"""


def sambung(a):
    try:
        import pymysql
    except ImportError:
        raise SystemExit("Paket PyMySQL belum ada. Jalankan: python -m pip install PyMySQL")
    try:
        my = pymysql.connect(host=a.host, port=a.port, user=a.pengguna, password=a.sandi,
                             charset="utf8mb4", autocommit=False, local_infile=False)
    except pymysql.err.OperationalError as e:
        kode = e.args[0]
        if kode == 2003:
            raise SystemExit(
                f"Tidak bisa menyambung ke MySQL di {a.host}:{a.port}. "
                "Buka XAMPP Control Panel, tekan Start di baris MySQL sampai hijau, lalu ulangi.") from e
        if kode in (1045, 1044):
            raise SystemExit(
                f"Pengguna/sandi ditolak MySQL (pengguna '{a.pengguna}'). "
                "XAMPP bawaannya root tanpa sandi.") from e
        raise
    # Mode ketat: nilai yang terlalu panjang / NULL di kolom NOT NULL = GALAT,
    # bukan dipotong diam-diam jadi peringatan (lihat salin()).
    with my.cursor() as k:
        k.execute("SET SESSION sql_mode = 'STRICT_ALL_TABLES,NO_ENGINE_SUBSTITUTION'")
    return my


def siapkan_database(my, nama_db: str) -> None:
    with my.cursor() as k:
        k.execute(f"CREATE DATABASE IF NOT EXISTS `{nama_db}` "
                  "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")
        k.execute(f"USE `{nama_db}`")
    my.commit()


def kolom_sqlite(sq: sqlite3.Connection, tabel: str) -> list[str]:
    return [r[1] for r in sq.execute(f"PRAGMA table_info({tabel})")]


def sudah_ada(my, tabel: str, kunci: tuple[str, ...]) -> tuple | None:
    """Kunci terbesar yang sudah tersalin, untuk --lanjut."""
    with my.cursor() as k:
        k.execute(f"SELECT {', '.join(kunci)} FROM `{tabel}` "
                  f"ORDER BY {', '.join(f'{x} DESC' for x in kunci)} LIMIT 1")
        return k.fetchone()


def salin(sq: sqlite3.Connection, my, tabel: str, lanjut: bool, batch: int, lapor=print) -> int:
    kolom = kolom_sqlite(sq, tabel)
    kunci = KUNCI_LANJUT[tabel]
    with my.cursor() as k:
        k.execute(f"CREATE TABLE IF NOT EXISTS `{tabel}` ({SKEMA[tabel]}) ENGINE=InnoDB "
                  "DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci")
        if not lanjut:
            k.execute(f"TRUNCATE TABLE `{tabel}`")
    my.commit()

    syarat, params = "", []
    if lanjut:
        terakhir = sudah_ada(my, tabel, kunci)
        if terakhir:
            if len(kunci) == 1:
                syarat, params = f" WHERE {kunci[0]} > ?", [terakhir[0]]
            else:
                syarat = f" WHERE ({kunci[0]}, {kunci[1]}) > (?, ?)"
                params = [terakhir[0], terakhir[1]]
            lapor(f"  lanjut dari {dict(zip(kunci, terakhir))}")

    urut = ", ".join(kunci)
    # INSERT biasa, BUKAN INSERT IGNORE: IGNORE di MySQL/MariaDB juga menelan
    # galat "data terlalu panjang" dan "NULL di kolom NOT NULL" — nilainya
    # dipotong diam-diam. Terbukti 28 Sep 2026: 117 kontrol_waktu (maks 179
    # aksara) akan terpotong ke 40. Duplikat tidak mungkin muncul: tanpa
    # --lanjut tabelnya dikosongkan, dengan --lanjut hanya kunci > terakhir.
    sisip = (f"INSERT INTO `{tabel}` ({', '.join(kolom)}) "
             f"VALUES ({', '.join(['%s'] * len(kolom))})")
    baca = sq.execute(f"SELECT {', '.join(kolom)} FROM {tabel}{syarat} ORDER BY {urut}", params)

    total, mulai = 0, time.time()
    while True:
        baris = baca.fetchmany(batch)
        if not baris:
            break
        with my.cursor() as k:
            k.executemany(sisip, [tuple(b) for b in baris])
        my.commit()
        total += len(baris)
        if total % (batch * 20) == 0:
            laju = total / max(time.time() - mulai, 0.001)
            lapor(f"  {total:,} baris ({laju:,.0f}/detik)".replace(",", "."))
    return total


def buat_indeks(my, tabel: str, lapor=print) -> None:
    import pymysql
    for perintah in INDEKS.get(tabel, []):
        try:
            with my.cursor() as k:
                k.execute(perintah)
        except pymysql.err.MySQLError as e:
            if e.args[0] in (1061, 1831):        # indeks sudah ada
                continue
            lapor(f"  indeks gagal ({e.args[0]}): {perintah.split(' ON ')[0]}")
    my.commit()


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=3306)
    p.add_argument("--pengguna", default="root")
    p.add_argument("--sandi", default="", help="XAMPP bawaannya kosong")
    p.add_argument("--db", default="catur", help="nama database MySQL (dibuat kalau belum ada)")
    p.add_argument("--tabel", default="kecil",
                   help="kecil | besar | semua | nama tabel (boleh beberapa, pisah koma)")
    p.add_argument("--batch", type=int, default=5000, help="baris per sekali kirim")
    p.add_argument("--lanjut", action="store_true", help="lanjutkan, jangan kosongkan tabel")
    p.add_argument("--lihat", action="store_true", help="periksa sambungan dan hitung baris saja")
    p.add_argument("--contoh", action="store_true", help="tampilkan contoh query latihan")
    p.add_argument("--sqlite", type=Path, default=PATH_DB)
    a = p.parse_args()

    if a.contoh:
        print(CONTOH)
        return 0

    pilihan = {"kecil": KECIL, "besar": BESAR, "semua": SEMUA}
    tabel = pilihan.get(a.tabel) or tuple(t.strip() for t in a.tabel.split(","))
    tidak_dikenal = [t for t in tabel if t not in SKEMA]
    if tidak_dikenal:
        p.error(f"tabel tidak dikenal: {', '.join(tidak_dikenal)}")

    sq = sqlite3.connect(f"file:{Path(a.sqlite).resolve().as_posix()}?mode=ro", uri=True)
    my = sambung(a)
    try:
        siapkan_database(my, a.db)
        print(f"MySQL {my.get_server_info()} di {a.host}:{a.port}, database `{a.db}`")
        if a.lihat:
            with my.cursor() as k:
                for t in tabel:
                    di_sqlite = sq.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                    try:
                        k.execute(f"SELECT COUNT(*) FROM `{t}`")
                        di_mysql = k.fetchone()[0]
                    except Exception:            # noqa: BLE001 - tabelnya memang belum ada
                        di_mysql = 0
                    print(f"  {t:<18} sqlite {di_sqlite:>10,} | mysql {di_mysql:>10,}".replace(",", "."))
            return 0

        for t in tabel:
            print(f"{t}...")
            mulai = time.time()
            n = salin(sq, my, t, a.lanjut, a.batch)
            buat_indeks(my, t)
            print(f"  selesai: {n:,} baris dalam {time.time() - mulai:.0f} detik".replace(",", "."))
        print("\nSelesai. Buka MySQL Workbench, lalu:  USE " + a.db + ";")
        print("Contoh query latihan: python ke_mysql.py --contoh")
    finally:
        sq.close()
        my.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
