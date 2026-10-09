"""
Rencana latihan minggu ini (A40).

Kelemahan Ray sudah terhitung di beberapa halaman (Ringkasan, Kekalahan,
Kelemahan, Repertoire), tapi tidak ada satu tempat yang menjawab "minggu ini
saya latih apa". Modul ini TIDAK menghitung ulang apa pun: ia membaca
ringkasan_pemain() dan catatan latihan, lalu memilih paling banyak tiga
kelemahan yang selisihnya paling besar — beserta angkanya, supaya bisa
dibantah.

Aturan yang dipakai memilih:
  * hanya kelompok dengan sampel cukup (MIN_PARTAI / MIN_LANGKAH);
  * yang dibandingkan selalu sesama kelompok sendiri (bullet vs blitz, fase vs
    fase), bukan terhadap patokan dari luar yang tidak ada datanya;
  * kalau selisihnya di bawah ambang, kelemahan itu TIDAK ditampilkan —
    lebih baik daftar pendek daripada saran yang dikarang.

Angka blunder di sini berasal dari analisis profil `cepat` (depth 10) untuk
sebagian besar partai, jadi disebut apa adanya di `catatan`.
"""

from __future__ import annotations

import sqlite3
from datetime import date, timedelta

from fastapi import APIRouter, HTTPException, Query

from .db import koneksi
from .latihan_catatan import MODE, tanggal_lokal
from .ringkasan import ringkasan_pemain

router = APIRouter(prefix="/api", tags=["rencana"])

MIN_PARTAI = 30        # kelompok partai (format, pembukaan) di bawah ini diabaikan
MIN_LANGKAH = 200      # kelompok langkah (fase) di bawah ini diabaikan
SELISIH_FASE = 0.5     # blunder per 100 langkah
SELISIH_FORMAT = 0.2   # blunder per partai
SELISIH_SKOR = 8.0     # persen, untuk pembukaan


def _per_100(b: dict) -> float | None:
    return round(100 * b["blunder"] / b["langkah"], 2) if b.get("langkah") else None


def _n(x) -> str:
    """Angka gaya Indonesia: desimal koma, ribuan titik. 1234.5 -> '1.234,5'."""
    if x is None:
        return "?"
    teks = f"{x:,.2f}".rstrip("0").rstrip(".") if isinstance(x, float) else f"{x:,}"
    # tukar peran titik dan koma lewat penanda sementara
    return teks.replace(",", "	").replace(".", ",").replace("	", ".")


def kelemahan(ringkas: dict) -> list[dict]:
    """Paling banyak tiga kelemahan, urut dari selisih terbesar."""
    calon = []

    # 1. Fase permainan: blunder per 100 langkah, dibandingkan antar fase.
    fase = [f for f in ringkas.get("per_fase", []) if (f.get("langkah") or 0) >= MIN_LANGKAH]
    if len(fase) >= 2:
        urut = sorted(fase, key=lambda f: -(_per_100(f) or 0))
        buruk, baik = urut[0], urut[-1]
        selisih = (_per_100(buruk) or 0) - (_per_100(baik) or 0)
        if selisih >= SELISIH_FASE:
            calon.append({
                "jenis": "fase",
                "judul": f"Kesalahan besar paling padat di fase {buruk['fase']}",
                "angka": f"{_n(_per_100(buruk))} blunder per 100 langkah, "
                         f"dibanding {_n(_per_100(baik))} di fase {baik['fase']}",
                "selisih": round(selisih, 2),
                "saran": "Kerjakan soal Latihan dari kesalahan partai sendiri; "
                         "kartu Perkembangan memilah hasilnya per babak.",
                "tautan": [{"teks": "Latihan kesalahan", "url": "/static/latihan.html"}],
            })

    # 2. Format waktu: blunder per partai, dibandingkan antar format.
    fmt = [f for f in ringkas.get("per_format", []) if f["partai"] >= MIN_PARTAI
           and f.get("blunder_per_partai") is not None]
    if len(fmt) >= 2:
        urut = sorted(fmt, key=lambda f: -f["blunder_per_partai"])
        buruk, baik = urut[0], urut[-1]
        selisih = buruk["blunder_per_partai"] - baik["blunder_per_partai"]
        if selisih >= SELISIH_FORMAT:
            calon.append({
                "jenis": "format",
                "judul": f"Paling banyak kesalahan besar di {buruk['nama']}",
                "angka": f"{_n(buruk['blunder_per_partai'])} blunder per partai di {buruk['nama']} "
                         f"({_n(buruk['partai'])} partai), dibanding {_n(baik['blunder_per_partai'])} "
                         f"di {baik['nama']} ({_n(baik['partai'])} partai)",
                "selisih": round(selisih, 2),
                "saran": f"Soal dari partai {buruk['nama']} saja, supaya latihannya "
                         "sesuai kecepatan yang paling sering kamu mainkan.",
                "tautan": [{"teks": f"Latihan {buruk['nama']}",
                            "url": f"/static/latihan.html?format={buruk['nama']}"}],
            })

    # 3. Pembukaan: skor terendah di antara pembukaan yang sering dimainkan.
    pemb = [p for p in ringkas.get("per_pembukaan", []) if p["partai"] >= MIN_PARTAI
            and p.get("skor_persen") is not None]
    if len(pemb) >= 2:
        urut = sorted(pemb, key=lambda p: p["skor_persen"])
        buruk = urut[0]
        rata = sum(p["skor_persen"] for p in pemb) / len(pemb)
        selisih = rata - buruk["skor_persen"]
        if selisih >= SELISIH_SKOR:
            calon.append({
                "jenis": "pembukaan",
                "judul": f"Skor paling rendah di {buruk['nama']}",
                "angka": f"{_n(buruk['skor_persen'])}% dari {_n(buruk['partai'])} partai, "
                         f"dibanding rata-rata {_n(round(rata, 1))}% di pembukaan lain",
                "selisih": round(selisih, 1),
                "saran": "Telusuri posisi awalnya di Jelajah dan bandingkan dengan pilihan GM.",
                "tautan": [{"teks": "Jelajah pembukaan", "url": "/jelajah"},
                           {"teks": "Repertoire", "url": "/static/repertoire.html"}],
            })

    calon.sort(key=lambda c: -c["selisih"])
    return calon[:3]


