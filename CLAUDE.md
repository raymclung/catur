# CLAUDE.md — aturan proyek Catur

Proyek pribadi Ray: database partai GM + alat belajar catur. Python, SQLite,
FastAPI, HTML/JS biasa. Dibaca otomatis oleh setiap sesi Claude Code.

**Status: ditetapkan Ray pada 10 Sep 2026 sebagai aturan yang mengikat.** Semua
yang dikerjakan di folder ini, oleh sesi mana pun, harus sejalan dengan file
ini. Pekerjaan yang melanggar dianggap belum selesai walaupun kodenya jalan.

Kalau dua dokumen bertentangan, yang menang berurutan:
`CLAUDE.md` → `SESI.md` bagian 6 (keputusan final) → `ARSITEKTUR.md`
→ `README.md` → `RENCANA-PROYEK.md`. Kalau aturan di sini ternyata salah,
usulkan perubahannya ke Ray; jangan dilanggar diam-diam.

## 0. Hal pertama di tiap sesi

1. Baca `SESI.md` bagian 4–5. Dua sesi bekerja paralel di folder ini; file itu
   satu-satunya tempat keduanya saling melihat.
2. Tahu peranmu: **backend** atau **frontend** (SESI.md bagian 1). Kalau tidak
   disebut di prompt, tanya Ray. Jangan menebak.
3. Tulis baris MULAI di SESI.md sebelum mengedit apa pun, SELESAI sesudahnya.
4. `git status` sebelum menyimpan file. File berubah yang bukan ulahmu = jangan
   sentuh, catat, lanjut ke hal lain.

## 1. Prinsip yang tidak ditawar

- **Urutan prioritas:** correctness → integritas data → fungsi catur → nilai
  belajar → performa → tampilan → skalabilitas. Jangan korbankan yang di kiri
  demi yang di kanan.
- **Jangan mengarang data.** Pemain, rating, turnamen, tanggal, PGN, hasil,
  sumber: kalau tidak ada di sumbernya, simpan NULL atau tandai belum
  terverifikasi. Tidak ada "kira-kira".
- **Buktikan, jangan asumsikan.** Tiap langkah punya "bukti selesai" yang bisa
  diulang orang lain: tes, angka dari database, atau respons HTTP nyata.
  "Seharusnya jalan" bukan bukti.
- **Database yang menolak, bukan kode yang berjanji.** Aturan integritas
  (UNIQUE, CHECK, FOREIGN KEY) ditegakkan di skema dan tiap aturan punya tes.
- **Jujur soal batas.** Kalau sesuatu tidak diselesaikan (mis. `Carlsen,M.` vs
  `Carlsen, Magnus` tetap dua pemain), tulis di dokumen dan buat tes yang
  mencatat batas itu. Jangan disembunyikan.

## 2. Bahasa

Semua dalam **bahasa Indonesia**: jawaban ke Ray, nama fungsi/variabel/kolom/
tabel, docstring, komentar, pesan commit, dokumen. Istilah catur dan teknis
yang lazim (PGN, FEN, Elo, endpoint, commit) tetap apa adanya.

## 3. Stack tetap, jangan diganti tanpa keputusan Ray

| | Dipakai | Bukan |
|---|---|---|
| Bahasa | Python 3.12 | Java (Ray belajar Java di folder terpisah) |
| Catur | python-chess 1.11.2 | parser buatan sendiri |
| Database | SQLite lewat `sqlite3`, SQL mentah | ORM, SQL Server, PostgreSQL |
| Web | FastAPI + uvicorn | Flask, Django |
| Frontend | HTML/CSS/JS biasa, `web/vendor/` (chessboard.js, chess.js) | React/Vue, Node.js (tidak terinstall), CDN |
| Engine | Stockfish di `engine/`, profil RAM di `src/catur_analysis/engine.py` | menjalankan engine tanpa batas RAM |
| Tampilan | mode terang | dark mode |

Dependency baru = tulis alasannya di `ARSITEKTUR.md` dulu.

## 4. Arsitektur: tiga batas yang dijaga

- **Frontend tidak pernah menyentuh database.** Hanya lewat `/api/...`.
- **Modul impor tidak tahu soal HTTP.** Impor bisa jalan dari command line
  tanpa server.
