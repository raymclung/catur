# Panduan terminal

Perintah siap tempel. Jalankan dari folder `Catur` di terminal VS Code.

**Sebagian besar pekerjaan harian sudah bisa lewat browser**, jadi terminal
sekarang dipakai untuk empat hal saja:

1. melanjutkan analisis setelah komputer dinyalakan lagi,
2. menyalakan server web,
3. pekerjaan besar (impor ribuan partai, indeks, pemeliharaan),
4. memeriksa keadaan waktu ada yang aneh.

Lewat browser di `http://127.0.0.1:8000`:
**Impor** (`/static/impor.html`) untuk menambah PGN atau tautan broadcast, dan
**Mesin** (`/static/mesin.html`) untuk memilih partai, melihat perkiraan waktu,
menjalankan analisis, dan memantau kemajuannya.

---

## 1. Melanjutkan analisis setelah komputer menyala lagi

Batch analisis **tidak menyala sendiri** setelah shutdown. Tempel ini:

```
python analisis_partai.py --sumber lichess-user --format blitz --lawan-min-elo 2201 --hasil kalah

python analisis_partai.py --repertoire --min-elo 2750 --terbaru 150 --sumber twic
```

Yang pertama: partai blitz sendiri yang **kalah** lawan rating di atas 2200 —
bahan halaman Kekalahan. Yang kedua: partai GM di pembukaan repertoire sendiri
(English, Catalan, Ragozin, Sicilian).

Aman diulang kapan saja: partai yang sudah dianalisis dilewati, jadi perintah
yang sama akan melanjutkan, bukan mengulang. Hasil disimpan **per partai**,
jadi mati listrik paling banyak menghilangkan satu partai yang sedang dihitung.

## 2. Menyalakan server web

```
python -m uvicorn app.main:aplikasi --port 8000
```

Biarkan terminalnya terbuka selama memakai web; kalau ditutup, servernya mati
dan browser menjawab `ERR_CONNECTION_REFUSED`. Batch analisis **tidak** ikut
mati — itu proses terpisah.

Jangan pakai `--reload`: di mesin ini ia sering gagal memuat rute baru tanpa
memberi tahu. Setelah mengubah kode `app/`, matikan lalu nyalakan lagi.

## 3. Sebelum menjalankan apa pun yang menulis database

```
python -m app.kunci_penulis
```

Jawabannya `bebas, boleh mulai`, atau `SEDANG DIPAKAI` beserta perintah yang
sedang memegangnya. Impor, indeks, dan penggabungan pemain **harus sendirian**.
Batch analisis tidak perlu menunggu perintah ini — ia menunggu sendiri kalau
ada impor yang berjalan.

```
python -m app.db --info
```

## 4. Aturan menjalankan batch

| Aturan | Angka |
|---|---|
| Batch analisis bersamaan | maksimal **4** (`MAKS_BATCH` di `app/analisa_web.py`) |
| Penulis besar bersamaan (impor, indeks, gabung) | **1** |
| Profil engine | `cepat` untuk memborong partai GM, `hemat` untuk partai sendiri |
| Kecepatan | ±50 detik per partai pada profil hemat |

**Profil `cepat` (depth 10) untuk memborong.** Diukur 13 Sep 2026 pada tiga
partai blitz, depth 16 sebagai acuan:

| depth | detik/partai | lebih cepat | kesalahan besar ketemu | palsu |
|---|---|---|---|---|
| 16 (hemat) | 82 | 1x | 12 dari 12 | 0 |
| 14 | 30 | 2,8x | 9 dari 12 | 1 |
| 12 | 10 | 7,8x | 9 dari 12 | 2 |
| 10 (cepat) | 2,7 | **30x** | 9 dari 12 | 3 |
| 8 | 1,7 | 47x | 6 dari 12 | 3 |

Depth 12 tidak menemukan lebih banyak daripada depth 10, padahal empat kali
lebih lambat — jadi kalau mau cepat, langsung ke 10. Pakai `cepat` untuk partai
GM yang jumlahnya ratusan ribu; pakai `hemat` untuk partai sendiri yang
benar-benar dipelajari satu per satu.

Profil lebih dalam **tidak mengubah kesimpulan**, hanya memperlambat. Diuji 12
Sep 2026 pada partai yang sama: depth 16, 20, dan 24 sama-sama menunjuk langkah
kesalahan besar pertama dan blunder terparah yang sama; akurasinya 91,2 / 91,6 /
91,3 persen, tapi waktunya 84 / 335 / 890 detik. Untuk 25 partai: hemat 18
menit, normal 135 menit, dalam 506 menit.

## 5. Menghentikan batch dengan benar

Cara termudah: tombol **Hentikan** di halaman Mesin.

Dari terminal, **jangan** percaya nomor proses yang muncul saat menjalankan
lewat `Start-Process`. Di Windows, perintah `python` adalah jalan pintas yang
langsung keluar setelah menyalakan python yang sebenarnya, sehingga nomor itu
menunjuk proses yang sudah mati. Akibat nyata pada 12 Sep 2026: dua batch
dikira sudah berhenti, padahal masih jalan, dan pekerjaannya kembar.

