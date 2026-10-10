# Chess GM Database

Database partai catur untuk belajar dari grandmaster. Phase 1: simpan, cari,
buka, dan mainkan ulang partai.

Perintah harian yang tinggal ditempel (impor GM top 10, analisis per GM,
pembaruan mingguan): **`PANDUAN-TERMINAL.md`**.

## Menjalankan

```
python -m uvicorn app.main:aplikasi --reload
```

Lalu buka **http://127.0.0.1:8000** di browser.

**Untuk pembantu lewat internet:** `jalankan-pembantu.cmd` menyalakan server
KHUSUS pembantu di port 8001 (`CATUR_MODE=pembantu`) — jalur selain halaman
pembantu ditolak dari siapa pun, termasuk 127.0.0.1. Tunnel Cloudflare
diarahkan ke port itu, bukan ke port 8000. Server biasa tidak boleh ditunnel.

**Tanpa terminal:** klik dua kali **`jalankan-server.cmd`** (atau shortcut
"Catur - Nyalakan Server" di Desktop). Server dinyalakan di latar belakang lalu
browser dibuka sendiri; kalau servernya sudah menyala, browsernya saja yang
dibuka. Pasangannya **`matikan-server.cmd`**. Keduanya TIDAK menyala otomatis
saat Windows hidup - permintaan Ray, 13 Sep 2026.

Alamat itu hanya bisa diakses dari komputer ini sendiri. Dokumentasi API
otomatis ada di http://127.0.0.1:8000/docs.

Supaya **tablet/HP ikut menganalisis** (lihat "Pembantu tablet/HP" di bawah),
jalankan dengan `--host 0.0.0.0`. Perangkat lain di WiFi tetap hanya bisa
membuka halaman pembantu, dan itu pun wajib kode; sisanya dijawab 403.

Tekan `Ctrl+C` di terminal untuk mematikan server.

## Yang dibutuhkan

| | Versi | Cara memasang |
|---|---|---|
| Python | 3.12 | sudah ada |
| python-chess | 1.11 | sudah ada |
| fastapi + uvicorn | 0.141 / 0.52 | `python -m pip install fastapi "uvicorn[standard]"` |

Library papan (`chessboard.js`, `chess.js`, `jQuery`) sudah tersimpan lokal di
`web/vendor/` — aplikasi jalan tanpa internet.

## Memasukkan partai

**Tanpa terminal:** buka **http://127.0.0.1:8000/static/impor.html** (tab
"Impor"). Di sana bisa seret/pilih berkas PGN, menempel isi PGN, atau menempel
tautan broadcast Lichess — satu ronde atau seluruh turnamen sekaligus.

- Indeks posisi partai baru ikut dibangun, jadi langsung terlihat di Jelajah
  dan Repertoire.
- Batas 8 MB per unggahan. Berkas lebih besar tetap lewat `impor_pgn.py`.
- Kalau ada impor/indeks lain yang sedang jalan, halaman menjawab 409 dan
  menyebut siapa pemegang kuncinya — bukan gagal diam-diam.
- Ronde broadcast yang sudah final dilewati, jadi tautan yang sama aman
  diimpor berkali-kali.

Lewat terminal:

```
# file PGN lokal
python impor_pgn.py data/pgn/nama-file.pgn
python impor_pgn.py --semua

# siaran turnamen Lichess — tempel URL dari browser
python impor_broadcast.py "https://lichess.org/broadcast/.../kmXN5u46"
python impor_broadcast.py kmXN5u46 --turnamen        # semua ronde
python impor_broadcast.py kmXN5u46 --turnamen --lihat-saja   # cek dulu

# partai sendiri dari Lichess / Chess.com (akun ada di ambil_partai.py)
python ambil_partai.py --kecepatan rapid classical
python ambil_partai.py --semua-kecepatan
```

Semua perintah impor **aman diulang**: partai yang sudah ada tidak
diduplikasi, dan partai broadcast yang tadinya belum selesai diperbarui.

Setelah impor, kolom turunan `partai.format` (bullet/blitz/rapid/klasik/tanpa,
dari TimeControl — lihat `app/format_partai.py`) dan tanda `pemain.akun_pribadi`
diisi otomatis. Untuk menghitung ulang tanpa impor: `python impor_pgn.py --rapikan`.
Keduanya dipakai saringan `format=` di `/api/partai`, `/api/ringkasan`, dan
`/api/pemain?pribadi=true`.

