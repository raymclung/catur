/* Halaman satu partai: papan catur dan pemutaran ulang langkah. */

(function () {
  "use strict";

  var papan = null;
  var posisi = [];      // FEN untuk tiap titik: posisi[0] = sebelum langkah 1
  var langkah = [];     // hasil history({verbose:true}) dari chess.js
  var indeks = 0;       // titik yang sedang ditampilkan
  var timerAuto = null;
  var analisis = null;  // hasil /api/partai/{id}/analisis, null = belum ada

  /* Variasi: langkah yang dimainkan pengguna dari posisi partai (menyimpang
     dari partai asli). null = papan mengikuti partai. */
  var variasi = null;   // { dari: indeks awal, permainan: Chess, san: [] }
  var klik = null;      // KlikPapan, dipasang setelah papan dibuat
  var terpilih = null;  // kotak yang dipilih lewat klik
  var timerVariasi = null;
  var permintaanVariasi = null;

  var peluangMenang = RingkasanAnalisis.peluangMenang;
  var formatEval = RingkasanAnalisis.formatEval;

  var el = {
    info: document.getElementById("info"),
    daftar: document.getElementById("daftar-langkah"),
    fen: document.getElementById("fen"),
    galat: document.getElementById("galat"),
    autoplay: document.getElementById("autoplay")
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

  function tampilkanGalat(pesan) {
    el.galat.textContent = pesan;
    el.galat.hidden = false;
  }

  function idDariUrl() {
    var bagian = window.location.pathname.split("/").filter(Boolean);
    return bagian[bagian.length - 1];
  }

  /* ---------- membangun daftar posisi dari PGN ----------
     chess.js dipakai untuk memutar ulang partai dari awal, mencatat FEN di
     tiap titik. Menghitungnya sekali di depan membuat navigasi maju-mundur
     jadi sekadar mengganti indeks, bukan memutar ulang tiap kali. */

  function siapkanPosisi(pgn) {
    var permainan = new Chess();
    if (!permainan.load_pgn(pgn)) {
      return false;
    }
    langkah = permainan.history({ verbose: true });

    /* Partai "From Position" (Lichess) dan soal/studi membawa tag [FEN]:
       memutarnya dari posisi awal standar menghasilkan partai karangan
       (audit 27 Sep 2026: partai 8435 tampil 1. e5 Nf3 dengan warna
       tertukar; 1.860 partai seperti ini). */
    var ulang = AlatCatur.permainanAwal(permainan);
    posisi = [ulang.fen()];
    for (var i = 0; i < langkah.length; i++) {
      ulang.move(langkah[i].san);
      posisi.push(ulang.fen());
    }
    return true;
  }

  /* ---------- tampilan ---------- */

  function gambarDaftarLangkah() {
    el.daftar.textContent = "";
    if (langkah.length === 0) {
      el.daftar.appendChild(buat("div", "pesan", "Partai ini tidak punya langkah."));
      return;
    }
    /* Nomor dari FEN awal, bukan dari indeks: partai ber-FEN bisa mulai
       dengan Hitam ("1... e5") atau dari langkah ke-30. */
    var nomor = nomorSan(posisi[0], langkah.map(function (l) { return l.san; }));
    for (var i = 0; i < langkah.length; i++) {
      if (nomor[i].nomor) {
        el.daftar.appendChild(buat("span", "nomor", nomor[i].nomor));
      }
      var s = buat("span", "langkah", langkah[i].san);
      s.dataset.indeks = i + 1;
      s.addEventListener("click", function () {
        keIndeks(parseInt(this.dataset.indeks, 10));
      });
      el.daftar.appendChild(s);
      el.daftar.appendChild(teks(" "));
    }
  }

  function perbaruiSorotan() {
    var semua = el.daftar.querySelectorAll(".langkah");
    for (var i = 0; i < semua.length; i++) {
      var aktif = parseInt(semua[i].dataset.indeks, 10) === indeks;
      semua[i].classList.toggle("aktif", aktif);
      if (aktif) gulirKeLangkah(semua[i]);
    }
  }

  /* scrollIntoView menggulung SELURUH halaman kalau daftar langkah sedang di
     luar layar (menekan → membuat halaman melompat ke bawah). Yang boleh
     bergulir hanya kotak daftar langkah itu sendiri. */
  function gulirKeLangkah(elemen) {
    var wadah = el.daftar;
    var atas = elemen.getBoundingClientRect().top - wadah.getBoundingClientRect().top + wadah.scrollTop;
    var bawah = atas + elemen.offsetHeight;
    if (atas < wadah.scrollTop || bawah > wadah.scrollTop + wadah.clientHeight) {
      wadah.scrollTop = atas - wadah.clientHeight / 2;
    }
  }

  function fenSaatIni() {
    return variasi ? variasi.permainan.fen() : posisi[indeks];
  }

  function keIndeks(i) {
    if (variasi) tutupVariasi();
    indeks = Math.max(0, Math.min(i, posisi.length - 1));
    papan.position(posisi[indeks]);
    el.fen.textContent = posisi[indeks];
    perbaruiSorotan();
    perbaruiAnalisisTampilan();
    GrafikPartai.penandaJam(indeks);
    perbaruiPenjelajah();
    if (indeks >= posisi.length - 1) hentikanAuto();
  }

  /* ---------- praktik GM & penjelajah: web/praktik-gm.js ---------- */

  /* Pindah ke titik i dari kartu mana pun (praktik GM, ringkasan analisis):
     autoplay berhenti dulu. Disuntik ke modul supaya mereka tidak perlu
     tahu apa-apa soal autoplay maupun navigasi langkah. */
  function lompat(i) {
    hentikanAuto();
    keIndeks(i);
  }

  function cariKeluarTeori(g) {
    PraktikGm.cariKeluarTeori(g, langkah, posisi, el.daftar, {
      lompat: lompat,
      mainkan: function (i, uci) {
        lompat(i);
        if (uci) mainkanVariasi(uci.slice(0, 2), uci.slice(2, 4), uci.slice(4));
      }
    });
  }

  function perbaruiPenjelajah() {
    PraktikGm.perbaruiPenjelajah(fenSaatIni(), function (l) {
      if (variasi) return false;
      var la = langkahAnalisis(indeks + 1);
      return la ? la.uci === l.uci
        : !!(langkah[indeks] && langkah[indeks].from + langkah[indeks].to === l.uci.substr(0, 4));
    });
  }

  /* ---------- variasi: memainkan langkah sendiri dari posisi partai ----------
     Papan partai bisa diseret/diklik. Langkah pertama yang menyimpang membuka
     "variasi" dari posisi saat itu; papan, FEN, penjelajah, dan evaluasi engine
     (/api/evaluasi) mengikuti variasi. ←/undo mundur satu langkah variasi;
     Esc atau navigasi partai (→, klik daftar langkah) menutupnya. */

  var elV = { panel: document.getElementById("variasi") };

  function permainanSaatIni() {
    return variasi ? variasi.permainan : new Chess(posisi[indeks]);
  }

  /* promosi: bidak pilihan ("q","r","b","n"); kosong = ratu. Penangan
     seret/klik menanyakannya lewat PilihPromosi sebelum memanggil ini. */
  function mainkanVariasi(dari, ke, promosi) {
    var baru = !variasi;
    var g = baru ? new Chess(posisi[indeks]) : variasi.permainan;
    var m = g.move({ from: dari, to: ke, promotion: promosi || "q" });
    if (!m) return null;
    if (baru) variasi = { dari: indeks, permainan: g, san: [] };
    variasi.san.push(m.san);
    perbaruiVariasi();
    return m;
  }

  function undoVariasi() {
    if (!variasi) return;
    variasi.permainan.undo();
    variasi.san.pop();
    if (!variasi.san.length) { tutupVariasi(); keIndeks(indeks); return; }
    perbaruiVariasi();
  }

  function tutupVariasi() {
    variasi = null;
    clearTimeout(timerVariasi);
    if (permintaanVariasi) { permintaanVariasi.abort(); permintaanVariasi = null; }
    if (klik) klik.hapusSorot();
    terpilih = null;
    if (elV.panel) elV.panel.hidden = true;
    elA.bar.hidden = !analisis;
  }

  /* Nomor langkah dari FEN dasar: "4." / "4..." untuk langkah pertama. */
  function nomorSan(fenDasar, daftarSan) {
    var bagian = fenDasar.split(/\s+/);
    var hitam = bagian[1] === "b";
    var nomor = parseInt(bagian[5], 10) || 1;
    var keluar = [];
    daftarSan.forEach(function (san, j) {
      var n = nomor + Math.floor((j + (hitam ? 1 : 0)) / 2);
      var giliranHitam = (j + (hitam ? 1 : 0)) % 2 === 1;
      if (!giliranHitam) keluar.push({ nomor: n + ".", san: san });
      else keluar.push({ nomor: j === 0 ? n + "..." : "", san: san });
    });
    return keluar;
  }

  function perbaruiVariasi() {
    if (!variasi || !elV.panel) return;
    var fen = variasi.permainan.fen();
    papan.position(fen);
    el.fen.textContent = fen;
    if (klik) klik.hapusSorot();
    terpilih = null;

    elV.panel.textContent = "";
    elV.panel.hidden = false;
    elV.panel.className = "komentar variasi";

    var judul = buat("div");
    var sebelum = variasi.dari > 0 ? nomorSan(posisi[variasi.dari - 1], [langkah[variasi.dari - 1].san])[0] : null;
    var dasar = sebelum ? "langkah " + sebelum.nomor + " " + sebelum.san : "posisi awal";
    judul.appendChild(buat("strong", null, "Variasi "));
    judul.appendChild(buat("span", "redup kecil", "dari " + dasar + ": "));
    var baris = buat("span", "variasi-langkah");
    nomorSan(posisi[variasi.dari], variasi.san).forEach(function (x, j) {
      if (x.nomor) baris.appendChild(buat("span", "nomor", x.nomor + " "));
      baris.appendChild(buat("span", "langkah" + (j === variasi.san.length - 1 ? " aktif" : ""), x.san));
      baris.appendChild(teks(" "));
    });
    judul.appendChild(baris);
    elV.panel.appendChild(judul);

    var mesin = buat("div", "variasi-mesin redup kecil", "Mengevaluasi...");
    mesin.id = "variasi-mesin";
    elV.panel.appendChild(mesin);

    var alat = buat("div", "variasi-alat");
    var bUndo = buat("button", null, "◀ undo (←)");
    bUndo.addEventListener("click", undoVariasi);
    var bTutup = buat("button", null, "kembali ke partai (Esc)");
    bTutup.addEventListener("click", function () { tutupVariasi(); keIndeks(indeks); });
    alat.appendChild(bUndo);
    alat.appendChild(bTutup);
    elV.panel.appendChild(alat);

    perbaruiPenjelajah();
    jadwalkanEvaluasiVariasi();
  }

  function jadwalkanEvaluasiVariasi() {
    clearTimeout(timerVariasi);
    timerVariasi = setTimeout(evaluasiVariasi, 300);
  }

  function evaluasiVariasi() {
    if (!variasi) return;
    var fen = variasi.permainan.fen();
    var mesin = document.getElementById("variasi-mesin");
    if (permintaanVariasi) permintaanVariasi.abort();
    permintaanVariasi = new AbortController();
    var sinyal = permintaanVariasi.signal;

    fetch("/api/evaluasi?fen=" + encodeURIComponent(fen) + "&multipv=1", { signal: sinyal })
      .then(function (r) {
        return r.text().then(function (t) {
          var b = null;
          try { b = JSON.parse(t); } catch (e) { b = null; }
          return { ok: r.ok, status: r.status, body: b };
        });
      })
      .then(function (res) {
        if (!variasi || variasi.permainan.fen() !== fen || !mesin) return;
        mesin.textContent = "";
        if (!res.ok) {
          var d = res.body && res.body.detail ? String(res.body.detail) : "";
          mesin.appendChild(teks(res.status === 409 ? "Engine sedang dipakai; coba lagi sebentar." :
                                 res.status === 503 ? "Engine tidak tersedia di server." :
                                 "Evaluasi gagal (" + res.status + ")" + (d ? ": " + d : "")));
          return;
        }
        var b = res.body || {};
        var g = (b.garis && b.garis[0]) || null;
        var cp = g ? g.cp : (b.skor ? b.skor.cp : null);
        var mat = g ? g.mate : (b.skor ? b.skor.mate : null);
        var pemenang = b.skor && b.skor.pemenang ? b.skor.pemenang : null;
        if (cp === null && mat === null && !pemenang) {
          mesin.appendChild(teks("Engine tidak mengembalikan evaluasi."));
          return;
        }

        var persen = pemenang ? (pemenang === "putih" ? 100 : 0)
          : (mat !== null && mat !== undefined) ? (mat > 0 ? 100 : 0)
          : peluangMenang(cp || 0) * 100;
        elA.bar.hidden = false;
        elA.barIsi.style.height = persen + "%";
        elA.angka.textContent = pemenang ? (pemenang === "putih" ? "1-0" : "0-1") : formatEval(cp || 0, mat);
        elA.angka.className = "eval-bar-angka " + (persen >= 50 ? "bawah" : "atas");

        mesin.className = "variasi-mesin kecil";
        mesin.appendChild(buat("strong", null, "Engine " + (pemenang ? "skakmat, " + pemenang + " menang" : formatEval(cp || 0, mat))));
        if (g && g.pv && g.pv.length) {
          mesin.appendChild(buat("span", "redup", "  terbaik: "));
          nomorSan(fen, g.pv.slice(0, 10)).forEach(function (x, j) {
            if (x.nomor) mesin.appendChild(buat("span", "nomor", x.nomor + " "));
            var sp = buat("span", "langkah", x.san);
            sp.title = "Mainkan garis engine sampai langkah ini";
            sp.addEventListener("click", function () {
              if (!variasi || variasi.permainan.fen() !== fen) return;
              for (var k = 0; k <= j; k++) {
                var m = variasi.permainan.move(g.pv[k]);
                if (!m) break;
                variasi.san.push(m.san);
              }
              perbaruiVariasi();
            });
            mesin.appendChild(sp);
            mesin.appendChild(teks(" "));
          });
        }
        var durasi = b.durasi_ms ? " · " + (b.durasi_ms / 1000).toFixed(1).replace(".", ",") + " s" : "";
        mesin.appendChild(buat("span", "redup", " · depth " + (b.depth || "?") + durasi));
      })
      .catch(function (e) {
        if (e.name !== "AbortError" && mesin) mesin.textContent = "Gagal menghubungi server: " + e.message;
      })
      .then(function () {
        if (permintaanVariasi && permintaanVariasi.signal === sinyal) permintaanVariasi = null;
      });
  }

  /* seret & klik di papan partai */
  function bolehGeserPartai(source, piece) {
    if (klik) klik.hapusSorot();
    terpilih = null;
    var g = permainanSaatIni();
    if (g.game_over()) return false;
    return piece.charAt(0) === g.turn();
  }

  function saatJatuhPartai(source, target) {
    if (source === target) return "snapback";
    hentikanAuto();
    if (PilihPromosi.tunda(permainanSaatIni(), source, target, function (p) {
      mainkanVariasi(source, target, p);
    })) return "snapback";
    return mainkanVariasi(source, target) ? undefined : "snapback";
  }

  function pilihKotakPartai(sq) {
    klik.hapusSorot();
    terpilih = sq;
    klik.sorotPilih(sq);
    permainanSaatIni().moves({ square: sq, verbose: true }).forEach(function (m) {
      klik.sorotTarget(m.to, !!m.captured);
    });
  }

  function klikKotakPartai(sq) {
    if (!sq) { klik.hapusSorot(); terpilih = null; return; }
    var g = permainanSaatIni();
    var bidak = g.get(sq);
    if (terpilih === null) {
      if (bidak && !g.game_over() && bidak.color === g.turn()) pilihKotakPartai(sq);
      return;
    }
    if (sq === terpilih) { klik.hapusSorot(); terpilih = null; return; }
    hentikanAuto();
    var dari = terpilih;
    if (PilihPromosi.tunda(g, dari, sq, function (p) { mainkanVariasi(dari, sq, p); })) {
      klik.hapusSorot(); terpilih = null;
      return;
    }
    if (mainkanVariasi(terpilih, sq)) return;
    if (bidak && bidak.color === g.turn()) pilihKotakPartai(sq);
    else { klik.hapusSorot(); terpilih = null; }
  }

  /* ---------- analisis engine ----------
     Dimuat terpisah dari partai: kalau belum ada hasil, papan tetap jalan
     dan cuma ada keterangan cara menganalisisnya. Halaman ini hanya membaca
     hasil yang sudah tersimpan (analisis_partai.py); evaluasi on-demand di
     server ada di /api/evaluasi dan dipakai halaman papan analisis, bukan
     di sini (ARSITEKTUR.md, Phase 5). */

  var elA = {
    bar: document.getElementById("eval-bar"),
    barIsi: document.getElementById("eval-bar-isi"),
    angka: document.getElementById("eval-angka"),
    komentar: document.getElementById("komentar"),
    grafikKartu: document.getElementById("grafik-kartu"),
    grafik: document.getElementById("grafik"),
    ringkasan: document.getElementById("ringkasan-analisis")
  };

  /* Baris analisis untuk titik indeks i (i = ply + 1). i = 0 -> posisi awal. */
  function langkahAnalisis(i) {
    if (!analisis || i <= 0) return null;
    return analisis.langkah[i - 1] || null;
  }

  function perbaruiEvalBar() {
    if (!analisis) return;
    var la = langkahAnalisis(indeks);
    var cp = la ? la.eval_putih_cp : (analisis.langkah[0] ? analisis.langkah[0].eval_sebelum_cp : 0);
    var mat = la ? la.mat_sesudah : null;
    var persen;
    if (mat !== null && mat !== undefined) {
      /* mat_sesudah dari sudut pandang Putih; 0 = mat sudah terjadi, lihat cp */
      persen = mat > 0 || (mat === 0 && cp > 0) ? 100 : 0;
    } else {
      persen = peluangMenang(cp) * 100;
    }
    elA.barIsi.style.height = persen + "%";
    elA.angka.textContent = formatEval(cp, mat);
    elA.angka.className = "eval-bar-angka " + (persen >= 50 ? "bawah" : "atas");
  }

  function perbaruiKomentar() {
    if (!analisis) return;
    RingkasanAnalisis.komentar(elA.komentar, langkahAnalisis(indeks));
  }

  function sorotKotak() {
    if (!papan) return;
    var $ = window.jQuery;
    $("#papan [class*='square-']").removeClass("sorot-main sorot-terbaik");
    var la = langkahAnalisis(indeks);
    if (!la) return;
    $("#papan .square-" + la.uci.substr(0, 2)).addClass("sorot-main");
    $("#papan .square-" + la.uci.substr(2, 2)).addClass("sorot-main");
    if (la.langkah_terbaik_uci && la.langkah_terbaik_uci !== la.uci &&
        (la.klasifikasi === "blunder" || la.klasifikasi === "mistake" || la.klasifikasi === "inaccuracy")) {
      $("#papan .square-" + la.langkah_terbaik_uci.substr(0, 2)).addClass("sorot-terbaik");
      $("#papan .square-" + la.langkah_terbaik_uci.substr(2, 2)).addClass("sorot-terbaik");
    }
  }

  function perbaruiAnalisisTampilan() {
    if (!analisis) return;
    perbaruiEvalBar();
    perbaruiKomentar();
    sorotKotak();
    GrafikPartai.penandaEval(indeks);
  }

  function hiasDaftarLangkah() {
    RingkasanAnalisis.hiasDaftar(el.daftar, langkahAnalisis);
  }

  function muatAnalisis(g) {
    fetch("/api/partai/" + g.id + "/analisis")
      .then(function (res) {
        if (res.status === 404) return res.json().then(function (d) { throw { belum: true, detail: d.detail }; });
        if (!res.ok) throw new Error("Server menjawab " + res.status);
        return res.json();
      })
      .then(function (a) {
        analisis = a;
        elA.bar.hidden = false;
        elA.komentar.hidden = false;
        hiasDaftarLangkah();
        GrafikPartai.gambarEval(analisis, klikGrafik);
        RingkasanAnalisis.gambar(elA.ringkasan, analisis, g, lompat);
        perbaruiAnalisisTampilan();
      })
      .catch(function (e) { RingkasanAnalisis.gambarBelum(elA.ringkasan, g, e); });
  }

  /* ---------- autoplay ---------- */

  /* Klik di grafik mana pun = pindah ke langkah itu dan berhenti autoplay.
     Disuntik ke GrafikPartai supaya modul grafik tidak perlu tahu apa-apa
     soal autoplay maupun navigasi langkah. */
  function klikGrafik(i) {
    hentikanAuto();
    keIndeks(i);
  }

  function hentikanAuto() {
    if (timerAuto) {
      clearInterval(timerAuto);
      timerAuto = null;
      el.autoplay.textContent = "▶";
    }
  }

  function togglelAuto() {
    if (timerAuto) { hentikanAuto(); return; }
    if (indeks >= posisi.length - 1) keIndeks(0);
    el.autoplay.textContent = "⏸";
    timerAuto = setInterval(function () { keIndeks(indeks + 1); }, 800);
  }

  /* ---------- mulai ---------- */

  fetch("/api/partai/" + idDariUrl())
    .then(function (r) {
      if (r.status === 404) throw new Error("Partai ini tidak ada di database.");
      if (!r.ok) throw new Error("Server menjawab " + r.status);
      return r.json();
    })
    .then(function (g) {
      document.title = g.putih + " vs " + g.hitam + " — McLung ChessBase";
      InfoPartai.gambar(el.info, g);
      NavigasiPartai.siapkan(g.id);

      if (!siapkanPosisi(g.pgn)) {
        tampilkanGalat("PGN partai ini tidak bisa dibaca, jadi papan tidak " +
                       "bisa ditampilkan. Informasi partainya tetap ada di samping.");
        return;
      }

      papan = Chessboard("papan", {
        position: posisi[0],
        pieceTheme: "/static/img/chesspieces/{piece}.png",
        showNotation: true,
        draggable: true,
        onDragStart: bolehGeserPartai,
        onDrop: saatJatuhPartai,
        onSnapEnd: function () { papan.position(fenSaatIni()); }
      });
      window.addEventListener("resize", function () { papan.resize(); });
      if (window.KlikPapan) {
        klik = KlikPapan(document.getElementById("papan"), {
          orientasi: function () { return papan.orientation(); },
          saatKlik: klikKotakPartai
        });
      }

      gambarDaftarLangkah();
      GrafikPartai.gambarJam(g, klikGrafik);
      keIndeks(0);
      muatAnalisis(g);
      cariKeluarTeori(g);

      /* Kait pengujian tanpa mouse (Chrome headless): ?uji_klik=e2,e4 */
      var ujiKlik = new URLSearchParams(window.location.search).get("uji_klik");
      if (ujiKlik && klik) {
        ujiKlik.split(",").forEach(function (sq, i) {
          setTimeout(function () { klikKotakPartai(sq.trim()); }, 200 + 30 * i);
        });
      }
    })
    .catch(function (e) {
      el.info.textContent = "";
      tampilkanGalat("Gagal memuat partai: " + e.message);
    });

  /* ---------- kendali ---------- */

  document.getElementById("ke-awal").addEventListener("click", function () { hentikanAuto(); keIndeks(0); });
  document.getElementById("mundur").addEventListener("click", function () { hentikanAuto(); keIndeks(indeks - 1); });
  document.getElementById("maju").addEventListener("click", function () { hentikanAuto(); keIndeks(indeks + 1); });
  document.getElementById("ke-akhir").addEventListener("click", function () { hentikanAuto(); keIndeks(posisi.length - 1); });
  document.getElementById("autoplay").addEventListener("click", togglelAuto);
  document.getElementById("balik").addEventListener("click", function () { if (papan) papan.flip(); });

  document.addEventListener("keydown", function (ev) {
    if (ev.target.tagName === "INPUT" || !papan) return;
    /* Ctrl+P (cetak), Ctrl+F (cari), Alt+←: milik peramban, bukan pintasan
       halaman ini (audit 27 Sep 2026). */
    if (ev.ctrlKey || ev.metaKey || ev.altKey) return;
    var tangani = true;
    switch (ev.key) {
      case "ArrowLeft":  hentikanAuto(); if (variasi) undoVariasi(); else keIndeks(indeks - 1); break;
      case "ArrowRight": hentikanAuto(); if (!variasi) keIndeks(indeks + 1); break;
      case "Escape":     if (variasi) { tutupVariasi(); keIndeks(indeks); } else if (klik) { klik.hapusSorot(); terpilih = null; } break;
      case "Home":       hentikanAuto(); keIndeks(0); break;
      case "End":        hentikanAuto(); keIndeks(posisi.length - 1); break;
      case "f": case "F": papan.flip(); break;
      case "n": case "N": NavigasiPartai.keBerikutnya(); break;
      case "p": case "P": NavigasiPartai.keSebelumnya(); break;
      case " ":          togglelAuto(); break;
      default: tangani = false;
    }
    if (tangani) ev.preventDefault();
  });
})();
