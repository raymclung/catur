/* Tombol "Simpan posisi" untuk halaman yang punya elemen #fen (jelajah: div,
   papan analisis: input). Dipasang lewat atribut data supaya skrip halaman
   tidak perlu diubah:

     <button id="simpan-posisi" data-asal="jelajah" hidden>☆</button>

   Endpoint POST /api/posisi-tersimpan (SESI.md Permintaan API #6). Tombol
   hanya ditampilkan kalau /openapi.json memuat path itu; sebelum backend
   memasangnya, tombol tetap tersembunyi supaya tidak ada janji palsu. */

(function () {
  "use strict";

  var tombol = document.getElementById("simpan-posisi");
  var sumber = document.getElementById("fen");
  if (!tombol || !sumber) return;

  /* Kait uji headless: ?uji_simpan=<judul> menekan tombolnya tanpa prompt. */
  var paksaJudul = new URLSearchParams(location.search).get("uji_simpan");

  function fenSekarang() {
    var t = sumber.value !== undefined && sumber.tagName === "INPUT" ? sumber.value : sumber.textContent;
    return (t || "").trim();
  }

  /* Judul bawaan supaya tinggal tekan Enter: di penjelajah dan halaman partai
     baris langkah sudah menjelaskan posisinya. */
  function judulBawaan() {
    var baris = document.getElementById("baris-langkah");
    var teks = baris ? (baris.textContent || "").trim() : "";
    if (teks && teks !== "Posisi awal") return teks;
    return "Posisi dari " + (tombol.dataset.asal || "papan");
  }

  /* Halaman partai (/partai/{id}): posisi yang disimpan dicatat asalnya,
     supaya dari daftar Tersimpan bisa kembali ke partainya. Dulu selalu
     null (audit 27 Sep 2026). */
  function partaiAsal() {
    if (tombol.dataset.asal !== "partai") return null;
    var m = location.pathname.match(/\/partai\/(\d+)\/?$/);
    return m ? parseInt(m[1], 10) : null;
  }

  function kedip(teksBaru) {
    var lama = tombol.textContent;
    tombol.textContent = teksBaru;
    tombol.disabled = true;
    setTimeout(function () { tombol.textContent = lama; tombol.disabled = false; }, 1400);
  }

  fetch("/openapi.json").then(function (r) { return r.json(); }).then(function (d) {
    if (!d.paths || !d.paths["/api/posisi-tersimpan"]) return;
    tombol.hidden = false;
    if (paksaJudul !== null) setTimeout(function () { tombol.click(); }, 300);
    tombol.addEventListener("click", function () {
      var fen = fenSekarang();
      if (!fen || fen.indexOf("/") < 0) return;
      var judul = paksaJudul !== null ? paksaJudul : window.prompt("Judul posisi", judulBawaan());
      if (judul === null) return;
      fetch("/api/posisi-tersimpan", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ fen: fen, judul: judul.trim(), catatan: "", asal: tombol.dataset.asal || "", partai_id: partaiAsal() })
      }).then(function (r) {
        kedip(r.ok ? "✓" : "✕");
      }).catch(function () { kedip("✕"); });
    });
  }).catch(function () { /* tombol tetap tersembunyi */ });
})();