## Papan catur

| Tombol | Keyboard | Fungsi |
|---|---|---|
| ⏮ | `Home` | langkah pertama |
| ◀ | `←` | mundur |
| ▶ | `spasi` | putar otomatis / jeda |
| ▶\| | `→` | maju |
| ⏭ | `End` | langkah terakhir |
| ⇅ | `F` | balik papan |

Klik langkah mana pun di daftar untuk melompat ke sana.

Di papan analisis (`/analisis`), **Analisa** (`spasi`) mengevaluasi posisi
±1,5 detik; **Terus** menganalisis tanpa henti — depth naik dan garisnya
diperbarui sampai kamu menekan **Henti**, posisinya berubah, depth 30, atau 2
menit. Selama Terus berjalan, engine server dipegang aliran itu, jadi evaluasi
di tab lain menjawab "engine sedang dipakai". Pilih **Engine: perangkat ini**
untuk memakai Stockfish di browser (misalnya dari HP lewat tunnel); Analisa
dan Terus sama-sama berjalan di sana dan tidak memakai engine laptop.

## Struktur

```
app/          server FastAPI + akses database
  db.py         skema (sumber kebenaran)
  impor.py      PGN -> database
  repositori.py semua SQL
  main.py       rute HTTP
web/          halaman + JS + library papan
data/
  catur.db      database (tidak masuk git)
  pgn/          file PGN sumber
tests/        python -m unittest discover -s tests -v
```

Dokumen rancangan dan catatan keputusan: `ARSITEKTUR.md` (semua fase).
Visi jangka panjang: `RENCANA-PROYEK.md`.

## Analisis engine (Phase 2)

Ada dua jenis analisis, dan keduanya punya tempat sendiri:

- **Satu posisi, langsung dari papan** — halaman `/analisis` (susun posisi,
  FEN dua arah, tekan Analisa) memanggil `GET /api/evaluasi?fen=`. Server
  menjalankan **satu** Stockfish profil hemat on-demand (~1,5 detik per
  permintaan, mati sendiri setelah 5 menit menganggur, 409 kalau sedang
  dipakai). Tidak butuh terminal. Lihat `ARSITEKTUR.md` (Phase 5).
- **Partai penuh** (tiap langkah diklasifikasi, akurasi, turning point,
  tersimpan di database) — terlalu berat untuk sebuah request (puluhan detik
  sampai menit per partai), jadi selalu dijalankan sebagai proses tersendiri,
  dan halaman web membaca hasilnya. Batasnya **dua batch engine sekaligus**
  (aturan kita, bukan batas mesin: 10 inti fisik, dua batch cuma 38% CPU);
  engine server papan analisis di luar hitungan itu.

**Tanpa terminal:** halaman **http://127.0.0.1:8000/static/mesin.html** (tab
"Mesin") menjalankan batch yang sama tanpa mengetik perintah:

- pilih partai (partai sendiri / partai GM / nomor partai tertentu), lalu
  **Lihat dulu** menampilkan jumlah partai, perkiraan waktu, contoh partainya,
  dan perintah terminal yang setara;
- **Mulai analisa** meluncurkan `analisis_partai.py` sebagai proses lepas —
  halaman boleh ditutup, server boleh dimatikan, batchnya tetap jalan;
- kemajuan (x dari y, sisa berapa lama, ekor log) dan tombol Hentikan ada di
  kartu kanan; batch yang dijalankan dari terminal ikut terlihat di situ;
- halaman menampilkan "N dari 2 slot engine dipakai" (batch dari terminal ikut
  dihitung); tombol Mulai baru mati dan permintaannya ditolak 409 kalau semua
  slot terpakai.

**Antrean otomatis** (13 Sep 2026) — supaya engine tidak menganggur setiap kali
satu batch selesai:

- susun saringannya, lalu tekan **+ Antrekan** alih-alih Mulai; pekerjaan masuk
  daftar di kartu "Antrean otomatis";
- tekan **Hidupkan penjaga** sekali. Penjaga (`jaga_antrean.py`) berjalan lepas
  dan menjalankan pekerjaan berikutnya begitu ada slot engine kosong;
