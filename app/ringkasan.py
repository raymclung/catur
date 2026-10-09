"""
Ringkasan lintas partai untuk satu (atau beberapa akun) pemain.

Menjawab pertanyaan yang tidak bisa dijawab dari satu partai:
  - di fase mana (pembukaan / tengah / akhir) poin paling banyak hilang
  - apakah kesalahan muncul lebih awal saat lawan jauh lebih kuat
  - di pembukaan mana hasilnya paling buruk

Semua angka dihitung HANYA dari langkah pemain itu sendiri, di partai yang
sudah dianalisis. Jumlah partai (n) selalu ikut dikembalikan supaya
kesimpulan dari sampel kecil tidak terlihat lebih pasti dari kenyataannya.
"""

from __future__ import annotations

import sqlite3
from collections import defaultdict

# Batas fase dalam ply. Ini pembagian kasar berbasis nomor langkah, bukan
# berbasis materi — cukup untuk melihat pola, tapi jangan dibaca terlalu
# harfiah. Pembukaan = 12 langkah pertama, akhir = setelah langkah 40.
FASE = (("pembukaan", 0, 24), ("tengah", 24, 80), ("akhir", 80, 10 ** 6))

# Kelompok selisih rating lawan dikurangi rating sendiri.
KELOMPOK_SELISIH = (
    ("lawan jauh lebih lemah (< -200)", -10 ** 6, -200),
    ("lawan lebih lemah (-200 .. -1)", -200, 0),
    ("seimbang (0 .. 199)", 0, 200),
    ("lawan lebih kuat (200 .. 399)", 200, 400),
    ("lawan jauh lebih kuat (>= 400)", 400, 10 ** 6),
)

# Sampel di bawah ini ditandai supaya pembaca tahu angkanya belum bisa
# dipercaya.
N_MINIMAL = 10


def _dasar(pemain_ids: list[int], format_: str | None = None) -> tuple[str, list]:
    tanda = ",".join("?" * len(pemain_ids))
    sql = f"""
        FROM analisis_langkah al
        JOIN partai g   ON g.id = al.partai_id
        JOIN analisis a ON a.partai_id = g.id
        WHERE ((g.putih_id IN ({tanda}) AND al.warna = 'putih')
            OR (g.hitam_id IN ({tanda}) AND al.warna = 'hitam'))
    """
    params = list(pemain_ids) + list(pemain_ids)
    # Ringkasan per format (permintaan #8): bullet, blitz, rapid dipisah supaya
    # kesalahan tekanan waktu tidak tercampur dengan kesalahan pemahaman.
    if format_:
        sql += " AND g.format = ?"
        params.append(format_)
    return sql, params


def _kelompok(nilai, tabel):
    for nama, lo, hi in tabel:
        if lo <= nilai < hi:
            return nama
    return None


def _nama_hasil(hasil: str | None, warna: str) -> str:
    """menang / kalah / remis dari sudut pemain sendiri."""
    if hasil == "1/2-1/2":
        return "remis"
    if hasil not in ("1-0", "0-1"):
        return "belum selesai"
    return "menang" if (hasil == "1-0") == (warna == "putih") else "kalah"


def tertimbang(per_hasil: list[dict], hasil_semua: dict) -> dict | None:
    """
    Perkiraan angka kalau partai yang dianalisis TIDAK condong ke kekalahan.

    Cara: hitung tiap angka di dalam kelompok hasil (menang/kalah/remis) —
    di dalam satu kelompok tidak ada bias, karena semua partai kalah setara —
    lalu rata-ratakan ulang memakai porsi hasil SEBENARNYA dari seluruh partai.
    Ini rata-rata tertimbang biasa (post-stratification), bukan model.

    Syarat: tiap kelompok yang punya bobot harus punya sampel; kalau ada yang
    kosong, kembalikan None daripada menebak.
    """
    nilai = {b["nama"]: b for b in per_hasil}
    bobot = {k: v for k, v in (hasil_semua or {}).items()
             if k in ("menang", "kalah", "remis") and v}
    if not bobot or any(k not in nilai or not nilai[k]["partai"] for k in bobot):
        return None
    total_bobot = sum(bobot.values())
    keluar = {"dasar": {k: round(100 * v / total_bobot, 1) for k, v in bobot.items()},
              "partai_dipakai": sum(nilai[k]["partai"] for k in bobot)}
    for kunci in ("skor_persen", "akurasi", "rata_kehilangan",
                  "blunder_per_partai", "mistake_per_partai"):
        jumlah = pakai = 0.0
        for k, b in bobot.items():
            v = nilai[k].get(kunci)
            if v is not None:
                jumlah += b * v
                pakai += b
        keluar[kunci] = round(jumlah / pakai, 2 if "per_partai" in kunci else 1) if pakai else None
    return keluar


