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

# Ambang klasifikasi, dalam centipawn loss (selisih antara langkah terbaik
# menurut engine dan langkah yang benar-benar dimainkan).
AMBANG_BLUNDER = 300
AMBANG_MISTAKE = 100
AMBANG_INACCURACY = 50

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
    akurasi: float
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
    """
    menang_sebelum = peluang_menang(cp_sebelum) * 100
    menang_sesudah = peluang_menang(cp_sesudah) * 100
    turun = max(0.0, menang_sebelum - menang_sesudah)
    nilai = 103.1668 * math.exp(-0.04354 * turun) - 3.1669
    return max(0.0, min(100.0, nilai))


def klasifikasikan(kehilangan_cp: int, adalah_terbaik: bool) -> str:
    if adalah_terbaik:
        return "terbaik"
    if kehilangan_cp >= AMBANG_BLUNDER:
        return "blunder"
    if kehilangan_cp >= AMBANG_MISTAKE:
        return "mistake"
    if kehilangan_cp >= AMBANG_INACCURACY:
        return "inaccuracy"
    return "baik"


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

        board.push(move)

        if board.is_game_over():
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

        kehilangan = min(max(0, eval_sebelum - eval_sesudah), BATAS_KEHILANGAN_CP)
        adalah_terbaik = bool(terbaik_san) and terbaik_san == san

        if ply < lewati_langkah_awal:
            klasifikasi = "buku"
        else:
            klasifikasi = klasifikasikan(kehilangan, adalah_terbaik)

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

    laporan.ringkasan_putih = _ringkas(laporan, "putih", laporan.putih)
    laporan.ringkasan_hitam = _ringkas(laporan, "hitam", laporan.hitam)
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
                    terbaik_uci, terbaik_san = gerak.uci(), board.san(gerak)
                    variasi = (entri.get("variation") or terbaik_san).split()[:6]
            except ValueError:
                pass

        board.push(move)

        if entri and ("eval" in entri or "mate" in entri):
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

        kehilangan = min(max(0, eval_sebelum - eval_sesudah), BATAS_KEHILANGAN_CP)
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
                klasifikasi=klasifikasikan(kehilangan, False),
                mat_sesudah=mat_sesudah,
            )
        )
        eval_sebelumnya_putih = cp_putih_sesudah

    if not laporan.langkah:
        return None
    laporan.ringkasan_putih = _ringkas(laporan, "putih", laporan.putih)
    laporan.ringkasan_hitam = _ringkas(laporan, "hitam", laporan.hitam)
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


def _ringkas(laporan: LaporanPartai, warna: str, nama: str) -> RingkasanPemain:
    langkah = [l for l in laporan.langkah if l.warna == warna and l.klasifikasi != "buku"]
    if not langkah:
        return RingkasanPemain(nama=nama, warna=warna, akurasi=0.0,
                               rata_kehilangan_cp=0.0, jumlah_langkah=0)

    nilai_akurasi = [
        akurasi_satu_langkah(l.eval_sebelum_cp, l.eval_sesudah_cp) for l in langkah
    ]
    return RingkasanPemain(
        nama=nama,
        warna=warna,
        akurasi=round(sum(nilai_akurasi) / len(nilai_akurasi), 1),
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
