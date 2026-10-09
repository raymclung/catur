"""
Tanya database dengan bahasa Indonesia; LLM lokal menerjemahkannya jadi SQL.

Kenapa begini, bukan LLM yang menjawab langsung: model bahasa tidak bisa
diandalkan soal fakta catur. Diuji di mesin ini 12 Sep 2026 dengan posisi
`6k1/5ppp/8/8/8/8/8/R5RK w` — Stockfish menjawab `Ra8#` dalam 0,001 detik,
`qwen2.5:14b` menjawab `Rxg8#` dalam 64 detik, dan langkah itu **ilegal**
(benteng g1 terhalang pion g7; g8 berisi raja, tidak bisa ditangkap). Jadi
LLM di sini tidak pernah menjadi sumber kebenaran: ia hanya mengarang SQL,
dan database yang menjawab. Tiap angka yang sampai ke Ray berasal dari baris
nyata di `catur.db`, sesuai aturan "jangan mengarang data" di CLAUDE.md.

Risiko yang diterima dan cara menahannya — SQL dari LLM diperlakukan sebagai
masukan bermusuhan, berlapis, pertahanan terpenting di paling bawah:

1. `periksa_sql()`   hanya satu perintah, harus SELECT/WITH, kata kunci
                     perubahan data ditolak mentah-mentah.
2. bungkus LIMIT     hasil dibatasi walaupun LLM lupa menulis LIMIT.
3. koneksi `mode=ro` SQLite membuka berkas hanya-baca di tingkat sistem.
4. `set_authorizer`  tiap operasi yang bukan baca ditolak oleh SQLite
                     sendiri, bukan oleh tebakan regex di berkas ini.

Lapis 1-2 memberi pesan kesalahan yang enak dibaca; lapis 3-4 yang benar-benar
menjamin. Ini penerapan aturan "database yang menolak, bukan kode yang
berjanji": seandainya seluruh pemeriksaan teks di sini punya celah, `mode=ro`
dan authorizer tetap menahan tulisan.

Tanpa dependency baru: Ollama diakses lewat REST API-nya di localhost memakai
`urllib` bawaan, sesuai tabel stack tetap di CLAUDE.md.
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

from . import llm
from .db import PATH_DB

AKAR = Path(__file__).resolve().parent.parent
DB_BAWAAN = PATH_DB            # ikut CATUR_DB, seperti bagian lain aplikasi

HOST_OLLAMA = llm.HOST
MODEL_BAWAAN = llm.MODEL
BATAS_BARIS = 200
TIMEOUT_LLM = llm.TIMEOUT
TIMEOUT_SQL_MS = 15_000

# Tabel yang boleh dibaca. `posisi` (9,4 juta baris) sengaja tidak disertakan:
# pertanyaan bebas yang menyentuhnya hampir selalu berakhir jadi pemindaian
# penuh berpuluh detik. Kebutuhan itu sudah dilayani /api/posisi yang memakai
# indeks.
TABEL_DIIZINKAN = (
    "partai", "pemain", "turnamen", "analisis",
    "analisis_langkah", "sumber_impor", "posisi_tersimpan",
)

# Kolom yang tidak pernah berguna dijawab dan hanya membanjiri hasil.
KOLOM_DISEMBUNYIKAN = {"pgn", "hash_unik", "variasi_terbaik"}

_KATA_TERLARANG = (
    "insert", "update", "delete", "drop", "alter", "create", "replace",
    "attach", "detach", "pragma", "vacuum", "reindex", "trigger",
    "load_extension", "writefile", "readfile",
)


class DatabaseSibuk(RuntimeError):
    """Database terkunci penulis lain; pertanyaannya sendiri tidak salah."""


class SqlDitolak(ValueError):
    """SQL dari LLM tidak lolos pemeriksaan (jadi HTTP 422)."""


# Dipakai ulang dari app/llm.py supaya pemanggil cukup menangkap satu jenis
# kesalahan, dari modul mana pun fitur LLM-nya datang.
LlmTidakAda = llm.LlmTidakAda


def _buka_ro(db: Path) -> sqlite3.Connection:
    """Koneksi hanya-baca, plus authorizer yang menolak operasi bukan baca.

    Dua-duanya dipasang karena menjawab ancaman yang berbeda: `mode=ro`
    menahan tulisan ke berkas, authorizer menahan operasi yang tidak
    menyentuh berkas ini tapi tetap tidak diinginkan (ATTACH ke berkas lain,
    misalnya).
    """
    if not db.exists():
        raise FileNotFoundError(f"database tidak ada: {db}")
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=10)
    con.row_factory = sqlite3.Row
    con.execute(f"PRAGMA busy_timeout = {TIMEOUT_SQL_MS}")

    diizinkan = {sqlite3.SQLITE_READ, sqlite3.SQLITE_SELECT}
    fungsi = getattr(sqlite3, "SQLITE_FUNCTION", None)
    if fungsi is not None:          # perlu untuk COUNT/AVG/MAX
        diizinkan.add(fungsi)

    def penjaga(aksi, arg1, arg2, nama_db, pemicu):
        return sqlite3.SQLITE_OK if aksi in diizinkan else sqlite3.SQLITE_DENY

    con.set_authorizer(penjaga)
    return con


def skema_untuk_prompt(db: Path = DB_BAWAAN) -> str:
    """Skema tabel yang diizinkan, dibaca dari database itu sendiri.

    Dibaca lewat PRAGMA, tidak ditulis ulang sebagai teks di berkas ini:
    salinan skema selalu menyimpang dari `app/db.py` yang jadi sumber
    kebenaran (aturan CLAUDE.md bagian 4).
    """
    # PRAGMA butuh koneksi tanpa authorizer; authorizer read-only memblokirnya.
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=10)
    try:
        potongan = []
        for tabel in TABEL_DIIZINKAN:
            kolom = [
                f"{r[1]} {r[2]}"
                for r in con.execute(f"PRAGMA table_info({tabel})")
                if r[1] not in KOLOM_DISEMBUNYIKAN
            ]
            if not kolom:
                continue
            jumlah = con.execute(f"SELECT COUNT(*) FROM {tabel}").fetchone()[0]
            potongan.append(f"{tabel} ({jumlah:,} baris): " + ", ".join(kolom))
        return "\n".join(potongan)
    finally:
        con.close()


def bangun_prompt(pertanyaan: str, skema: str) -> str:
    """Prompt yang memaksa jawaban berupa satu SELECT, tanpa basa-basi."""
    return f"""Kamu penerjemah pertanyaan bahasa Indonesia menjadi SQL SQLite.