- antreannya tersimpan di `data/antrean-analisa.json`, jadi selamat kalau server
  dimuat ulang **atau komputer dimatikan**. Yang perlu jelas: selama komputer
  mati tidak ada yang berjalan — begitu dinyalakan lagi, jalankan penjaganya
  (dari halaman Mesin, atau `python jaga_antrean.py --jaga`) dan sisanya
  dilanjutkan.

**Pembantu tablet/HP** (13 Sep 2026) — perangkat lain di WiFi yang sama ikut
menghitung dengan Stockfish 18 lite di browser (satu inti, tanpa memasang apa pun):

- di tablet/HP buka alamat yang tertulis di kartu "Pembantu tablet/HP" halaman
  Mesin (misalnya `http://192.168.1.11:8000/pembantu`), masukkan kodenya, tekan
  **Mulai**, biarkan layar menyala;
- laptop membagi satu partai per perangkat (partai GM dulu lalu partai sendiri, dari yang
  terlama; batch laptop mengambil yang terbaru), browser mengirim evaluasi tiap
  posisi, dan laporan disusun di laptop dengan `analisis_partai()` yang sama —
  tersimpan sebagai engine "Stockfish 18 lite (browser pembantu)", profil cepat;
- dicocokkan dengan Stockfish 18 laptop profil cepat pada 12 partai: selisih
  akurasi median 0,65 poin (maks 3,9), jumlah blunder sama di 8/12, langkah
  terbaik sama 61%;
- syarat: server dijalankan dengan `--host 0.0.0.0`, Python diizinkan di
  Windows Firewall, engine browser sudah ada (`python -m app.pembantu
  --unduh-engine`, disimpan di `engine/browser/`, tidak ikut git).

Lewat terminal:

```
python jaga_antrean.py --lihat      # isi antrean
python jaga_antrean.py --jaga       # jaga terus (bawaan 2 batch = batas mesin MAKS_BATCH)
python jaga_antrean.py --sekali     # satu kali periksa
python jaga_antrean.py --daftarkan  # hidup sendiri tiap masuk Windows
python jaga_antrean.py --cabut      # batalkan pendaftaran itu
```

`--daftarkan` menaruh satu berkas `.cmd` di folder Startup pengguna
(`%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup`), bukan di Task
Scheduler: membuat tugas terjadwal ditolak tanpa hak administrator di mesin ini,
dan folder Startup melakukan hal yang sama tanpa hak khusus — isinya juga bisa
dilihat dan dihapus sendiri. Pendaftaran itu **mencabut dirinya sendiri begitu
antrean habis**, jadi tidak ada pengaturan yang menggantung setelah semua partai
selesai dianalisis. Penjaga kedua tidak akan menumpuk: kalau sudah ada yang
jalan, yang baru langsung berhenti.

Lewat terminal:

```
python analisis_partai.py --id 326 11669              # partai tertentu
python analisis_partai.py --gelar-lawan GM IM         # partai sendiri lawan GM/IM
python analisis_partai.py --lawan-min-elo 2700 --terbaru 50
python analisis_partai.py --sumber lichess-broadcast --terbaru 20 --profil normal
python analisis_partai.py --pemain Carlsen --terbaru 50            # partai GM: pilih pemainnya
python analisis_partai.py --pemain Gukesh Erigaisi --sumber twic --terbaru 30
python analisis_partai.py --min-elo 2700 --terbaru 100 --lihat-saja  # antar-GM 2700+, daftar dulu
python analisis_partai.py --gelar-lawan GM --lihat-saja   # daftar saja
```

- Partai yang sudah dianalisis dilewati; `--ulang` untuk memaksa.
- Sekitar 49 detik per partai (profil hemat, terukur 10 Sep 2026). 50 partai
  ≈ 40 menit. Jalankan di jendela terminal sendiri dan biarkan terbuka; laptop
  jangan tidur. Kalau terputus, jalankan perintah yang sama lagi: yang sudah
  dianalisis dilewati.
- Hasilnya tampil di halaman partai (bar evaluasi, komentar tiap langkah,
  grafik) dan dipakai halaman latihan.