- **SQL ada di modul fitur, TIDAK PERNAH di `app/main.py`.** Aturan lama
  ("SQL hanya di enam modul") sudah dilanggar sejak lama: 21 Sep 2026
  terhitung 20 modul `app/` dan 8 skrip akar menulis SQL sendiri, termasuk
  `main.py` — persis yang dilarangnya. Aturan yang dilanggar tiap hari
  mengajarkan bahwa aturan di berkas ini boleh dilanggar, jadi diganti
  dengan yang benar-benar dijaga:
    * `app/main.py` bebas SQL; rute intinya memanggil modul
      (`repositori.pemain_ada()`, dst). Dijaga `tests/test_arsitektur.py`.
    * Tiap modul fitur (`ringkasan.py`, `sebab_kalah.py`, `pemain_stat.py`, …)
      boleh punya kuerinya sendiri — memaksanya lewat satu modul tengah hanya
      memindahkan kerumitan, bukan menghilangkannya.
    * Nilai SELALU lewat parameter `?`, tidak pernah disambung ke string.
      Daftar `IN (...)` boleh dibangun dari jumlah tanda tanya.
- Skema = `app/db.py`, satu-satunya sumber kebenaran. Jangan menyalin SQL ke
  dokumen; salinan selalu menyimpang. Perubahan skema = naikkan
  `VERSI_SKEMA`, migrasi aditif, tes.

## 5. Perintah yang dipakai

```
python -m unittest discover -s tests -v          # tes (pytest TIDAK terinstall)
set CATUR_UJI_HEADLESS=1 && python -m unittest tests.test_halaman_headless   # uji halaman di Edge
python -m app.db --info                          # isi database
python -m uvicorn app.main:aplikasi --reload     # server, http://127.0.0.1:8000, /docs
python impor_pgn.py --semua                      # partai pribadi dari data/pgn/
python impor_twic.py 1661 --coba                 # TWIC, lihat README untuk pilihan
python impor_broadcast.py --resmi --tier 5 --lihat-saja
python indeks_posisi.py                          # indeks posisi (bisa dilanjutkan)
python analisis_partai.py --lihat-saja ...       # engine, lihat README
```

Sejak 12 Sep 2026 dua pekerjaan ini juga bisa dilakukan Ray **tanpa terminal**,
lewat halaman web (tetap tunduk aturan bagian 7):

```
/static/impor.html    unggah/seret/tempel PGN, atau tempel tautan broadcast Lichess
/static/mesin.html    pilih partai -> perkiraan waktu -> jalankan analisis engine
```

Sebelum menyatakan selesai: tes lolos semua, dan bukti selesai dari SESI.md
bagian 4 sudah dipenuhi.

## 6. Kode

- UTF-8, **LF** (bukan CRLF), 4 spasi — untuk berkas KODE dan DOKUMEN;
  periksa dengan `git ls-files --eol`. Pengecualiannya tidak lagi diingat-ingat
  sendiri, tapi ditegakkan `.gitattributes` (audit 25 Sep 2026):
  `*.cmd` **wajib CRLF** (cmd.exe bisa tidak membaca baris terakhir kalau LF),
  `data/pgn/**` dan `data/laporan/*.txt` `-text` (data sumber dan log: biarkan
  apa adanya, byte-per-byte), `*.png *.ico *.db` biner. Aturan "semua file LF"
  yang dulu ditulis di sini SALAH untuk berkas-berkas itu.
- Docstring menjelaskan *kenapa* dan risiko yang diterima, bukan mengulang
  nama fungsi. Komentar hanya kalau membantu.
- File kecil, satu tanggung jawab. Kalau menemukan kode buruk, perbaiki,
  jangan menumpuk workaround.
- **Patch bertarget, bukan tulis ulang file.** Menulis ulang file yang ada
  adalah cara file orang lain hilang (sudah terjadi 9 Sep).
- Nama file baru: cek dulu apakah sudah ada. Skrip CLI di akar, modul di
  `app/`, tes di `tests/test_<modul>.py` dengan `unittest`.
- Tanpa kredensial di kode. Tanpa hard-code path Windows selain lewat
  `Path(__file__)`.

## 7. Data, sumber, dan lisensi

- `data/catur.db`, `data/twic/`, `engine/` **tidak masuk git** (turunan atau
  berlisensi). `data/pgn/` (partai pribadi Ray) masuk git.
