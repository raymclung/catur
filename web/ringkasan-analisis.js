/* Tampilan hasil analisis engine di halaman partai: kartu ringkasan (akurasi,
   titik balik, momen kritis), kotak "belum dianalisis", komentar per langkah,
   dan tanda ?? ? ?! di daftar langkah.

   Dipisah dari papan.js 27 Sep 2026 (F4: berkas itu 988 baris). Modul ini
   hanya menggambar dari data /api/partai/{id}/analisis; satu-satunya yang ia
   butuhkan dari halaman adalah "pindah ke titik i", yang disuntik lewat
   `lompat(i)`, jadi ia tidak tahu apa-apa soal papan maupun autoplay.

   peluangMenang dan formatEval ikut diekspor karena papan.js memakainya untuk
   eval bar, baik saat mengikuti partai maupun saat variasi.

   Dipakai oleh web/partai.html lewat window.RingkasanAnalisis; harus dimuat
   SEBELUM papan.js. */

window.RingkasanAnalisis = (function () {
  "use strict";

  var TANDA = { blunder: "??", mistake: "?", inaccuracy: "?!", terbaik: "!" };
  var NAMA_KLAS = {
    blunder: "Blunder", mistake: "Kesalahan", inaccuracy: "Kurang akurat",
    terbaik: "Langkah terbaik", baik: "Baik", buku: "Teori pembukaan"
  };

  function teks(isi) {
    return document.createTextNode(isi === null || isi === undefined ? "" : String(isi));
  }

  function buat(tag, kelas, isi) {
    var e = document.createElement(tag);
    if (kelas) e.className = kelas;
    if (isi !== undefined) e.appendChild(teks(isi));
    return e;
  }

  /* Peluang menang 0..1 dari centipawn, rumus Lichess. Sama dengan yang
     dipakai backend supaya eval bar dan akurasi konsisten. */
  function peluangMenang(cp) {
    var c = Math.max(-1000, Math.min(1000, cp));
    return 0.5 + 0.5 * (2 / (1 + Math.exp(-0.00368208 * c)) - 1);
  }

  function formatEval(cp, mat) {
    if (mat !== null && mat !== undefined) {
      if (mat === 0) return "#";
      return (mat > 0 ? "M" : "-M") + Math.abs(mat);
    }
    return (cp >= 0 ? "+" : "") + (cp / 100).toFixed(2).replace(".", ",");
  }

  /* Komentar untuk satu baris analisis `la` (null = posisi awal). */
  function komentar(elKomentar, la) {
    elKomentar.textContent = "";
    if (!la) {
      elKomentar.appendChild(teks("Posisi awal. Tekan → untuk mulai."));
      return;
    }
    var judul = buat("div");
    var tandaTeks = TANDA[la.klasifikasi] || "";
    judul.appendChild(buat("span", "klas-" + la.klasifikasi,
      (la.warna === "putih" ? la.nomor_langkah + ". " : la.nomor_langkah + "... ") +
      la.san + tandaTeks + "  "));
    judul.appendChild(buat("span", null, NAMA_KLAS[la.klasifikasi] || la.klasifikasi));
    if (la.kehilangan_cp > 0 && la.klasifikasi !== "buku") {
      judul.appendChild(buat("span", "redup", "  (−" + la.kehilangan_cp + " cp)"));
    }
    elKomentar.appendChild(judul);

    if (la.langkah_terbaik_san && la.langkah_terbaik_san !== la.san && la.klasifikasi !== "buku") {
      var v = buat("div", "variasi");
      v.appendChild(buat("strong", null, "Seharusnya " + la.langkah_terbaik_san));
      if (la.variasi_terbaik && la.variasi_terbaik.length > 1) {
        v.appendChild(teks("  →  " + la.variasi_terbaik.join(" ")));
      }
      elKomentar.appendChild(v);
    }
  }

  /* Tanda ?? ? ?! dan keterangan di tiap langkah daftar. `langkahAnalisis(i)`
     memberi baris analisis untuk titik i (data-indeks), atau null. */
  function hiasDaftar(elDaftar, langkahAnalisis) {
    var semua = elDaftar.querySelectorAll(".langkah");
    for (var i = 0; i < semua.length; i++) {
      var la = langkahAnalisis(parseInt(semua[i].dataset.indeks, 10));
      if (!la) continue;
      if (TANDA[la.klasifikasi]) {
        semua[i].classList.add("klas-" + la.klasifikasi);
        semua[i].appendChild(buat("span", "tanda", TANDA[la.klasifikasi]));
      }
      semua[i].title = (NAMA_KLAS[la.klasifikasi] || "") +
        (la.kehilangan_cp ? " (−" + la.kehilangan_cp + " cp)" : "");
    }
  }

  /* Ayunan peluang menang terbesar (dari sudut pandang pihak yang jalan),
     paling banyak lima, diurutkan menurut nomor langkah. Ambang 10 poin
     supaya ketidakakuratan kecil tidak ikut. */
  function momenKritis(analisis) {
    var daftar = [];
    analisis.langkah.forEach(function (l) {
      if (l.eval_sebelum_cp === null || l.eval_sebelum_cp === undefined) return;
      var sesudah = l.eval_sesudah_cp;
      if ((sesudah === null || sesudah === undefined) && l.mat_sesudah !== null && l.mat_sesudah !== undefined) {
        sesudah = l.mat_sesudah > 0 ? 1000 : -1000;
      }
      if (sesudah === null || sesudah === undefined) return;
      var turun = peluangMenang(l.eval_sebelum_cp) - peluangMenang(sesudah);
      /* Blunder selalu masuk walau ayunannya kecil (mis. posisi sudah kalah). */
      if (turun >= 0.10 || l.klasifikasi === "blunder") daftar.push({ l: l, turun: turun });
    });
    daftar.sort(function (a, b) { return b.turun - a.turun; });
    return daftar.slice(0, 5).sort(function (a, b) { return a.l.ply - b.l.ply; });
  }

  function gambarMomenKritis(r, analisis, lompat) {
    var momen = momenKritis(analisis);
    if (!momen.length) return;
    var judul = buat("div", null);
    judul.style.marginTop = "10px";
    judul.appendChild(buat("strong", null, "Momen kritis"));
    judul.appendChild(buat("span", "redup kecil", "  penurunan peluang menang pihak yang jalan"));
    var latih = document.createElement("a");
    latih.href = "/static/latihan.html?partai_id=" + analisis.partai_id;
    latih.className = "kecil";
    latih.style.marginLeft = "10px";
    latih.title = "Latihan: temukan langkah terbaik di posisi-posisi kesalahan partai ini";
    latih.appendChild(teks("latih →"));
    judul.appendChild(latih);
    r.appendChild(judul);
    var wadah = buat("div", "momen-daftar");
    momen.forEach(function (m) {
      var l = m.l;
      var baris = buat("div", "momen-baris");
      baris.title = "Lompat ke langkah ini";
      baris.appendChild(buat("span", "turun", "−" + Math.round(100 * m.turun) + "%"));
      baris.appendChild(buat("span", "langkah klas-" + l.klasifikasi,
        (l.warna === "putih" ? l.nomor_langkah + "." : l.nomor_langkah + "…") + l.san + (TANDA[l.klasifikasi] || "")));
      if (l.langkah_terbaik_san && l.langkah_terbaik_san !== l.san) {
        baris.appendChild(buat("span", "redup", "seharusnya " + l.langkah_terbaik_san));
      }
      baris.addEventListener("click", function () { lompat(l.ply + 1); });
      wadah.appendChild(baris);
    });
    r.appendChild(wadah);
  }

  /* Kartu ringkasan: engine, akurasi kedua pihak, titik balik, momen kritis. */
  function gambar(r, analisis, g, lompat) {
    r.textContent = "";
    /* Hasil Lichess tidak punya depth (disimpan 0); menulis "depth 0" menyesatkan. */
    r.appendChild(buat("h2", null, "Analisis " + analisis.engine +
      (analisis.depth ? " · depth " + analisis.depth : "")));

    /* Analisis kasar (profil cepat, atau dari Lichess) boleh diulang dengan
       teliti. Tanpa tautan ini tombol "Analisa partai ini" hilang begitu
       partainya sudah dianalisis, jadi rencana Ray "dianalisa dulu, nanti
       dianalisis ulang" tidak punya jalan dari halaman partai. */
    if (analisis.profil === "cepat" || analisis.profil === "lichess") {
      var ulang = document.createElement("div");
      ulang.className = "kecil redup";
      ulang.style.margin = "-4px 0 8px";
      ulang.appendChild(document.createTextNode(analisis.profil === "cepat"
        ? "Dianalisis cepat (depth 10): akurasi mirip, label blunder kurang teliti. "
        : "Dianalisis Lichess: nilainya sedikit berbeda dari engine laptop. "));
      var a = document.createElement("a");
      a.href = "/static/mesin.html?kelompok=id&id=" + g.id + "&ulang=1&profil=hemat";
      a.appendChild(document.createTextNode("Analisa ulang lebih teliti →"));
      ulang.appendChild(a);
      r.appendChild(ulang);
    }

    var kotak = buat("div", "akurasi-kotak");
    [["putih", g.putih, analisis.akurasi_putih, analisis.blunder_putih, analisis.mistake_putih, analisis.inaccuracy_putih],
     ["hitam", g.hitam, analisis.akurasi_hitam, analisis.blunder_hitam, analisis.mistake_hitam, analisis.inaccuracy_hitam]
    ].forEach(function (s) {
      var k = buat("div");
      k.appendChild(buat("div", "label", s[1]));
      k.appendChild(buat("div", "angka-besar", s[2] !== null ? s[2].toFixed(1).replace(".", ",") + "%" : "—"));
      var rinci = buat("div", "kecil");
      rinci.appendChild(buat("span", "klas-blunder", "?? " + s[3] + "  "));
      rinci.appendChild(buat("span", "klas-mistake", "? " + s[4] + "  "));
      rinci.appendChild(buat("span", "klas-inaccuracy", "?! " + s[5]));
      k.appendChild(rinci);
      kotak.appendChild(k);
    });
    r.appendChild(kotak);

    if (analisis.turning_point_ply !== null && analisis.turning_point_ply !== undefined) {
      var tp = analisis.langkah[analisis.turning_point_ply];
      if (tp) {
        var t = buat("div", null);
        t.style.marginTop = "10px";
        t.appendChild(buat("strong", null, "Titik balik: "));
        var tautan = buat("span", "langkah klas-" + tp.klasifikasi,
          (tp.warna === "putih" ? tp.nomor_langkah + "." : tp.nomor_langkah + "...") + tp.san + (TANDA[tp.klasifikasi] || ""));
        tautan.addEventListener("click", function () { lompat(tp.ply + 1); });
        t.appendChild(tautan);
        if (tp.langkah_terbaik_san && tp.langkah_terbaik_san !== tp.san) {
          t.appendChild(buat("span", "redup", "  seharusnya " + tp.langkah_terbaik_san));
        }
        r.appendChild(t);
      }
    } else {
      var s0 = buat("div", "redup kecil", "Tidak ada titik balik yang jelas — partainya berjalan stabil.");
      s0.style.marginTop = "10px";
      r.appendChild(s0);
    }
    gambarMomenKritis(r, analisis, lompat);
    r.hidden = false;
  }

  /* Kartu pengganti kalau analisis belum ada (e.belum) atau gagal dimuat. */
  function gambarBelum(r, g, e) {
    r.textContent = "";
    if (e && e.belum) {
      r.appendChild(buat("h2", null, "Belum dianalisis"));
      /* Tombol lebih dulu daripada perintah terminal: nomor partai tidak
         perlu diketik sendiri, halaman Mesin menerimanya lewat ?id= */
      var tombol = document.createElement("a");
      tombol.className = "tombol-analisa";
      tombol.href = "/static/mesin.html?kelompok=id&id=" + g.id;
      tombol.appendChild(document.createTextNode("Analisa partai ini dengan engine →"));
      r.appendChild(tombol);
      r.appendChild(buat("div", "kecil redup",
        "Satu partai kira-kira satu menit (profil hemat). Atau lewat terminal:"));
      var kode = buat("pre", "eval-fen", e.detail && e.detail.perintah ? e.detail.perintah : "python analisis_partai.py --id " + g.id);
      kode.style.borderTop = "none";
      r.appendChild(kode);
    } else {
      r.appendChild(buat("div", "redup kecil", "Analisis tidak bisa dimuat: " + (e.message || e)));
    }
    r.hidden = false;
  }

  return {
    peluangMenang: peluangMenang,
    formatEval: formatEval,
    komentar: komentar,
    hiasDaftar: hiasDaftar,
    gambar: gambar,
    gambarBelum: gambarBelum
  };
})();
