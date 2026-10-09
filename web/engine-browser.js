/* Engine di perangkat ini: Stockfish 18 lite (WASM, satu inti) di Web Worker.

   Dipakai papan analisis kalau Ray memilih "Engine: perangkat ini" — misalnya
   dari HP/tablet lewat tunnel, supaya analisis memakai prosesor perangkat itu
   sendiri dan tidak kena 409 "engine sedang dipakai" selagi batch berjalan di
   laptop. Berkas engine-nya sama dengan halaman Pembantu (app/pembantu.py,
   /pembantu/engine/), jadi tidak ada dependency baru.

   KONTRAK: evaluasi() mengembalikan bentuk yang SAMA dengan GET /api/evaluasi
   (SESI.md Permintaan API #4): {fen, profil, depth, durasi_ms, garis:[{multipv,
   cp, mate, pv:[SAN], depth}], skor, giliran, selesai}, cp/mate dari sudut
   pandang Putih. Karena itu papan tidak perlu tahu engine mana yang menjawab.

   Satu worker, satu pekerjaan: permintaan baru menghentikan yang lama ("stop"),
   dan hasil yang lama dibuang. Butuh chess.js (window.Chess) untuk SAN. */

(function () {
  "use strict";

  var ENGINE = "/pembantu/engine/stockfish-18-lite-single.js";
  var WAKTU_BAWAAN_MS = 1500;     // sama dengan bawaan `waktu` di /api/evaluasi
  var BATAS_SIAP_MS = 60000;      // muat WASM pertama kali di HP bisa lama

  var worker = null;
  var siap = null;                // Promise sampai "readyok"
  var jalan = null;               // pekerjaan yang sedang dihitung engine
  var antre = null;               // pekerjaan yang menunggu "bestmove" pekerjaan lama

  function galatBatal() {
    var e = new Error("dihentikan");
    e.name = "AbortError";
    return e;
  }

  function nyalakan() {
    if (siap) return siap;
    siap = new Promise(function (selesai, gagal) {
      try {
        worker = new Worker(ENGINE);
      } catch (e) {
        gagal(new Error("Browser ini tidak bisa menjalankan engine: " + e.message));
        return;
      }
      var batas = setTimeout(function () { gagal(new Error("Engine tidak menjawab dalam 60 detik.")); }, BATAS_SIAP_MS);
      worker.onerror = function (e) {
        clearTimeout(batas);
        gagal(new Error("Engine gagal dimuat" + (e.message ? ": " + e.message : " (sudah diunduh? python -m app.pembantu --unduh-engine)")));
      };
      worker.onmessage = function (e) {
        if (String(e.data) === "readyok") {
          clearTimeout(batas);
          worker.onmessage = terima;
          selesai();
        }
      };
      worker.postMessage("uci");
      worker.postMessage("setoption name Hash value 16");
      worker.postMessage("isready");
    });
    /* Gagal sekali jangan dikunci selamanya: coba lagi pada permintaan berikut. */
    siap.catch(function () { siap = null; if (worker) worker.terminate(); worker = null; });
    return siap;
  }

  function terima(e) {
    var baris = String(e.data);
    if (!jalan) return;
    if (baris.indexOf("info ") === 0 && baris.indexOf(" score ") > 0 && !/ (lower|upper)bound/.test(baris)) {
      var skor = / score (cp|mate) (-?\d+)/.exec(baris);
      var mpv = / multipv (\d+)/.exec(baris);
      var dalam = / depth (\d+)/.exec(baris);
      var pv = / pv (.+)$/.exec(baris);
      if (skor && pv) {
        jalan.garis[mpv ? parseInt(mpv[1], 10) : 1] = {
          jenis: skor[1], nilai: parseInt(skor[2], 10),
          depth: dalam ? parseInt(dalam[1], 10) : null,
          pv: pv[1].trim().split(/\s+/)
        };
      }
    } else if (baris.indexOf("bestmove") === 0) {
      var selesai = jalan;
      jalan = null;
      if (selesai.batal) selesai.gagal(galatBatal());
      else selesai.selesai(bentukHasil(selesai));
      if (antre) { var berikut = antre; antre = null; mulai(berikut); }
    }
  }

  function keSan(fen, uci) {
    var g = new Chess(fen);
    var keluar = [];
    for (var i = 0; i < uci.length; i++) {
      var u = uci[i];
      var m = g.move({ from: u.slice(0, 2), to: u.slice(2, 4), promotion: u.length > 4 ? u[4] : undefined });
      if (!m) break;
      keluar.push(m.san);
    }
    return keluar;
  }

  function bentukHasil(t) {
    var tanda = t.putihJalan ? 1 : -1;
    var garis = [];
    for (var k = 1; k <= t.multipv; k++) {
      var g = t.garis[k];
      if (!g) continue;
      garis.push({
        multipv: k,
        cp: g.jenis === "cp" ? g.nilai * tanda : null,
        mate: g.jenis === "mate" ? g.nilai * tanda : null,
        pv: keSan(t.fen, g.pv),
        depth: g.depth
      });
    }
    var utama = garis[0] || {};
    return {
      fen: t.fen, profil: "browser", depth: utama.depth || null,
      durasi_ms: Date.now() - t.mulai, garis: garis,
      skor: { cp: utama.cp === undefined ? null : utama.cp, mate: utama.mate === undefined ? null : utama.mate },
      giliran: t.putihJalan ? "putih" : "hitam", selesai: false
    };
  }

  /* Posisi yang sudah selesai dijawab tanpa engine, persis seperti backend:
     garis kosong, skor.mate 0, skor.pemenang kalau mat. */
  function posisiSelesai(fen) {
    var g = new Chess(fen);
    if (!g.game_over()) return null;
    var putihJalan = g.turn() === "w";
    var skor = { cp: null, mate: 0 };
    if (g.in_checkmate()) skor.pemenang = putihJalan ? "hitam" : "putih";
    else skor.cp = 0;
    return { fen: fen, profil: "browser", depth: 0, durasi_ms: 0, garis: [], skor: skor,
             giliran: putihJalan ? "putih" : "hitam", selesai: true };
  }

  function mulai(t) {
    jalan = t;
    t.mulai = Date.now();
    worker.postMessage("setoption name MultiPV value " + t.multipv);
    worker.postMessage("position fen " + t.fen);
    worker.postMessage(t.depth ? "go depth " + t.depth : "go movetime " + WAKTU_BAWAAN_MS);
  }

  /* opsi: {multipv, depth (opsional), signal (AbortSignal, opsional)} */
  function evaluasi(fen, opsi) {
    opsi = opsi || {};
    var akhir = posisiSelesai(fen);
    if (akhir) return Promise.resolve(akhir);
    return nyalakan().then(function () {
      return new Promise(function (selesai, gagal) {
        var t = {
          fen: fen, multipv: Math.max(1, Math.min(5, parseInt(opsi.multipv, 10) || 1)),
          depth: parseInt(opsi.depth, 10) || 0, putihJalan: fen.split(/\s+/)[1] !== "b",
          garis: {}, batal: false, selesai: selesai, gagal: gagal
        };
        if (opsi.signal) {
          if (opsi.signal.aborted) { gagal(galatBatal()); return; }
          opsi.signal.addEventListener("abort", function () {
            if (antre === t) { antre = null; gagal(galatBatal()); }
            else if (jalan === t) { t.batal = true; worker.postMessage("stop"); }
          });
        }
        if (antre) { antre.gagal(galatBatal()); antre = null; }
        if (jalan) {
          jalan.batal = true;
          worker.postMessage("stop");
          antre = t;
        } else {
          mulai(t);
        }
      });
    });
  }

  window.EngineBrowser = { evaluasi: evaluasi, ENGINE: ENGINE };
})();