- Sumber partai GM yang sah: **TWIC** ("free for personal use only", jangan
  disebarkan ulang), **broadcast Lichess** (API resmi), dan **PGN Mentor**
  (berkas diunduh sendiri oleh Ray ke `data/pgnmentor/`). Bukan ChessBase,
  bukan chessgames.com, bukan halaman yang memasang pemeriksaan browser.
  Ketiganya yang dihitung sebagai partai GM (`SUMBER_GM` di `app/posisi.py`).
- Nama pemain dari PGN Mentor **bukan sumber kebenaran**: berkasnya memuat
  "Giri,Abhishek" untuk Anish Giri dan "Eliseev, Alexey" untuk partai Urii
  Eliseev. Nama resmi diambil dari FIDE lewat `rapikan_nama.py`.
- Unduhan berurutan dengan jeda; User-Agent yang jujur; hormati batas API
  Lichess/Chess.com. Tidak ada scraping yang melanggar ToS atau robots.txt.
- Tiap partai menyimpan `sumber` dan `sumber_url`. Pertanyaan "partai ini dari
  mana?" harus selalu bisa dijawab.
- Hanya peran **backend** yang menulis `catur.db` dari terminal. Jangan
  menjalankan dua penulis besar (impor, indeks, migrasi, penggabungan pemain)
  bersamaan: semuanya wajib memegang kunci `app/kunci_penulis.py`.
