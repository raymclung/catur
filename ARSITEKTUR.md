# Arsitektur — Chess GM Database

> **Satu berkas, satu rancangan.** Sampai 12 Sep 2026 dokumen ini terpecah jadi
> `ARSITEKTUR-PHASE1.md` … `ARSITEKTUR-PHASE5.md`. Ray menggabungkannya supaya
> revisi cukup **mengedit bagian yang relevan**, bukan menambah berkas baru —
> lima salinan rancangan yang sama selalu berakhir saling menyimpang.
>
> **Fase berikutnya = bagian `## Phase N` baru di berkas ini.** Jangan membuat
> `ARSITEKTUR-PHASE6.md`.
>
> Isi kelima berkas lama dipindahkan utuh; hanya level heading yang diturunkan
> satu tingkat. Penyimpangan sadar dari `RENCANA-PROYEK.md` tetap dicatat di
> [Phase 1 bagian 10](#10-penyimpangan-sadar-dari-rencana-proyekmd).

## Daftar isi

- [Phase 1 — Chess GM Database](#phase-1--chess-gm-database)
  - [1. Analisis requirement](#1-analisis-requirement)
  - [2. Rekomendasi tech stack](#2-rekomendasi-tech-stack)
  - [3. Arsitektur](#3-arsitektur)
  - [4. Struktur folder](#4-struktur-folder)
  - [5. Skema database](#5-skema-database)
  - [6. Dependency](#6-dependency)
  - [7. Rencana implementasi bertahap](#7-rencana-implementasi-bertahap)
  - [8. Keadaan data saat ini](#8-keadaan-data-saat-ini)
  - [9. Yang TIDAK dikerjakan di Phase 1](#9-yang-tidak-dikerjakan-di-phase-1)
  - [10. Penyimpangan sadar dari RENCANA-PROYEK.md](#10-penyimpangan-sadar-dari-rencana-proyekmd)
- [Phase 2 — Analisis Engine](#phase-2--analisis-engine)
  - [Tujuan](#tujuan)
  - [Satu keputusan arsitektur yang perlu disadari](#satu-keputusan-arsitektur-yang-perlu-disadari)
  - [Skema tambahan (skema v2)](#skema-tambahan-skema-v2)
  - [Langkah](#langkah)
  - [Yang dipakai ulang tanpa diubah](#yang-dipakai-ulang-tanpa-diubah)
- [Phase 3 — Data GM dari Arsip TWIC & Statistik Pembukaan](#phase-3--data-gm-dari-arsip-twic--statistik-pembukaan)
  - [Kenapa ini Phase 3](#kenapa-ini-phase-3)
  - [Temuan yang menentukan desain (diukur 9 Sep 2026)](#temuan-yang-menentukan-desain-diukur-9-sep-2026)
  - [Skema v3 (aditif)](#skema-v3-aditif)
  - [Langkah](#langkah)
- [Phase 4 — Penjelajah Pembukaan](#phase-4--penjelajah-pembukaan)
  - [Kenapa ini Phase 4](#kenapa-ini-phase-4)
  - [Desain](#desain)
  - [Langkah](#langkah)
- [Phase 5 — Papan Analisis (engine di dalam server)](#phase-5--papan-analisis-engine-di-dalam-server)
  - [Keputusan yang dibalik, dan kenapa](#keputusan-yang-dibalik-dan-kenapa)
  - [Kontrak GET /api/evaluasi](#kontrak-get-apievaluasi)
  - [Bukti (10 Sep 2026)](#bukti-10-sep-2026)
  - [Yang tetap seperti sebelumnya](#yang-tetap-seperti-sebelumnya)
  - [Ditunda ke versi berikutnya (dicatat, bukan dilupakan)](#ditunda-ke-versi-berikutnya-dicatat-bukan-dilupakan)

- [Phase 6 — Tanya Database dengan Bahasa Indonesia](#phase-6--tanya-database-dengan-bahasa-indonesia)
  - [Kenapa LLM tidak boleh jadi sumber kebenaran](#kenapa-llm-tidak-boleh-jadi-sumber-kebenaran)
  - [Desain: LLM mengarang SQL, database menjawab](#desain-llm-mengarang-sql-database-menjawab)
  - [Empat lapis pagar](#empat-lapis-pagar)
  - [Bukti (12 Sep 2026)](#bukti-12-sep-2026)
  - [Batas yang diketahui](#batas-yang-diketahui)
  - [Belum tersambung](#belum-tersambung)
---

## Phase 1 — Chess GM Database

Dokumen ini menjawab permintaan: analisis requirement, rekomendasi tech stack,
arsitektur, struktur folder, skema database, dan daftar dependency —
**sebelum** implementasi dimulai.

Status: **disetujui 9 Sep 2026.** Implementasi berjalan — lihat bagian 7.

| | |
|---|---|
| Langkah selesai | **8 dari 8 — Phase 1 berjalan penuh** (9 Sep 2026) |
| Isi database | 11.690 partai · 7.161 pemain · 674 turnamen · 79 pemain terverifikasi FIDE |
| Sumber partai | `lichess-user` 10.677 · `chesscom` 895 · `lichess-broadcast` 118 (partai GM) |
| Dependency baru terinstall | fastapi 0.141.1 + uvicorn ✅ |
| Database | `data/catur.db`, skema v1, 7 index. Skema: `app/db.py` (sumber kebenaran, bukan dokumen ini) |
| Tes otomatis | `python -m unittest discover -s tests -v` — 27 tes |
| Riwayat | git, sejak 9 Sep 2026 |

---

### 1. Analisis requirement

#### Yang harus jalan di akhir Phase 1

| # | Kriteria sukses | Konsekuensi teknis |
|---|---|---|
| 1 | Aplikasi jalan lokal | Perlu server HTTP, bukan sekadar file HTML |
| 2 | Lihat daftar partai | Endpoint daftar + paginasi (11.712 partai tidak boleh dikirim sekaligus) |
| 3 | Buka satu partai | Endpoint detail |
| 4 | Lihat papan catur | Library papan di browser |
| 5 | Next / Previous move | State langkah di frontend |
| 6 | Replay seluruh partai | Parsing PGN di browser |
| 7 | Lihat pemain, turnamen, tanggal, hasil | Skema relasional dengan join |
| 8 | Data tersimpan benar | Database + dedup + validasi saat impor |

#### Risiko utama yang harus ditangani sejak awal

**a. Identitas pemain.** Ini penyebab kegagalan paling umum di database catur
buatan sendiri. Nama pemain di PGN tidak konsisten: `Carlsen, Magnus` /
`Magnus Carlsen` / `Carlsen,M.` semuanya orang yang sama. Kalau ditangani
belakangan, tabel pemain sudah telanjur penuh duplikat dan sulit dibersihkan.

*Penyelesaian:* pakai `fide_id` sebagai kunci identitas kalau tersedia — dan
broadcast Lichess menyediakannya. Kalau tidak ada, pakai nama yang
dinormalisasi, dan tandai bahwa identitasnya belum terverifikasi.

*Batas yang diketahui:* normalisasi hanya menyamakan huruf besar-kecil, tanda
baca, dan gelar, jadi `carlsen magnus` ≠ `carlsen m` (dicatat sebagai tes di
`tests/test_impor.py`). Pencocokan inisial (B13) menyatukan singkatan yang
hanya punya **satu** calon nama lengkap. Diukur 9 Okt 2026: dari 3.257 nama
singkat, 3.239 tidak punya pasangan nama lengkap sama sekali (jadi bukan
pemain ganda), dan hanya **18 nama / 63 partai** yang bercalon ganda. Sebelas
digabung atas pilihan Ray setelah Elo per tahunnya dibandingkan dengan tiap
calon (log `data/laporan/log-gabung-singkatan-20261009-*.txt`). Tujuh sisanya
sengaja dibiarkan karena datanya sendiri menolak tebakan: `Volkov,S` dan
`Petrov,M` ternyata campuran dua orang (Elo 2554–2632 lalu 2296–2456; atau
dua rentang tahun yang tidak bersambung), `Xu,X` ber-Elo 2424 saat Xu
Xiangyu sudah 2570, dan `Chen, J.`, `Ravi,T`, `Szabo,K`, `Novikov,S` tidak
bisa dibedakan dari calonnya.

*Batas yang diketahui (gelar & negara, diukur 25 Sep 2026):* kolom `gelar`
hanya bisa diisi untuk pemain yang punya `fide_id`, karena satu-satunya sumber
gelar yang tidak mengarang adalah FIDE (`rapikan_nama.py`). FIDE ID hanya
datang dari TWIC dan broadcast Lichess; PGN Mentor dan akun online tidak
membawanya, dan PGN yang tersimpan juga tidak memuat tag `WhiteTitle` (0 dari
272.960 partai). Setelah `rapikan_nama.py --semua --jalan` (1.579 permintaan
ke FIDE): 3.231 pemain ber-FIDE-ID (16%), **2.559 di antaranya bergelar (79%;
sisanya memang tidak bergelar menurut FIDE)**, negara 1.760 → 3.216. Secara
keseluruhan gelar terisi 13% — dan angka itu **langit-langitnya**, bukan
kelalaian: 84% pemain tidak punya ID di sumber mana pun. Akibat untuk aturan
produk: saringan "lawan bergelar" (CLAUDE.md bagian 11) dan "gelar WGM" pada
impor broadcast hanya menyala untuk pemain TWIC/broadcast. Dua jalan yang
menaikkannya tanpa mengarang: penggabungan pemain kembar (baris pgnmentor
melebur ke baris TWIC yang ber-FIDE-ID), dan untuk lawan akun online, gelar
dari profil akun lewat API Lichess/Chess.com (`app/gelar_akun.py`, 25 Sep
2026 — hanya gelar FIDE, `LM`/`BOT` dibuang). Pencarian *nama* ke FIDE
semula sengaja tidak dipakai: dua orang bisa senama, dan itu menebak.

*Diubah 9 Okt 2026 (Ray: "Tulis", setelah melihat uji kering):* pencocokan
nama ke daftar pemain resmi FIDE (`cocokkan_fide.py`, `players_list.zip` dari
ratings.fide.com) dipakai, tapi hanya kalau semua pagar terpenuhi: nama unik
di daftar FIDE, bukan singkatan, bukan akun online, umur masuk akal (partai
pertama >= tahun lahir + 6), dan Elo >= 2300 di partai harus bergelar di FIDE.
Dua pagar terakhir lahir dari uji kering: `Botvinnik, Mikhail` (partai 1936)
hampir dicocokkan ke orang Israel kelahiran 1983, dan `Larsen, Bent` (GM,
Elo 2660) ke orang Denmark senama tanpa gelar. Hasil: **2.532 pemain dapat
FIDE ID, gelar terisi 3.087 → 5.039 (15% → 25%)**. `terverifikasi` tidak
dinaikkan; tiap baris ada di `data/laporan/cocokkan-fide-20261009-224901.tsv`
supaya bisa dibatalkan satu per satu. **Batas yang diterima:** orang yang
namanya unik di FIDE tapi partainya sebenarnya milik orang lain yang tidak
terdaftar FIDE tetap bisa lolos; pagar umur dan Elo menahan sebagian besar,
bukan semuanya. Yang sengaja tidak tersentuh: 2.507 pemain tidak ada di FIDE
sama sekali (kebanyakan pemain lama), 301 bernama ganda di FIDE, 3.310 nama
singkat, 7.513 akun online.

*Diperbaiki 12 Sep 2026 (B13):* nama yang depannya disingkat kini dicocokkan
satu lapis di atas normalisasi, di `dapatkan_atau_buat_pemain()`. `Aronian,L`
menemukan `Aronian, Levon` kalau — dan hanya kalau — kedua nama berkoma
(`Belakang, Depan`), nama belakangnya sama persis, huruf depannya sama, dan
**calonnya tepat satu**. `Smith,J` yang bisa berarti John atau Jane tetap jadi
pemain baru; dua nama lengkap berbeda (`Levon` vs `Leonid`) tetap dua orang.
Alasannya diukur: tanpa ini, impor 32 berkas PGN Mentor melahirkan 499 pemain
ganda yang menyangkut 50.673 kemunculan partai. Sepuluh tes di
`tests/test_nama_inisial.py` menjaga kedua arah: yang sama menyatu, yang
berbeda tetap terpisah.

**b. Impor ganda.** Turnamen yang sedang berlangsung akan diambil berkali-kali;
partai yang sama tidak boleh masuk dua kali.

*Penyelesaian:* hash unik per partai, dengan constraint `UNIQUE` di database —
bukan pengecekan di kode. Database yang menolak, bukan program yang berjanji.

*Kasus yang tidak diselesaikan hash:* partai broadcast yang diambil saat masih
berjalan punya hasil `*` dan langkah yang belum lengkap. Versi selesainya punya
hash **berbeda**, jadi tanpa aturan tambahan tiap impor ulang menambah satu
versi basi. Aturannya (di `impor.py`): kalau ada partai tersimpan dengan
turnamen + babak + pasangan pemain yang sama dan hasilnya masih `*`, partai
itu **diperbarui**, bukan ditambah. Aturan ini menuntut turnamen dan babak
terisi, jadi tidak pernah menyentuh partai online Ray (semuanya `Round "-"`).

**c. Ukuran data.** Sudah ada 11.712 partai pribadi, ditambah partai broadcast
yang terus bertambah.

*Penyelesaian:* paginasi wajib sejak awal, plus index pada kolom yang dicari.

**d. Dua jenis data dalam satu aplikasi.** Partai GM (untuk dipelajari) dan
partai Ray sendiri (untuk dianalisis nanti) punya skema yang identik.

*Penyelesaian:* satu tabel, dibedakan kolom `sumber`. Bukan dua sistem
terpisah — itu akan menduplikasi seluruh kode tanpa manfaat.

---

### 2. Rekomendasi tech stack

#### Backend: Python 3.12 + FastAPI + Uvicorn

**Alasan memilih FastAPI:**

- Validasi request otomatis. Parameter salah ditolak sebelum menyentuh
  database, tanpa perlu menulis pengecekan manual di tiap endpoint.
- Dokumentasi API otomatis di `/docs`. Saat membangun frontend, endpoint bisa
  dicoba langsung dari browser tanpa menulis kode dulu. Ini menghemat banyak
  waktu debugging.
- Stabil dan banyak dipakai, bukan pilihan eksperimental.

**Alternatif yang dipertimbangkan dan alasan tidak dipilih:**

| Pilihan | Kenapa tidak |
|---|---|
| Flask | Lebih ringan, tapi tanpa validasi dan tanpa `/docs`. Keduanya harus ditulis manual — hemat 1 dependency, tambah pekerjaan terus-menerus. |
| `http.server` bawaan Python (seperti proyek puzzle) | Nol dependency, tapi routing ditulis tangan. Untuk 4 endpoint masih wajar; begitu bertambah, jadi beban perawatan. |
| Node/Express | Node.js tidak terinstall, dan seluruh perkakas catur yang sudah ada di mesin ini berbasis Python. |
| Django | Terlalu besar. Admin panel dan sistem user tidak dibutuhkan Phase 1. |

#### Database: SQLite lewat modul `sqlite3` bawaan Python

**Alasan:** nol konfigurasi, satu file, tidak perlu server terpisah. Untuk
skala ratusan ribu partai masih jauh dari batasnya. Proyek puzzle-mu juga
sudah memakai SQLite, jadi polanya sudah familiar.

**Tanpa ORM (tanpa SQLAlchemy).** Phase 1 cuma 3 tabel dengan query
sederhana. ORM menambah lapisan abstraksi dan kurva belajar yang besar tanpa
memberi keuntungan pada ukuran ini. SQL mentah di satu modul repositori lebih
mudah dibaca dan di-debug.

Kalau nanti benar-benar perlu pindah ke PostgreSQL, SQL-nya hampir tidak
berubah karena tidak memakai fitur khusus SQLite.

##### Kenapa bukan SQL Server, padahal SSMS sudah terinstall

Hasil pemeriksaan mesin ini (9 Sep 2026):

```
SQL Server Management Studio 22          terinstall
ODBC Driver 18 / OLE DB Driver 19        terinstall
Service SQL Server (engine-nya sendiri)  TIDAK ADA
```

Yang terpasang cuma **klien** untuk menyambung ke SQL Server, bukan mesin
databasenya. Memakai SQL Server berarti harus install SQL Server Express
(~1 GB) plus service yang berjalan terus-menerus.

Itu masalah nyata di laptop ini: RAM bebas cuma sekitar 2,5 GB dari 15,7 GB,
sementara Stockfish di Phase 2 sudah butuh ~500 MB sendiri.

Konsekuensi yang diterima: **SSMS tidak bisa membuka file SQLite.** Untuk
melihat isi database lewat GUI, pakai extension SQLite di VS Code.

#### Parsing catur

| Tempat | Library | Status |
|---|---|---|
| Backend (impor PGN, ekstrak metadata) | `python-chess` 1.11.2 | ✅ sudah terinstall |
| Frontend (replay, validasi langkah) | `chess.js` | ✅ sudah ada di proyek puzzle |
| Frontend (menggambar papan) | `chessboard.js` 1.0.0 | ✅ sudah ada di proyek puzzle |

Ketiganya sudah tersedia. Tidak ada yang perlu diunduh.

#### Frontend: HTML + CSS + JavaScript biasa

**Tanpa React/Vue/Svelte.** Alasannya:

- Node.js tidak terinstall, dan framework modern butuh build step.
- Phase 1 cuma dua halaman: daftar partai dan halaman partai.
- `chessboard.js` adalah library jQuery-style yang justru lebih mudah dipakai
  tanpa framework.

Mode terang saja, sesuai permintaan.

#### Deployment

Phase 1: lokal saja (`uvicorn`). Pembahasan hosting ditunda sampai aplikasinya
benar-benar jalan.

---

### 3. Arsitektur

```
   Browser
   ├── index.html    daftar + pencarian partai
   └── partai.html   papan catur + replay
        │  chessboard.js (gambar)  +  chess.js (aturan & PGN)
        │
        │  HTTP / JSON
        ▼
   FastAPI  (app/)
   ├── main.py        rute HTTP
   ├── repositori.py  query database
   └── impor.py       PGN -> database
        │
        │  sqlite3
        ▼
   data/catur.db
        ▲
        │
   Sumber PGN
   ├── file lokal        data/pgn/*.pgn  (11.712 partai Ray)
   └── broadcast Lichess https://lichess.org/api/broadcast/round/{id}.pgn
```

Satu aturan yang dijaga: **frontend tidak pernah menyentuh database
langsung**, dan **modul impor tidak pernah tahu soal HTTP**. Keduanya cuma
bertemu lewat repositori. Ini yang membuat impor bisa dijalankan dari
command line tanpa menyalakan server.

---

### 4. Struktur folder

```
Catur/
├── app/                        ← BARU (Phase 1)
│   ├── __init__.py             ✅ ada
│   ├── db.py                   ✅ ada — koneksi + pembuatan skema
│   ├── impor.py                ✅ ada, teruji — PGN -> database, dedup, pembaruan broadcast
│   ├── repositori.py           ✅ ada, dibuktikan lewat /api/partai (200, 404, 422)
│   └── main.py                 ✅ ada, dibuktikan — 11 aset statis 200 OK
│
├── tests/                      ← BARU
│   ├── test_db.py              skema: 11 tes, aturan yang ditegakkan database
│   └── test_impor.py           impor: 16 tes, dedup + broadcast + turnamen + nama
│
├── web/                        ← BARU (Phase 1)
│   ├── index.html              daftar partai
│   ├── partai.html             papan + replay
│   ├── gaya.css
│   ├── daftar.js
│   ├── papan.js
│   └── vendor/                 chessboard.js, chess.js (disalin dari puzzle)
│
├── impor_pgn.py                🔶 ada, belum dijalankan ke file nyata
├── impor_broadcast.py          ⬜ CLI impor broadcast Lichess
├── .gitignore                  catur.db, engine/, __pycache__ tidak ikut git
│
├── data/
│   ├── catur.db                ← BARU (turunan; tidak masuk git)
│   ├── pgn/                    11.712 partai (sudah ada)
│   └── laporan/
│
├── ambil_partai.py             sudah ada
├── saring_partai.py            sudah ada
├── analisis.py                 sudah ada  ← Phase 2, TIDAK dipakai Phase 1
├── cek_engine.py               sudah ada  ← Phase 2
├── engine/stockfish.exe        sudah ada  ← Phase 2
└── src/catur_analysis/         sudah ada  ← Phase 2
```

Kode analisis Stockfish yang sudah dibangun **tidak dihapus dan tidak
diutak-atik**. Dia menganggur sampai Phase 2, sesuai permintaan bahwa Phase 1
tidak menyertakan engine.

---

### 5. Skema database

**SQL-nya ada di `app/db.py`, dan hanya di sana.** Dokumen ini pernah menyalin
skemanya dan salinan itu langsung menyimpang (kurang `CHECK` pada
`terverifikasi`, kurang `idx_partai_sumber`). Salinan selalu menyimpang, jadi
dihapus. Yang dipertahankan di sini hanya *alasan* di balik bentuknya.

Tiga tabel: `pemain`, `turnamen`, `partai`. Tujuh index. Aturan yang ditegakkan
database, masing-masing dibuktikan oleh satu tes di `tests/test_db.py`:

| Aturan | Mekanisme |
|---|---|
| Satu FIDE ID = satu pemain | `UNIQUE` pada `fide_id` (NULL boleh berulang) |
| `terverifikasi` hanya 0 atau 1 | `CHECK` |
| Partai harus menunjuk pemain yang ada | `FOREIGN KEY` + `PRAGMA foreign_keys = ON` |
| Partai tidak boleh ganda | `UNIQUE` pada `hash_unik` |
| Turnamen dibedakan per tahun | `UNIQUE(nama, tahun)` |

#### Keputusan skema yang perlu dijelaskan

**PGN utuh disimpan di kolom `pgn`.** Terlihat boros, tapi ini jaring
pengaman: kalau nanti ternyata ada metadata yang lupa diekstrak, datanya masih
utuh dan tinggal diproses ulang tanpa mengunduh ulang apa pun.

**`hash_unik` dengan constraint `UNIQUE`.** Dihitung dari nama kedua pemain
yang dinormalisasi + tanggal + hasil + rangkaian langkah. Yang menolak duplikat
adalah database, bukan kode — jadi impor bisa dijalankan berulang kali dengan
aman, termasuk untuk turnamen yang masih berlangsung.

**`terverifikasi`.** Bernilai 1 hanya kalau pemain punya `fide_id`. Ini
mencegah kesan bahwa semua identitas pemain sudah pasti benar, padahal yang
tanpa FIDE ID masih hasil pencocokan nama. Sesuai aturanmu: kalau tidak yakin,
katakan tidak yakin — jangan mengarang.

**`hasil` boleh `*`.** Partai broadcast yang masih berjalan belum punya hasil.
Menyimpannya sebagai `*` (standar PGN) lebih jujur daripada mengosongkan atau
menebak. Baris ber-`*` inilah yang boleh diperbarui saat impor ulang (lihat
risiko 1b).

**Event generik bukan turnamen.** `rated bullet game`, `Live Chess`, dan
sejenisnya adalah partai lepas. Kalau dijadikan turnamen, tabel `turnamen`
berisi baris seperti "rated bullet game / 2025" dengan ribuan partai yang
tidak berarti apa-apa. Partai seperti itu disimpan dengan `turnamen_id` NULL.
Informasinya tidak hilang: kecepatan ada di `kontrol_waktu`, PGN utuh ada di
`pgn`. Arena dan Team Battle tetap dianggap turnamen.

**Arena online yang berulang digabung per tahun, dan itu diterima.** Kunci
turnamen adalah `(nama, tahun)`, jadi ratusan "Hourly Bullet Arena" dalam
setahun menjadi satu baris. Keputusan Ray (9 Sep 2026): fokus proyek ini adalah
turnamen yang di-broadcast Lichess/Chess.com tempat GM super bermain, dan
untuk itu `(nama, tahun)` sudah tepat ("Norway Chess", 2026). Statistik per
event untuk arena online **bukan** tujuan proyek, jadi kuncinya tidak diubah.

**Tag `ChessComTournament` didahulukan atas `Event`.** Semua partai Chess.com
ber-`Event "Live Chess"`; nama turnamen sebenarnya hanya ada di tag tambahan
yang ditulis `ambil_partai.py`. Isinya URL API, jadi nama diambil dari
ujungnya (`arena-kings-31036071`) dan URL-nya disimpan di `turnamen.sumber_url`.

**`kontrol_normal` adalah field respons, bukan kolom** (12 Sep 2026, Ray: *"180+0
ini ganti jadi 3+0 saja pakai format normal"*). Tag PGN `TimeControl` disimpan
apa adanya di `kontrol_waktu` (`"180+0"` = 180 detik), sementara yang dibaca
pemain adalah `"3+0"`. Bentuk tampilannya dihitung `kontrol_normal()` di
`app/format_partai.py` dan ditempelkan ke tiap baris partai oleh
`_baris_partai()` di `app/repositori.py`, jadi daftar maupun detail selalu
mengirimnya. Alasan tidak dijadikan kolom: turunan murni dari `kontrol_waktu` +
`sumber`, jadi kolom hanya menambah satu tempat yang bisa basi (bandingkan
kolom `format`, yang memang disimpan karena dipakai untuk **menyaring** di SQL).
Aturan menit/detiknya satu, dipakai bersama `format_dari_kontrol_waktu()`:
sumber daring memakai detik, sumber OTB dengan dasar < 180 sudah menit, dasar
di bawah satu menit diberi satuan (`"15+0"` -> `"15s+0"`) supaya `"10+2"` dari
Chess.com tidak terbaca 10 menit. `web/alat-catur.js` memakai field ini lewat
`kontrolPartai()`; `kontrolNormal()` di berkas yang sama hanya cadangan untuk
respons lama, dan diuji supaya tidak menyimpang dari versi Python.

---

### 6. Dependency

**Dua paket baru, sudah terinstall (9 Sep 2026):**

```
fastapi        0.141.1
uvicorn[standard]
```

**Yang sudah ada, tidak perlu diinstall:**

| | |
|---|---|
| Python 3.12.10 | ✅ |
| python-chess 1.11.2 | ✅ |
| sqlite3 | ✅ bawaan Python |
| chessboard.js, chess.js | ✅ disalin dari proyek puzzle, bukan dari CDN |

Total dependency baru: **dua**.

---

### 7. Rencana implementasi bertahap

Tiap langkah menghasilkan sesuatu yang bisa dijalankan dan diuji.

| # | Isi | Cara membuktikan berhasil | Status |
|---|---|---|---|
| 1 | `app/db.py` + skema | Tabel terbentuk, constraint benar-benar menolak data salah | ✅ **selesai** |
| 2 | `app/impor.py` + `impor_pgn.py` | Impor 1 file PGN, hitungannya cocok | ✅ 11.572 partai dari 6 file, 139 detik |
| 3 | Uji dedup | Impor file yang sama dua kali, jumlah partai tidak bertambah | ✅ impor ulang: 0 baru, 1.041 duplikat, **+0** |
| 4 | `app/repositori.py` + endpoint daftar | `/api/partai` mengembalikan JSON | ✅ 200 OK; parameter salah ditolak 422, id tak ada 404 |
| 5 | `web/index.html` | Daftar partai tampil di browser, pencarian jalan | ✅ pencarian, 4 saringan, paginasi |
| 6 | Endpoint detail + `web/partai.html` | Papan tampil dengan posisi awal | ✅ 11 aset statis 200 OK |
| 7 | Replay | First / Prev / Next / Last berfungsi | ✅ + autoplay, balik papan, keyboard, klik langkah |
| 8 | `impor_broadcast.py` | Partai broadcast Lichess masuk database | ✅ 118 partai GM dari 30 ronde |

#### Catatan langkah 2–8 (selesai 9 Sep 2026)

**Yang dibuktikan, bukan diasumsikan:**

| Uji | Hasil |
|---|---|
| Impor 6 file PGN | 11.572 baru, 3 duplikat, 136 dilewati (partai tanpa langkah) |
| Impor ulang file yang sama | 0 baru, 1.041 duplikat, **+0 partai** |
| Impor ulang broadcast | 0 baru, 114 duplikat, **4 diperbarui**, +0 partai |
| Aset statis (11 berkas) | semua 200 OK dengan content-type benar |
| Parsing PGN ala `chess.js` pada 400 partai acak | 400/400 bersih, 28.791 langkah |
| Tes otomatis | 27 lolos |

**Dua bug yang ditemukan dan diperbaiki saat pengujian:**

1. **`elo_terakhir` terisi angka acak.** Kolom ini diperbarui tiap partai, jadi
   nilainya berasal dari partai yang *kebetulan diproses terakhir* — bukan yang
   terbaru. Akibatnya rating Ray tercatat 1500 (rating provisional Lichess),
   padahal partai terbarunya menunjukkan 2450. Diperbaiki: elo diisi sekali di
   akhir lewat `perbarui_elo_terakhir()`, diambil dari partai dengan tanggal
   terbaru. Hasil setelah perbaikan cocok dengan rating asli (2441, 2695).

2. **Aset statis 404.** `StaticFiles` dipasang di dalam `if FOLDER_WEB.is_dir()`
   yang dievaluasi saat modul dimuat. Server yang dinyalakan sebelum folder
   `web/` dibuat tidak akan pernah menyajikannya walau foldernya muncul
   kemudian. Cukup direstart, tapi perlu diingat saat deployment.

**Catatan komentar jam pada PGN Lichess.** PGN Lichess berisi
`{ [%clk 0:01:00] }`. Komentar itu mengandung `]`, jadi memisahkan header
dengan mencari `]` terakhir akan merusak seluruh partai. `chess.js` memisahkan
per baris lalu membuang komentar (`/* delete comments */`), dan pemisahan di
sisi Python harus mengikuti cara yang sama.

#### Catatan langkah 1 (selesai 9 Sep 2026)

Berkas: `app/__init__.py`, `app/db.py`. Dijalankan dengan `python -m app.db`.

Skema tidak cuma dibuat, tapi **diuji apakah aturannya benar-benar ditegakkan**
— 10 pengujian, semuanya lolos:

| Yang diuji | Hasil yang diharapkan | Hasil |
|---|---|---|
| Dua pemain dengan `fide_id` sama | ditolak | ✅ UNIQUE |
| Beberapa pemain dengan `fide_id` NULL | diterima | ✅ |
| `terverifikasi` diisi 2 | ditolak | ✅ CHECK |
| Partai menunjuk pemain yang tidak ada | ditolak | ✅ FOREIGN KEY |
| Dua partai dengan `hash_unik` sama | ditolak | ✅ UNIQUE |
| Turnamen sama + tahun sama | ditolak | ✅ |
| Turnamen sama + tahun beda | diterima | ✅ |
| `buat_skema()` dijalankan dua kali | data tetap utuh | ✅ |

Poin pentingnya: **duplikat ditolak oleh database, bukan dijanjikan oleh kode.**

Pengujian ini semula dijalankan sekali lalu dibuang. Sekarang tersimpan sebagai
`tests/test_db.py` supaya klaim "lolos" bisa diulang siapa saja, kapan saja.

#### Catatan langkah 2 (sedang berjalan)

Tiga keputusan yang diambil saat menulis `app/impor.py`, semuanya punya tes di
`tests/test_impor.py`:

1. Partai broadcast yang belum selesai diperbarui, bukan diduplikasi
   (risiko 1b). Versi yang lebih pendek dari yang tersimpan diabaikan.
2. Event generik tidak membuat baris turnamen (bagian 5).
3. Tag `ChessComTournament` dibaca (bagian 5).

Yang belum: menjalankan `impor_pgn.py` ke file nyata dan mencocokkan
hitungannya dengan tabel di bagian 8. Sebelum itu, langkah 2 belum selesai.

Tiga PRAGMA yang selalu diaktifkan di `koneksi()`:

- `foreign_keys = ON` — SQLite mematikannya secara default; tanpa ini foreign
  key cuma jadi hiasan
- `journal_mode = WAL` — pembacaan tidak terblokir saat ada penulisan, supaya
  impor broadcast bisa jalan sementara halaman web dibuka
- `busy_timeout = 5000` — tunggu sebentar kalau database sedang dipakai,
  daripada langsung gagal

---

### 8. Keadaan data saat ini

Sudah tersedia di `data/pgn/`, siap dipakai menguji impor di langkah 2.
Semuanya sudah diperiksa dan ter-parse bersih (0 partai rusak).

| Berkas | Partai | Catatan |
|---|---|---|
| `lichess-McLung_07.pgn` | 8.712 | |
| `lichess-Sakamoto_PMan.pgn` | 1.049 | |
| `lichess-Mikaysr.pgn` | 1.045 | |
| `chesscom-rayylung.pgn` | 549 | |
| `chesscom-ryumclungg.pgn` | 186 | **hanya Arena Kings** — partai lain di akun itu dimainkan orang lain |
| `chesscom-stanley-squarepants.pgn` | 170 | |
| `uji-scholars-mate.pgn` | 1 | fixture uji, bukan partai nyata |
| **Total** | **11.712** | |

Catatan integritas data:

- Akun `ryumclungg` disaring ke turnamen Arena Kings saja. Nama turnamen
  **tidak ada di PGN** (semua partai Chess.com bertag `[Event "Live Chess"]`);
  penyaringan dilakukan lewat field `tournament` di JSON API, lalu asalnya
  dicatat sebagai tag tambahan `[ChessComTournament "..."]`. Tag ini dibaca
  `impor.py` (bagian 5).
- Enam akun Ray akan menjadi enam baris `pemain` yang tidak saling kenal.
  Pemetaan akun → orang ditunda sampai Phase 7 (analisis partai pribadi);
  skema saat ini tidak menghalanginya.
- Belum ada satu pun partai GM di sini. Itu masuk lewat impor broadcast di
  langkah 8.
- Partai Ray dan partai GM akan berada di tabel yang sama, dibedakan kolom
  `sumber`.

---

### 9. Yang TIDAK dikerjakan di Phase 1

Sesuai permintaan:

- ❌ Stockfish / evaluasi engine
- ❌ Analisis gaya GM
- ❌ Sistem latihan
- ❌ Analisis partai pribadi
- ❌ Recommendation engine
- ❌ Pipeline scraping kompleks

**Catatan soal impor broadcast:** ini masuk Phase 1 karena termasuk "Import
PGN", dan Lichess menyediakannya sebagai satu endpoint API resmi yang langsung
mengembalikan PGN — bukan scraping.

Namun **pembaruan otomatis** ("tiap ada update turnamen langsung masuk") butuh
proses yang berjalan terus dan melakukan polling berkala. Itu di luar Phase 1.
Phase 1 menyediakan perintah manual; otomatisasinya menyusul setelah aplikasi
dasarnya terbukti stabil.

---

### 10. Penyimpangan sadar dari RENCANA-PROYEK.md

RENCANA adalah bank ide, bukan kontrak. Supaya hubungannya bisa diaudit dan
bukan cuma diniatkan, setiap penyimpangan dicatat di sini.

| RENCANA bilang | Yang dikerjakan | Alasan |
|---|---|---|
| Phase 1 = Foundation, Phase 2 = PGN & viewer | "Phase 1" di sini = gabungan keduanya | Database tanpa data dan tanpa cara melihatnya tidak bisa dibuktikan jalan |
| Dark mode | Mode terang saja | Permintaan langsung Ray, 9 Sep 2026 |
| Database "scalable", tabel `Moves`, `Analysis`, `Opening` | SQLite, 3 tabel | Belum ada yang membutuhkannya; PGN utuh tersimpan, jadi tabel itu bisa diturunkan kapan saja |
| "Strongly typed", clean architecture, ORM tersirat | SQL mentah di satu modul, tanpa ORM | Tiga tabel tidak butuh lapisan abstraksi (bagian 2) |
| Data ingestion pipeline 10 tahap | Satu modul `impor.py` | Tahap engine, metadata, dan index-for-search belum ada isinya di Phase 1 |
| Style tags, peak rating, birth year pada Players | Tidak ada | Tidak ada di PGN; mengisinya berarti mengarang (RENCANA §22) |
| Roadmap 8 fase | Hanya Phase 1 yang dirancang | Fase berikutnya dirancang setelah Phase 1 terbukti jalan |

Kalau salah satu baris ini berubah pikiran, ubah tabel ini dulu, baru kodenya.

---

## Phase 2 — Analisis Engine

Status: **selesai 9 Sep 2026 — 6 dari 6 langkah berjalan dan dibuktikan.**
Phase 1 (bagian di atas) tidak diubah; skema naik ke v2 secara
aditif (11.690 partai lama utuh).

| | |
|---|---|
| Tes otomatis | 34 lolos (`python -m unittest discover -s tests -v`) |
| Berkas baru | `app/analisis_db.py`, `app/ringkasan.py`, `analisis_partai.py`, `web/ringkasan.html`, `web/ringkasan.js`, `tests/test_analisis_db.py` |
| Berkas diubah | `app/db.py` (v2), `app/repositori.py`, `app/main.py`, `web/partai.html`, `web/papan.js`, `web/index.html`, `web/daftar.js`, `web/gaya.css` |
| Halaman baru | `/ringkasan` |
| API baru | `GET /api/partai/{id}/analisis`, `GET /api/ringkasan?pemain_id=…`, parameter `gelar` & `dianalisis` di `/api/partai` |

### Tujuan

Menyambungkan mesin analisis Stockfish yang **sudah ada** di
`src/catur_analysis/` ke database dan halaman papan, supaya:

1. Tiap partai di database bisa dianalisis dan hasilnya tersimpan.
2. Halaman papan menampilkan evaluasi, tanda blunder/mistake/inaccuracy,
   langkah yang seharusnya, dan grafik evaluasi — seperti papan analisis
   Lichess / Chess.com / Chessis.
3. Ada ringkasan lintas partai yang menjawab pertanyaan Ray:
   *"kenapa aku blunder dari awal saat lawan jauh lebih kuat?"*

Yang **tidak** masuk Phase 2: AI/LLM, style profile GM, curriculum, training
system, position similarity. Itu tetap di RENCANA-PROYEK sebagai bank ide.

### Satu keputusan arsitektur yang perlu disadari

**Stockfish tidak pernah dijalankan di dalam server web.**

> **Diperbarui 10 Sep 2026:** keputusan ini dibalik sebagian atas permintaan
> Ray — server kini menjalankan **satu** engine on-demand untuk evaluasi
> *satu posisi* (`/api/evaluasi`, papan analisis). Analisis *partai penuh*
> tetap lewat CLI seperti dijelaskan di bawah. Rincian dan alasannya:
> bagian **Phase 5** di bawah. Teks di bawah dibiarkan sebagai catatan sejarah
> keputusan Phase 2.

Alasannya diukur, bukan diduga:

| Fakta | Angka |
|---|---|
| RAM satu proses Stockfish 18 | 371–687 MB tergantung profil |
| Waktu analisis satu partai 40 langkah | 25 detik (hemat) – 4 menit (normal) |
| RAM bebas laptop ini | ~2,5 GB |

Kalau analisis dijalankan saat halaman dibuka: request timeout, dan dua klik
berturut-turut = dua Stockfish = 1 GB RAM. Jadi:

```
analisis_partai.py (CLI, batch)  --tulis-->  tabel analisis  <--baca--  FastAPI
         |
   Stockfish, satu proses,
   bisa dihentikan & dilanjutkan
```

Server web hanya **membaca** hasil. Kalau partai belum dianalisis, halaman
bilang begitu dan menunjukkan perintah untuk menganalisisnya.

### Skema tambahan (skema v2)

Dua tabel, keduanya **tambahan** — tabel Phase 1 tidak diubah.

```
analisis            satu baris per partai yang sudah dianalisis
  partai_id  UNIQUE -> partai.id
  engine, depth, profil
  akurasi_putih, akurasi_hitam
  blunder/mistake/inaccuracy putih & hitam
  turning_point_ply
  durasi_detik, dianalisis_pada

analisis_langkah    satu baris per ply
  partai_id, ply     UNIQUE bersama
  san, uci
  eval_sebelum_cp, eval_sesudah_cp   (sudut pandang pemain yang jalan)
  eval_putih_cp                      (sudut pandang Putih, untuk grafik)
  mat_sesudah
  kehilangan_cp, klasifikasi
  langkah_terbaik_san, langkah_terbaik_uci, variasi_terbaik
```

Menganalisis ulang partai (depth lebih dalam) = hapus baris lama, tulis baru.
`analisis.partai_id UNIQUE` yang menjaga tidak ada dobel.

### Langkah

| # | Isi | Bukti selesai | Status |
|---|---|---|---|
| 1 | Skema v2 di `app/db.py` + migrasi dari v1 | `python -m app.db` naik ke v2, 11.690 partai utuh | ✅ |
| 2 | `app/analisis_db.py` — simpan/baca `LaporanPartai` | bolak-balik partai nyata (43 ply) **SAMA PERSIS**; simpan 2× tetap 1 baris; 5 tes unit | ✅ |
| 3 | `analisis_partai.py` CLI | 3 partai GM dianalisis & tersimpan; `--id` yang sudah ada → 0 dipilih; `Ctrl+C` aman (commit per partai) | ✅ |
| 4 | API baca hasil + saringan `gelar` & `dianalisis` | 200 dengan 134 langkah; 404 berisi perintah CLI; `gelar=XX` → 422; `cari=McLung_07&gelar=GM` → 11 | ✅ |
| 5 | Papan: eval bar, tanda, komentar "seharusnya", sorot kotak, grafik SVG, ringkasan akurasi | aset 200, cek kurung JS OK; verifikasi visual oleh Ray | ✅ |
| 6 | `/api/ringkasan` + halaman | ringkasan 6 akun Ray dari 9 partai GM; tanpa `pemain_id` → 422 | ✅ |

#### Catatan pengerjaan (9 Sep 2026)

**Saringan gelar dibuat umum, bukan "lawan Ray".** `gelar=GM` berarti *salah
satu pemain bergelar GM*. Digabung `cari=McLung_07` hasilnya "Ray lawan GM"
karena akun Ray tidak bergelar; di partai broadcast, `gelar=GM` saja berarti
partai antar-GM. Dengan begitu `app/` tidak perlu tahu daftar akun Ray —
daftar itu hanya ada di `ambil_partai.py`. CLI `analisis_partai.py
--gelar-lawan` yang butuh konsep "lawan" mengimpor daftar akun dari sana.

**Estimasi waktu dibuat adaptif.** Tebakan awal 0,12 s/ply meleset 4×
(diukur dari partai 7 langkah; partai GM 67 langkah di depth 16 nyatanya
~0,95 s/ply). Sekarang estimasi diambil dari `durasi_detik` analisis
sebelumnya dengan profil yang sama; konstanta hanya cadangan.

**Jalankan ulang CLI dengan `--terbaru N` mengambil N berikutnya yang belum
dianalisis**, bukan 0. Itu disengaja (batch yang bisa dilanjutkan); bukti
idempotensi yang tepat adalah `--id` partai yang sudah dianalisis → 0.

**Server harus dinyalakan dengan `--reload`** (README sudah diperbarui).
Tanpa itu, `StaticFiles` dan rute baru tidak terlihat sampai restart — ini
yang bikin aset 404 di Phase 1 dan rute analisis 404 di Phase 2 sebelum restart.

#### Hasil pertama — dibaca dengan hati-hati

Dari **9 partai Ray lawan GM** yang sudah dianalisis (profil hemat, depth 16),
semuanya bertanda *sampel kecil*:

| Fase | Langkah | Rata loss | ?? | ? |
|---|---|---|---|---|
| pembukaan (1–12) | 108 | 21,8 cp | 0 | 4 |
| tengah (13–40) | 195 | **86,5 cp** | **11** | 20 |
| akhir (41+) | 98 | 55,6 cp | 5 | 6 |

Kesalahan besar pertama rata-rata muncul di langkah **14–20**, bukan di
pembukaan. Ini **berbeda** dari dugaan Ray ("blunder dari awal lawan kuat")
— tapi 9 partai bullet/blitz belum cukup untuk menyimpulkan apa pun. Yang
bisa dikatakan: alatnya sekarang bisa menguji dugaan itu; tinggal
menganalisis lebih banyak partai (`--lawan-min-elo 2700 --terbaru 100`).

Tiap langkah bisa dijalankan sendiri. Langkah 1–3 murni backend dan tidak
menyentuh web sama sekali.

### Yang dipakai ulang tanpa diubah

- `src/catur_analysis/engine.py` — pembungkus Stockfish dengan batas RAM
- `src/catur_analysis/partai.py` — `analisis_partai()` → `LaporanPartai`
- Rumus akurasi (Lichess) dan ambang klasifikasi yang sudah diuji di sana

---

## Phase 3 — Data GM dari Arsip TWIC & Statistik Pembukaan

Status: **dikerjakan sejak 9 Sep 2026** setelah Ray bilang "lanjut phase 3".
Phase 1–2 tidak diubah; skema naik ke v3 secara aditif.

### Kenapa ini Phase 3

**Cakupan diperluas 11 Sep 2026:** Ray memutuskan bukan 3 tahun, melainkan
seluruh arsip TWIC yang tersedia (edisi 920, Juni 2012, sampai sekarang;
edisi 1-919 tidak tersedia sebagai zip satuan). Saringan tetap sama.
Backfill: `python perbarui.py --hanya twic --edisi 920-1505`.

Permintaan asli Ray: *database partai GM dunia dari 3 tahun ke belakang,
bersumber dari turnamen yang benar-benar ada, untuk mempelajari gaya dan
langkah mereka.* Setelah Phase 2, database baru berisi **118 partai GM dari
satu turnamen**. Statistik pembukaan GM, perbandingan pemain, dan pencarian
sesuai repertoire baru berarti setelah datanya ada.

### Temuan yang menentukan desain (diukur 9 Sep 2026)

| Sumber | Fakta | Peran |
|---|---|---|
| Lichess `/api/broadcast` | maksimal 100 turnamen, mundur hanya sampai Sep 2025 | — |
| Lichess `/api/broadcast/top?page=N` | valid sampai halaman 5 (~120 turnamen, Agu 2026) | **pembaruan berkala**: turnamen aktif & baru selesai, ronde live |
| **TWIC** (`theweekinchess.com/zips/twicNNNNg.zip`) | mingguan sejak 1994; `twic1661` = 7.670 partai, **FIDE ID 99,8%**, tag `Source`; zip lama (2023) masih bisa diunduh; tidak ada `robots.txt` | **backfill 3 tahun** (~156 zip, ~2 MB tiap) |

TWIC berisi campuran: sebagian besar partai online (Titled Tuesday, 3-0
Thursday) yang bukan tujuan proyek ini. Penanda yang dapat diandalkan: tag
`Site` berisi kata `INT` (mis. `Chess.com INT`, `lichess.org INT`).

#### Saringan default TWIC

- buang `Site` yang mengandung `INT` (online)
- kedua pemain **Elo ≥ 2500** (angka ini diukur, lihat catatan langkah 2)
- tidak menyaring berdasarkan nama event — nama event tidak konsisten dan
  akan membuang turnamen sah

Semua yang lolos disimpan dengan `sumber = 'twic'` dan `sumber_url` menunjuk
zip asalnya, jadi tiap partai bisa ditelusuri ke sumbernya (§3 spec).

#### Dedup lintas sumber — bukan diasumsikan, diuji

Partai GCL 2026 sudah masuk lewat broadcast Lichess dan muncul lagi di
twic1661. **Hasil uji: bocor.** Dari 53 partai GCL yang lolos saringan TWIC,
23 dikenali duplikat, tapi **14 lolos sebagai baru** padahal tanggal dan
seluruh langkahnya identik. Penyebabnya: TWIC menyingkat nama depan
(`Anand,V`), Lichess tidak (`Anand, Viswanathan`); hash memakai nama lengkap
yang dinormalisasi, jadi `anand v` ≠ `anand viswanathan`. Pemainnya sendiri
sudah menyatu lewat FIDE ID — yang bocor cuma sidik jari partainya.

Perbaikan (bukan pelonggaran):

1. `hash_partai()` sekarang memakai **nama belakang** (`nama_belakang()`:
   bagian sebelum koma, atau kata pertama) + tanggal + hasil + langkah.
2. `hitung_ulang_hash()` menghitung ulang semua hash lama; kalau dua baris
   bertabrakan, yang dipertahankan adalah versi selesai (bukan `*`), lalu
   yang langkahnya lebih banyak. Dijalankan lewat
   `python impor_pgn.py --hitung-ulang-hash` — dari 11.756 partai, **tepat 14
   duplikat** terhapus, dan twic1661 diimpor ulang → 0 baru, 89 duplikat.
3. Aturan "partai `*` diperbarui oleh versi selesainya" diperluas lintas
   sumber: pasangan pemain + tanggal + langkah lama adalah awalan langkah
   baru — **hanya** kalau kedua pemain punya FIDE ID. Partai online Ray
   (tanpa FIDE ID) tetap tidak pernah tersentuh; jaminan lama di
   `tests/test_impor.py` tetap berlaku dan sekarang punya tes pasangannya di
   `tests/test_hash_lintas_sumber.py`.

Cadangan sebelum hitung ulang: `data/catur.db.bak-sebelum-hash`.

### Skema v3 (aditif)

```
sumber_impor      catatan apa yang sudah diimpor, supaya backfill bisa
                  dihentikan & dilanjutkan tanpa mengunduh ulang
  jenis           'twic' | 'lichess-broadcast'
  kunci           nomor TWIC ('1661') atau id ronde Lichess
  nama, url
  jumlah_partai, jumlah_lolos_saringan
  selesai         1 kalau sumbernya final (zip TWIC selalu 1; ronde Lichess
                  hanya kalau finished)
  diimpor_pada
  UNIQUE(jenis, kunci)
```

### Langkah

| # | Isi | Bukti selesai | Status |
|---|---|---|---|
| 1 | Skema v3 + `sumber_impor` | migrasi v2→v3, 11.690 partai utuh; 9 tes | ✅ |
| 2 | `impor_twic.py` — unduh (cache `data/twic/`), saring, impor, catat | twic1661: 7.670 → 89 lolos; jalankan ulang → dilewati; **dedup lintas sumber bocor lalu diperbaiki** (lihat di atas) | ✅ |
| 3 | Backfill 3 tahun | **selesai 9 Sep malam**: 155 zip (twic1506–1661), 1.227.750 partai di sumber → 35.409 lolos saringan (offline, kedua Elo ≥ 2500) → **35.326 baru**, 20 duplikat, 63 dilewati; 23,6 menit. Database: **47.068 partai**, 865 pemain terverifikasi FIDE | ✅ |
| 4 | `impor_broadcast.py --resmi --tier N` | `--lihat-saja` tier 5: 18 turnamen; turnamen belum mulai dilewati; ronde final dicatat & dilewati saat diulang | ✅ (mode nyata belum dijalankan penuh, menunggu backfill selesai agar tidak dua penulis) |
| 5 | Profil pemain + pembukaan: `/api/pemain/{id}`, `/api/pemain/{id}/pembukaan`, `/pemain/{id}` | Carlsen: 485 partai, skor 69%, Putih 76,4% / Hitam 61,4%; pembukaan per keluarga ECO dengan skor | ✅ |
| 6 | Saringan repertoire (tombol di daftar + parameter `eco_dari`/`eco_sampai`, `pemain_id`, `warna`) | Sicilian (twic) 3.453; Catalan antar-GM 632; Ragozin antar-GM 279; nama pemain di daftar & papan jadi tautan ke profil | ✅ |

#### Catatan pengerjaan

**Keluarga ECO (`app/eco.py`).** TWIC tidak menyertakan nama pembukaan, jadi
statistik pembukaan dikelompokkan per rentang ECO standar; Ragozin (D38–D39)
dan Catalan (E00–E09) dipisah dari induknya karena itu repertoire Ray. Tes
memastikan semua kode A00–E99 terpetakan.

**Reloader uvicorn bisa mati diam-diam.** Setelah `db.py` berubah saat
backfill menulis database, worker lama tetap melayani rute lama (404 polos
untuk rute baru) tanpa traceback di log. Gejalanya: `python -c "from app.main
import aplikasi"` sehat tapi server 404. Obatnya: matikan semua yang
menempel di port 8000 (`netstat -ano | findstr :8000` → `taskkill`), lalu
nyalakan lagi.

**Estimasi awal jumlah partai TWIC salah arah.** Aku menduga 89 partai/minggu
× 156 minggu ≈ 14 ribu. Nyatanya minggu turnamen besar jauh lebih padat:
hasil akhirnya **35.326 partai** (rata-rata 228/minggu, 2,5× dugaan).

**`impor_twic.py` sedang direvisi oleh sesi paralel (10 Sep pagi)** dan
berganti desain beberapa kali dalam hitungan menit — antara saringan Elo
(yang dipakai backfill di atas) dan daftar FIDE ID sasaran
(`data/gm_target.txt`). Dokumen ini sengaja **tidak** mendaftar flag-nya;
acuannya adalah docstring file itu sendiri (`python impor_twic.py --help`).
Yang tetap berlaku apa pun versinya: data 35.326 partai yang ada berasal
dari saringan Elo ≥ 2500 tanpa online; lisensi TWIC "free for personal use
only" → `data/twic/` tidak masuk git dan tidak disebarkan ulang; dedup hash
melindungi dari impor ganda lintas versi. `tests/test_sumber_impor.py`
hanya menguji `app/sumber_impor.py`, bukan CLI itu.

Yang **tidak** masuk Phase 3: analisis gaya (label "agresif/posisional"),
perbandingan dua GM, pattern library, penjadwalan otomatis (cron). Pembaruan
berkala di Phase 3 masih perintah manual yang aman diulang.

---

## Phase 4 — Penjelajah Pembukaan

Status: **dikerjakan sejak 9 Sep 2026 malam** ("lanjut" setelah Phase 3).

### Kenapa ini Phase 4

Setelah Phase 3, database berisi puluhan ribu partai elite 3 tahun terakhir
dengan sumber yang jelas. Pertanyaan yang paling langsung berguna untuk
repertoire Ray (English, Catalan, Ragozin, Sicilian) belum bisa dijawab:

> *Dari posisi ini, apa yang dimainkan GM, seberapa sering, dan skornya?*

Itu persis fungsi "Masters database" di Lichess — tapi di sini datanya
milik sendiri, bisa disaring (Elo, sumber, tahun), dan tiap angka bisa
ditelusuri ke partai asalnya.

### Desain

#### Indeks posisi (skema v4, aditif)

```
posisi
  fen_kunci    posisi TANPA penghitung langkah (papan, giliran, rokade, en passant)
  partai_id    -> partai.id (ON DELETE CASCADE)
  ply          nomor ply posisi ini (0 = posisi awal)
  langkah_uci  langkah yang dimainkan DARI posisi ini
  langkah_san
  PRIMARY KEY (partai_id, ply)
  INDEX (fen_kunci)
```

Hanya `MAKS_PLY` ply pertama tiap partai (default 36 = 18 langkah) yang
diindeks. Alasannya ukuran: 40 ribu partai × 36 ply ≈ 1,4 juta baris —
cukup untuk teori pembukaan, dan tidak membengkak jadi ratusan MB.

`fen_kunci` membuang dua field terakhir FEN (halfmove clock, fullmove
number) supaya posisi yang sama lewat urutan langkah berbeda (transposisi)
tetap dikenali sebagai satu posisi.

#### Pembangunan indeks

`indeks_posisi.py` — CLI, seperti alat lain: bisa dihentikan, dilanjutkan
(partai yang sudah punya baris `posisi` dilewati), commit berkala. Server web
tidak pernah membangun indeks.

#### API

```
GET /api/posisi?fen=<FEN>&sumber=gm|semua&min_elo=2500
  -> langkah:  [{san, uci, n, menang_putih, remis, menang_hitam, skor_putih, elo_rata, terakhir}]
     partai:   10 contoh partai dengan Elo tertinggi dari posisi ini
     total
```

Default `sumber=gm` (twic + lichess-broadcast). Partai online Ray sendiri
ikut hanya kalau `sumber=semua` — supaya "apa yang dimainkan GM" tidak
tercampur partai bullet.

#### Halaman

- **`/jelajah`** — papan yang bisa dimainkan (drag bidak, divalidasi
  `chess.js`), tabel langkah dari database, daftar partai contoh, tombol
  undo/balik/reset, bisa mulai dari `?fen=`.
- **Halaman partai** — kartu "Di database, dari posisi ini GM memainkan…"
  yang mengikuti posisi papan, plus tautan "jelajahi dari sini".

### Langkah

| # | Isi | Bukti selesai | Status |
|---|---|---|---|
| 1 | Skema v4 + `app/posisi.py` | 9 tes: transposisi 1.e4 e6 2.d4 d5 = 1.d4 d5 2.e4 e6, penghitung dibuang, FEN rusak → 422, hapus partai → baris posisi ikut terhapus | ✅ |
| 2 | `indeks_posisi.py` | 47.068/47.068 partai, **1.656.035 baris**, ~250 partai/detik; dihentikan di tengah lalu dilanjutkan tanpa mengulang | ✅ |
| 3 | `/api/posisi` | posisi awal (GM): 34.520 partai — e4 16.592 (54,7%), d4 11.228 (53,5%), Nf3 3.616, c4 2.818 (56,5%) | ✅ |
| 4 | `/jelajah` | Catalan 1.d4 Nf6 2.c4 e6 3.g3: 297 partai — d5 203, Bb4+ 61 (45,1% untuk Putih), c5 33; Ragozin 4…Bb4 Elo ≥ 2600: 241 — Bg5 92, Qa4+ 60; Najdorf 5…a6: 1.298 — Be3 345 | ✅ (verifikasi visual oleh Ray) |
| 5 | Kartu penjelajah di halaman partai | mengikuti posisi, ditunda 250 ms supaya menahan → tidak memicu satu permintaan per ply | ✅ |

#### Catatan pengerjaan (10 Sep 2026)

**Kecepatan.** Posisi awal — posisi dengan baris terbanyak — dijawab dalam
~1,1 detik (agregasi 34 ribu baris + join partai). Posisi lebih dalam jauh
lebih cepat. Kalau nanti terasa lambat, langkah berikutnya adalah tabel
ringkasan per (fen_kunci, langkah) yang dihitung saat indeks dibangun;
belum dilakukan karena belum perlu.

**Diperbarui 27 Sep 2026: cache, bukan tabel ringkasan.** Dengan 273 ribu
partai, posisi awal GM kini 259 ribu baris: 1-3 detik di mesin tenang, 11
detik lewat server saat batch engine jalan. `repertoire/pgn` menanyakan
puluhan posisi awal (118 panggilan, 4,5-8,7 detik). Dipilih cache di memori
server (`cari_langkah` di `app/posisi.py`), bukan tabel ringkasan: tabel
harus dijaga ikut berubah di setiap impor, gabung pemain, dan penandaan
pemain mesin — lebih banyak tempat untuk salah daripada yang dihemat.
Ketentuannya (disetujui Ray): hanya hasil >= 1.000 partai yang disimpan;
kuncinya memuat `MAX(partai.id)` sehingga impor dari terminal langsung
terlihat; impor web mengosongkannya; selebihnya kedaluwarsa 10 menit.
**Batas yang diterima:** gabung pemain, hasil siaran yang diperbarui, dan
penandaan pemain mesin dari terminal bisa tertinggal sampai 10 menit
(dicatat oleh tes `UjiCacheLangkah.test_hasil_diubah_basi_sampai_dikosongkan`).
Terukur: posisi awal GM 3,70 → 0,00 detik (kedua kali), repertoire 8,68 → 1,93.
Pembukaan pertama sesudah server menyala tetap lambat.

**Default `sumber=gm`.** Partai online Ray sendiri (10 ribu lebih) tidak
ikut kecuali diminta, supaya "apa yang dimainkan GM" tidak tercampur bullet.
Saat indeks baru sebagian (3.000 partai pertama = partai Ray), penjelajah
menjawab 0 untuk posisi awal — itu benar, bukan bug.

**Reloader uvicorn** mati diam-diam untuk kedua kalinya saat file berubah
ketika indeks/backfill menulis database. Gejala dan obatnya sama seperti
dicatat di Phase 3.

Yang **tidak** masuk Phase 4: pohon repertoire tersimpan, latihan
"tebak langkah GM", perbandingan dua pemain.

---

## Phase 5 — Papan Analisis (engine di dalam server)

Status: **B5 selesai 10 Sep 2026** (backend, commit lihat SESI.md); F6
(halaman `/analisis`) dikerjakan frontend; B6 = dokumen ini.

### Keputusan yang dibalik, dan kenapa

Sejak Phase 2 aturannya: *Stockfish tidak pernah dijalankan oleh server web;
analisis hanya lewat CLI `analisis_partai.py`, web cuma membaca hasilnya.*
Alasannya diukur: satu proses engine 371–687 MB, satu partai 25 detik–4 menit,
RAM bebas laptop ~2,5 GB.

Pada 10 Sep 2026 Ray meminta: *"bisa analisa dari papan langsung, edit papan,
tampilkan FEN, analisa langsung"* — tanpa perlu perintah terminal atau
bantuan sesi Claude. Itu kebutuhan yang sah dan memang tujuan alat ini.
Keputusannya dicatat final di `SESI.md` bagian 6.

Batasan lama tidak dibuang, tapi dipenuhi dengan desain:

| Kekhawatiran lama | Cara memenuhinya sekarang |
|---|---|
| RAM: dua klik = dua engine | **Satu** proses per server (`app/engine_server.py`), profil HEMAT. Permintaan kedua saat engine sibuk → **409**, bukan engine kedua |
| Satu partai = menit-menitan, request timeout | Endpoint hanya mengevaluasi **satu posisi**, `Limit(time=1.5 s, depth≤30)`. Analisis partai penuh tetap batch CLI |
| Engine menganggur memakan RAM | Dimatikan otomatis 5 menit setelah permintaan terakhir |
| Batch dan web bertabrakan | Proses terpisah; aturan mesin ini: maksimal **dua** engine bersamaan. Diukur: server 302 MB + batch 372 MB = **675 MB** |

### Kontrak `GET /api/evaluasi`

Parameter: `fen` (wajib), `multipv` 1–5 (default 3), `depth` 1–30 (batas
atas, opsional), `waktu` 0,1–10 detik (default 1,5).

```
200 {
  "fen": str, "giliran": "putih"|"hitam", "profil": "hemat",
  "depth": int, "durasi_ms": int, "selesai": bool,
  "skor":  {"cp": int|null, "mate": int|null [, "pemenang": str]},   // = garis[0], sudut pandang Putih
  "garis": [{"multipv": 1, "cp": int|null, "mate": int|null, "pv": [SAN...], "depth": int}, ...]
}
422  FEN tidak sah        409  engine sedang dipakai        503  engine/stockfish.exe tidak ada
```

Posisi yang sudah selesai (mat/remis) dijawab tanpa memanggil engine:
`selesai: true`, `garis: []`, `skor.mate: 0` + `pemenang`.

### Bukti (10 Sep 2026)

| Uji | Hasil |
|---|---|
| Mat-1 (Qxf7#), permintaan pertama | `mate: 1`, `pv: [Qxf7#]`, **696 ms** termasuk menyalakan engine |
| Posisi awal, multipv=3 | 1.505 ms, depth 18: d4 +33, Nf3 +32, e4 +32 |
| FEN rusak | 422 dengan pesan python-chess |
| Fool's Mate (sudah mat) | `selesai: true`, `garis: []`, pemenang hitam |
| Dua permintaan bersamaan | `[200, 409]` |
| RAM saat batch B3 juga berjalan | 675 MB total, dua proses |
| Tes otomatis | 79 lolos; 4 tes engine dilewati otomatis bila `engine/stockfish.exe` tidak ada |

### Yang tetap seperti sebelumnya

- Analisis **partai penuh** (klasifikasi tiap langkah, akurasi, turning point,
  tabel `analisis`) tetap lewat `analisis_partai.py` — server tidak menulis
  hasil evaluasi papan ke database.
- Profil RAM engine tetap di `src/catur_analysis/engine.py`; `evaluasi()`
  kini menerima `waktu` (aditif, batch tidak berubah).

### Ditunda ke versi berikutnya (dicatat, bukan dilupakan)

Yang masih ditunda: engine di browser (WASM) untuk papan analisis.

Yang dulu tercatat di sini dan sekarang sudah ada (diperiksa 9 Okt 2026):

- **Analisis tanpa henti** — tombol **Terus** di `/analisis`,
  `GET /api/evaluasi/aliran` (`app/evaluasi_aliran.py`, SSE). Engine dan
  kunci SAMA dengan `/api/evaluasi`, jadi tetap satu Stockfish server; selama
  aliran berjalan `/api/evaluasi` menjawab 409. Depth naik satu per satu
  (tiap kiriman = multipv lengkap pada satu depth), berhenti saat Henti /
  posisi berubah / tab ditutup / depth 30 / 2 menit. Koneksi putus →
  `Stockfish.evaluasi(berhenti=...)` menghentikan pencarian dalam ~0,1 s dan
  kunci baru dilepas sesudah engine benar-benar berhenti. Bukti di Edge
  sungguhan (DevTools, waktu nyata): depth 8 → 17 dalam 3 s; `/api/evaluasi`
  0,3 s sesudah Henti → 200; posisi diganti ke mat-1 → Terus berhenti dan
  evaluasi otomatis menampilkan +M1. Frontend memakai fetch + ReadableStream,
  bukan EventSource (EventSource tidak bisa membaca 409 dan menyambung ulang
  sendiri sesudah server menutup aliran).
- **Menyimpan posisi hasil susun** — tombol Simpan di `/analisis`
  (`web/simpan-posisi.js`, tabel `posisi_tersimpan`, skema v5).
- **Memicu analisis partai penuh dari tombol web** — "Analisa partai ini
  dengan engine" di kartu "Belum dianalisis" halaman partai
  (`web/ringkasan-analisis.js`) membuka `/static/mesin.html?kelompok=id&id=<partai>`.

---

## Phase 6 — Tanya Database dengan Bahasa Indonesia

Status: **selesai dan tersambung 25 Sep 2026.** Modul intinya sudah jadi sejak
12 Sep, tapi endpoint dan halamannya baru dibuat 25 Sep — 13 hari fitur utuh
berikut 48 tesnya tidak bisa dipakai Ray sama sekali, karena tidak ada satu pun
tautan ke arahnya. `tests/test_tanya_web.py` sekarang menjaga hal itu: satu tes
gagal kalau ada halaman bernav yang tidak menaut ke `/tanya`.
Permintaan Ray: *"tolong bantu latih ollama di komputer ini supaya ahli catur
dan supaya saya bisa memasukan AI di program ini"* — arah yang dipilih Ray
setelah diberi empat pilihan: **chatbot tanya-jawab database**.

| | |
|---|---|
| Berkas baru | `app/tanya_db.py`, `app/tanya_web.py`, `web/tanya.html`, `web/tanya.js`, `tests/test_tanya_db.py`, `tests/test_tanya_web.py` |
| Tes | 25 tes `tanya_db` + 14 `peta_aplikasi` + **17 `tanya_web`** (jalur HTTP, Ollama ditambal); seluruh suite **547 tes lolos** (3 dilewati) |
| Dependency baru | **tidak ada** — Ollama diakses lewat REST API-nya pakai `urllib` bawaan |
| Model | `qwen2.5-coder:7b` (4,7 GB) — lihat "Pilihan model"; 3b sempat dipakai lalu ditinggalkan karena 2 dari 7 SQL-nya gagal |
| Endpoint | `GET /api/tanya/status` (Ollama hidup? model apa?), `POST /api/tanya` `{pertanyaan, mode, model?}`; 503 kalau Ollama mati, 422 kalau SQL ditolak |
| Halaman | `/tanya`, tertaut dari nav 16 halaman |
| Bukti HTTP nyata (25 Sep) | `/api/tanya/status` 200 `siap:true`; "berapa partai per sumber" dengan `qwen2.5-coder:3b` → **23,6 detik**, SQL `SELECT sumber, COUNT(*) ... GROUP BY sumber`, hasil twic 164.836 / pgnmentor 89.822 / lichess-user 10.702 / lichess-broadcast 6.092 / chesscom 1.508; mode `aplikasi` mengembalikan peta yang dipakai; pertanyaan kosong dan mode asing → 422 |

### Kenapa LLM tidak boleh jadi sumber kebenaran

Permintaan aslinya adalah *melatih* Ollama supaya ahli catur. Itu tidak
dikerjakan, dan tiga alasannya masing-masing sudah cukup:

1. **Ollama tidak bisa melatih.** Ia runtime inference. `ollama create` +
   Modelfile hanya membungkus model dengan system prompt; bobotnya tidak
   berubah satu angka pun.
2. **Hardware tidak memungkinkan.** Fine-tuning butuh NVIDIA CUDA. Mesin ini
   Intel UHD Graphics, `nvidia-smi` dan `nvcc` tidak ada, VRAM 2 GB dibagi
   sistem, RAM bebas 2–5 GB. QLoRA 7B butuh ≥8 GB VRAM.
3. **Hasilnya tetap buruk, dan ini yang menentukan.** Diukur di mesin ini
   dengan posisi `6k1/5ppp/8/8/8/8/8/R5RK w - - 0 1`:

| | Stockfish | `qwen2.5:14b` |
|---|---|---|
| Jawaban | `Ra8#` | `Rxg8#` |
| Legal? | ya, mate in 1 | **tidak** — benteng g1 terhalang pion g7, dan g8 berisi raja |
| Waktu | 0,001 detik | 64 detik |

LLM salah di catur bukan karena kurang latihan, tapi karena ia memprediksi
token yang *menyerupai* notasi catur tanpa melacak isi papan. Fine-tuning
memperbaiki gaya jawaban, bukan kemampuan menghitung. Karena itu aturan
"jangan mengarang data" di `CLAUDE.md` melarang LLM jadi sumber kebenaran
catur: `Rxg8#` persis pelanggaran yang dilarang aturan itu.

Catatan yang sengaja disimpan: dalam pengujian yang sama, sesi Claude
**juga** salah menghitung mate pertama yang diajukan (`6k1/6pp/...`, ternyata
raja lari ke f7 dan tidak ada mate). Yang mengoreksi keduanya adalah
python-chess dan Stockfish. Itu argumen terkuat untuk desain di bawah.

### Desain: LLM mengarang SQL, database menjawab

Pembagian tugas yang tidak boleh dilanggar:

| Tugas | Pengerjanya |
|---|---|
| Langkah terbaik, evaluasi, mat | Stockfish |
| Legalitas langkah, FEN | python-chess |
| Fakta partai, pemain, statistik | SQLite `catur.db` |
| Menerjemahkan pertanyaan Indonesia jadi SQL | LLM |

LLM tidak pernah menyebut angka. Ia hanya mengarang SQL; angkanya datang dari
baris nyata di database. Karena itu model kecil sudah cukup — dan itu penting,
karena `qwen2.5:14b` (9 GB) **timeout di 120 detik** pada mesin dengan RAM
bebas 5 GB, sementara `qwen2.5-coder:3b` menjawab dalam 12–24 detik.

Jawaban selalu memuat `sql` yang dipakai, supaya tiap angka bisa ditelusuri
Ray sendiri — bukan kotak hitam.

### Empat lapis pagar

SQL dari LLM diperlakukan sebagai masukan bermusuhan. Lapis penting di bawah:

1. `periksa_sql()` — satu perintah saja, harus `SELECT`/`WITH`, kata kunci
   pengubah data ditolak, tabel di luar daftar ditolak.
2. Bungkus `SELECT * FROM (<sql>) LIMIT n` — batas tidak bisa dihindari
   walaupun LLM menulis `LIMIT 999`. Membungkus dipilih, bukan menyisipkan
   teks `LIMIT`, karena menempel ke SQL ber-`ORDER BY`/`UNION` gampang salah.
3. Koneksi `file:...?mode=ro` — SQLite membuka berkas hanya-baca.
4. `set_authorizer` — tiap operasi selain `SQLITE_READ`/`SELECT`/`FUNCTION`
   ditolak SQLite sendiri, termasuk `ATTACH` ke berkas lain.

Lapis 1–2 memberi pesan yang enak dibaca; lapis 3–4 yang menjamin. Ini
penerapan "database yang menolak, bukan kode yang berjanji": `TesDatabaseMenolak`
sengaja **melewati** lapis 1 dan membuktikan `DELETE`/`UPDATE`/`INSERT`/`DROP`
tetap gagal, lalu memeriksa jumlah baris tidak berubah.

`posisi` (9,4 juta baris) sengaja di luar daftar tabel: pertanyaan bebas yang
menyentuhnya hampir selalu jadi pemindaian penuh berpuluh detik. Kebutuhan itu
sudah dilayani `/api/posisi` yang memakai indeks. Kolom `pgn`, `hash_unik`, dan
`variasi_terbaik` tidak ditawarkan ke LLM karena hanya membanjiri hasil.

### Bukti (12 Sep 2026)

Dua pertanyaan terhadap database sungguhan, model `qwen2.5-coder:3b`:

```
[1] "Siapa 5 pemain dengan partai terbanyak? Tampilkan nama dan jumlah partainya."
    SQL (24 s): SELECT p.nama, COUNT(*) AS jumlah_partai FROM pemain p
                JOIN partai pa ON p.id = pa.putih_id OR p.id = pa.hitam_id
                GROUP BY p.id ORDER BY jumlah_partai DESC LIMIT 5
    Nakamura, Hikaru 10.442 · McLung_07 8.596 · Carlsen, Magnus 8.024
    Caruana, Fabiano 6.778 · Grischuk, Alexander 6.736

[2] "Berapa banyak partai untuk setiap sumber?"
    SQL (12 s): SELECT sumber, COUNT(*) AS jumlah_partai FROM partai GROUP BY sumber
    twic 164.836 · pgnmentor 89.822 · lichess-user 10.679
    chesscom 895 · lichess-broadcast 379
```

Angka pertama dicek silang dengan SQL yang ditulis tangan, bukan oleh LLM:
`Nakamura, Hikaru` = 10.442, cocok persis — dan cocok pula dengan catatan B15
sesi lain di `SESI.md` yang menggabungkan baris ganda Nakamura hari itu.

### Mode kedua: tanya tentang aplikasinya sendiri

Permintaan Ray menyusul: *"tolong latih ollamanya supaya mahir tentang semua
web ini"*. Sama seperti sebelumnya, bukan dengan melatih — melainkan
`app/peta_aplikasi.py` membangun **peta aplikasi saat dijalankan** dari
kodenya sendiri, lalu mengirimkannya sebagai konteks.

Peta dibangun dari dua sumber, bukan ditulis tangan:

| Isi peta | Sumbernya |
|---|---|
| 15 halaman: nama + bagian di dalamnya | berkas `web/*.html` (`<title>`, `<h1>`, `<h2>`) |
| 23 endpoint: metode, path, ringkasan | `aplikasi.openapi()` |

Kenapa saat dijalankan: aplikasi ini dikerjakan beberapa sesi paralel yang
terus menambah halaman — pada 12 Sep 2026 saja bertambah halaman Impor dan
Mesin dalam satu hari. Peta yang ditulis tangan akan usang dalam hitungan
jam, dan LLM dengan peta usang menjawab **dengan yakin** tentang halaman yang
sudah berubah: persis karangan yang dilarang CLAUDE.md.

Satu jebakan yang sudah kena dan dihindari: menelusuri `aplikasi.routes`
**melewatkan** router yang disertakan lewat `include_router()` — objeknya
muncul sebagai `_IncludedRouter` tanpa path, jadi seluruh endpoint
`app/impor_web.py` dan `app/analisa_web.py` hilang. Terukur: cara rute
menemukan 14 endpoint, `aplikasi.openapi()` menemukan 23. Ada tes yang
menjaga ini supaya tidak kembali (`test_router_yang_disertakan_ikut_terbaca`).

Berbeda dari mode SQL, di sini **LLM yang menyusun kalimat**, jadi karangan
tetap mungkin — yang dijamin hanya bahwa fakta yang tersedia untuknya benar
dan mutakhir. Karena itu `tanya_aplikasi()` mengembalikan peta yang dipakai:
kalau tidak bisa diverifikasi otomatis, minimal sumbernya bisa dilihat.

### Pemisahan `app/llm.py`

Pemanggilan Ollama dipindah ke `app/llm.py` supaya tidak tersalin di tiap
modul yang butuh LLM. `app/tanya_db.py` memakainya dan mengekspor ulang
`LlmTidakAda`, jadi pemanggil cukup menangkap satu jenis kesalahan. Di situ
juga ada `model_tersedia()` supaya "Ollama mati" bisa dibedakan dari "Ollama
hidup tapi modelnya belum diunduh" — dua keadaan yang perlu pesan berbeda.

### Pilihan model, dari banding terukur

Diuji 12 Sep 2026: 7 pertanyaan SQL bertingkat (dua di antaranya punya kunci
jawaban dari SQL tulisan tangan) dan 4 pertanyaan tentang aplikasi.

| Model | SQL jalan | Peta aplikasi benar | Rata-rata |
|---|---|---|---|
| `qwen2.5-coder:3b` (1,9 GB) | 5 dari 7 | 2 dari 4 | 11 s |
| **`qwen2.5-coder:7b` (4,7 GB)** | **7 dari 7** | **4 dari 4** | 41 s |
| `qwen2.5:14b` (9 GB) | tidak dipakai | — | ~147 s, timeout |

7B dipilih walaupun hampir 4x lebih lambat. Kesalahan 3B bukan yang bisa
diabaikan: ia mengarang halaman bernama "Pembukaan" yang tidak ada, dan
menulis `JOIN analisis a ON p.id = a.partai_id` yang menyamakan pemain
dengan partai. Untuk alat belajar, jawaban salah lebih merugikan daripada
menunggu 30 detik lebih lama. 3B tetap tersedia lewat parameter `model=`
untuk saat RAM sempit karena batch analisis sedang berjalan.

Catatan kecepatan yang perlu diketahui: permintaan pertama memakan 40–85
detik karena memuat model ke RAM; selama model masih hangat (Ollama menahannya
5 menit) permintaan berikutnya 2–5 detik untuk pertanyaan aplikasi.

### Kesalahan yang paling berbahaya: SQL yang jalan tapi salah

Temuan terpenting dari banding itu bukan soal kecepatan. Dua kali LLM
menghasilkan SQL yang **tidak error** tapi jawabannya menyesatkan:

1. `WHERE pemain.nama = 'Magnus Carlsen'` → **0 baris**, karena database
   menyimpan `'Carlsen, Magnus'`. Tanpa penanda, ini terbaca sebagai
   "Carlsen tidak pernah bermain putih".
2. `JOIN partai pa ON p.id = pa.putih_id OR p.id = pa.hitam_id` lalu
   `AVG(a.akurasi_putih)` → angka rapi yang mencampur akurasi pemain itu
   dengan akurasi lawannya.

Dua tanggapan, keduanya sudah dikerjakan:

- **Prompt diperbaiki** dengan jebakan yang terbukti: format nama
  `"Belakang, Depan"` dan wajib `LIKE`, serta bahwa `analisis.partai_id`
  menunjuk ke `partai.id` bukan `pemain.id`. Hasilnya terukur — pertanyaan
  nomor 1 di atas kini menghasilkan `LIKE 'Carlsen%'` dan jawaban nyata
  (Sicilian 149, Ruy Lopez 131, English 72).
- **`peringatan_hasil()`** menandai pola yang mencurigakan: hasil kosong,
  JOIN ber-`OR` yang dipakai merata-rata kolom satu warna, dan pemain.id
  yang disamakan dengan partai_id. Pertanyaan nomor 2 sekarang tetap
  menghasilkan SQL yang salah, tapi **ditandai**.

Versi pertama penanda itu punya peringatan palsu: ia menandai
`pa.id = a.partai_id` yang justru benar. Sudah diperbaiki supaya hanya
menandai alias yang benar-benar merujuk tabel `pemain`, dengan dua tes yang
menjaganya. Peringatan palsu lebih merugikan daripada tidak ada peringatan,
karena membuat semua peringatan lain ikut diabaikan.

### Batas yang diketahui

- **12–24 detik untuk pertanyaan baru** — itu batas model 7b di CPU laptop
  ini. Diperbaiki 9 Okt 2026 (commit `afd0209`) sejauh yang bisa tanpa
  mengganti model: (1) skema untuk prompt di-cache 10 menit — `COUNT(*)`
  analisis_langkah makan 9,4 s saat cache disk dingin; (2) SQL di-cache per
  pertanyaan, jadi pertanyaan yang sama 0,05 s tanpa LLM, sedangkan hasilnya
  tetap dibaca ulang dari database; (3) `POST /api/tanya/panaskan` memuat
  model saat halaman dibuka, `keep_alive` 10 menit. Cache hilang kalau
  server dimatikan. Waktu dengan Ollama sungguhan belum diukur ulang (RAM
  bebas 2,8 GB saat itu, model butuh 4,7 GB).
- **LLM bisa mengarang SQL yang salah secara logika**, bukan cuma sintaks.
  `peringatan_hasil()` menangkap tiga pola yang terbukti terjadi, tapi itu
  petunjuk, bukan jaminan: SQL salah yang tidak cocok pola apa pun tetap
  lolos tanpa ditandai. SQL-nya selalu ditampilkan supaya Ray bisa menilai
  sendiri. Pemeriksaan kebenaran logika sepenuhnya tidak bisa diotomatiskan.
- **Kalimat jawaban dari templat, bukan LLM** (`kalimat_jawaban`, 9 Okt
  2026). Angka disalin apa adanya dari baris, jadi kalimat dan tabel tidak
  bisa berbeda. Batasnya: kalimatnya kaku, memakai alias kolom dari SQL, dan
  "baris pertama" mengikuti urutan SQL — bukan berarti "teratas".
- (Butir lama "`tanya_db.py` memuat SQL, penyimpangan belum disetujui" sudah
  gugur: sejak 21 Sep `CLAUDE.md` bagian 4 mengizinkan modul fitur punya
  kuerinya sendiri; yang dilarang hanya SQL di `app/main.py`.)
- Model perlu RAM: jangan menjalankan pertanyaan bersamaan dengan batch
  analisis engine. Aturan "maksimal dua proses engine" di mesin ini tetap
  berlaku, dan LLM ikut menghitung.

### Penyambungan

Endpoint `/api/tanya` (`app/tanya_web.py`) dan halaman `/tanya` (`web/tanya.html`)
tersambung 25 Sep 2026 (commit `02275fe`), 13 hari setelah modul intinya.
Sempat tertunda karena pada 12 Sep tiga sesi lain sedang menulis `app/main.py`
dan nav `web/`; modul inti yang mandiri membuat penyambungannya memang cuma
beberapa baris.

---

## Catatan lintas fase: siapa yang boleh menyuruh server (27 Sep 2026)

Server mendengar di laptop Ray dan, untuk pembantu, di WiFi/tunnel. Ada dua
middleware di `app/main.py`, logikanya di modul sendiri supaya bisa diuji:

| Lapis | Modul | Yang ditolak |
|---|---|---|
| perangkat lain | `app/pembantu.py` (`boleh_dari_luar`) | alamat bukan lokal / lewat proxy, kecuali jalur pembantu |
| asal permintaan | `app/penjaga_asal.py` (`asal_diizinkan`) | Host yang bukan laptop ini (DNS rebinding); POST/PUT/DELETE yang `Origin`-nya bukan halaman ini |

Lapis kedua ditambahkan setelah audit 27 Sep: sebelumnya situs apa pun yang
dibuka di browser laptop bisa mengirim POST ke `127.0.0.1:8000` (impor, jeda
mesin, hapus posisi) karena permintaan itu memang datang dari 127.0.0.1.
**Batas yang diterima:** permintaan tanpa `Origin` (python/curl dari terminal)
tetap dilayani — browser selalu menyertakannya untuk POST lintas situs, jadi
penyerang lewat browser tidak bisa memakai celah itu. Jalur pembantu
dikecualikan dari lapis kedua; jalur itu dijaga kode 6 angka, ditambah dua
perbaikan di hari yang sama: `/api/pembantu/kirim` hanya menerima partai yang
dipinjamkan ke perangkat itu (atau, kalau sewanya kedaluwarsa, partai yang
masih perlu dianalisis — hasil yang ada tidak pernah tertimpa), dan alamat
untuk batas tebakan kode hanya mempercayai `CF-Connecting-IP` dari koneksi
lokal (cloudflared), bukan `X-Forwarded-For`/`X-Real-IP` kiriman pengirim.
