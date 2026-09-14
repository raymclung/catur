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

  var TANDA = { blunder: "??", mistake: "?", inaccuracy: "?!", terbaik: "!" };
  var NAMA_KLAS = {
    blunder: "Blunder", mistake: "Kesalahan", inaccuracy: "Kurang akurat",
    terbaik: "Langkah terbaik", baik: "Baik", buku: "Teori pembukaan"
  };

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

    var ulang = new Chess();
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
    for (var i = 0; i < langkah.length; i++) {
      if (i % 2 === 0) {
        el.daftar.appendChild(buat("span", "nomor", (i / 2 + 1) + "."));
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
    gambarPenandaJam();
    perbaruiPenjelajah();
    if (indeks >= posisi.length - 1) hentikanAuto();
  }

  /* ---------- keluar dari praktik GM (RENCANA Phase 7: bandingkan dengan GM) ----------
     Berjalan ply demi ply lewat /api/posisi?sumber=gm sampai posisi tidak ada
     lagi di indeks GM (paling jauh 40 ply). Partai yang sendiri bersumber GM
     (TWIC/broadcast) selalu menghitung dirinya, jadi ambangnya 1, bukan 0. */

  var elT = document.getElementById("teori-gm");
  var MAKS_PLY_TEORI = 40;

  function cariKeluarTeori(g) {
    if (!elT || !langkah.length) return;
    var sendiri = (g.sumber === "twic" || g.sumber === "lichess-broadcast") ? 1 : 0;
    var maks = Math.min(MAKS_PLY_TEORI, langkah.length);
    var i = 0;
    var terakhir = null;   // {i, d}: posisi terakhir yang masih ada di praktik GM

    function selesai() { gambarTeori(terakhir, i, maks, sendiri); }
    function periksa() {
      if (i > maks) { selesai(); return; }
      fetch("/api/posisi?fen=" + encodeURIComponent(posisi[i]) + "&sumber=gm")
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (d) {
          if (!d || d.total - sendiri <= 0) { selesai(); return; }
          terakhir = { i: i, d: d };
          i++;
          periksa();
        })
        .catch(selesai);
    }
    periksa();
  }

  function gambarTeori(terakhir, iKeluar, maks, sendiri) {
    elT.textContent = "";
    var h = buat("h2", null, "Praktik GM");
    elT.appendChild(h);

    if (!terakhir) {
      elT.appendChild(buat("div", "kecil redup", "Posisi awal tidak ada di indeks GM (indeks belum dibangun?)."));
      elT.hidden = false;
      return;
    }
    if (iKeluar > maks) {
      elT.appendChild(buat("div", "kecil redup", maks + " langkah pertama semuanya ada dalam praktik GM."));
      elT.hidden = false;
      return;
    }

    /* langkah[terakhir.i] = langkah yang pertama kali keluar dari praktik GM */
    var l = langkah[terakhir.i];
    var nomor = Math.floor(terakhir.i / 2) + 1;
    var label = (terakhir.i % 2 === 0 ? nomor + "." : nomor + "…") + l.san;
    var baris = buat("div", null);
    baris.appendChild(buat("span", null, sendiri ? "Langkah baru di luar database GM: " : "Keluar dari praktik GM di "));
    var tautan = buat("span", "langkah", label);
    tautan.title = "Lompat ke langkah ini";
    tautan.addEventListener("click", function () { hentikanAuto(); keIndeks(terakhir.i + 1); });
    baris.appendChild(tautan);
    baris.appendChild(buat("span", "redup kecil", "  · posisi sebelumnya ada di " + (terakhir.d.total - sendiri).toLocaleString("id-ID") + " partai GM lain"));
    elT.appendChild(baris);

    /* Partai bersumber GM ikut terhitung di indeks: kurangi langkahnya sendiri
       supaya "GM memainkan" hanya berisi partai lain. */
    var alternatif = terakhir.d.langkah.map(function (m) {
      return sendiri && m.san === l.san ? { san: m.san, uci: m.uci, n: m.n - 1, skor_putih: m.skor_putih } : m;
    }).filter(function (m) { return m.n > 0; });
    var alt = buat("div", "teori-langkah");
    alt.appendChild(buat("span", "redup kecil", alternatif.length ? "GM lain memainkan:" : "Tidak ada partai GM lain dari posisi ini."));
    alternatif.slice(0, 4).forEach(function (m) {
      var s = buat("span", "langkah", m.san);
      s.title = "Mainkan sebagai variasi";
      s.addEventListener("click", function () {
        hentikanAuto();
        keIndeks(terakhir.i);
        if (m.uci && typeof mainkanVariasi === "function") mainkanVariasi(m.uci.slice(0, 2), m.uci.slice(2, 4));
      });
      alt.appendChild(s);
      alt.appendChild(buat("span", "n", m.n.toLocaleString("id-ID") + "× · " + (m.skor_putih === null ? "—" : m.skor_putih + "%")));
    });
    var j = document.createElement("a");
    j.href = "/jelajah?fen=" + encodeURIComponent(posisi[terakhir.i]);
    j.className = "kecil";
    j.appendChild(teks("jelajah →"));
    alt.appendChild(j);
    elT.appendChild(alt);

    var tanda = el.daftar.querySelector('.langkah[data-indeks="' + (terakhir.i + 1) + '"]');
    if (tanda) {
      tanda.classList.add("keluar-teori");
      tanda.title = (tanda.title ? tanda.title + " · " : "") + "keluar dari praktik GM";
    }
    elT.hidden = false;
  }

  /* ---------- penjelajah pembukaan (mengikuti posisi papan) ----------
     Ditunda sedikit supaya menahan → tidak memicu satu permintaan per ply. */

  var elJ = {
    tabel: document.getElementById("penjelajah-tabel"),
    tautan: document.getElementById("tautan-jelajah"),
    tautanDaftar: document.getElementById("tautan-daftar-posisi")
  };

  /* Bar + = − seperti di /jelajah, versi ringkas. */
  function selBarHasil(l) {
    var td = document.createElement("td");
    var selesai = l.menang_putih + l.remis + l.menang_hitam;
    if (!selesai) return td;
    var w = document.createElement("div");
    w.style.cssText = "display:flex;height:12px;width:110px;border-radius:3px;overflow:hidden;border:1px solid #ccc";
    [[l.menang_putih, "#f4f4f2"], [l.remis, "#a5a5a0"], [l.menang_hitam, "#3a3a3a"]].forEach(function (x) {
      var s = document.createElement("div");
      s.style.cssText = "width:" + (100 * x[0] / selesai) + "%;background:" + x[1];
      s.title = x[0];
      w.appendChild(s);
    });
    td.appendChild(w);
    td.appendChild(buat("span", "redup kecil", " " + l.menang_putih + "/" + l.remis + "/" + l.menang_hitam));
    return td;
  }

  /* Tautan ke daftar partai yang mencapai posisi SETELAH langkah san. */
  function selTautanDaftar(fen, san) {
    var td = buat("td", "kecil");
    td.style.whiteSpace = "nowrap";
    var c = new Chess(fen);
    if (!c.move(san)) return td;
    var a = document.createElement("a");
    a.href = "/?fen=" + encodeURIComponent(c.fen());
    a.title = "Daftar partai yang mencapai posisi setelah " + san;
    a.appendChild(teks("daftar →"));
    td.appendChild(a);
    return td;
  }
  var timerJelajah = null;
  var permintaanJelajah = 0;

  function perbaruiPenjelajah() {
    if (!elJ.tabel) return;
    var fen = fenSaatIni();
    elJ.tautan.href = "/jelajah?fen=" + encodeURIComponent(fen);
    if (elJ.tautanDaftar) elJ.tautanDaftar.href = "/?fen=" + encodeURIComponent(fen);
    clearTimeout(timerJelajah);
    timerJelajah = setTimeout(function () {
      var ke = ++permintaanJelajah;
      fetch("/api/posisi?fen=" + encodeURIComponent(fen) + "&sumber=gm")
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (d) {
          if (ke !== permintaanJelajah) return;
          elJ.tabel.textContent = "";
          if (!d || d.langkah.length === 0) {
            var tr0 = document.createElement("tr");
            var td0 = buat("td", "pesan kecil", d ? "Tidak ada partai GM dari posisi ini." : "Indeks posisi belum dibangun (python indeks_posisi.py).");
            td0.colSpan = 6;
            tr0.appendChild(td0);
            elJ.tabel.appendChild(tr0);
            return;
          }
          d.langkah.slice(0, 8).forEach(function (l) {
            var tr = document.createElement("tr");
            tr.style.cursor = "default";
            var dimainkan = !variasi && (langkahAnalisis(indeks + 1) ? langkahAnalisis(indeks + 1).uci === l.uci
                          : (langkah[indeks] && langkah[indeks].from + langkah[indeks].to === l.uci.substr(0, 4)));
            var td = buat("td", "mono", l.san);
            if (dimainkan) td.appendChild(buat("span", "redup kecil", "  ← dimainkan"));
            tr.appendChild(td);
            tr.appendChild(buat("td", "mono", l.n + "  (" + Math.round(100 * l.n / d.total) + "%)"));
            tr.appendChild(buat("td", "mono", l.skor_putih === null ? "—" : l.skor_putih + "%"));
            tr.appendChild(selBarHasil(l));
            tr.appendChild(buat("td", "mono", l.elo_rata || "—"));
            tr.appendChild(selTautanDaftar(fen, l.san));
            elJ.tabel.appendChild(tr);
          });
        })
        .catch(function () { /* kartu penjelajah opsional */ });
    }, 250);
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

  function mainkanVariasi(dari, ke) {
    var baru = !variasi;
    var g = baru ? new Chess(posisi[indeks]) : variasi.permainan;
    var m = g.move({ from: dari, to: ke, promotion: "q" });
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
    var dasar = variasi.dari === 0 ? "posisi awal"
      : "langkah " + Math.ceil(variasi.dari / 2) + (variasi.dari % 2 ? "." : "...") + " " + langkah[variasi.dari - 1].san;
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
    var la = langkahAnalisis(indeks);
    elA.komentar.textContent = "";
    if (!la) {
      elA.komentar.appendChild(teks("Posisi awal. Tekan → untuk mulai."));
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
    elA.komentar.appendChild(judul);

    if (la.langkah_terbaik_san && la.langkah_terbaik_san !== la.san && la.klasifikasi !== "buku") {
      var v = buat("div", "variasi");
      v.appendChild(buat("strong", null, "Seharusnya " + la.langkah_terbaik_san));
      if (la.variasi_terbaik && la.variasi_terbaik.length > 1) {
        v.appendChild(teks("  →  " + la.variasi_terbaik.join(" ")));
      }
      elA.komentar.appendChild(v);
    }
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
    gambarPenandaGrafik();
  }

  function hiasDaftarLangkah() {
    var semua = el.daftar.querySelectorAll(".langkah");
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

  /* ---------- grafik jam (dari komentar %clk di PGN) ----------
     Sisa waktu Putih dan Hitam setelah tiap langkah. Garis merah = 10 detik,
     batas zeitnot yang dipakai halaman kekalahan. Tidak tampil kalau PGN
     tidak punya jam (mis. partai TWIC). */

  var elJam = { kartu: document.getElementById("jam-kartu"), grafik: document.getElementById("grafik-jam"), ket: document.getElementById("jam-ket") };
  var jamPly = [];        // detik setelah ply i (indeks 0 = setelah langkah pertama)
  var ZEITNOT = 10;

  function bacaJam(pgn) {
    var badan = pgn.replace(/\[(\w+) "([^"]*)"\]\s*/g, "");
    var re = /\{[^}]*%clk\s+(\d+):(\d\d):(\d\d(?:\.\d+)?)[^}]*\}/g, m, hasil = [];
    while ((m = re.exec(badan)) !== null) hasil.push(parseInt(m[1], 10) * 3600 + parseInt(m[2], 10) * 60 + parseFloat(m[3]));
    return hasil;
  }

  function formatDetik(d) {
    if (d >= 60) return Math.floor(d / 60) + ":" + ("0" + Math.floor(d % 60)).slice(-2);
    return (Math.round(d * 10) / 10) + " s";
  }

  function gambarGrafikJam(g) {
    if (!elJam.kartu) return;
    jamPly = bacaJam(g.pgn || "");
    if (jamPly.length < 4) return;
    var awal = parseInt(String(g.kontrol_waktu || "").split("+")[0], 10);
    var maks = Math.max(isNaN(awal) ? 0 : awal, Math.max.apply(null, jamPly));
    if (!maks) return;
    var T = 80, L = 600, n = jamPly.length;
    var x = function (i) { return ((i + 1) / n) * L; };
    var y = function (d) { return T - 4 - (Math.max(0, Math.min(maks, d)) / maks) * (T - 8); };
    var svg = elJam.grafik;
    svg.textContent = "";
    svg.appendChild(svgEl("rect", { x: 0, y: 0, width: L, height: T, fill: "#fafaf8" }));
    if (maks > 60) svg.appendChild(svgEl("line", { x1: 0, y1: y(ZEITNOT), x2: L, y2: y(ZEITNOT), stroke: "#b3261e", "stroke-width": "0.8", "stroke-dasharray": "3,3" }));
    [["putih", "#8a8a85"], ["hitam", "#2f2f2f"]].forEach(function (sisi, k) {
      var titik = [];
      for (var i = k; i < n; i += 2) titik.push(x(i) + "," + y(jamPly[i]));
      svg.appendChild(svgEl("polyline", { points: titik.join(" "), fill: "none", stroke: sisi[1], "stroke-width": "1.4" }));
    });
    svg.appendChild(svgEl("line", { id: "penanda-jam", x1: 0, y1: 0, x2: 0, y2: T, stroke: "#2f6f4f", "stroke-width": "1.5" }));
    svg.onclick = function (ev) {
      var kotak = svg.getBoundingClientRect();
      hentikanAuto();
      keIndeks(Math.round(((ev.clientX - kotak.left) / kotak.width) * n));
    };

    /* keterangan: langkah pertama masing-masing pihak masuk zeitnot */
    var ket = [];
    [["Putih", 0], ["Hitam", 1]].forEach(function (s) {
      var pertama = null;
      for (var i = s[1]; i < n; i += 2) { if (jamPly[i] < ZEITNOT) { pertama = i; break; } }
      ket.push(s[0] + ": " + (pertama === null ? "tidak pernah < 10 s" : "< 10 s sejak langkah " + (Math.floor(pertama / 2) + 1)) +
        " · akhir " + formatDetik(jamPly[n - 1 - ((n - 1 - s[1]) % 2)]));
    });
    elJam.ket.textContent = ket.join("   ·   ");
    elJam.kartu.hidden = false;
  }

  function gambarPenandaJam() {
    var p = document.getElementById("penanda-jam");
    if (!p || !jamPly.length) return;
    var xx = (indeks / jamPly.length) * 600;
    p.setAttribute("x1", xx); p.setAttribute("x2", xx);
    if (elJam.ket && indeks > 0 && jamPly[indeks - 1] !== undefined) {
      elJam.ket.title = "Setelah langkah ini: " + formatDetik(jamPly[indeks - 1]) + " tersisa";
    }
  }

  /* ---------- grafik evaluasi (SVG inline, tanpa library) ---------- */

  var LEBAR = 600, TINGGI = 120, BATAS = 1000;

  function yDariCp(cp, mat) {
    var v = cp;
    if (mat !== null && mat !== undefined) v = mat === 0 ? cp : (mat > 0 ? BATAS : -BATAS);
    v = Math.max(-BATAS, Math.min(BATAS, v));
    return TINGGI / 2 - (v / BATAS) * (TINGGI / 2 - 4);
  }

  function svgEl(tag, atribut) {
    var e = document.createElementNS("http://www.w3.org/2000/svg", tag);
    for (var k in atribut) e.setAttribute(k, atribut[k]);
    return e;
  }

  function gambarGrafik() {
    var g = elA.grafik;
    g.textContent = "";
    var n = analisis.langkah.length;
    if (n === 0) return;

    var xDari = function (i) { return (i / n) * LEBAR; };

    /* latar: atas = Putih unggul, bawah = Hitam unggul */
    g.appendChild(svgEl("rect", { x: 0, y: 0, width: LEBAR, height: TINGGI / 2, fill: "#f4f4f2" }));
    g.appendChild(svgEl("rect", { x: 0, y: TINGGI / 2, width: LEBAR, height: TINGGI / 2, fill: "#3a3a3a" }));

    var titik = ["0," + yDariCp(analisis.langkah[0].eval_sebelum_cp * (analisis.langkah[0].warna === "putih" ? 1 : -1), null)];
    for (var i = 0; i < n; i++) {
      var la = analisis.langkah[i];
      titik.push(xDari(i + 1) + "," + yDariCp(la.eval_putih_cp, la.mat_sesudah));
    }
    var area = svgEl("polygon", {
      points: "0," + TINGGI + " " + titik.join(" ") + " " + LEBAR + "," + TINGGI,
      fill: "#e8e8e4", opacity: "0.9"
    });
    g.appendChild(area);
    g.appendChild(svgEl("polyline", { points: titik.join(" "), fill: "none", stroke: "#6b6b6b", "stroke-width": "1.2" }));
    g.appendChild(svgEl("line", { x1: 0, y1: TINGGI / 2, x2: LEBAR, y2: TINGGI / 2, stroke: "#999", "stroke-width": "0.6" }));

    for (var j = 0; j < n; j++) {
      var lb = analisis.langkah[j];
      if (lb.klasifikasi === "blunder" || lb.klasifikasi === "mistake") {
        g.appendChild(svgEl("circle", {
          cx: xDari(j + 1), cy: yDariCp(lb.eval_putih_cp, lb.mat_sesudah), r: 3,
          fill: lb.klasifikasi === "blunder" ? "#b3261e" : "#c2620b"
        }));
      }
    }
    if (analisis.turning_point_ply !== null && analisis.turning_point_ply !== undefined) {
      var xt = xDari(analisis.turning_point_ply + 1);
      g.appendChild(svgEl("line", { x1: xt, y1: 0, x2: xt, y2: TINGGI, stroke: "#b3261e", "stroke-width": "1", "stroke-dasharray": "3,3" }));
    }
    g.appendChild(svgEl("line", { id: "penanda-grafik", x1: 0, y1: 0, x2: 0, y2: TINGGI, stroke: "#2f6f4f", "stroke-width": "1.5" }));

    g.onclick = function (ev) {
      var kotak = g.getBoundingClientRect();
      var i = Math.round(((ev.clientX - kotak.left) / kotak.width) * n);
      hentikanAuto();
      keIndeks(i);
    };
    elA.grafikKartu.hidden = false;
  }

  function gambarPenandaGrafik() {
    var p = document.getElementById("penanda-grafik");
    if (!p || !analisis) return;
    var x = (indeks / analisis.langkah.length) * LEBAR;
    p.setAttribute("x1", x); p.setAttribute("x2", x);
  }

  /* Ayunan peluang menang terbesar (dari sudut pandang pihak yang jalan),
     paling banyak lima, diurutkan menurut nomor langkah. Ambang 10 poin
     supaya ketidakakuratan kecil tidak ikut. */
  function momenKritis() {
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

  function gambarMomenKritis(r) {
    var momen = momenKritis();
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
      baris.addEventListener("click", function () { hentikanAuto(); keIndeks(l.ply + 1); });
      wadah.appendChild(baris);
    });
    r.appendChild(wadah);
  }

  function gambarRingkasanAnalisis(g) {
    var r = elA.ringkasan;
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
        tautan.addEventListener("click", function () { hentikanAuto(); keIndeks(tp.ply + 1); });
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
    gambarMomenKritis(r);
    r.hidden = false;
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
        gambarGrafik();
        gambarRingkasanAnalisis(g);
        perbaruiAnalisisTampilan();
      })
      .catch(function (e) {
        var r = elA.ringkasan;
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
      });
  }

  /* ---------- autoplay ---------- */

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

  /* ---------- info partai ---------- */

  function namaLengkap(nama, gelar, elo, terverifikasi, pemainId) {
    var f = document.createDocumentFragment();
    if (gelar) f.appendChild(buat("span", "gelar", gelar));
    if (pemainId) {
      var a = document.createElement("a");
      a.href = "/pemain/" + pemainId;
      a.appendChild(teks(nama));
      f.appendChild(a);
    } else {
      f.appendChild(teks(nama));
    }
    if (terverifikasi) {
      var v = buat("span", "tercentang", " ✓");
      v.title = "Identitas dipastikan lewat FIDE ID";
      f.appendChild(v);
    }
    if (elo) f.appendChild(buat("span", "redup mono", " (" + elo + ")"));
    return f;
  }

  function gambarInfo(g) {
    el.info.textContent = "";

    /* Kepala pertandingan: Putih · hasil · Hitam, seperti papan skor turnamen. */
    var laga = buat("div", "laga");
    function sisi(nama, gelar, elo, terverifikasi, id, warna) {
      var s = buat("div", "laga-sisi " + warna);
      var n = buat("div", "laga-nama");
      n.appendChild(namaLengkap(nama, gelar, null, terverifikasi, id));
      s.appendChild(n);
      s.appendChild(buat("div", "kecil redup", (warna === "putih" ? "Putih" : "Hitam") + (elo ? " · " + elo : "")));
      return s;
    }
    laga.appendChild(sisi(g.putih, g.putih_gelar, g.elo_putih, g.putih_terverifikasi, g.putih_id, "putih"));
    var hasil = buat("div", "laga-hasil hasil " +
      (g.hasil === "1-0" ? "hasil-1-0" :
       g.hasil === "0-1" ? "hasil-0-1" :
       g.hasil === "*" ? "hasil-jalan" : "hasil-remis"),
      g.hasil === "*" ? "…" : g.hasil === "1/2-1/2" ? "½–½" : g.hasil.replace("-", "–"));
    hasil.title = g.hasil === "*" ? "sedang berjalan" : "hasil " + g.hasil;
    laga.appendChild(hasil);
    laga.appendChild(sisi(g.hitam, g.hitam_gelar, g.elo_hitam, g.hitam_terverifikasi, g.hitam_id, "hitam"));
    el.info.appendChild(laga);

    var dl = document.createElement("dl");
    function baris(label, nilai, tautan) {
      if (!nilai) return;
      dl.appendChild(buat("dt", null, label));
      var dd = document.createElement("dd");
      if (tautan) {
        var a = document.createElement("a");
        a.href = nilai; a.target = "_blank"; a.rel = "noopener noreferrer";
        a.appendChild(teks("buka sumber"));
        dd.appendChild(a);
      } else {
        dd.appendChild(teks(nilai));
      }
      dl.appendChild(dd);
    }
    baris("Turnamen", g.turnamen);
    baris("Tanggal", g.tanggal);
    baris("Babak", g.babak);
    baris("Pembukaan", [g.eco, g.pembukaan].filter(Boolean).join(" — "));
    baris("Kontrol waktu", AlatCatur.kontrolPartai(g));
    baris("Jumlah langkah", g.jumlah_langkah);
    baris("Sumber", g.sumber);
    baris("Tautan", g.sumber_url, true);
    el.info.appendChild(dl);
  }

  /* ---------- navigasi partai sebelumnya/berikutnya dalam daftar ----------
     Halaman daftar mengirim saringannya lewat ?daftar=<query /api/partai>.
     Halaman ini memuat ulang daftar itu untuk menemukan tetangga partai
     sekarang; di tepi halaman, halaman sebelah ikut diambil. Tidak butuh
     endpoint baru, dan tanpa parameter itu kartu navigasi tetap tersembunyi. */

  var elN = document.getElementById("navigasi-daftar");
  var tetangga = { sebelum: null, sesudah: null };

  function urlPartai(id, q) { return "/partai/" + id + "?daftar=" + encodeURIComponent(q.toString()); }

  function ambilDaftar(q) {
    return fetch("/api/partai?" + q.toString()).then(function (r) { return r.ok ? r.json() : null; });
  }

  function halamanTepi(q, halaman, ujung) {
    if (halaman < 1) return Promise.resolve(null);
    var q2 = new URLSearchParams(q.toString());
    q2.set("halaman", halaman);
    return ambilDaftar(q2).then(function (d) {
      if (!d || d.partai.length === 0) return null;
      var g = ujung === "awal" ? d.partai[0] : d.partai[d.partai.length - 1];
      return { id: g.id, q: q2 };
    });
  }

  function siapkanNavigasi(idSekarang) {
    var qs = new URLSearchParams(window.location.search).get("daftar");
    if (!qs || !elN) return;
    var q = new URLSearchParams(qs);
    var halaman = parseInt(q.get("halaman") || "1", 10);
    ambilDaftar(q).then(function (d) {
      if (!d) return;
      var ids = d.partai.map(function (g) { return g.id; });
      var i = ids.indexOf(idSekarang);
      if (i === -1) return;   // daftar berubah sejak diklik; tanpa navigasi
      var nomor = (d.halaman - 1) * d.per_halaman + i + 1;
      var sebelum = i > 0 ? Promise.resolve({ id: ids[i - 1], q: q }) : halamanTepi(q, halaman - 1, "akhir");
      var sesudah = i < ids.length - 1 ? Promise.resolve({ id: ids[i + 1], q: q })
                  : (halaman < d.jumlah_halaman ? halamanTepi(q, halaman + 1, "awal") : Promise.resolve(null));
      return Promise.all([sebelum, sesudah]).then(function (t) {
        tetangga.sebelum = t[0];
        tetangga.sesudah = t[1];
        gambarNavigasi(q, nomor, d.total);
      });
    }).catch(function () { /* navigasi opsional */ });
  }

  function gambarNavigasi(q, nomor, total) {
    elN.textContent = "";
    function tautan(label, t, judul) {
      var a = document.createElement("a");
      a.appendChild(teks(label));
      if (t) { a.href = urlPartai(t.id, t.q); a.title = judul; } else { a.className = "redup"; }
      return a;
    }
    var kembali = new URLSearchParams(q.toString());
    kembali.delete("per_halaman");
    if (kembali.get("halaman") === "1") kembali.delete("halaman");
    var ak = document.createElement("a");
    ak.href = "/?" + kembali.toString();
    ak.appendChild(teks("kembali ke daftar · partai " + nomor.toLocaleString("id-ID") + " dari " + total.toLocaleString("id-ID")));
    elN.appendChild(tautan("‹ sebelumnya", tetangga.sebelum, "Partai sebelumnya dalam daftar (P)"));
    elN.appendChild(teks("   "));
    elN.appendChild(ak);
    elN.appendChild(teks("   "));
    elN.appendChild(tautan("berikutnya ›", tetangga.sesudah, "Partai berikutnya dalam daftar (N)"));
    elN.hidden = false;
  }

  function keTetangga(t) { if (t) window.location.href = urlPartai(t.id, t.q); }

  /* ---------- mulai ---------- */

  fetch("/api/partai/" + idDariUrl())
    .then(function (r) {
      if (r.status === 404) throw new Error("Partai ini tidak ada di database.");
      if (!r.ok) throw new Error("Server menjawab " + r.status);
      return r.json();
    })
    .then(function (g) {
      document.title = g.putih + " vs " + g.hitam + " — McLung ChessBase";
      gambarInfo(g);
      siapkanNavigasi(g.id);

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
      gambarGrafikJam(g);
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
    var tangani = true;
    switch (ev.key) {
      case "ArrowLeft":  hentikanAuto(); if (variasi) undoVariasi(); else keIndeks(indeks - 1); break;
      case "ArrowRight": hentikanAuto(); if (!variasi) keIndeks(indeks + 1); break;
      case "Escape":     if (variasi) { tutupVariasi(); keIndeks(indeks); } else if (klik) { klik.hapusSorot(); terpilih = null; } break;
      case "Home":       hentikanAuto(); keIndeks(0); break;
      case "End":        hentikanAuto(); keIndeks(posisi.length - 1); break;
      case "f": case "F": papan.flip(); break;
      case "n": case "N": keTetangga(tetangga.sesudah); break;
      case "p": case "P": keTetangga(tetangga.sebelum); break;
      case " ":          togglelAuto(); break;
      default: tangani = false;
    }
    if (tangani) ev.preventDefault();
  });
})();