Skema database catur (hanya tabel ini yang ada):
{skema}

Keterangan kolom yang mudah disalahpahami:
- **pemain.nama berformat "Belakang, Depan"** — 'Carlsen, Magnus', BUKAN
  'Magnus Carlsen'. Sebagian sumber menyingkat nama depan ('Carlsen,M').
  Jadi untuk mencari pemain, SELALU pakai LIKE pada nama belakang:
  `pemain.nama LIKE 'Carlsen%'`. Jangan pakai `=` dengan nama lengkap.
- partai.pembukaan dan partai.eco ada di tabel partai, bukan di tabel lain
- partai.hasil berisi '1-0', '0-1', '1/2-1/2', atau '*' untuk partai yang
  belum selesai (16 baris, sisa broadcast yang tertangkap saat siaran masih
  berjalan). Jangan menganggap jumlah menang + kalah + remis = jumlah partai.
- partai.putih_id dan partai.hitam_id menunjuk ke pemain.id
- **analisis.partai_id menunjuk ke partai.id, BUKAN ke pemain.id.** Untuk
  akurasi seorang pemain, JOIN pemain ke partai dulu, lalu partai ke analisis,
  dan pakai akurasi_putih hanya kalau pemain itu bermain putih
  (`pa.putih_id = p.id`), akurasi_hitam kalau dia bermain hitam.
- partai.tanggal berformat teks 'YYYY.MM.DD' atau 'YYYY-MM-DD'
- partai.sumber berisi 'twic', 'pgnmentor', 'lichess-user', 'chesscom',
  'lichess-broadcast', atau 'file'
- pemain.gelar berisi 'GM', 'IM', 'FM', dan sejenisnya; boleh NULL
- pemain.akun_pribadi = 1 untuk akun Ray sendiri
- analisis_langkah.klasifikasi berisi 'blunder', 'mistake', 'inaccuracy',
  atau NULL untuk langkah biasa

Aturan jawaban:
- Jawab HANYA satu perintah SELECT. Tanpa penjelasan, tanpa blok kode,
  tanpa titik koma di akhir.
- Jangan menulis INSERT, UPDATE, DELETE, DROP, ALTER, CREATE, ATTACH,
  atau PRAGMA.