def cakupan_analisis(conn: sqlite3.Connection, pemain_ids: list[int],
                     format_: str | None = None) -> dict:
    """
    Berapa bagian partai pemain ini yang sudah dianalisis, dan apakah yang
    dianalisis CONDONG ke partai kalah.

    Perlu karena partai dipilih untuk dianalisis dengan aturan Ray sendiri
    ("prioritas utama scannya partai yang kalah"), jadi subsetnya bukan sampel
    acak. Tanpa angka ini, ringkasan menyajikan skor 24,2% untuk pemain yang
    skor sebenarnya 52,7% — terukur pada McLung_07, 13 Sep 2026: 422 dari
    8.596 partai dianalisis, 75% di antaranya kalah padahal porsi kalah
    sesungguhnya 44%.
    """
    if not pemain_ids:
        return {"partai_total": 0, "partai_dianalisis": 0}
    tanda = ",".join("?" * len(pemain_ids))
    syarat = f"(g.putih_id IN ({tanda}) OR g.hitam_id IN ({tanda}))"
    params = list(pemain_ids) + list(pemain_ids)
    if format_:
        syarat += " AND g.format = ?"
        params.append(format_)
    baris = conn.execute(f"""
        SELECT CASE WHEN g.hasil = '1/2-1/2' THEN 'remis'
                    -- '*' dan hasil lain HARUS keluar lebih dulu: untuk '*'
                    -- (g.hasil = '1-0') bernilai 0, sama dengan
                    -- (putih_id IN ...) = 0 saat pemainnya Hitam, sehingga
                    -- partai yang belum selesai terhitung MENANG (audit 27 Sep:
                    -- pemain 7954, satu-satunya partainya '*', tercatat menang 1).
                    WHEN g.hasil NOT IN ('1-0', '0-1') THEN 'lain'
                    WHEN (g.hasil = '1-0') = (g.putih_id IN ({tanda})) THEN 'menang'
                    WHEN g.hasil IN ('1-0', '0-1') THEN 'kalah'
                    ELSE 'lain' END AS hasil_saya,
               COUNT(*) AS semua,
               SUM(CASE WHEN a.partai_id IS NOT NULL THEN 1 ELSE 0 END) AS dianalisis
        FROM partai g LEFT JOIN analisis a ON a.partai_id = g.id
        WHERE {syarat}
        -- JANGAN beri alias ini nama `hasil`: SQLite akan mengelompokkan kolom
        -- g.hasil ('1-0'/'0-1'/...) dan labelnya diambil dari baris acak dalam
        -- kelompok itu. Terjadi sungguhan saat fungsi ini ditulis: menang
        -- terbaca 4.181 padahal 4.247.
        GROUP BY hasil_saya
    """, list(pemain_ids) + params).fetchall()

    semua = {r["hasil_saya"]: r["semua"] for r in baris}
    dianalisis = {r["hasil_saya"]: r["dianalisis"] for r in baris}
    n_semua = sum(semua.values())
    n_an = sum(dianalisis.values())

    def porsi_kalah(d: dict) -> float | None:
        n = sum(d.values())
        return d.get("kalah", 0) / n if n else None

    p_semua, p_an = porsi_kalah(semua), porsi_kalah(dianalisis)
    # 15 poin persentase: cukup besar untuk mengubah kesimpulan, cukup longgar
    # untuk tidak berisik saat sampelnya kecil.
    condong = bool(p_semua is not None and p_an is not None and n_an >= 20
                   and p_an - p_semua >= 0.15)
    return {
        "partai_total": n_semua,
        "partai_dianalisis": n_an,
        "persen_dianalisis": round(100 * n_an / n_semua, 1) if n_semua else None,
        "hasil_semua": semua,
        "hasil_dianalisis": dianalisis,
        "porsi_kalah_semua": round(100 * p_semua, 1) if p_semua is not None else None,
        "porsi_kalah_dianalisis": round(100 * p_an, 1) if p_an is not None else None,
        "condong_ke_kalah": condong,
    }