def _minggu(hari: date) -> date:
    return hari - timedelta(days=hari.weekday())


def kemajuan_latihan(conn: sqlite3.Connection, hari_ini: date | None = None) -> dict:
    """Berapa soal dijawab minggu ini dan minggu lalu, per mode."""
    hari_ini = hari_ini or date.today()
    senin_ini = _minggu(hari_ini)
    senin_lalu = senin_ini - timedelta(weeks=1)
    kosong = lambda: {"dijawab": 0, "benar": 0}
    hasil = {m: {"minggu_ini": kosong(), "minggu_lalu": kosong()} for m in MODE}
    for mode, h, w in conn.execute(
            "SELECT mode, hasil, dijawab_pada FROM latihan_jawaban WHERE hasil != 'pilihan'"):
        if mode not in hasil:
            continue
        senin = _minggu(tanggal_lokal(w))
        bagian = ("minggu_ini" if senin == senin_ini else
                  "minggu_lalu" if senin == senin_lalu else None)
        if bagian:
            hasil[mode][bagian]["dijawab"] += 1
            hasil[mode][bagian]["benar"] += (h == "benar")
    return hasil


def rencana_latihan(conn: sqlite3.Connection, pemain_ids: list[int],
                    hari_ini: date | None = None) -> dict:
    hari_ini = hari_ini or date.today()
    ringkas = ringkasan_pemain(conn, pemain_ids)
    cakupan = ringkas.get("cakupan", {})
    daftar = kelemahan(ringkas)

    catatan = ["Angka kesalahan berasal dari analisis engine; sebagian besar partai "
               "dianalisis profil cepat (depth 10), jadi anggap sebagai petunjuk, bukan vonis."]
    if cakupan.get("condong_ke_kalah"):
        catatan.append("Partai yang dianalisis condong ke kekalahan, jadi pola kesalahan "
                       "boleh dibaca tapi angka skornya tidak.")
    if not daftar:
        catatan.append("Belum ada kelemahan yang selisihnya cukup besar untuk disebut — "
                       "entah datanya masih sedikit, atau memang merata.")

    return {
        "minggu_mulai": _minggu(hari_ini).isoformat(),
        "kelemahan": daftar,
        "latihan": kemajuan_latihan(conn, hari_ini),
        "cakupan": {"partai_dianalisis": cakupan.get("partai_dianalisis"),
                    "partai_total": cakupan.get("partai_total"),
                    "persen": cakupan.get("persen_dianalisis")},
        "catatan": catatan,
    }


@router.get("/rencana", summary="Rencana latihan minggu ini: kelemahan terbesar + kemajuan latihan")
def api_rencana(pemain_id: list[int] = Query(..., description="id akun sendiri, boleh beberapa")):
    if len(pemain_id) > 20:
        raise HTTPException(status_code=422, detail="Maksimal 20 pemain sekaligus")
    conn = koneksi()
    try:
        return rencana_latihan(conn, pemain_id)
    finally:
        conn.close()