Lihat proses yang sungguhan dulu:

```
powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.Name -like 'python*' -and $_.CommandLine -match 'analisis_partai' } | Select-Object ProcessId, CommandLine | Format-List"
```

Lalu hentikan dengan nomor dari daftar itu:

```
taskkill /F /PID <nomor>
```

Memeriksa jumlah engine yang menyala:

```
powershell -NoProfile -Command "(Get-Process stockfish -ErrorAction SilentlyContinue).Count"
```

## 6. Menarik partai sendiri dari Lichess & Chess.com

Lewat server, hanya partai **baru** sejak partai terakhir yang sudah ada:

```
curl -X POST http://127.0.0.1:8000/api/impor/akun -H "Content-Type: application/json" -d "{}"
```

Satu akun saja, misalnya:

```
curl -X POST http://127.0.0.1:8000/api/impor/akun -H "Content-Type: application/json" -d "{\"akun\":[\"Mikaysr\"]}"
```

Cara lama yang menyimpan PGN ke berkas dulu (untuk unduhan besar):

```
python ambil_partai.py --semua-kecepatan

python impor_pgn.py --semua
```

## 7. Menyusun perintah analisis sendiri

`python analisis_partai.py` + minimal satu pemilih + pembatas.

| Pemilih | Arti |
|---|---|
| `--pemain Tal` | partai pemain itu; potongan nama cukup, pakai tanda kutip kalau ada spasi |
| `--pemain Gukesh Erigaisi` | salah satu dari beberapa nama |
| `--min-elo 2700` | kedua pemain minimal segitu |
| `--sumber twic` | juga `pgnmentor`, `lichess-broadcast`, `lichess-user`, `chesscom` |
| `--repertoire` | hanya English, Catalan, Ragozin, Sicilian |
| `--eco A10-A39` | kode ECO, awalan (`A`), atau rentang; boleh beberapa |
| `--hasil kalah` | dari sisi sendiri: `kalah`, `menang`, `remis` |
| `--format blitz` | `bullet`, `blitz`, `rapid`, `klasik`, `tanpa` |
| `--gelar-lawan GM IM` | partai sendiri lawan pemain bergelar |
| `--lawan-min-elo 2201` | partai sendiri lawan rating minimal segitu |
| `--id 11670` | partai tertentu; nomornya dari alamat halaman partai |

| Pembatas dan opsi | Arti |
|---|---|
| `--terbaru 50` | ambil 50 partai terbaru dari hasil pemilihan |
| `--lihat-saja` | tampilkan daftar dan perkiraan waktu, engine tidak menyala |
| `--profil cepat` | depth 10, 30x lebih cepat, untuk memborong partai GM |
| `--profil normal` | lebih dalam, RAM 500 MB; bawaan `hemat` |
| `--ulang` | analisis ulang yang sudah pernah |
| `--tanpa-tunggu` | jangan menunggu impor selesai dulu |

Pola yang dianjurkan: jalankan dulu dengan `--lihat-saja`, baca jumlah partai
dan perkiraan menitnya, baru ulangi perintah yang sama tanpa `--lihat-saja`.

## 8. Pemeliharaan

```
python cadangkan_db.py

python cadangkan_db.py --inti

python perbarui.py

python impor_pgn.py --rapikan

python impor_pgn.py --jejak

python gabung_pemain.py --tanpa-fide

python indeks_posisi.py
```

Berturut-turut: cadangan database (disimpan 5 terakhir), pembaruan mingguan
TWIC dan broadcast, mengisi ulang kolom turunan dan tanda akun pribadi,
memeriksa jejak asal koleksi pgnmentor (lihat saja; tambah `--tulis` untuk
mengisi `sumber_url` dan mencatat `sumber_impor`),
menggabungkan baris pemain ganda (tanpa `--jalan` hanya menampilkan usulan),
dan membangun indeks posisi untuk partai yang belum terindeks.

## 9. Catatan mesin ini

- **Laptop tidak tidur saat dicolok listrik** (diubah 12 Sep 2026), supaya
  batch semalaman tidak terhenti. Layar tetap mati sendiri. Mengembalikan:
  `powercfg /change standby-timeout-ac 20`.
- **Saat memakai baterai laptop tetap tidur setelah 3 menit**, dan analisis
  ikut berhenti sampai dibangunkan. Untuk batch panjang, colok listrik.
- Log batch dan laporan ada di `data/laporan/`.
- Tiap batch menulis kemajuannya ke `data/laporan/.kemajuan-<pid>.json`;
  berkas milik proses yang sudah mati dibersihkan sendiri.
- Isi database per 13 Sep 2026: **266.805 partai**, 1.939 sudah dianalisis
  engine. Sumber: twic 164.836, pgnmentor 89.822, lichess-user 10.679,
  chesscom 1.089, lichess-broadcast 379.

## 10. Lupa

```
python analisis_partai.py --help
```
