// Pembantu analisis (app/pembantu.py): minta partai dari laptop, hitung tiap
// posisi dengan Stockfish WASM di browser, kirim evaluasinya kembali.
// Laporan (akurasi, blunder, langkah terbaik) disusun di laptop dengan rumus
// yang sama dengan batch biasa; halaman ini hanya menggantikan engine.

(function () {
  "use strict";

  const ENGINE = "/pembantu/engine/stockfish-18-lite-single.js";
  const BATAS_POSISI_MS = 60000;      // satu posisi depth 10 tidak wajar lebih lama dari ini
  const JEDA_ULANG_MS = 15000;

  const $ = (id) => document.getElementById(id);
  let jalan = false;
  let worker = null;
  let partaiSekarang = null;
  let wakeLock = null;
  let batalkan = null;                // menggagalkan evaluasi yang sedang menunggu engine
  const hitung = { partai: 0, posisi: 0, detik: 0 };

  function simpan(k, v) { try { localStorage.setItem(k, v); } catch (e) { /* mode privat */ } }
  function baca(k) { try { return localStorage.getItem(k); } catch (e) { return null; } }
  const tidur = (ms) => new Promise((r) => setTimeout(r, ms));

  function namaBawaan() {
    const ua = navigator.userAgent;
    const jenis = /iPad|Tablet/i.test(ua) || (/Android/i.test(ua) && !/Mobile/i.test(ua)) ? "Tablet"
      : /Android|iPhone|Mobile/i.test(ua) ? "HP" : "Perangkat";
    return jenis + " " + Math.floor(100 + Math.random() * 900);
  }

  function pesan(teks, galat) {
    const el = $("pesan");
    el.textContent = teks;
    el.className = "status" + (galat ? " galat" : "");
  }

  function angka(n, digit) {
    return n.toLocaleString("id-ID", { maximumFractionDigits: digit || 0 });
  }

  function perbaruiAngka() {
    $("n-partai").textContent = angka(hitung.partai);
    $("n-kecepatan").textContent = hitung.detik > 0 ? angka(hitung.posisi / hitung.detik, 1) : "–";
    $("n-rata").textContent = hitung.partai > 0 ? angka(hitung.detik / hitung.partai, 0) : "–";
  }

  // ------------------------------------------------------------ engine

  function nyalakanEngine() {
    return new Promise((selesai, gagal) => {
      if (worker) worker.terminate();
      try {
        worker = new Worker(ENGINE);
      } catch (e) {
        gagal(new Error("Browser ini tidak bisa menjalankan engine: " + e.message));
        return;
      }
      const batas = setTimeout(() => gagal(new Error("Engine tidak menjawab dalam 60 detik.")), 60000);
      worker.onerror = (e) => { clearTimeout(batas); gagal(new Error("Engine gagal dimuat" + (e.message ? ": " + e.message : "."))); };
      worker.onmessage = (e) => {
        if (String(e.data) === "readyok") {
          clearTimeout(batas);
          selesai();
        }
      };
      worker.postMessage("uci");
      worker.postMessage("setoption name Hash value 16");
      worker.postMessage("isready");
    });
  }

  // Evaluasi satu posisi. Hasil dari sudut PUTIH: {cp|mate, pv, depth},
  // atau null kalau posisinya sudah selesai (mat/pat, engine tidak punya langkah).
  function evaluasi(fen, langkah, depth, putihJalan) {
    return new Promise((selesai, gagal) => {
      let terakhir = null;
      const batas = setTimeout(() => gagal(new Error("waktu habis")), BATAS_POSISI_MS);
      batalkan = () => { clearTimeout(batas); gagal(new Error("dihentikan")); };
      worker.onmessage = (e) => {
        const baris = String(e.data);
        if (baris.startsWith("info ") && baris.includes(" score ") && !/ (lower|upper)bound/.test(baris)) {
          const skor = / score (cp|mate) (-?\d+)/.exec(baris);
          const kedalaman = / depth (\d+)/.exec(baris);
          const pv = / pv (.+)$/.exec(baris);
          if (skor) terakhir = { jenis: skor[1], nilai: parseInt(skor[2], 10),
                                 depth: kedalaman ? parseInt(kedalaman[1], 10) : null,
                                 pv: pv ? pv[1].trim().split(/\s+/) : [] };
        } else if (baris.startsWith("bestmove")) {
          clearTimeout(batas);
          const terbaik = baris.split(/\s+/)[1];
          if (!terbaik || terbaik === "(none)" || !terakhir) { selesai(null); return; }
          const tanda = putihJalan ? 1 : -1;
          const hasil = { depth: terakhir.depth, pv: terakhir.pv.length ? terakhir.pv : [terbaik] };
          if (terakhir.jenis === "mate") hasil.mate = terakhir.nilai * tanda;
          else hasil.cp = terakhir.nilai * tanda;
          selesai(hasil);
        }
      };
      worker.postMessage("position fen " + fen + (langkah.length ? " moves " + langkah.join(" ") : ""));
      worker.postMessage("go depth " + depth);
    });
  }

  // ------------------------------------------------------------ server

  async function panggil(jalur, badan) {
    const r = await fetch(jalur, { method: "POST", headers: { "Content-Type": "application/json" },
                                   body: JSON.stringify(badan) });
    let isi = null;
    try { isi = await r.json(); } catch (e) { /* bukan JSON */ }
    if (!r.ok) {
      const err = new Error((isi && (typeof isi.detail === "string" ? isi.detail : JSON.stringify(isi.detail))) || ("HTTP " + r.status));
      err.status = r.status;
      throw err;
    }
    return isi;
  }

  // Beberapa tab di perangkat yang sama berbagi nama tersimpan. Server melepas
  // partai lama milik nama yang sama setiap kali minta partai baru, jadi tiap
  // tab diberi akhiran sendiri supaya tidak saling melepas partai.
  const idTab = Math.random().toString(36).slice(2, 5);

  function identitas() {
    return { kode: $("kode").value.trim(), nama: ($("nama").value.trim() || "Perangkat") + " · " + idTab };
  }

  async function kerjakanSatu() {
    const tugas = await panggil("/api/pembantu/ambil", Object.assign(identitas(), { dukung960: true }));
    if (tugas.partai_id == null) return false;
    partaiSekarang = tugas.partai_id;

    const jumlahPosisi = tugas.langkah.length + 1;
    const putihAwal = tugas.fen_awal.split(" ")[1] === "w";
    const posisi = [];
    const mulai = performance.now();
    $("sekarang").textContent = `Partai #${tugas.partai_id}: ${tugas.putih} – ${tugas.hitam} (${tugas.langkah.length} ply)` +
      (tugas.mendalam ? ` · analisis mendalam (depth ${tugas.depth})` : "");
    worker.postMessage("setoption name UCI_Chess960 value " + (tugas.chess960 ? "true" : "false"));
    worker.postMessage("ucinewgame");

    for (let i = 0; i < jumlahPosisi; i++) {
      if (!jalan) return true;
      let hasil;
      try {
        hasil = await evaluasi(tugas.fen_awal, tugas.langkah.slice(0, i), tugas.depth, putihAwal === (i % 2 === 0));
      } catch (e) {
        if (!jalan) return true;                // dihentikan di tengah posisi
        await nyalakanEngine();                 // engine macet: nyalakan ulang, ulangi posisi ini sekali
        hasil = await evaluasi(tugas.fen_awal, tugas.langkah.slice(0, i), tugas.depth, putihAwal === (i % 2 === 0));
      }
      posisi.push(hasil);
      $("bar").style.width = Math.round(100 * (i + 1) / jumlahPosisi) + "%";
    }
    const detik = (performance.now() - mulai) / 1000;

    // Simpan dicoba ulang kalau database laptop sedang sibuk atau WiFi putus sebentar.
    for (;;) {
      try {
        await panggil("/api/pembantu/kirim", Object.assign(identitas(), { partai_id: tugas.partai_id, depth: tugas.depth, posisi, detik }));
        break;
      } catch (e) {
        if (e.status === 422 || e.status === 404 || e.status === 403) throw e;
        pesan("Belum terkirim (" + e.message + "), dicoba lagi…", true);
        await tidur(JEDA_ULANG_MS);
        if (!jalan) return true;
      }
    }
    partaiSekarang = null;
    hitung.partai += 1;
    hitung.posisi += jumlahPosisi;
    hitung.detik += detik;
    perbaruiAngka();
    pesan("Sedang menganalisis…");
    return true;
  }

  async function mulai() {
    const { kode } = identitas();
    if (!/^\d{6}$/.test(kode)) { pesan("Kode harus 6 angka. Lihat di halaman Mesin di laptop.", true); return; }
    simpan("pembantu.kode", kode);
    simpan("pembantu.nama", $("nama").value.trim());
    jalan = true;
    $("tombol").textContent = "Berhenti";
    $("tombol").className = "berhenti";
    pesan("Memuat engine (±7 MB, sekali saja)…");
    try {
      if ("wakeLock" in navigator) wakeLock = await navigator.wakeLock.request("screen");
    } catch (e) { /* hanya tersedia di https; petunjuk layar ada di halaman */ }
    try {
      await nyalakanEngine();
      pesan("Sedang menganalisis…");
      while (jalan) {
        let ada;
        try {
          ada = await kerjakanSatu();
        } catch (e) {
          if (e.status === 403) { pesan(e.message, true); break; }
          if (e.status === 422) { pesan("Satu partai dilewati: " + e.message, true); continue; }
          pesan("Tidak tersambung ke laptop (" + e.message + "). Dicoba lagi…", true);
          await tidur(JEDA_ULANG_MS);
          continue;
        }
        if (!ada) { pesan("Semua partai sudah dianalisis. Terima kasih!"); break; }
      }
    } catch (e) {
      pesan(e.message, true);
    }
    await berhenti();
  }

  async function berhenti() {
    const adaPartai = partaiSekarang;
    jalan = false;
    partaiSekarang = null;
    if (batalkan) { batalkan(); batalkan = null; }
    if (worker) { worker.terminate(); worker = null; }
    if (wakeLock) { try { await wakeLock.release(); } catch (e) { /* sudah lepas */ } wakeLock = null; }
    $("tombol").textContent = "Mulai";
    $("tombol").className = "";
    $("bar").style.width = "0";
    if (adaPartai != null) {
      try { await panggil("/api/pembantu/lepas", Object.assign(identitas(), { partai_id: adaPartai })); }
      catch (e) { /* sewa habis sendiri dalam 20 menit */ }
      $("sekarang").textContent = "Dihentikan. Partai yang belum selesai dikembalikan ke antrean.";
    }
  }

  $("kode").value = new URLSearchParams(location.search).get("kode") || baca("pembantu.kode") || "";
  $("nama").value = baca("pembantu.nama") || namaBawaan();
  $("tombol").addEventListener("click", () => { if (jalan) berhenti(); else mulai(); });
  // ?uji_mulai=1: langsung mulai, dipakai uji browser tanpa layar
  if (new URLSearchParams(location.search).get("uji_mulai") === "1") mulai();
  window.addEventListener("pagehide", () => {
    if (jalan && partaiSekarang != null) {
      navigator.sendBeacon("/api/pembantu/lepas", new Blob(
        [JSON.stringify(Object.assign(identitas(), { partai_id: partaiSekarang }))], { type: "application/json" }));
    }
  });
})();
