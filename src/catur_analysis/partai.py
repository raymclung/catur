"""
Analisis satu partai penuh: setiap langkah dinilai, bukan cuma yang blunder.

Bedanya dengan pgn_to_puzzles.py di proyek puzzle:

    pgn_to_puzzles.py  -> cari posisi yang LAYAK JADI PUZZLE, sisanya dibuang
    modul ini          -> catat SEMUA langkah, supaya bisa dibuat laporan
                          partai lengkap (akurasi, grafik eval, turning point)

Loop analisisnya sengaja mengikuti pola yang sudah terbukti di
pgn_to_puzzles.py, termasuk trik memakai ulang hasil analisis posisi sesudah
langkah sebagai "sebelum" untuk langkah berikutnya. Trik itu memangkas jumlah
panggilan engine jadi separuh.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, asdict

import chess
import chess.engine
import chess.pgn

from .engine import NORMAL, ProfilResource, Stockfish

# Skor yang dipakai untuk mewakili mat. Sama dengan pgn_to_puzzles.py supaya
# angka dari kedua alat bisa dibandingkan.
SKOR_MAT = 100_000

# Batas atas evaluasi yang dipakai saat menghitung akurasi. Tanpa batas ini,
# posisi yang sudah menang telak (+3000) membuat satu langkah kurang tepat
# terlihat seolah kesalahan besar, padahal secara praktis tidak berpengaruh.
BATAS_EVAL_CP = 1000

# Batas atas centipawn loss satu langkah.
#
# Tanpa batas ini, satu langkah yang membiarkan mat menghasilkan loss 100.000
# dan langsung merusak rata-rata seluruh partai. Secara catur pun tidak ada
# bedanya: kehilangan 10 bidak dan kehilangan mat sama-sama "kalah total",
# jadi keduanya cukup dicatat sebagai loss maksimum.
BATAS_KEHILANGAN_CP = 1000

# Ambang klasifikasi = milik Lichess (lila modules/tree Advice.scala): turunnya
# "winning chances" (-1..1, cp dibatasi ±1000) dari sudut pemain yang jalan.
# Sampai 28 Sep 2026 di sini dipakai centipawn mentah (300/100/50), padahal
# dokumen menyebut "rumus Lichess": sepertiga "blunder" bukan blunder menurut
# Lichess (mis. +16 -> +7 saat sudah menang telak), sebaliknya 112 ribu
# "mistake" adalah blunder Lichess (audit 28 Sep 2026).
AMBANG_BLUNDER = 0.3
AMBANG_MISTAKE = 0.2
AMBANG_INACCURACY = 0.1
# Untuk urutan mat (Advice.scala MateCreated/MateLost): seberapa jauh posisi
# sudah kalah/menang (cp, sudut pemain) sebelum mat muncul/hilang.
MAT_RINGAN_CP = 999
MAT_SEDANG_CP = 700
# cp posisi awal standar yang dipakai Lichess untuk akurasi (Cp.initial).
CP_AWAL = 15

KLASIFIKASI_ID = {
    "blunder": "Blunder",
    "mistake": "Kesalahan",
    "inaccuracy": "Kurang akurat",
    "baik": "Baik",
    "terbaik": "Terbaik",
    "buku": "Teori pembukaan",
}


@dataclass
class LangkahAnalisis:
    """Hasil analisis satu langkah."""

    ply: int
    nomor_langkah: int
    warna: str                 # "putih" / "hitam"
    san: str
    uci: str
    fen_sebelum: str
    eval_sebelum_cp: int       # sudut pandang pemain yang jalan
    eval_sesudah_cp: int       # sudut pandang pemain yang jalan
    kehilangan_cp: int         # centipawn loss, minimum 0
    langkah_terbaik_san: str
    langkah_terbaik_uci: str
    variasi_terbaik: list[str]
    klasifikasi: str
    mat_sesudah: int | None = None

    @property
    def eval_putih_cp(self) -> int:
        """Evaluasi setelah langkah, selalu dari sudut pandang Putih."""
        return self.eval_sesudah_cp if self.warna == "putih" else -self.eval_sesudah_cp

    @property
    def adalah_kesalahan(self) -> bool:
        return self.klasifikasi in ("blunder", "mistake", "inaccuracy")


@dataclass
class RingkasanPemain:
    nama: str
    warna: str
    akurasi: float | None      # None = pihak ini belum melangkah (bukan 0%)
    rata_kehilangan_cp: float
    jumlah_langkah: int
    blunder: int = 0
    mistake: int = 0
    inaccuracy: int = 0


@dataclass
class LaporanPartai:
    """Laporan lengkap satu partai."""

    # Metadata langsung dari header PGN. Tidak dikarang: kalau tidak ada di
    # PGN, isinya string kosong.
    event: str
    site: str
    tanggal: str
    babak: str
    putih: str
    hitam: str
    hasil: str
    eco: str
    pembukaan: str
    elo_putih: str
    elo_hitam: str
    kontrol_waktu: str

    depth: int
    langkah: list[LangkahAnalisis] = field(default_factory=list)
    ringkasan_putih: RingkasanPemain | None = None
    ringkasan_hitam: RingkasanPemain | None = None
    turning_point_ply: int | None = None

    @property
    def turning_point(self) -> LangkahAnalisis | None:
        if self.turning_point_ply is None:
            return None
        for lang in self.langkah:
            if lang.ply == self.turning_point_ply:
                return lang
        return None

    def ke_dict(self) -> dict:
        data = asdict(self)
        # Tambahkan nilai turunan supaya frontend tidak perlu menghitung ulang.
        for i, lang in enumerate(self.langkah):
            data["langkah"][i]["eval_putih_cp"] = lang.eval_putih_cp
        return data


# ---------------------------------------------------------------------------
# Helper skor
# ---------------------------------------------------------------------------


def skor_cp(skor: chess.engine.PovScore, pov: chess.Color) -> int:
    """
    Ubah PovScore jadi centipawn dari sudut pandang `pov`.

    Mat diwakili angka besar supaya tetap bisa dibandingkan secara numerik.
    Pola ini diambil dari pgn_to_puzzles.py agar angkanya konsisten.
    """
    s = skor.pov(pov)
    if s.is_mate():
        m = s.mate()
        return SKOR_MAT if m and m > 0 else -SKOR_MAT
    return s.score(mate_score=SKOR_MAT) or 0


def peluang_menang(cp: int) -> float:
    """
    Ubah evaluasi centipawn jadi perkiraan peluang menang 0..1.

    Memakai rumus yang dipublikasikan Lichess (kode mereka terbuka).
    Nilainya perkiraan, bukan kebenaran mutlak — dua engine dengan kedalaman
    berbeda bisa memberi angka berbeda untuk posisi yang sama.
    """
    cp_terbatas = max(-BATAS_EVAL_CP, min(BATAS_EVAL_CP, cp))
    return 0.5 + 0.5 * (2 / (1 + math.exp(-0.00368208 * cp_terbatas)) - 1)


def akurasi_satu_langkah(cp_sebelum: int, cp_sesudah: int) -> float:
    """
    Akurasi satu langkah dalam persen, memakai rumus Lichess.

    Dihitung dari selisih PELUANG MENANG, bukan selisih centipawn. Alasannya:
    kehilangan 100 cp saat posisi seimbang jauh lebih fatal daripada saat sudah
    unggul 900 cp, dan rumus berbasis peluang menang menangkap itu.

    Persis lila `AccuracyPercent.fromWinPercents`: tidak turun = 100, dan +1
    "bonus ketidakpastian" (analisis engine tidak sempurna).
    """
    return _akurasi_dari_persen(peluang_menang(cp_sebelum) * 100,
                                peluang_menang(cp_sesudah) * 100)


def _akurasi_dari_persen(sebelum: float, sesudah: float) -> float:
    if sesudah >= sebelum:
        return 100.0
    nilai = 103.1668100711649 * math.exp(-0.04354415386753951 * (sebelum - sesudah)) \
        - 3.166924740191411 + 1
    return max(0.0, min(100.0, nilai))


def _adalah_mat(cp: int) -> bool:
    return abs(cp) >= SKOR_MAT


def klasifikasikan(eval_sebelum_cp: int, eval_sesudah_cp: int, adalah_terbaik: bool) -> str:
    """
    Klasifikasi satu langkah, aturan Lichess (lila Advice.scala). Kedua eval
    dari sudut pemain yang jalan; mat diwakili ±SKOR_MAT.

      * keduanya bukan mat: turunnya winning chances >= 0.3 blunder,
        >= 0.2 mistake, >= 0.1 inaccuracy;
      * MAT MUNCUL (tadinya cp, kini lawan punya mat): blunder, kecuali posisi
        memang sudah kalah (< -999 inaccuracy, < -700 mistake);
      * MAT HILANG (tadinya punya mat, kini tidak): blunder, kecuali tetap
        menang besar (> 999 inaccuracy, > 700 mistake);
      * mat ke mat lain (tertunda, dsb.) tidak dinilai.
    """
    if adalah_terbaik:
        return "terbaik"
    mat_sebelum, mat_sesudah = _adalah_mat(eval_sebelum_cp), _adalah_mat(eval_sesudah_cp)
    if not mat_sebelum and not mat_sesudah:
        wc = lambda cp: 2 * peluang_menang(cp) - 1          # noqa: E731 - winning chances -1..1
        turun = wc(eval_sebelum_cp) - wc(eval_sesudah_cp)
        if turun >= AMBANG_BLUNDER:
            return "blunder"
        if turun >= AMBANG_MISTAKE:
            return "mistake"
        if turun >= AMBANG_INACCURACY:
            return "inaccuracy"
        return "baik"
    if not mat_sebelum and eval_sesudah_cp <= -SKOR_MAT:      # mat muncul
        if eval_sebelum_cp < -MAT_RINGAN_CP:
            return "inaccuracy"
        return "mistake" if eval_sebelum_cp < -MAT_SEDANG_CP else "blunder"
    if eval_sebelum_cp >= SKOR_MAT and eval_sesudah_cp < SKOR_MAT:   # mat hilang
        if mat_sesudah:
            return "blunder"                                   # dari mat menang ke dimat
        if eval_sesudah_cp > MAT_RINGAN_CP:
            return "inaccuracy"
        return "mistake" if eval_sesudah_cp > MAT_SEDANG_CP else "blunder"
    return "baik"


def akurasi_partai(langkah: list[LangkahAnalisis], cp_awal_putih: int) -> dict[str, float | None]:
    """
    Akurasi per warna, persis lila `AccuracyPercent.gameAccuracy`.

    Bukan rata-rata biasa: tiap langkah ditimbang volatilitas posisinya
    (simpangan baku win% dalam jendela geser), lalu rata-rata tertimbang itu
    dirata-rata dengan rata-rata HARMONIK. Rata-rata biasa membuat partai
    dengan satu blunder tetap terlihat ~90% — median 4,6 poin di atas Lichess,
    45 sisi berselisih >= 20 poin (audit 28 Sep 2026).

    `cp_awal_putih`: eval posisi sebelum langkah pertama (Putih).
    """
    import statistics

    if not langkah:
        return {"putih": None, "hitam": None}
    wp = lambda cp: peluang_menang(cp) * 100                 # noqa: E731
    semua = [wp(cp_awal_putih)] + [wp(l.eval_putih_cp) for l in langkah]
    n = len(langkah)
    jendela = max(2, min(8, n // 10))
    if len(semua) <= jendela:
        geser = [semua]
    else:
        geser = [semua[i:i + jendela] for i in range(len(semua) - jendela + 1)]
    daftar_jendela = [semua[:jendela]] * (min(jendela, len(semua)) - 2) + geser
    bobot = [max(0.5, min(12.0, statistics.pstdev(j))) for j in daftar_jendela]

    per_warna: dict[str, list[tuple[float, float]]] = {"putih": [], "hitam": []}
    for i, l in enumerate(langkah):
        if l.klasifikasi == "buku":
            continue
        sebelum, sesudah = semua[i], semua[i + 1]
        if l.warna == "hitam":
            sebelum, sesudah = 100 - sebelum, 100 - sesudah
        per_warna[l.warna].append((_akurasi_dari_persen(sebelum, sesudah), bobot[i]))

    hasil: dict[str, float | None] = {}
    for warna, isi in per_warna.items():
        if not isi:
            hasil[warna] = None
            continue
        tertimbang = sum(a * b for a, b in isi) / sum(b for _, b in isi)
        harmonik = statistics.harmonic_mean([a for a, _ in isi])
        hasil[warna] = round((tertimbang + harmonik) / 2, 1)
    return hasil


# ---------------------------------------------------------------------------
# Analisis partai
# ---------------------------------------------------------------------------


def analisis_partai(
    game: chess.pgn.Game,
    sf: Stockfish,
    depth: int | None = None,
    lewati_langkah_awal: int = 0,
) -> LaporanPartai:
    """
    Analisis satu partai penuh.

    lewati_langkah_awal : jumlah ply pembukaan yang dilewati analisisnya.
                          Berguna kalau tidak mau membuang waktu engine di
                          langkah teori yang sudah pasti.
    """
    h = game.headers
    laporan = LaporanPartai(
        event=h.get("Event", ""),
        site=h.get("Site", ""),
        tanggal=h.get("Date", ""),
        babak=h.get("Round", ""),
        putih=h.get("White", ""),
        hitam=h.get("Black", ""),
        hasil=h.get("Result", ""),
        eco=h.get("ECO", ""),
        pembukaan=h.get("Opening", ""),
        elo_putih=h.get("WhiteElo", ""),
        elo_hitam=h.get("BlackElo", ""),
        kontrol_waktu=h.get("TimeControl", ""),
        depth=depth or sf.profil.depth,
    )

    board = game.board()
    hasil_sebelum: list[dict] | None = None

    for move in game.mainline_moves():
        ply = board.ply()
        pemain = board.turn
        fen_sebelum = board.fen()
        san = board.san(move)

        # Analisis posisi SEBELUM langkah. Kalau iterasi sebelumnya sudah
        # menganalisis posisi ini, hasilnya dipakai ulang.
        if hasil_sebelum is None:
            hasil_sebelum = sf.evaluasi(board, depth=depth, multipv=1)

        eval_sebelum = _cp_dari_hasil(hasil_sebelum[0], pemain)
        terbaik_pv = hasil_sebelum[0]["pv"]
        terbaik_san = terbaik_pv[0] if terbaik_pv else ""

        # Langkah terbaik dalam UCI, untuk dipakai frontend menggambar panah.
        terbaik_uci = ""
        if terbaik_san:
            try:
                terbaik_uci = board.parse_san(terbaik_san).uci()
            except ValueError:
                terbaik_uci = ""

        # Langkah paksa (hanya satu langkah sah) tidak bisa salah, berapa pun
        # angka engine-nya: efek horizon antara dua pencarian dulu memberinya
        # kehilangan sampai 1000 dan menjadikannya "sebab kalah" (audit 28 Sep
        # 2026: 470 dari 1.137 langkah paksa dalam sampel diberi kehilangan).
        paksa = board.legal_moves.count() == 1

        board.push(move)

        # HANYA posisi tanpa langkah sah yang tidak bisa dievaluasi engine.
        # `is_game_over()` juga benar untuk pengulangan lima kali / 75 langkah
        # / materi tidak cukup, padahal PGN-nya masih berlanjut: dulu posisi
        # itu diberi eval 0 buatan (410 titik di 325 partai, audit 28 Sep 2026).
        if board.is_checkmate() or board.is_stalemate():
            # Posisi akhir tidak perlu dianalisis engine.
            eval_sesudah = _eval_posisi_akhir(board, pemain)
            hasil_sesudah = None
            # Nilai 0 berarti mat SUDAH terjadi (bukan "mat dalam 0 langkah").
            # Tandanya tidak relevan karena partainya sudah selesai.
            mat_sesudah = 0 if board.is_checkmate() else None
        else:
            hasil_sesudah = sf.evaluasi(board, depth=depth, multipv=1)
            eval_sesudah = _cp_dari_hasil(hasil_sesudah[0], pemain)
            # evaluasi() sudah mengembalikan mate dari sudut pandang Putih,
            # dan disimpan apa adanya supaya semua tampilan konsisten.
            mat_sesudah = hasil_sesudah[0]["mate"]

        adalah_terbaik = (bool(terbaik_san) and terbaik_san == san) or paksa
        # Langkah yang SAMA dengan pilihan engine tidak kehilangan apa pun;
        # selisih eval-nya murni beda kedalaman antara dua pencarian (5.357
        # baris "terbaik" dulu berkehilangan >= 300, audit 28 Sep 2026).
        kehilangan = 0 if adalah_terbaik else min(max(0, eval_sebelum - eval_sesudah),
                                                  BATAS_KEHILANGAN_CP)

        if ply < lewati_langkah_awal:
            klasifikasi = "buku"
        else:
            klasifikasi = klasifikasikan(eval_sebelum, eval_sesudah, adalah_terbaik)

        laporan.langkah.append(
            LangkahAnalisis(
                ply=ply,
                nomor_langkah=ply // 2 + 1,
                warna="putih" if pemain == chess.WHITE else "hitam",
                san=san,
                uci=move.uci(),
                fen_sebelum=fen_sebelum,
                eval_sebelum_cp=eval_sebelum,
                eval_sesudah_cp=eval_sesudah,
                kehilangan_cp=kehilangan,
                langkah_terbaik_san=terbaik_san,
                langkah_terbaik_uci=terbaik_uci,
                variasi_terbaik=terbaik_pv[:6],
                klasifikasi=klasifikasi,
                mat_sesudah=mat_sesudah,
            )
        )

        # Hasil analisis posisi sesudah jadi "sebelum" untuk langkah berikutnya.
        hasil_sebelum = hasil_sesudah

    _isi_ringkasan(laporan, game)
    laporan.turning_point_ply = _cari_turning_point(laporan)
    return laporan


def analisis_dari_eval(game: chess.pgn.Game) -> LaporanPartai | None:
    """
    Susun laporan analisis dari komentar [%eval ...] di PGN, tanpa engine.

    Lichess menyertakan evaluasi di tiap langkah partai siarannya (terukur
    13 Sep 2026: 638 evaluasi untuk 6 partai satu ronde). Evaluasi di PGN
    tidak memuat langkah terbaik, jadi laporannya tanpa langkah terbaik.
    Lihat analisis_dari_daftar() untuk aturan selengkapnya.
    """
    daftar: list[dict | None] = []
    node = game
    while node.variations:
        node = node.variations[0]
        skor = node.eval()
        if skor is None:
            daftar.append(None)
            continue
        putih = skor.white()
        daftar.append({"mate": putih.mate()} if putih.is_mate() else {"eval": putih.score()})
    return analisis_dari_daftar(game, daftar)


def analisis_dari_daftar(game: chess.pgn.Game, daftar: list[dict | None]) -> LaporanPartai | None:
    """
    Susun laporan dari evaluasi per langkah yang sudah jadi, tanpa engine.

    `daftar[i]` untuk ply ke-i, dari SUDUT PUTIH, sesudah langkah itu:
      {"eval": cp} atau {"mate": n}, plus opsional "best" (UCI, langkah yang
      seharusnya dimainkan di ply itu) dan "variation" (SAN dipisah spasi).
    Itu bentuk yang dipakai Lichess, baik di [%eval] PGN siaran maupun di
    `analysis` ekspor partai (analisis server, yang memberi "best" di tiap
    kesalahan).

    Laporannya dibuat dengan rumus yang SAMA dengan analisis_partai() —
    kehilangan, klasifikasi, akurasi, titik balik — jadi halaman lain
    membacanya persis seperti hasil Stockfish laptop. Yang sengaja tidak
    dikarang:
      * langkah terbaik hanya diisi kalau sumbernya memberi "best";
        klasifikasi "terbaik" tidak pernah muncul;
      * langkah pertama tidak diberi kehilangan, karena evaluasi posisi awal
        tidak disertakan.

    Mengembalikan None kalau ada langkah tanpa evaluasi (kecuali langkah
    terakhir yang mengakhiri partai): laporan setengah jadi lebih menyesatkan
    daripada tidak ada laporan.
    """
    h = game.headers
    laporan = LaporanPartai(
        event=h.get("Event", ""), site=h.get("Site", ""), tanggal=h.get("Date", ""),
        babak=h.get("Round", ""), putih=h.get("White", ""), hitam=h.get("Black", ""),
        hasil=h.get("Result", ""), eco=h.get("ECO", ""), pembukaan=h.get("Opening", ""),
        elo_putih=h.get("WhiteElo", ""), elo_hitam=h.get("BlackElo", ""),
        kontrol_waktu=h.get("TimeControl", ""), depth=0,
    )

    board = game.board()
    eval_sebelumnya_putih: int | None = None     # cp dari sudut Putih, sesudah langkah lalu

    for i, move in enumerate(game.mainline_moves()):
        ply = board.ply()
        pemain = board.turn
        fen_sebelum = board.fen()
        san = board.san(move)
        entri = daftar[i] if i < len(daftar) else None

        terbaik_uci = terbaik_san = ""
        variasi: list[str] = []
        if entri and entri.get("best"):
            try:
                gerak = chess.Move.from_uci(entri["best"])
                if gerak in board.legal_moves:
                    # Lichess menulis rokade sebagai raja-makan-benteng
                    # (`e8h8`); python-chess menerimanya, tapi papan dan soal
                    # latihan membandingkan dengan `e8g8`, jadi O-O yang benar
                    # dinilai salah (259 baris, audit 28 Sep 2026). Lewat SAN
                    # jadi bentuk UCI standar.
                    terbaik_san = board.san(gerak)
                    terbaik_uci = board.parse_san(terbaik_san).uci()
                    variasi = (entri.get("variation") or terbaik_san).split()[:6]
            except ValueError:
                pass

        paksa = board.legal_moves.count() == 1
        board.push(move)

        # Mat di papan didahulukan dari angka di PGN: [%eval -5.56] sesudah
        # Rc1# pernah tersimpan sebagai "baik" tanpa mat, dan [%eval #0]
        # terbaca mate=0 -> dikira dikalahkan (audit 28 Sep 2026).
        if board.is_checkmate() or board.is_stalemate():
            eval_sesudah = _eval_posisi_akhir(board, pemain)
            mat_sesudah = 0 if board.is_checkmate() else None
            cp_putih_sesudah = eval_sesudah if pemain == chess.WHITE else -eval_sesudah
        elif entri and entri.get("mate") == 0:
            return None                        # mat 0 tanpa mat di papan: sumbernya rusak
        elif entri and ("eval" in entri or "mate" in entri):
            if entri.get("mate") is not None:
                mat_sesudah = int(entri["mate"])
                cp_putih_sesudah = SKOR_MAT if mat_sesudah > 0 else -SKOR_MAT
            else:
                mat_sesudah = None
                cp_putih_sesudah = int(entri["eval"])
            eval_sesudah = cp_putih_sesudah if pemain == chess.WHITE else -cp_putih_sesudah
        elif board.is_game_over():
            eval_sesudah = _eval_posisi_akhir(board, pemain)
            mat_sesudah = 0 if board.is_checkmate() else None
            cp_putih_sesudah = eval_sesudah if pemain == chess.WHITE else -eval_sesudah
        else:
            return None                        # ada langkah tanpa evaluasi

        if eval_sebelumnya_putih is None:
            eval_sebelum = eval_sesudah         # posisi awal tidak dievaluasi: tanpa kehilangan
        else:
            eval_sebelum = eval_sebelumnya_putih if pemain == chess.WHITE else -eval_sebelumnya_putih

        kehilangan = 0 if paksa else min(max(0, eval_sebelum - eval_sesudah),
                                         BATAS_KEHILANGAN_CP)
        laporan.langkah.append(
            LangkahAnalisis(
                ply=ply,
                nomor_langkah=ply // 2 + 1,
                warna="putih" if pemain == chess.WHITE else "hitam",
                san=san,
                uci=move.uci(),
                fen_sebelum=fen_sebelum,
                eval_sebelum_cp=eval_sebelum,
                eval_sesudah_cp=eval_sesudah,
                kehilangan_cp=kehilangan,
                langkah_terbaik_san=terbaik_san,
                langkah_terbaik_uci=terbaik_uci,
                variasi_terbaik=variasi,
                klasifikasi=klasifikasikan(eval_sebelum, eval_sesudah, paksa),
                mat_sesudah=mat_sesudah,
            )
        )
        eval_sebelumnya_putih = cp_putih_sesudah

    if not laporan.langkah:
        return None
    _isi_ringkasan(laporan, game)
    laporan.turning_point_ply = _cari_turning_point(laporan)
    return laporan


def _cp_dari_hasil(hasil: dict, pov: chess.Color) -> int:
    """Ambil centipawn dari hasil evaluasi() dan putar ke sudut pandang pemain."""
    if hasil["mate"] is not None:
        cp_putih = SKOR_MAT if hasil["mate"] > 0 else -SKOR_MAT
    else:
        cp_putih = hasil["cp"] if hasil["cp"] is not None else 0
    return cp_putih if pov == chess.WHITE else -cp_putih


def _eval_posisi_akhir(board: chess.Board, pemain: chess.Color) -> int:
    """Evaluasi posisi yang sudah selesai, tanpa memanggil engine."""
    if board.is_checkmate():
        # Yang jalan barusan memberi mat, jadi dia menang.
        return SKOR_MAT
    return 0  # remis dalam segala bentuknya


def cp_awal_putih(game: chess.pgn.Game, langkah: list[LangkahAnalisis]) -> int:
    """
    Eval posisi sebelum langkah pertama untuk akurasi. Posisi awal standar =
    Cp.initial seperti Lichess; partai dari FEN memakai eval sebelum langkah
    pertamanya (sudut Putih).
    """
    if game.board().fen() == chess.STARTING_FEN or not langkah:
        return CP_AWAL
    l = langkah[0]
    return l.eval_sebelum_cp if l.warna == "putih" else -l.eval_sebelum_cp


def _isi_ringkasan(laporan: LaporanPartai, game: chess.pgn.Game) -> None:
    akurasi = akurasi_partai(laporan.langkah, cp_awal_putih(game, laporan.langkah))
    laporan.ringkasan_putih = _ringkas(laporan, "putih", laporan.putih, akurasi["putih"])
    laporan.ringkasan_hitam = _ringkas(laporan, "hitam", laporan.hitam, akurasi["hitam"])


def _ringkas(laporan: LaporanPartai, warna: str, nama: str,
             akurasi: float | None = None) -> RingkasanPemain:
    langkah = [l for l in laporan.langkah if l.warna == warna and l.klasifikasi != "buku"]
    if not langkah:
        # Belum melangkah = tidak ada akurasi, BUKAN 0% (dulu 0.0 ikut
        # dirata-ratakan di ringkasan; 17 partai satu-ply, audit 28 Sep 2026).
        return RingkasanPemain(nama=nama, warna=warna, akurasi=None,
                               rata_kehilangan_cp=0.0, jumlah_langkah=0)

    return RingkasanPemain(
        nama=nama,
        warna=warna,
        akurasi=akurasi,
        rata_kehilangan_cp=round(sum(l.kehilangan_cp for l in langkah) / len(langkah), 1),
        jumlah_langkah=len(langkah),
        blunder=sum(1 for l in langkah if l.klasifikasi == "blunder"),
        mistake=sum(1 for l in langkah if l.klasifikasi == "mistake"),
        inaccuracy=sum(1 for l in langkah if l.klasifikasi == "inaccuracy"),
    )


def _cari_turning_point(laporan: LaporanPartai) -> int | None:
    """
    Langkah yang paling mengubah jalannya partai.

    Bukan sekadar centipawn loss terbesar, tapi langkah yang paling besar
    menggeser PELUANG MENANG — karena itu yang benar-benar menentukan hasil.
    """
    terbaik_ply = None
    pergeseran_terbesar = 0.0

    for lang in laporan.langkah:
        if lang.klasifikasi == "buku":
            continue
        sebelum = peluang_menang(lang.eval_sebelum_cp)
        sesudah = peluang_menang(lang.eval_sesudah_cp)
        pergeseran = sebelum - sesudah
        if pergeseran > pergeseran_terbesar:
            pergeseran_terbesar = pergeseran
            terbaik_ply = lang.ply

    # Kalau tidak ada langkah yang menggeser peluang menang lebih dari 15%,
    # partainya tidak punya turning point yang jelas. Lebih jujur bilang tidak
    # ada daripada menunjuk langkah acak.
    return terbaik_ply if pergeseran_terbesar >= 0.15 else None