- `Ctrl+C` kapan pun aman — tiap partai disimpan begitu selesai.
- Profil `hemat` (default, ~370 MB, ~1 s/langkah), `normal` (~500 MB, depth 20),
  `dalam` (~690 MB, depth 24). Estimasi waktu ditampilkan sebelum mulai.

Setelah dianalisis, halaman partai menampilkan eval bar, tanda `??` `?` `?!`,
langkah yang seharusnya, sorotan kotak, grafik evaluasi (klik untuk lompat),
dan akurasi. Halaman **/ringkasan** menggabungkan banyak partai: kesalahan per
fase, per kekuatan lawan, per pembukaan.

Server perlu `--reload` supaya rute dan file baru langsung terlihat:

```
python -m uvicorn app.main:aplikasi --reload
```

## Partai GM dari arsip TWIC (Phase 3)

Dua sumber, keduanya resmi dan bisa ditelusuri per partai (`sumber_url`):

```
# TWIC — arsip mingguan sejak 1994 (free for personal use), dipakai untuk backfill.
# Perintah dan saringannya lihat:  python impor_twic.py --help
# (file ini sedang direvisi terpisah; docstring-nya adalah acuan terkini)

# Lichess broadcast — turnamen aktif & baru selesai, untuk pembaruan berkala
python impor_broadcast.py --resmi --tier 4         # semua turnamen resmi tier >= 4
python impor_broadcast.py --resmi --tier 5 --lihat-saja
```

**Saringan bintang** (yang menentukan partai broadcast mana yang masuk): satu
partai disimpan kalau **salah satu** pemainnya Elo >= 2400, atau **salah satu**
bergelar GM/WGM berapa pun ratingnya — lawannya tidak diperiksa. Partai tanpa
Elo dan tanpa gelar dibuang, karena kekuatan pemainnya tidak bisa dibuktikan.
Atur dengan `--min-elo-bintang` dan `--gelar-lolos`; nilai bawaannya ada di
`impor_broadcast.py` bersama riwayat keputusan Ray dan angka pengukurannya.

Ini memang meloloskan minoritas partai turnamen besar, dan itu benar: diukur
25 Sep 2026 pada 27 ronde Olimpiade Samarkand (2.364 partai), yang lolos 660
(28%) — bagian Open 481 dari 1.236 (39%), bagian Women 179 dari 1.128 (16%).
Ronde papan atas lolos 100% (48 dari 48); bagian bawah 0%, karena sebagian
besar peserta Olimpiade memang di bawah 2400. Aturan saringan ikut jadi bagian
kunci di `sumber_impor`, jadi kalau aturannya dilonggarkan, ronde yang dulu
tersaring otomatis diambil ulang pada pembaruan berikutnya (sebatas rentang
`--hari`, bawaannya 10 hari terakhir).

Cakupan: **seluruh arsip TWIC yang tersedia** (keputusan Ray 11 Sep 2026),
bukan 3 tahun. Zip hanya ada dari edisi 920 (Juni 2012) ke atas; edisi 1-919
tidak bisa diunduh satuan. Backfill arsip lama:
`python perbarui.py --hanya twic --edisi 920-1505` (jalankan sendirian).

Data yang sudah ada (35.326 partai TWIC, edisi 1506-1661) diimpor 9 Sep 2026 dengan
saringan: partai online (`Site … INT`) dibuang, kedua pemain Elo ≥ 2500. Zip
tersimpan di `data/twic/` (tidak masuk git, tidak disebarkan ulang). Ronde
broadcast yang sudah selesai tercatat di tabel `sumber_impor` dan dilewati
saat perintah diulang.

**Jejak asal koleksi PGN.** Tiap partai menyimpan `sumber` dan `sumber_url`,
jadi "partai ini dari mana?" selalu bisa dijawab. Koleksi karier pgnmentor
sempat masuk tanpa `sumber_url` dan tanpa baris `sumber_impor`; jejaknya
dipulihkan dengan mencocokkan ulang berkasnya ke database lewat hash partai:

```
python impor_pgn.py --jejak                  # lihat saja: per berkas, berapa
                                             # cocok / duplikat sumber lain /
                                             # TIDAK ADA di database
python impor_pgn.py --jejak --tulis          # isi sumber_url + catat sumber_impor
python impor_pgn.py --jejak --tanpa-jaringan # tanpa pemeriksaan HEAD ke pgnmentor
```