- Beri alias yang jelas untuk tiap kolom hasil hitungan.
- Kalau pertanyaannya menyebut nama pemain, JOIN ke tabel pemain.

Pertanyaan: {pertanyaan}

SQL:"""


def _bersihkan_jawaban(mentah: str) -> str:
    """Buang blok kode, label, dan titik koma yang biasa ditempel LLM."""
    teks = (mentah or "").strip()
    teks = re.sub(r"^```(?:sql)?\s*", "", teks, flags=re.I)
    teks = re.sub(r"\s*```$", "", teks)
    teks = re.sub(r"^\s*sql\s*:?\s*", "", teks, flags=re.I)
    return teks.strip().rstrip(";").strip()


def periksa_sql(sql: str) -> str:
    """Kembalikan SQL yang lolos, atau lempar `SqlDitolak` dengan alasannya.

    Ini lapis kenyamanan, bukan lapis keamanan: yang benar-benar menahan
    adalah `mode=ro` + authorizer di `_buka_ro()`. Gunanya di sini supaya Ray
    melihat alasan yang bisa dibaca, bukan `sqlite3.DatabaseError` mentah.
    """
    bersih = _bersihkan_jawaban(sql)
    if not bersih:
        raise SqlDitolak("LLM tidak mengembalikan SQL apa pun.")

    # Titik koma di dalam teks berkutip bukan pemisah perintah.
    tanpa_teks = re.sub(r"'[^']*'", "''", bersih)
    if ";" in tanpa_teks:
        raise SqlDitolak("SQL berisi lebih dari satu perintah.")

    if not re.match(r"^\s*(select|with)\b", bersih, flags=re.I):
        raise SqlDitolak("SQL harus dimulai dengan SELECT atau WITH.")

    tanpa_komentar = re.sub(r"--[^\n]*", " ", tanpa_teks)
    for kata in _KATA_TERLARANG:
        if re.search(rf"\b{kata}\b", tanpa_komentar, flags=re.I):
            raise SqlDitolak(
                f"SQL memuat kata kunci yang tidak diizinkan: {kata.upper()}."
            )

    dipakai = {
        m.lower()
        for m in re.findall(
            r"\b(?:from|join)\s+([A-Za-z_][A-Za-z0-9_]*)", tanpa_komentar, flags=re.I
        )
    }
    # alias CTE (WITH x AS (...)) bukan tabel sungguhan
    cte = {
        m.lower()
        for m in re.findall(
            r"\b([A-Za-z_][A-Za-z0-9_]*)\s+as\s*\(", tanpa_komentar, flags=re.I
        )
    }
    asing = dipakai - set(TABEL_DIIZINKAN) - cte
    if asing:
        raise SqlDitolak(
            "Tabel tidak dikenal atau tidak diizinkan: " + ", ".join(sorted(asing))
        )
    return bersih


def minta_sql(pertanyaan: str, *, model: str = MODEL_BAWAAN,
              host: str = HOST_OLLAMA, skema: str | None = None,
              db: Path = DB_BAWAAN, timeout: int = TIMEOUT_LLM) -> str:
    """Minta LLM menerjemahkan pertanyaan; kembalikan SQL mentah (belum diperiksa)."""
    if skema is None:
        skema = skema_untuk_prompt(db)
    return llm.panggil(
        bangun_prompt(pertanyaan, skema),
        model=model, host=host, timeout=timeout, num_predict=400,
    )


def jalankan_sql(sql: str, *, db: Path = DB_BAWAAN,
                 batas: int = BATAS_BARIS) -> tuple[list[str], list[list]]:
    """Jalankan SQL hanya-baca yang sudah lolos `periksa_sql()`.

    SQL dibungkus subquery ber-LIMIT, bukan disisipi LIMIT: menempelkan teks
    ke SQL orang lain gampang salah kalau ada ORDER BY atau UNION, sedangkan
    membungkus selalu benar dan batasnya tidak bisa dihindari.
    """
    con = _buka_ro(db)
    try:
        kursor = con.execute(f"SELECT * FROM ({sql}) LIMIT {int(batas)}")
        kolom = [d[0] for d in kursor.description or []]
        baris = [list(r) for r in kursor.fetchall()]
        return kolom, baris
    except sqlite3.OperationalError as e:
        # Audit 27 Sep 2026: kolom/tabel karangan LLM ("no such column") dulu
        # lolos jadi 500. Itu kesalahan SQL-nya -> 422 dengan pesan yang bisa
        # dibaca. Database terkunci bukan kesalahan pertanyaan -> 503.
        pesan = str(e)
        if "locked" in pesan or "busy" in pesan:
            raise DatabaseSibuk(f"Database sedang sibuk ({pesan}); coba lagi sebentar.") from e
        raise SqlDitolak(f"SQL dari LLM tidak bisa dijalankan: {pesan}. SQL: {sql}") from e
    finally:
        con.close()


def tanya(pertanyaan: str, *, model: str = MODEL_BAWAAN, host: str = HOST_OLLAMA,
          db: Path = DB_BAWAAN, batas: int = BATAS_BARIS) -> dict:
    """Rangkaian penuh: pertanyaan jadi SQL, SQL jadi baris database.

    Yang dikembalikan selalu memuat `sql` yang dipakai, supaya tiap angka bisa
    ditelusuri dan diperiksa ulang Ray sendiri — bukan kotak hitam.
    """
    if not (pertanyaan or "").strip():
        raise SqlDitolak("Pertanyaan kosong.")
    mentah = minta_sql(pertanyaan, model=model, host=host, db=db)
    sql = periksa_sql(mentah)
    kolom, baris = jalankan_sql(sql, db=db, batas=batas)
    return {
        "pertanyaan": pertanyaan,
        "sql": sql,
        "kolom": kolom,
        "baris": baris,
        "jumlah": len(baris),
        "terpotong": len(baris) >= batas,
        "model": model,
        "peringatan": peringatan_hasil(sql, baris),
    }


def peringatan_hasil(sql: str, baris: list) -> list[str]:
    """Pola SQL yang jalan tanpa error tapi jawabannya menyesatkan.

    Diperlukan karena kesalahan LLM yang paling berbahaya bukan yang bikin
    error — itu langsung terlihat — melainkan yang menghasilkan angka rapi
    dari SQL yang salah. Dua-duanya kejadian saat banding model 12 Sep 2026:

    - `WHERE pemain.nama = 'Magnus Carlsen'` → 0 baris, karena database
      menyimpan 'Carlsen, Magnus'. Tanpa peringatan, Ray akan menyimpulkan
      Carlsen tidak pernah bermain putih.
    - JOIN `p.id = pa.putih_id OR p.id = pa.hitam_id` lalu `AVG(akurasi_putih)`
      → angka yang mencampur akurasi pemain itu dengan akurasi lawannya.

    Ini petunjuk, bukan jaminan: tidak semua SQL salah tertangkap di sini,
    dan SQL yang ditandai belum tentu salah.
    """
    pesan = []
    if not baris:
        pesan.append(
            "Hasilnya kosong. Sering berarti SQL-nya salah, bukan datanya "
            "tidak ada — misalnya nama dicari dengan '=' padahal database "
            "memakai format 'Belakang, Depan'. Periksa SQL di atas."
        )
    padat = " ".join(sql.split()).lower()
    if re.search(r"join\s+\w+\s+\w*\s*on[^)]*\bor\b", padat):
        pesan.append(
            "SQL memakai JOIN dengan OR (pola 'putih_id OR hitam_id'). Itu "
            "benar untuk MENGHITUNG partai, tapi salah untuk merata-ratakan "
            "kolom yang khusus satu warna seperti akurasi_putih."
        )
    # Hanya diperingatkan kalau yang disamakan dengan partai_id benar-benar
    # alias tabel pemain. Versi pertama cuma mencari pola "…id = …partai_id"
    # lalu menandai `pa.id = a.partai_id` yang justru benar — peringatan
    # palsu lebih merugikan daripada tidak ada peringatan, karena Ray jadi
    # berhenti mempercayai semuanya.
    for alias in re.findall(r"\b(?:from|join)\s+pemain\s+(?:as\s+)?(\w+)", padat):
        if alias in ("on", "where", "group", "join"):      # pemain tanpa alias
            continue
        if re.search(rf"\bon\s+{alias}\.id\s*=\s*\w+\.partai_id\b", padat) or \
           re.search(rf"\bon\s+\w+\.partai_id\s*=\s*{alias}\.id\b", padat):
            pesan.append(
                f"SQL menyamakan pemain.id (alias `{alias}`) dengan partai_id. "
                "Keduanya tidak berhubungan; hasilnya tidak berarti."
            )
            break
    return pesan
