/* Papan analisis: susun posisi bebas, FEN dua arah, evaluasi engine langsung.

   Dua mode:
   - "main"  : langkah harus sah; chess.js adalah sumber kebenaran posisi.
   - "susun" : bidak boleh ditaruh/dibuang bebas (chessboard.js sparePieces);
               FEN dirakit dari papan + giliran + hak rokade dari kontrol.
   chessboard.js tidak bisa mengubah sparePieces saat hidup, jadi ganti mode =
   papan dibangun ulang.

   Engine dipanggil lewat GET /api/evaluasi (kontrak: SESI.md Permintaan API #4),
   atau — kalau Ray memilih "Engine: perangkat ini" (9 Okt 2026) — lewat
   web/engine-browser.js, Stockfish WASM di browser yang mengembalikan bentuk
   JSON yang sama. Terus (aliran) hanya untuk engine laptop. */

(function () {
  "use strict";

  var papan = null;
  var mode = "main";
  var permainan = new Chess();
  var orientasi = "white";
  var permintaan = null;        // AbortController evaluasi yang sedang berjalan
  var fenTerus = null;          // FEN yang sedang dianalisis tanpa henti (null = tidak)
  var timerAuto = null;
  var garisTerakhir = [];       // hasil evaluasi terakhir, dipakai saat klik langkah
  var fenTerakhir = null;       // FEN yang dievaluasi garisTerakhir
  var terpilih = null;          // kotak yang dipilih lewat klik (klik-untuk-jalan)
  var klik = null;              // KlikPapan (web/klik-papan.js), dipasang sekali di bawah

  var el = {
    galat: document.getElementById("galat"),
    fen: document.getElementById("fen"),
    statusPosisi: document.getElementById("status-posisi"),
    statusEngine: document.getElementById("status-engine"),
    garis: document.getElementById("garis"),
    barIsi: document.getElementById("eval-bar-isi"),
    barAngka: document.getElementById("eval-angka"),
    modeMain: document.getElementById("mode-main"),
    modeSusun: document.getElementById("mode-susun"),
    kotakSusun: document.getElementById("kotak-susun"),
    giliran: document.getElementById("giliran"),
    rokade: {
      K: document.getElementById("rokade-K"),
      Q: document.getElementById("rokade-Q"),
      k: document.getElementById("rokade-k"),
      q: document.getElementById("rokade-q")
    },
    engineSkor: document.getElementById("engine-skor"),
    engineMeta: document.getElementById("engine-meta"),
    analisa: document.getElementById("analisa"),
    otomatis: document.getElementById("otomatis"),
    multipv: document.getElementById("multipv"),
    depth: document.getElementById("depth"),
    terus: document.getElementById("terus"),
    engine: document.getElementById("engine")
  };

  var KUNCI_ENGINE = "catur.analisis.engine";   // pilihan engine, per perangkat

  function engineBrowser() { return el.engine.value === "browser"; }

  /* ---------- util ---------- */

  function buat(tag, kelas, isi) {
    var e = document.createElement(tag);
    if (kelas) e.className = kelas;
    if (isi !== undefined && isi !== null) e.appendChild(document.createTextNode(String(isi)));
    return e;
  }

  function galat(pesan) {
    if (!pesan) { el.galat.hidden = true; el.galat.textContent = ""; return; }
    el.galat.textContent = pesan;
    el.galat.hidden = false;
  }

  /* Baris status engine hanya untuk proses dan kegagalan; hasil yang berhasil
     ditampilkan lewat angka besar + meta, bukan kalimat. */
  function statusEngine(pesan, gagal) {
    el.statusEngine.textContent = pesan || "";
    el.statusEngine.className = "engine-status" + (gagal ? " gagal" : "");
  }

  function skorBesar(teks, kelas) {
    el.engineSkor.textContent = teks;
    el.engineSkor.className = "engine-skor" + (kelas ? " " + kelas : "");
  }

  /* ---------- FEN ---------- */

  function fenSekarang() {
    if (mode === "main") return permainan.fen();
    var rokade = "";
    ["K", "Q", "k", "q"].forEach(function (h) { if (el.rokade[h].checked) rokade += h; });
    return papan.fen() + " " + el.giliran.value + " " + (rokade || "-") + " - 0 1";
  }

  /* Cukup sah untuk dimainkan dan dievaluasi: chess.js menerima, dan tepat satu
     raja per warna (chess.js load() tidak memeriksa jumlah raja). Pemeriksaan
     lebih dalam (skak pada pihak yang tidak jalan, dsb.) diserahkan ke server
     yang menjawab 422. */
  function alasanTidakSah(fen) {
    var penempatan = fen.split(/\s+/)[0] || "";
    var putih = (penempatan.match(/K/g) || []).length;
    var hitam = (penempatan.match(/k/g) || []).length;
    if (putih !== 1 || hitam !== 1) return "raja Putih " + putih + ", Hitam " + hitam + " (harus 1)";
    if (!new Chess().load(fen)) return "FEN tidak sah";
    return null;
  }

  function isiKontrolDariFen(fen) {
    var bagian = fen.split(/\s+/);
    el.giliran.value = bagian[1] === "b" ? "b" : "w";
    var r = bagian[2] || "-";
    ["K", "Q", "k", "q"].forEach(function (h) { el.rokade[h].checked = r.indexOf(h) >= 0; });
  }

  /* Dipanggil tiap posisi berubah, dari sumber mana pun. */
  function perbaruiFen() {
    var fen = fenSekarang();
    el.fen.value = fen;

    var u = new URL(window.location.href);
    u.searchParams.set("fen", fen);
    window.history.replaceState(null, "", u.toString());

    var giliran = fen.split(/\s+/)[1] === "b" ? "Hitam" : "Putih";
    var masalah = mode === "susun" ? alasanTidakSah(fen) : null;
    if (masalah) {
      el.statusPosisi.textContent = "Belum sah · " + masalah;
    } else if (mode === "main" && permainan.game_over()) {
      el.statusPosisi.textContent = permainan.in_checkmate()
        ? "Skakmat · " + (giliran === "Putih" ? "Hitam" : "Putih") + " menang"
        : "Remis / pat";
    } else if (mode === "main" && permainan.in_check()) {
      el.statusPosisi.textContent = giliran + " jalan · skak";
    } else {
      el.statusPosisi.textContent = giliran + " jalan";
    }

    if (fen !== fenTerakhir) jadwalkanEvaluasi();
  }

  function terapkanFen(fen) {
    fen = (fen || "").trim();
    if (!fen) return false;
    galat(null);

    if (mode === "main") {
      var masalah = alasanTidakSah(fen);
      if (masalah) {
        galat("FEN tidak bisa dimainkan: " + masalah + ". Gunakan mode Susun.");
        return false;
      }
      permainan.load(fen);
      papan.position(permainan.fen(), false);
    } else {
      var bagian = fen.split(/\s+/);
      papan.position(bagian[0], false);      // chessboard hanya memakai bagian penempatan
      isiKontrolDariFen(fen);
    }
    perbaruiFen();
    return true;
  }

  /* ---------- papan ---------- */

  function bolehGeser(source, piece) {
    hapusPilihan();
    if (mode !== "main") return true;
    if (permainan.game_over()) return false;
    return piece.charAt(0) === permainan.turn();
  }

  function saatJatuh(source, target) {
    if (PilihPromosi.tunda(permainan, source, target, function (p) { jalankan(source, target, p); })) {
      return "snapback";
    }
    var m = permainan.move({ from: source, to: target, promotion: "q" });
    if (m === null) return "snapback";
    perbaruiFen();
  }

  /* Langkah yang tertunda menunggu pilihan bidak promosi (PilihPromosi). */
  function jalankan(dari, ke, promosi) {
    if (!permainan.move({ from: dari, to: ke, promotion: promosi })) return;
    papan.position(permainan.fen());
    perbaruiFen();
  }

  function buatPapan(fenAwal) {
    if (papan) papan.destroy();
    var cfg = {
      position: fenAwal || "start",
      draggable: true,
      orientation: orientasi,
      pieceTheme: "/static/img/chesspieces/{piece}.png"
    };
    if (mode === "susun") {
      cfg.sparePieces = true;
      cfg.dropOffBoard = "trash";
      cfg.onDragStart = bolehGeser;
      cfg.onChange = function () { perbaruiFen(); };
    } else {
      cfg.onDragStart = bolehGeser;
      cfg.onDrop = saatJatuh;
      cfg.onSnapEnd = function () { papan.position(permainan.fen()); };
    }
    papan = Chessboard("papan", cfg);
  }

  function gantiMode(baru) {
    if (baru === mode) return;
    var fen = fenSekarang();
    galat(null);

    if (baru === "main") {
      var masalah = alasanTidakSah(fen);
      if (masalah) {
        galat("Posisi belum sah untuk mode Main: " + masalah + ".");
        el.modeSusun.checked = true;
        return;
      }
      permainan.load(fen);
      mode = "main";
      buatPapan(permainan.fen());
    } else {
      mode = "susun";
      isiKontrolDariFen(fen);
      buatPapan(fen);
    }
    el.kotakSusun.hidden = mode !== "susun";
    document.getElementById("undo").disabled = mode !== "main";
    perbaruiFen();
  }

  /* ---------- klik-untuk-jalan ----------
     Deteksi klik dan sorotan kotak ada di modul bersama web/klik-papan.js
     (dipakai juga halaman partai). Di sini hanya aturan permainannya. */

  function hapusPilihan() {
    terpilih = null;
    if (klik) klik.hapusSorot();
  }

  function pilihKotak(sq) {
    hapusPilihan();
    terpilih = sq;
    klik.sorotPilih(sq);
    if (mode !== "main") return;
    permainan.moves({ square: sq, verbose: true }).forEach(function (m) {
      klik.sorotTarget(m.to, !!m.captured);
    });
  }

  function bidakDi(sq) {
    if (mode === "main") return permainan.get(sq);           // {type, color} atau null
    var pos = papan.position();
    return pos[sq] ? { color: pos[sq].charAt(0), type: pos[sq].charAt(1).toLowerCase() } : null;
  }

  function klikKotak(sq) {
    if (!sq) { hapusPilihan(); return; }
    var bidak = bidakDi(sq);

    if (terpilih === null) {
      if (!bidak) return;
      if (mode === "main" && (permainan.game_over() || bidak.color !== permainan.turn())) return;
      pilihKotak(sq);
      return;
    }
    if (sq === terpilih) { hapusPilihan(); return; }

    if (mode === "main") {
      var dari = terpilih;
      if (PilihPromosi.tunda(permainan, dari, sq, function (p) { jalankan(dari, sq, p); })) {
        hapusPilihan();
        return;
      }
      var m = permainan.move({ from: terpilih, to: sq, promotion: "q" });
      if (m) {
        hapusPilihan();
        papan.position(permainan.fen());
        perbaruiFen();
        return;
      }
      if (bidak && bidak.color === permainan.turn()) pilihKotak(sq); else hapusPilihan();
      return;
    }

    /* mode susun: pindahkan bebas, boleh menimpa */
    var pos = papan.position();
    pos[sq] = pos[terpilih];
    delete pos[terpilih];
    hapusPilihan();
    papan.position(pos, false);      // memicu onChange -> perbaruiFen
  }

  /* Wadah #papan tetap ada walau papan dibangun ulang (destroy hanya
     mengosongkan isinya), jadi pemasangan sekali cukup. */
  if (window.KlikPapan) {
    klik = KlikPapan(document.getElementById("papan"), {
      orientasi: function () { return orientasi; },
      saatKlik: klikKotak
    });
  }

  /* ---------- evaluasi ---------- */

  function jadwalkanEvaluasi() {
    /* Posisi berubah: garis dari aliran lama tidak berlaku lagi. */
    if (fenTerus !== null && fenSekarang() !== fenTerus) hentikanTerus();
    if (!el.otomatis.checked) return;
    clearTimeout(timerAuto);
    timerAuto = setTimeout(evaluasi, 350);
  }

  function pesanGagal(status, body) {
    var detail = body && body.detail ? String(body.detail) : "";
    if (status === 409) return "Engine laptop sedang dipakai. Coba lagi sebentar, atau pilih Engine: perangkat ini.";
    if (status === 422) return "Posisi ditolak server: " + (detail || "FEN tidak sah");
    if (status === 503) return "Engine tidak tersedia di server.";
    if (status === 404) return "Endpoint /api/evaluasi belum ada.";
    return "Server menjawab " + status + (detail ? ": " + detail : "");
  }

  function evaluasi() {
    /* Selama Terus berjalan engine sedang dipegang aliran; tombol Analisa
       dimatikan, dan spasi juga tidak boleh memotongnya. */
    if (fenTerus !== null) return;
    var fen = fenSekarang();
    var masalah = alasanTidakSah(fen);
    if (masalah) { statusEngine("Tidak dievaluasi · " + masalah, true); return; }

    if (permintaan) permintaan.abort();
    permintaan = new AbortController();
    var sinyal = permintaan.signal;

    if (engineBrowser()) {
      statusEngine("Mengevaluasi di perangkat ini…");
      el.analisa.disabled = true;
      EngineBrowser.evaluasi(fen, { multipv: el.multipv.value, depth: el.depth.value, signal: sinyal })
        .then(function (body) { tampilkanHasil(body, fen); })
        .catch(function (e) {
          if (e.name === "AbortError") return;
          statusEngine(e.message, true);
        })
        .then(function () {
          if (permintaan && permintaan.signal === sinyal) { permintaan = null; el.analisa.disabled = false; }
        });
      return;
    }

    var u = new URL("/api/evaluasi", window.location.origin);
    u.searchParams.set("fen", fen);
    u.searchParams.set("multipv", el.multipv.value);
    if (el.depth.value) u.searchParams.set("depth", el.depth.value);

    statusEngine("Mengevaluasi…");
    el.analisa.disabled = true;

    fetch(u.toString(), { signal: sinyal })
      .then(function (r) {
        return r.text().then(function (t) {
          var body = null;
          try { body = t ? JSON.parse(t) : null; } catch (e) { body = null; }
          return { ok: r.ok, status: r.status, body: body };
        });
      })
      .then(function (res) {
        if (!res.ok) { statusEngine(pesanGagal(res.status, res.body), true); return; }
        tampilkanHasil(res.body, fen);
      })
      .catch(function (e) {
        if (e.name === "AbortError") return;
        statusEngine("Gagal menghubungi server: " + e.message, true);
      })
      .then(function () {
        if (permintaan && permintaan.signal === sinyal) { permintaan = null; el.analisa.disabled = false; }
      });
  }

  /* ---------- analisis tanpa henti (GET /api/evaluasi/aliran) ----------

     fetch + ReadableStream, bukan EventSource: EventSource tidak bisa
     membaca kode 409/422/503, dan otomatis menyambung ulang begitu server
     menutup aliran — yang berarti analisis mulai lagi dari awal tanpa diminta.
     Menghentikan = abort(); server melihat koneksi putus dan melepas engine
     sesudah langkah yang sedang dihitung selesai. */

  var BATAS_TERUS = 120;        // detik; sama dengan bawaan server

  function hentikanTerus() {
    if (fenTerus === null) return;
    if (permintaan) permintaan.abort();
    selesaiTerus();
  }

  function selesaiTerus() {
    fenTerus = null;
    permintaan = null;
    el.terus.textContent = "Terus";
    el.terus.classList.remove("utama");
    el.analisa.disabled = false;
  }

  function analisisTerus() {
    if (fenTerus !== null) { hentikanTerus(); statusEngine("Dihentikan."); return; }
    if (engineBrowser()) return;
    var fen = fenSekarang();
    var masalah = alasanTidakSah(fen);
    if (masalah) { statusEngine("Tidak dievaluasi · " + masalah, true); return; }

    if (permintaan) permintaan.abort();
    clearTimeout(timerAuto);
    permintaan = new AbortController();
    var sinyal = permintaan.signal;
    fenTerus = fen;
    el.terus.textContent = "Henti";
    el.terus.classList.add("utama");
    el.analisa.disabled = true;
    statusEngine("Menyalakan engine…");

    var u = new URL("/api/evaluasi/aliran", window.location.origin);
    u.searchParams.set("fen", fen);
    u.searchParams.set("multipv", el.multipv.value);
    u.searchParams.set("batas", String(BATAS_TERUS));

    var alasanSelesai = { "depth-maks": "depth maksimum tercapai",
                          "batas-waktu": (BATAS_TERUS >= 60 ? BATAS_TERUS / 60 + " menit" :
                                          BATAS_TERUS + " detik") + " habis",
                          "posisi-selesai": "posisi sudah selesai" };

    fetch(u.toString(), { signal: sinyal }).then(function (r) {
      if (!r.ok) {
        return r.text().then(function (t) {
          var body = null;
          try { body = t ? JSON.parse(t) : null; } catch (e) { body = null; }
          statusEngine(pesanGagal(r.status, body), true);
        });
      }
      var pembaca = r.body.getReader();
      var dekoder = new TextDecoder();
      var sisa = "";
      function baca() {
        return pembaca.read().then(function (hasil) {
          if (hasil.done) return;
          sisa += dekoder.decode(hasil.value, { stream: true });
          var potong;
          while ((potong = sisa.indexOf("\n\n")) >= 0) {
            olahKiriman(sisa.slice(0, potong));
            sisa = sisa.slice(potong + 2);
          }
          return baca();
        });
      }
      function olahKiriman(teks) {
        var jenis = "message", data = "";
        teks.split("\n").forEach(function (b) {
          if (b.indexOf("event: ") === 0) jenis = b.slice(7);
          else if (b.indexOf("data: ") === 0) data += b.slice(6);
        });
        var isi = null;
        try { isi = JSON.parse(data); } catch (e) { return; }
        if (sinyal.aborted || fenSekarang() !== fen) return;
        if (jenis === "garis") {
          tampilkanHasil(isi, fen);
          el.engineMeta.textContent += " · terus " + String(isi.detik).replace(".", ",") + " s";
          statusEngine("Terus menganalisis · depth " + isi.depth + " · tekan Henti untuk berhenti");
        } else if (jenis === "selesai") {
          statusEngine("Selesai: " + (alasanSelesai[isi.alasan] || isi.alasan) + ".");
        } else if (jenis === "galat") {
          statusEngine("Engine sedang dipakai. Coba lagi sebentar.", true);
        }
      }
      return baca();
    }).catch(function (e) {
      if (e.name === "AbortError") return;
      statusEngine("Gagal menghubungi server: " + e.message, true);
    }).then(function () {
      if (permintaan && permintaan.signal === sinyal) selesaiTerus();
    });
  }

  /* Toleran terhadap bentuk respons: {garis:[...]} sesuai kontrak, atau list langsung. */
  function normalisasi(body) {
    var garis = Array.isArray(body) ? body : (body && (body.garis || body.lines)) || [];
    return {
      garis: garis,
      depth: body && !Array.isArray(body) ? body.depth : undefined,
      profil: body && !Array.isArray(body) ? body.profil : undefined,
      durasi: body && !Array.isArray(body) ? body.durasi_ms : undefined
    };
  }

  function formatSkor(cp, mate) {
    if (mate !== null && mate !== undefined) return (mate > 0 ? "+M" : "-M") + Math.abs(mate);
    if (cp === null || cp === undefined) return "?";
    var p = cp / 100;
    return (p > 0 ? "+" : "") + p.toFixed(2).replace(".", ",");
  }

  function peluangPutih(cp, mate) {
    if (mate !== null && mate !== undefined) return mate > 0 ? 100 : 0;
    if (cp === null || cp === undefined) return 50;
    return 50 + 50 * (2 / (1 + Math.exp(-0.00368208 * cp)) - 1);
  }

  function perbaruiEvalBar(cp, mate) {
    var p = peluangPutih(cp, mate);
    el.barIsi.style.height = p + "%";
    el.barAngka.textContent = formatSkor(cp, mate);
    el.barAngka.className = "eval-bar-angka " + (p >= 50 ? "bawah" : "atas");
  }

  function tampilkanHasil(body, fen) {
    var h = normalisasi(body);
    var skor = body && !Array.isArray(body) ? body.skor : null;

    /* Posisi sudah selesai (mat/remis): backend mengirim garis kosong,
       skor.mate = 0, dan skor.pemenang kalau mat. */
    if (!h.garis.length) {
      garisTerakhir = []; fenTerakhir = fen;
      el.garis.innerHTML = "";
      if (skor && skor.pemenang) {
        var putihMenang = skor.pemenang === "putih";
        perbaruiEvalBar(null, putihMenang ? 1 : -1);
        el.barAngka.textContent = putihMenang ? "1-0" : "0-1";
        skorBesar(putihMenang ? "1-0" : "0-1", putihMenang ? "putih" : "hitam");
        el.engineMeta.textContent = "Skakmat";
        el.garis.appendChild(buat("div", "pesan kecil", "Skakmat · " + (putihMenang ? "Putih" : "Hitam") + " menang"));
        statusEngine("");
      } else if (body && !Array.isArray(body) && body.selesai) {
        perbaruiEvalBar(0, null);
        el.barAngka.textContent = "½";
        skorBesar("½", null);
        el.engineMeta.textContent = "Remis / pat";
        el.garis.appendChild(buat("div", "pesan kecil", "Tidak ada langkah"));
        statusEngine("");
      } else {
        statusEngine("Engine tidak mengembalikan garis.", true);
      }
      return;
    }

    garisTerakhir = h.garis;
    fenTerakhir = fen;

    var utama = h.garis[0];
    perbaruiEvalBar(utama.cp, utama.mate);
    skorBesar(formatSkor(utama.cp, utama.mate), ((utama.mate || utama.cp || 0) >= 0 ? "putih" : "hitam"));

    var bagian = fen.split(/\s+/);
    var giliranHitam = bagian[1] === "b";
    var nomorAwal = parseInt(bagian[5], 10) || 1;

    el.garis.innerHTML = "";
    h.garis.forEach(function (g, gi) {
      var baris = buat("div", "pv");
      var skor = buat("span", "pv-skor " + ((g.mate || g.cp || 0) >= 0 ? "putih" : "hitam"), formatSkor(g.cp, g.mate));
      baris.appendChild(skor);
      var wadah = buat("span", "pv-langkah");
      baris.appendChild(wadah);

      var pv = g.pv || [];
      if (!pv.length) wadah.appendChild(buat("span", "redup kecil", "—"));
      pv.forEach(function (san, j) {
        var nomor = nomorAwal + Math.floor((j + (giliranHitam ? 1 : 0)) / 2);
        var hitam = (j + (giliranHitam ? 1 : 0)) % 2 === 1;
        if (!hitam) wadah.appendChild(buat("span", "nomor", nomor + "."));
        else if (j === 0) wadah.appendChild(buat("span", "nomor", nomor + "…"));
        var s = buat("span", "langkah", san);
        s.addEventListener("click", function () { mainkanGaris(gi, j); });
        wadah.appendChild(s);
        wadah.appendChild(document.createTextNode(" "));
      });
      if (g.depth !== undefined) baris.appendChild(buat("span", "pv-depth", "d" + g.depth));
      el.garis.appendChild(baris);
    });

    var info = ["Stockfish"];
    if (h.depth !== undefined) info.push("depth " + h.depth);
    if (h.durasi !== undefined) info.push((h.durasi / 1000).toFixed(1).replace(".", ",") + " s");
    if (h.profil) info.push("profil " + h.profil);
    el.engineMeta.textContent = info.join(" · ");
    statusEngine("");
  }

  function mainkanGaris(gi, sampai) {
    if (fenSekarang() !== fenTerakhir) {
      statusEngine("Posisi berubah sejak evaluasi terakhir. Tekan Analisa.", true);
      return;
    }
    if (mode === "susun") {
      gantiMode("main");
      if (mode !== "main") return;
      el.modeMain.checked = true;
    }
    var pv = garisTerakhir[gi].pv || [];
    for (var j = 0; j <= sampai && j < pv.length; j++) {
      if (!permainan.move(pv[j])) break;
    }
    papan.position(permainan.fen());
    perbaruiFen();
  }

  /* ---------- kendali ---------- */

  document.getElementById("undo").addEventListener("click", function () {
    if (mode !== "main") return;
    permainan.undo();
    papan.position(permainan.fen());
    perbaruiFen();
  });

  document.getElementById("awal").addEventListener("click", function () {
    galat(null);
    if (mode === "main") {
      permainan.reset();
      papan.position("start");
    } else {
      papan.start(false);
      isiKontrolDariFen(new Chess().fen());
    }
    perbaruiFen();
  });

  document.getElementById("kosongkan").addEventListener("click", function () {
    if (mode !== "susun") { el.modeSusun.checked = true; gantiMode("susun"); }
    papan.clear(false);
    perbaruiFen();
  });

  document.getElementById("balik").addEventListener("click", function () {
    papan.flip();
    orientasi = papan.orientation();
  });

  document.getElementById("terapkan").addEventListener("click", function () { terapkanFen(el.fen.value); });
  el.fen.addEventListener("keydown", function (ev) {
    if (ev.key === "Enter") { terapkanFen(el.fen.value); ev.preventDefault(); }
  });

  document.getElementById("salin").addEventListener("click", function () {
    var fen = fenSekarang();
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(fen).then(function () { kedipStatus("FEN disalin"); });
    } else {
      el.fen.select();
      document.execCommand("copy");
      kedipStatus("FEN disalin");
    }
  });

  /* Pesan sesaat di baris status, lalu kembali ke status posisi. */
  function kedipStatus(pesan) {
    el.statusPosisi.textContent = pesan;
    setTimeout(perbaruiFen, 1200);
  }

  el.modeMain.addEventListener("change", function () { if (el.modeMain.checked) gantiMode("main"); });
  el.modeSusun.addEventListener("change", function () { if (el.modeSusun.checked) gantiMode("susun"); });
  el.giliran.addEventListener("change", perbaruiFen);
  ["K", "Q", "k", "q"].forEach(function (h) { el.rokade[h].addEventListener("change", perbaruiFen); });

  el.analisa.addEventListener("click", evaluasi);
  el.terus.addEventListener("click", analisisTerus);
  /* Terus memakai aliran server; engine browser cukup Analisa/Otomatis. */
  function terapkanEngine() {
    if (engineBrowser()) hentikanTerus();
    el.terus.disabled = engineBrowser();
    el.terus.title = engineBrowser()
      ? "Terus hanya untuk Engine: laptop"
      : "Analisis tanpa henti: engine berpikir makin dalam sampai dihentikan, posisi berubah, depth 30, atau 2 menit";
  }
  el.engine.addEventListener("change", function () {
    try { localStorage.setItem(KUNCI_ENGINE, el.engine.value); } catch (e) { /* mode privat */ }
    terapkanEngine();
    if (el.otomatis.checked) evaluasi();
  });
  el.multipv.addEventListener("change", function () { if (el.otomatis.checked) evaluasi(); });
  el.depth.addEventListener("change", function () { if (el.otomatis.checked) evaluasi(); });

  document.addEventListener("keydown", function (ev) {
    var t = ev.target.tagName;
    if (t === "INPUT" || t === "SELECT" || t === "TEXTAREA") return;
    if (ev.key === "ArrowLeft" && mode === "main") { document.getElementById("undo").click(); ev.preventDefault(); }
    if (ev.key === "f" || ev.key === "F") document.getElementById("balik").click();
    if (ev.key === "Escape") hapusPilihan();
    if (ev.key === " ") { evaluasi(); ev.preventDefault(); }
  });

  window.addEventListener("resize", function () { if (papan) papan.resize(); });
  /* Papan dibuat sebelum tata letak akhir (font, eval bar); ukur ulang sekali
     setelah semuanya termuat supaya tidak meluber di layar sempit. */
  window.addEventListener("load", function () { setTimeout(function () { if (papan) papan.resize(); }, 300); });

  /* ---------- mulai ---------- */

  buatPapan("start");
  var parameter = new URLSearchParams(window.location.search);
  var fenUrl = parameter.get("fen");

  /* ?engine=browser|server menang atas pilihan tersimpan (dipakai tes headless). */
  var engineAwal = parameter.get("engine");
  if (!engineAwal) { try { engineAwal = localStorage.getItem(KUNCI_ENGINE); } catch (e) { engineAwal = null; } }
  if (engineAwal === "browser" || engineAwal === "server") el.engine.value = engineAwal;
  terapkanEngine();

  /* Kait pengujian tanpa mouse (dipakai Chrome headless): ?uji_klik=e2,e4,g8,f6
     menjalankan klik kotak berurutan. Tidak berpengaruh kalau parameter tidak ada. */
  if (parameter.get("uji_klik")) {
    var urutan = parameter.get("uji_klik").split(",");
    setTimeout(function () {
      urutan.forEach(function (sq, i) { setTimeout(function () { klikKotak(sq.trim()); }, 30 * i); });
    }, 200);
  }
  /* Kait pengujian headless: ?uji_terus=3 menekan Terus dengan batas 3 detik
     (bawaannya 2 menit, terlalu lama untuk tes). */
  if (parameter.get("uji_terus")) {
    BATAS_TERUS = Math.max(1, parseInt(parameter.get("uji_terus"), 10) || 3);
    setTimeout(analisisTerus, 300);
  }
  if (fenUrl) {
    if (!terapkanFen(fenUrl)) {
      /* FEN dari URL tidak bisa dimainkan (mis. posisi setengah jadi): buka di mode susun. */
      el.modeSusun.checked = true;
      gantiMode("susun");
      terapkanFen(fenUrl);
    }
  } else {
    perbaruiFen();
  }
})();