URL koleksi (`https://www.pgnmentor.com/players/<Nama>.zip`) diperiksa HEAD
dulu satu per satu dengan jeda 1 detik; yang tidak menjawab 200 dibiarkan
NULL, tidak dikarang. Aman diulang: `sumber_url` yang sudah terisi tidak
ditimpa dan baris `sumber_impor` yang sudah ada tidak ditulis ulang.

Kalau rumus sidik jari partai berubah: `python impor_pgn.py --hitung-ulang-hash`.

Halaman **/pemain/{id}** (klik nama pemain di mana pun): rating, hasil
sebagai Putih/Hitam, pembukaan per keluarga ECO dengan skor, partai terbaru.
Di halaman daftar ada tombol repertoire (English, Catalan, Ragozin, Sicilian)
dan parameter `eco_dari`/`eco_sampai`, `pemain_id`, `warna`.

## Pembaruan mingguan (satu perintah)

```
python perbarui.py            # cadangan -> TWIC edisi baru -> broadcast tier 5 -> indeks posisi
python perbarui.py --coba     # hitung saja, tidak menulis database
python perbarui.py --hanya twic --hanya indeks
```

TWIC diambil dari edisi terakhir yang tercatat di `sumber_impor` sampai edisi
minggu ini (terbit tiap Senin); edisi yang belum terbit menghentikan tahap itu
tanpa error. Saringannya sama dengan backfill: kedua pemain Elo >= 2500, partai
online dibuang (`--min-elo` untuk mengubah). Log di `data/laporan/log-perbarui-*.txt`.
Jangan dijalankan bersamaan dengan `analisis_partai.py`.

## FIDE ID dan gelar untuk pemain tanpa ID

PGN Mentor dan PGN lama tidak membawa FIDE ID, jadi gelarnya kosong. Daftar
pemain resmi FIDE (unduhan publik bulanan, 45 MB) bisa mengisinya lewat nama,
dengan pagar ketat (nama unik, umur masuk akal, Elo sejalan dengan gelar —
lihat docstring skripnya):

```
curl --create-dirs -A "catur-analysis/0.1" -o data/fide/players_list.zip https://ratings.fide.com/download/players_list.zip
python cocokkan_fide.py data/fide/players_list.zip            # lihat saja + laporan TSV
python cocokkan_fide.py data/fide/players_list.zip --jalan    # tulis (cadangan dulu)
```

Menjalankan ulang aman: pemain yang sudah ber-ID dilewati.

## Cadangan database

`data/catur.db` tidak masuk git, dan hasil analisis engine di dalamnya mahal
dihitung ulang (~49 detik per partai). Cadangkan seminggu sekali, atau sebelum
migrasi skema:

```
python cadangkan_db.py            # -> data/backup/catur-YYYYMMDD-HHMMSS.db, simpan 5 terakhir
python cadangkan_db.py --daftar   # cadangan yang ada
python cadangkan_db.py --inti     # ekspor kecil (partai sendiri + analisis + latihan) -> data/backup/ DAN folder OneDrive\catur-cadangan
```

Memakai `VACUUM INTO` (satu snapshot baca), jadi aman dijalankan saat server
dan analisis batch sedang hidup. Salinan diperiksa `integrity_check` sebelum
dianggap jadi. Database ±4,8 GB, jadi cadangan butuh beberapa menit.

**Memulihkan** — urutannya penting:

1. Matikan server, penjaga antrean, dan semua batch analisis.
2. Hapus `data/catur.db-wal` dan `data/catur.db-shm` kalau ada. **Jangan
   dilewati:** sisa WAL dari database lama akan diputar SQLite ke atas cadangan
   begitu dibuka; `integrity_check` tetap "ok", tapi isinya campuran dan bukan
   cadangan itu lagi (terbukti 28 Sep 2026).
3. Salin berkas cadangan ke `data/catur.db`.
4. `python -m app.db --info` — jumlah partai harus sama dengan saat cadangan dibuat.

## Yang belum ada (Phase 4+)

Perbandingan dua pemain, pattern library, sistem latihan, penjadwalan
pembaruan otomatis (cron). Lihat `RENCANA-PROYEK.md`.