- **Ray boleh menulis lewat halaman web** (izin Ray, 12 Sep 2026), dan dua
  halaman itu tunduk pada aturan yang sama:
  - **Impor** (`/static/impor.html`) memegang kunci penulis yang sama dan
    menjawab **409** menyebut pemegangnya kalau sedang dipakai. Batas 8 MB per
    unggahan; yang lebih besar tetap lewat `impor_pgn.py`.
  - **Mesin** (`/static/mesin.html`) tidak menganalisis di dalam request: ia
    meluncurkan `analisis_partai.py` sebagai proses terpisah, dan menolak
    **409** kalau slot batch sudah penuh — termasuk batch yang dijalankan dari
    terminal oleh sesi lain.
  - Aturan engine (**diubah Ray 12 Sep**, sebelumnya "maksimal dua proses,
    tidak pernah dua batch sekaligus"): **maksimal dua batch engine
    bersamaan** (sempat 4 pada 13 Sep sore; 13 Sep malam jadi 2 karena RAM laptop penuh — sisanya dibantu komputer lain lewat halaman pembantu), angkanya di `MAKS_BATCH` pada `app/analisa_web.py`. Dasarnya
    ukuran mesin ini: 10 inti fisik, profil hemat 2 utas + ~371 MB per batch;
    dua batch hanya memakai 38% CPU dan menyisakan 4,9 GB RAM. Engine server
    papan analisis (satu proses, hidup sebentar) di luar hitungan itu.
  - Yang TIDAK berubah: **penulis besar tetap satu** (impor, indeks, migrasi,
    penggabungan pemain) — lihat bagian 7 dan `app/kunci_penulis.py`. Batch
    analisis menulis sedikit, jadi boleh berbarengan; impor tidak.

## 8. Dokumen: siapa memegang apa

| File | Sifat | Aturan |
|---|---|---|
| `RENCANA-PROYEK.md` | bank ide (hasil ekspansi GPT), **beku** | jangan diedit, jangan dikutip sebagai requirement tanpa lewat ARSITEKTUR |
| `ARSITEKTUR.md` | rancangan hidup, semua fase dalam **satu** berkas | diperbarui tiap langkah selesai; fase baru = bagian `## Phase N` baru di berkas yang sama, jangan bikin berkas baru; penyimpangan dari RENCANA dicatat di bagian Phase 1 nomor 10 |
| `SESI.md` | papan koordinasi dua sesi | MULAI/SELESAI per tugas; keputusan final di bagian 6 |
| `README.md` | cara pakai | hanya perintah yang benar-benar jalan |
| `PANDUAN-TERMINAL.md` | perintah siap tempel untuk Ray, termasuk catatan khas terminal (cara menghentikan batch, aturan tidur laptop) | perintah harian; kalau sebuah pekerjaan sudah bisa lewat halaman web, sebutkan di sini |
| `CLAUDE.md` | file ini | ubah hanya kalau Ray minta |

Ketentuan produk yang sudah final ada di **bagian 11 berkas ini**; riwayat dan
alasan tiap keputusan tetap di `SESI.md` bagian 6. Jangan dibuka lagi; kalau
tidak setuju, tulis keberatan, jangan diam-diam mengubah.

## 9. Git

- Hanya peran backend yang `git add`/`commit`. Frontend tidak menyentuh git.
- Commit kecil per tugas, pesan bahasa Indonesia: baris pertama ringkas,
  badan menjelaskan bukti. Jangan `git add -A` kalau ada file sesi lain yang
  setengah jadi; sebut di pesan commit kalau memang mengamankan WIP orang lain.
- Jangan `reset --hard`, `push --force`, atau menghapus file yang bukan
  buatanmu tanpa konfirmasi Ray.

## 10. Cara berkomunikasi dengan Ray

- Jawab langsung, singkat, bahasa Indonesia santai (aku/kamu).
- Sebelum perubahan besar: jelaskan apa, file mana, kenapa, baru kerjakan.
- Requirement ambigu: ambil asumsi yang masuk akal, sebutkan, lanjut. Berhenti
  bertanya hanya kalau jawabannya mengubah arsitektur atau tidak bisa dibatalkan.
- Kalau ada risiko atau ketidaksetujuan, katakan sekali dengan jelas, lalu
  ikuti keputusan Ray.
- Angka dan bukti, bukan "sudah beres". Kalau tes gagal, tunjukkan outputnya.

## 11. Ketentuan produk (ditetapkan Ray — jangan dibuka lagi)

Riwayat dan alasan tiap keputusan ada di `SESI.md` bagian 6. Kalau keduanya
berbeda, yang di sini yang menang (urutan dokumen di awal berkas).

**Akun pribadi Ray**

| Tempat | Akun (ejaan seperti di `ambil_partai.py`) |
|---|---|
| Lichess | `McLung_07`, `Mikaysr`, `Sakamoto_PMan` |
| Chess.com | `rayylung`, `stanley-squarepants` |
| **Bukan akun Ray** | `RyuMcLungG`. Partainya hanya diunduh untuk turnamen Arena Kings (partai lain di akun itu dimainkan orang lain), tapi **tidak** dihitung sebagai akun Ray: jangan ditandai `akun_pribadi`, jangan ikut di halaman latihan/ringkasan. Dikecualikan juga di `web/akun-pribadi.js` |

**Partai pribadi yang dipakai untuk analisis dan latihan**: hanya kalau lawan
ber-rating **di atas 2200** (aturan ini menggantikan "+200 di atas Ray"); lawan
bergelar tetap ikut walau ratingnya di bawah itu.

**Prioritas analisis**: partai yang **kalah** didahulukan, lalu diselidiki
sebabnya — habis waktu, miss-click, atau blunder sungguhan.

**Format waktu dipisah**: bullet / blitz / rapid / klasik, dihitung dari
TimeControl (detik awal + 40 x inkremen: < 180 bullet, < 480 blitz, < 1500
rapid, selebihnya klasik — batas Lichess, lihat `app/format_partai.py`).
Ringkasan dan latihan harus bisa dipilah per format.

**Data GM**

- Fokusnya turnamen OTB dan broadcast, bukan arena online.
- Cakupan TWIC = **seluruh arsip yang tersedia**, bukan 3 tahun terakhir.
  Batas nyata: zip hanya ada dari edisi 920 (Juni 2012); edisi 1-919 hanya
  lewat donasi 30 pound ke TWIC, jadi di luar jangkauan sampai Ray memutuskan lain.
- Saringan impor TWIC: kedua pemain Elo >= 2500, partai online dibuang.
  Ambang 2200 yang pernah dibahas dipakai untuk **data pembukaan**, bukan untuk
  memasukkan partai baru.
- Partai remis **tidak dihapus**; dipisah lewat saringan hasil di tampilan.
- Kunci turnamen = (nama, tahun); arena online yang berulang boleh tergabung
  per tahun.

**Tampilan dan engine**

- Mode terang saja.
- Papan analisis boleh memakai satu Stockfish profil hemat on-demand di server;
  batch engine tetap proses terpisah (lihat batas dua engine di bagian 7).
