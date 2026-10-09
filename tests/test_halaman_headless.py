"""
Uji halaman web sungguhan lewat browser tanpa layar (Edge headless).

Audit 21 Sep 2026: 9.500 baris JavaScript di `web/` praktis tanpa tes; hanya
dua berkas yang punya kait uji, sisanya diperiksa dengan mata. Node tidak ada
di mesin ini, tapi polanya sudah terbukti sejak A2: jalankan server sendiri,
suruh Edge membuka halamannya, lalu baca DOM yang sudah diisi JavaScript.

Yang diperiksa di sini bukan tampilan, melainkan bahwa JS-nya benar-benar
JALAN dan mengisi halaman dari API: tidak ada `Uncaught` di konsol, dan
penanda isi yang hanya muncul kalau `fetch` berhasil sudah terisi.

Dilewati kecuali diminta, karena butuh Edge dan ±1 menit:

    set CATUR_UJI_HEADLESS=1 && python -m unittest tests.test_halaman_headless

Database yang dipakai adalah database kecil buatan tes (bukan data Ray), jadi
hasilnya sama di komputer mana pun.
"""

import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

AKAR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(AKAR))

# Peramban tanpa layar. Edge ada di Windows, Chrome/Chromium di CI Linux.
# CATUR_BROWSER menimpa semuanya kalau peramban dipasang di tempat lain.
BROWSER_TETAP = [
    Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
    Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
    Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
    Path(r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"),
]
BROWSER_PATH = ("msedge", "google-chrome", "google-chrome-stable", "chromium",
                "chromium-browser", "chrome")

# Halaman -> penanda yang hanya ada kalau JavaScript-nya berhasil mengisi DOM.
# Dipilih yang berasal dari API, bukan teks yang sudah ada di HTML.
HALAMAN = (
    # 'slot' dibuang 25 Sep 2026: kata itu ada di mesin.html statis, jadi tidak
    # membuktikan apa pun. Yang tersisa ditulis JS ke #jam.
    ("/static/mesin.html", ("id=\"jam\">diperbarui",)),
    # 'Lichess' dan 'Tarik' KEDUANYA ada di impor.html statis, jadi halaman ini
    # sebelumnya lolos walau JavaScript-nya mati total. Diganti nama akun yang
    # dirender dari /api/impor/akun (daftarnya konstanta di ambil_partai.py,
    # jadi stabil berapa pun isi database uji).
    ("/static/impor.html", ("Sakamoto_PMan",)),
    # halaman Repertoire mengisi <select id="akun"> dari /api/pemain?pribadi=1,
    # dan tabelnya berhenti di "memuat..." hanya kalau JS-nya mati
    ("/static/repertoire.html", ("McLung_07", "Belum ada", "tidak ada")),
    ("/jelajah", ("e4", "d4")),
    # Dua halaman terbesar JS-nya (papan.js 1.194 baris, latihan.js 1.007).
    # Ditambahkan 25 Sep 2026 SEBELUM berkasnya dipecah: pemecahan tanpa jaring
    # pengaman adalah cara halaman tersibuk rusak diam-diam.
    # partai.html mengisi nama pemain dan FEN dari /api/partai/{id}; teksnya
    # tidak ada di HTML, jadi kemunculannya membuktikan JS-nya jalan.
    # 'rnbqkbnr' adalah FEN yang ditulis papan.js ke #fen, dan 'square-55d63'
    # kelas kotak yang baru ada setelah chessboard.js merender papannya.
    ("/partai/1", ("rnbqkbnr", "square-55d63")),
    # latihan.html tanpa data analisis berhenti di pesan yang ditulis JS ke
    # #galat. Penanda pertama sempat dipilih kata "soal" — dan itu SALAH:
    # kata itu ada 6 kali di HTML statis, jadi tesnya lolos walau JS mati
    # total. test_penanda_tidak_ada_di_html_statis sekarang menjaganya.
    ("/static/latihan.html", ("Belum ada partai akun ini yang dianalisis",
                              "square-55d63")),
    # Sepuluh halaman berikut ditambahkan 27 Sep 2026 (audit: hanya 8 dari 17
    # halaman yang diuji). Penandanya diambil dari DOM sungguhan di Chrome
    # headless dengan database uji ini, dikurangi teks yang sudah ada di HTML
    # statisnya — bukan ditebak dari membaca kode.
    ("/", ("6 pemain",)),                        # daftar.js, dari /api/statistik
    ("/static/beranda.html", ("1 dianalisis",)),
    ("/static/kekalahan.html", ("Tidak ada partai kalah untuk saringan ini",)),
    ("/static/kelemahan.html", ("Belum ada partai teranalisis untuk saringan ini",)),
    ("/static/kemajuan.html", ("2026.01.03",)),  # rentang tanggal partai akun
    ("/pemain/1", ("Carlsen, Magnus",)),
    ("/ringkasan", ("dari 2 (50%)",)),
    # Kalimat keadaan Ollama berbeda di tiap komputer (di CI tidak ada Ollama),
    # tapi ketiganya ditulis tanya.js ke tempat yang sama setelah /api/tanya/status.
    ("/tanya", ("siap di komputer ini", "sudah di memori", "belum termuat", "Ollama tidak berjalan", "Ollama berjalan tapi model")),
    ("/static/tersimpan.html", ("Belum ada yang disimpan",)),
    # Halaman pembantu tidak memanggil API sebelum Mulai; ?uji_mulai=1 tanpa
    # kode berhenti di pemeriksaan kode — cukup untuk membuktikan JS-nya
    # termuat dan jalan, tanpa mengunduh engine 7 MB.
    ("/static/pembantu.html?uji_mulai=1", ("Kode harus 6 angka",)),
)


def berkas_html(jalur: str) -> str:
    """Nama berkas di web/ untuk sebuah jalur halaman (tanpa ?query)."""
    jalur = jalur.split("?")[0]
    if jalur.endswith(".html"):
        return jalur.split("/")[-1]
    return {"/": "index.html"}.get(jalur, jalur.strip("/").split("/")[0] + ".html")


def port_bebas() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def edge() -> Path | None:
    """Peramban yang dipakai, atau None kalau tidak ada satu pun."""
    import shutil
    pilihan = os.environ.get("CATUR_BROWSER")
    if pilihan:
        p = Path(pilihan)
        return p if p.is_file() else None
    for p in BROWSER_TETAP:
        if p.is_file():
            return p
    for nama in BROWSER_PATH:
        jalur = shutil.which(nama)
        if jalur:
            return Path(jalur)
    return None


def buat_db(folder: Path) -> Path:
    """Database kecil: beberapa partai GM + satu partai akun sendiri, terindeks."""
    from app.db import buat_skema, koneksi
    from app.impor import impor_teks, tandai_akun_pribadi
    from app.posisi import indeks_partai
    path = folder / "uji-halaman.db"
    conn = koneksi(path)
    buat_skema(conn)
    langkah = ["1. c4 e5 2. Nc3 Nf6 3. g3 d5 4. cxd5 Nxd5 5. Bg2 Nb6 6. d3 Be7 7. Nf3 O-O",
               "1. d4 Nf6 2. c4 e6 3. g3 d5 4. Bg2 Be7 5. Nf3 O-O 6. O-O dxc4 7. Qc2 a6",
               "1. e4 c5 2. Nf3 d6 3. d4 cxd4 4. Nxd4 Nf6 5. Nc3 a6 6. Be3 e5 7. Nb3 Be7"]
    for i, (putih, hitam, sumber) in enumerate([
            ("Carlsen, Magnus", "Caruana, Fabiano", "twic"),
            ("Nakamura, Hikaru", "Giri, Anish", "twic"),
            ("McLung_07", "lawan", "lichess-user")]):
        tag = ['[Event "uji"]', f'[Date "2026.01.0{i + 1}"]', f'[White "{putih}"]',
               f'[Black "{hitam}"]', '[Result "1-0"]', '[WhiteElo "2700"]', '[BlackElo "2700"]']
        impor_teks(conn, "\n".join(tag) + "\n\n" + langkah[i] + " 1-0\n", sumber)
    tandai_akun_pribadi(conn, ["McLung_07"])

    # Satu partai dengan jam (%clk) DAN hasil analisis, supaya kedua grafik
    # halaman partai benar-benar tergambar saat diuji. Tanpa ini, blok grafik
    # di web/grafik-partai.js keluar lebih awal (jam < 4 titik, analisis
    # kosong) dan tesnya lolos tanpa pernah menjalankannya — rasa aman palsu
    # yang persis sama dengan penanda yang ada di HTML statis.
    _partai_berjam_dan_teranalisis(conn)

    for (pid, pgn) in conn.execute("SELECT id, pgn FROM partai").fetchall():
        indeks_partai(conn, pid, pgn)
    conn.commit()
    conn.close()
    return path


def _partai_berjam_dan_teranalisis(conn) -> None:
    """Partai ke-4: PGN ber-%clk, plus baris analisis dan enam ply-nya."""
    from app.db import sekarang

    # (san, uci, warna, eval_sebelum, eval_sesudah, eval_putih, kehilangan, klasifikasi)
    langkah = [("e4", "e2e4", "putih", 20, 15, 15, 5, "inaccuracy"),
               ("e5", "e7e5", "hitam", -15, -10, 10, 0, "baik"),
               ("Nf3", "g1f3", "putih", 10, 5, 5, 5, "baik"),
               ("Nc6", "b8c6", "hitam", -5, -40, 40, 35, "mistake"),
               ("Bb5", "f1b5", "putih", 40, 250, 250, 0, "baik"),
               ("a6", "a7a6", "hitam", -250, -600, 600, 350, "blunder")]
    jam = ["0:03:00", "0:02:58", "0:02:55", "0:02:50", "0:02:41", "0:02:20"]
    potongan = []
    for i, l in enumerate(langkah):
        awalan = "%d. " % (i // 2 + 1) if i % 2 == 0 else ""
        potongan.append(awalan + l[0] + " { [%clk " + jam[i] + "] }")
    tag = ['[Event "uji jam"]', '[Date "2026.02.01"]', '[White "McLung_07"]',
           '[Black "lawan"]', '[Result "1-0"]', '[WhiteElo "2400"]',
           '[BlackElo "2400"]', '[TimeControl "180+0"]']
    pgn = "\n".join(tag) + "\n\n" + " ".join(potongan) + " 1-0\n"

    conn.execute(
        "INSERT INTO partai(putih_id, hitam_id, tanggal, hasil, pgn, sumber, "
        "kontrol_waktu, jumlah_langkah, hash_unik, diimpor_pada) "
        "SELECT p1.id, p2.id, '2026.02.01', '1-0', ?, 'lichess-user', '180+0', 3, "
        "       'hash-uji-jam', ? FROM pemain p1, pemain p2 "
        "WHERE p1.nama = 'McLung_07' AND p2.nama = 'lawan'",
        (pgn, sekarang()))
    pid = conn.execute(
        "SELECT id FROM partai WHERE hash_unik = 'hash-uji-jam'").fetchone()[0]
    conn.execute(
        "INSERT INTO analisis(partai_id, engine, depth, profil, akurasi_putih, "
        "akurasi_hitam, blunder_hitam, mistake_hitam, inaccuracy_putih, "
        "turning_point_ply, dianalisis_pada) "
        "VALUES (?, 'Stockfish 18', 12, 'cepat', 92.5, 71.0, 1, 1, 1, 5, ?)",
        (pid, sekarang()))
    conn.executemany(
        "INSERT INTO analisis_langkah(partai_id, ply, nomor_langkah, warna, san, uci, "
        "eval_sebelum_cp, eval_sesudah_cp, eval_putih_cp, kehilangan_cp, klasifikasi) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        [(pid, i, i // 2 + 1, l[2], l[0], l[1], l[3], l[4], l[5], l[6], l[7])
         for i, l in enumerate(langkah)])
    conn.commit()


def diminta() -> bool:
    """
    Jalan kalau diminta, ATAU kalau ini CI.

    Audit 25 Sep 2026: tes ini tidak pernah benar-benar dieksekusi — satu-satunya
    pemicunya variabel yang tidak pernah diset siapa pun. Di CI justru tempat
    yang tepat (yang lambat bukan laptop Ray), jadi `CI=true` bawaan GitHub
    Actions ikut menyalakannya. `CATUR_UJI_HEADLESS=0` tetap mematikannya.
    """
    pilihan = os.environ.get("CATUR_UJI_HEADLESS")
    if pilihan is not None:
        return pilihan == "1"
    return os.environ.get("CI", "").lower() in ("1", "true")


@unittest.skipUnless(diminta(),
                     "set CATUR_UJI_HEADLESS=1 untuk menjalankan (butuh peramban, ±1 menit)")
@unittest.skipIf(edge() is None, "tidak ada Edge/Chrome/Chromium")
class HalamanDiBrowser(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        folder = Path(cls.tmp.name)
        cls.port = port_bebas()
        lingkungan = {**os.environ, "CATUR_DB": str(buat_db(folder))}
        cls.log = (folder / "server.log").open("w", encoding="utf-8")
        cls.server = subprocess.Popen(
            [sys.executable, "-X", "utf8", "-m", "uvicorn", "app.main:aplikasi",
             "--host", "127.0.0.1", "--port", str(cls.port)],
            cwd=str(AKAR), env=lingkungan, stdout=cls.log, stderr=subprocess.STDOUT)
        cls.tunggu_server()

    @classmethod
    def tunggu_server(cls, batas: float = 45.0) -> None:
        habis = time.time() + batas
        while time.time() < habis:
            try:
                with socket.create_connection(("127.0.0.1", cls.port), timeout=1):
                    return
            except OSError:
                time.sleep(0.5)
        raise RuntimeError("server uji tidak menyala")

    @classmethod
    def tearDownClass(cls):
        cls.server.terminate()
        try:
            cls.server.wait(timeout=15)
        except subprocess.TimeoutExpired:
            cls.server.kill()
            cls.server.wait(timeout=15)
        cls.log.close()
        cls.tmp.cleanup()

    def buka(self, jalur: str, jeda_ms: int = 20000) -> tuple[str, list[str]]:
        """(DOM sesudah JS jalan, daftar pesan Uncaught di konsol)."""
        with tempfile.TemporaryDirectory() as profil:
            hasil = subprocess.run(
                [str(edge()), "--headless=new", "--disable-gpu", f"--user-data-dir={profil}",
                 "--enable-logging=stderr", "--v=0", f"--virtual-time-budget={jeda_ms}",
                 "--dump-dom", f"http://127.0.0.1:{self.port}{jalur}"],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180)
        galat = [b for b in (hasil.stderr or "").splitlines() if "Uncaught" in b]
        # Peramban yang tidak mengembalikan apa pun adalah masalah PERAMBAN,
        # bukan halaman. 25 Sep 2026 Edge 153.0.4234.48 di mesin ini menjawab
        # kode 0 dengan stdout kosong bahkan untuk about:blank, dan tiap tes
        # gagal dengan "halaman tidak terisi" — tuduhan yang salah alamat dan
        # makan waktu untuk ditelusuri. CATUR_BROWSER= menunjuk peramban lain
        # (Chrome di mesin ini bekerja normal).
        self.assertTrue(hasil.stdout.strip(),
                        f"{edge()} tidak mengembalikan DOM apa pun untuk {jalur} "
                        f"(kode {hasil.returncode}). Ini kegagalan peramban, bukan "
                        "halaman; coba CATUR_BROWSER=<jalur peramban lain>.")
        return hasil.stdout, galat

    def test_halaman_utama_terisi_tanpa_galat_javascript(self):
        for jalur, penanda in HALAMAN:
            with self.subTest(jalur=jalur):
                dom, galat = self.buka(jalur)
                self.assertEqual(galat, [], f"{jalur}: ada galat JavaScript")
                ketemu = [p for p in penanda if p in dom]
                self.assertTrue(ketemu, f"{jalur}: halaman tidak terisi (tidak ada {penanda})")

    def test_papan_analisis_menerima_langkah_lewat_kait_uji(self):
        """?uji_klik= memainkan langkah tanpa mouse; SAN-nya harus muncul di daftar langkah."""
        dom, galat = self.buka("/static/analisis.html?uji_klik=e2,e4,e7,e5")
        self.assertEqual(galat, [])
        self.assertIn("e4", dom)
        self.assertIn("e5", dom)

    def test_grafik_partai_tergambar(self):
        """
        Kedua grafik halaman partai benar-benar digambar — bukan salah satu.

        Penjaga untuk web/grafik-partai.js, yang dipisah dari papan.js 25 Sep
        2026. Partai uji ke-4 sengaja punya %clk DAN hasil analisis: tanpa
        keduanya kode grafik keluar lebih awal (jam butuh minimal 4 titik,
        evaluasi butuh baris analisis) dan tesnya lolos tanpa pernah
        menjalankannya.

        assertIn satu per satu, bukan "salah satu penanda ada": grafik jam yang
        mati tidak boleh tertutupi oleh grafik evaluasi yang hidup.
        """
        dom, galat = self.buka("/partai/4")
        self.assertEqual(galat, [], "ada galat JavaScript di halaman partai")
        self.assertIn('id="penanda-jam"', dom, "grafik jam tidak tergambar")
        self.assertIn('id="penanda-grafik"', dom, "grafik evaluasi tidak tergambar")
        # Keterangan jam dihitung dari %clk, jadi isinya membuktikan bacaJam()
        # benar-benar mengurai PGN-nya, bukan cuma menggambar kotak kosong.
        self.assertIn("akhir 2:20", dom)

    def test_semua_bagian_halaman_partai_terisi(self):
        """
        Jaring pengaman F4 (27 Sep 2026), dipasang SEBELUM papan.js dipecah
        lagi dan dibuktikan hijau pada kode lama: tiap bagian yang dipindah ke
        modul sendiri punya penanda sendiri, supaya modul yang gagal termuat
        tidak tertutupi bagian lain yang hidup.
        """
        kasus = {
            # partai TWIC tanpa analisis: kotak "belum", info, praktik GM, penjelajah
            "/partai/1": ("Belum dianalisis", "Analisa partai ini dengan engine",
                          "laga-hasil", "Carlsen, Magnus",
                          "Langkah baru di luar database GM", "daftar →"),
            # partai teranalisis: ringkasan, momen kritis, tanda di daftar langkah,
            # komentar posisi awal, info kontrol waktu, keluar dari praktik GM
            "/partai/4": ("Analisis Stockfish 18 · depth 12", "92,5%", "Titik balik",
                          "Momen kritis", "Analisa ulang lebih teliti",
                          'class="tanda">??', "Posisi awal. Tekan", "Kontrol waktu",
                          "Keluar dari praktik GM"),
            # variasi dari papan partai lewat kait uji klik
            "/partai/4?uji_klik=d2,d4": ("Variasi ",),
        }
        for jalur, penanda in kasus.items():
            dom, galat = self.buka(jalur)
            self.assertEqual(galat, [], f"{jalur}: ada galat JavaScript")
            for p in penanda:
                with self.subTest(jalur=jalur, penanda=p):
                    self.assertIn(p, dom, f"{jalur}: {p!r} tidak muncul")

    def test_penanda_tidak_ada_di_html_statis(self):
        """
        Penanda hanya membuktikan sesuatu kalau ia BARU ada setelah JS jalan.

        Ditemukan 25 Sep 2026: penanda "soal" untuk halaman Latihan ternyata
        muncul enam kali di HTML statis (judul tombol dan label saringan), jadi
        tesnya lolos walau JavaScript-nya mati total. Tes ini membaca berkas
        HTML-nya langsung dan menolak penanda yang sudah ada di sana.
        """
        from app.main import FOLDER_WEB
        for jalur, penanda in HALAMAN:
            nama = berkas_html(jalur)
            berkas = FOLDER_WEB / nama
            # Dulu `continue` diam-diam: "/" dipetakan ke ".html" yang tidak
            # ada, jadi penandanya tidak pernah diperiksa. Kini wajib ketemu.
            self.assertTrue(berkas.is_file(), f"{jalur}: berkas {nama} tidak ada")
            statis = berkas.read_text(encoding="utf-8")
            for p in penanda:
                with self.subTest(jalur=jalur, penanda=p):
                    # assertTrue, bukan assertNotIn: yang terakhir mencetak
                    # seluruh isi berkas HTML ke keluaran tes.
                    self.assertTrue(p not in statis,
                                    f"{jalur}: penanda {p!r} sudah ada di "
                                    f"{nama}, jadi tidak membuktikan JS jalan")

    def test_halaman_yang_tidak_ada_menjawab_404(self):
        import urllib.error
        import urllib.request
        with self.assertRaises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(f"http://127.0.0.1:{self.port}/static/tidak-ada.html", timeout=30)
        self.assertEqual(e.exception.code, 404)


if __name__ == "__main__":
    unittest.main()