def ringkasan_pemain(conn: sqlite3.Connection, pemain_ids: list[int],
                     format_: str | None = None) -> dict:
    if not pemain_ids:
        return {"partai": 0}

    dasar, params = _dasar(pemain_ids, format_)

    # ---- per fase: langsung dari langkah ----
    fase_rows = conn.execute(
        f"""
        SELECT CASE WHEN al.ply < 24 THEN 'pembukaan'
                    WHEN al.ply < 80 THEN 'tengah'
                    ELSE 'akhir' END AS fase,
               COUNT(*)                                        AS langkah,
               AVG(al.kehilangan_cp)                           AS rata_kehilangan,
               SUM(al.klasifikasi = 'blunder')                 AS blunder,
               SUM(al.klasifikasi = 'mistake')                 AS mistake,
               SUM(al.klasifikasi = 'inaccuracy')              AS inaccuracy
        {dasar}
        GROUP BY fase
        """,
        params,
    ).fetchall()
    urutan = {n: i for i, (n, _, _) in enumerate(FASE)}
    per_fase = sorted((dict(r) for r in fase_rows), key=lambda r: urutan[r["fase"]])
    for r in per_fase:
        r["rata_kehilangan"] = round(r["rata_kehilangan"] or 0, 1)

    # ---- per partai: dasar untuk kelompok lawan & pembukaan ----
    partai_rows = conn.execute(
        f"""
        SELECT g.id,
               g.eco, g.pembukaan, g.hasil, g.format, al.warna,
               CASE WHEN al.warna = 'putih' THEN g.elo_putih ELSE g.elo_hitam END AS elo_saya,
               CASE WHEN al.warna = 'putih' THEN g.elo_hitam ELSE g.elo_putih END AS elo_lawan,
               CASE WHEN al.warna = 'putih' THEN a.akurasi_putih ELSE a.akurasi_hitam END AS akurasi,
               COUNT(*)                            AS langkah,
               AVG(al.kehilangan_cp)               AS rata_kehilangan,
               SUM(al.klasifikasi = 'blunder')     AS blunder,
               SUM(al.klasifikasi = 'mistake')     AS mistake,
               MIN(CASE WHEN al.klasifikasi IN ('blunder', 'mistake')
                        THEN al.ply END)           AS ply_kesalahan_pertama
        {dasar}
        GROUP BY g.id
        """,
        params,
    ).fetchall()

    def skor(r):
        if r["hasil"] == "1/2-1/2":
            return 0.5
        if r["hasil"] not in ("1-0", "0-1"):
            return None
        return 1.0 if (r["hasil"] == "1-0") == (r["warna"] == "putih") else 0.0

    kel = defaultdict(lambda: {"partai": 0, "skor": 0.0, "n_skor": 0, "kehilangan": 0.0,
                               "blunder": 0, "mistake": 0, "kesalahan_pertama": [],
                               "akurasi": [], "tanpa_kesalahan": 0})
    pemb = defaultdict(lambda: {"partai": 0, "skor": 0.0, "n_skor": 0, "kehilangan": 0.0,
                                "blunder": 0, "mistake": 0, "akurasi": []})
    # Per format waktu dan per hasil. Yang per hasil dipakai dua kali: dibaca
    # sendiri ("seburuk apa main saya waktu kalah") DAN untuk menimbang ulang
    # angka total, karena partai kalah sengaja didahulukan saat analisis.
    fmt = defaultdict(lambda: {"partai": 0, "skor": 0.0, "n_skor": 0, "kehilangan": 0.0,
                               "blunder": 0, "mistake": 0, "akurasi": []})
    hsl = defaultdict(lambda: {"partai": 0, "skor": 0.0, "n_skor": 0, "kehilangan": 0.0,
                               "blunder": 0, "mistake": 0, "akurasi": []})
    total = {"partai": 0, "skor": 0.0, "n_skor": 0, "kehilangan": 0.0,
             "blunder": 0, "mistake": 0, "akurasi": []}

    for r in partai_rows:
        s = skor(r)

        def isi(d):
            d["partai"] += 1
            d["kehilangan"] += r["rata_kehilangan"] or 0
            d["blunder"] += r["blunder"]
            d["mistake"] += r["mistake"]
            if r["akurasi"] is not None:
                d["akurasi"].append(r["akurasi"])
            if s is not None:
                d["skor"] += s
                d["n_skor"] += 1

        isi(total)

        if r["elo_saya"] and r["elo_lawan"]:
            nama = _kelompok(r["elo_lawan"] - r["elo_saya"], KELOMPOK_SELISIH)
            if nama:
                k = kel[nama]
                isi(k)
                if r["ply_kesalahan_pertama"] is not None:
                    k["kesalahan_pertama"].append(r["ply_kesalahan_pertama"])
                else:
                    k["tanpa_kesalahan"] += 1

        keluarga = (r["pembukaan"] or "").split(":")[0].strip() or (r["eco"] or "?")
        isi(pemb[keluarga])
        isi(fmt[r["format"] or "tanpa"])
        isi(hsl[_nama_hasil(r["hasil"], r["warna"])])

    def rapikan(nama, d, dengan_kesalahan=False):
        n = d["partai"]
        h = {
            "nama": nama,
            "partai": n,
            "skor_persen": round(100 * d["skor"] / d["n_skor"], 1) if d["n_skor"] else None,
            "rata_kehilangan": round(d["kehilangan"] / n, 1) if n else None,
            "akurasi": round(sum(d["akurasi"]) / len(d["akurasi"]), 1) if d["akurasi"] else None,
            "blunder_per_partai": round(d["blunder"] / n, 2) if n else None,
            "mistake_per_partai": round(d["mistake"] / n, 2) if n else None,
            "sampel_kecil": n < N_MINIMAL,
        }
        if dengan_kesalahan:
            kp = d["kesalahan_pertama"]
            # ply -> nomor langkah (ply 0 = langkah 1)
            h["kesalahan_pertama_rata_langkah"] = round(sum(kp) / len(kp) / 2 + 1, 1) if kp else None
            h["partai_tanpa_kesalahan_besar"] = d["tanpa_kesalahan"]
        return h

    per_lawan = [rapikan(n, kel[n], True) for n, _, _ in KELOMPOK_SELISIH if n in kel]
    per_pembukaan = sorted((rapikan(n, d) for n, d in pemb.items()),
                           key=lambda x: -x["partai"])[:15]
    urut_fmt = {n: i for i, n in enumerate(("bullet", "blitz", "rapid", "klasik", "tanpa"))}
    per_format = sorted((rapikan(n, d) for n, d in fmt.items()),
                        key=lambda x: urut_fmt.get(x["nama"], 99))
    per_hasil = sorted((rapikan(n, d) for n, d in hsl.items()),
                       key=lambda x: -x["partai"])

    cakupan = cakupan_analisis(conn, pemain_ids, format_)
    perkiraan = tertimbang(per_hasil, cakupan.get("hasil_semua", {}))
    catatan = [
        "Hanya partai yang sudah dianalisis yang dihitung.",
        "Fase dibagi berdasarkan nomor langkah (1-12, 13-40, 41+), bukan berdasarkan materi.",
        f"Kelompok dengan kurang dari {N_MINIMAL} partai ditandai sampel kecil — jangan ditarik kesimpulan darinya.",
    ]
    if cakupan.get("persen_dianalisis") is not None:
        # angka gaya Indonesia: ribuan titik, desimal koma
        ribuan = lambda n: f"{n:,}".replace(",", ".")
        persen = str(cakupan["persen_dianalisis"]).replace(".", ",")
        catatan.insert(0, f"Baru {ribuan(cakupan['partai_dianalisis'])} dari "
                          f"{ribuan(cakupan['partai_total'])} partai ({persen}%) yang dianalisis.")
    if cakupan.get("condong_ke_kalah"):
        catatan.insert(1,
            f"Partai yang dianalisis CONDONG ke partai kalah ({cakupan['porsi_kalah_dianalisis']}% "
            f"dari yang dianalisis, padahal {cakupan['porsi_kalah_semua']}% dari seluruh partai) — "
            "karena partai kalah sengaja didahulukan. Skor dan akurasi di bawah lebih buruk "
            "daripada permainan sesungguhnya; yang tetap sah dibaca adalah pola kesalahannya, "
            "bukan angka hasilnya.")
        if perkiraan:
            catatan.insert(2,
                "Perkiraan tertimbang mengoreksi condong itu: tiap angka dihitung di dalam "
                "kelompok menang/kalah/remis, lalu dirata-ratakan ulang memakai porsi hasil "
                "seluruh partai. Tetap perkiraan, bukan hasil analisis semua partai.")

    return {
        "pemain_ids": pemain_ids,
        "format": format_ or "semua",
        "total": rapikan("semua", total),
        "per_fase": per_fase,
        "per_kekuatan_lawan": per_lawan,
        "per_pembukaan": per_pembukaan,
        "per_format": per_format,
        "per_hasil": per_hasil,
        "perkiraan_tertimbang": perkiraan,
        "cakupan": cakupan,
        "catatan": catatan,
    }
