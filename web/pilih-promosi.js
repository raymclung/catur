/* Pemilih bidak promosi, dipakai semua papan (partai, papan analisis,
   penjelajah, latihan).

   Dulu setiap papan memanggil move({promotion: "q"}): pion yang sampai di
   baris akhir SELALU jadi ratu. Soal latihan yang jawabannya promosi kuda
   atau benteng (=N, =R) jadi tidak pernah bisa dijawab benar (audit 27 Sep
   2026). Sekarang, kalau langkah itu promosi, pengguna memilih bidaknya.

   Pemakaian di penangan seret/klik:

     if (PilihPromosi.tunda(permainan, dari, ke, function (p) {
       ...jalankan move({from: dari, to: ke, promotion: p})...
     })) return "snapback";

   tunda() mengembalikan true kalau pemilih dibuka; langkahnya baru
   dijalankan setelah bidak dipilih, dan hanya kalau posisi belum berubah.
   Esc atau klik di luar = batal. Tombol q/r/b/n memilih langsung. */

var PilihPromosi = (function () {
  "use strict";

  var BIDAK = [
    { kode: "q", nama: "Ratu (menteri)", huruf: "Q" },
    { kode: "r", nama: "Benteng", huruf: "R" },
    { kode: "b", nama: "Gajah (peluncur)", huruf: "B" },
    { kode: "n", nama: "Kuda", huruf: "N" }
  ];
  /* Pintasan = huruf SAN yang tampil di daftar langkah (=Q, =R, =B, =N). */
  var TOMBOL = { q: "q", r: "r", b: "b", n: "n" };

  var terbuka = null;   // { latar, selesai }

  function perlu(permainan, dari, ke) {
    return permainan.moves({ square: dari, verbose: true }).some(function (m) {
      return m.to === ke && !!m.promotion;
    });
  }

  function tutup(pilihan) {
    if (!terbuka) return;
    var t = terbuka;
    terbuka = null;
    document.removeEventListener("keydown", saatTombol, true);
    if (t.latar.parentNode) t.latar.parentNode.removeChild(t.latar);
    t.selesai(pilihan);
  }

  function saatTombol(ev) {
    if (!terbuka) return;
    var k = (ev.key || "").toLowerCase();
    if (k === "escape") tutup(null);
    else if (TOMBOL[k]) tutup(TOMBOL[k]);
    else return;
    /* Esc di halaman partai juga menutup variasi; jangan sampai terpicu. */
    ev.preventDefault();
    ev.stopPropagation();
  }

  function tanya(warna) {
    tutup(null);
    return new Promise(function (selesai) {
      var latar = document.createElement("div");
      latar.className = "promosi-latar";
      latar.style.cssText = "position:fixed;inset:0;z-index:1000;display:flex;align-items:center;" +
        "justify-content:center;background:rgba(0,0,0,.35)";
      latar.addEventListener("click", function (ev) { if (ev.target === latar) tutup(null); });

      var kotak = document.createElement("div");
      kotak.setAttribute("role", "dialog");
      kotak.setAttribute("aria-label", "Pilih bidak promosi");
      kotak.style.cssText = "background:var(--kartu,#fff);color:var(--teks,#1a1a1a);border:1px solid var(--garis,#ddd);" +
        "border-radius:10px;padding:12px 14px;box-shadow:0 8px 24px rgba(0,0,0,.25);text-align:center";
      var judul = document.createElement("div");
      judul.textContent = "Promosi jadi apa?";
      judul.style.cssText = "margin-bottom:8px;font-weight:600";
      kotak.appendChild(judul);

      BIDAK.forEach(function (b) {
        var tombol = document.createElement("button");
        tombol.type = "button";
        tombol.title = b.nama;
        tombol.dataset.promosi = b.kode;
        tombol.style.cssText = "margin:0 3px;padding:4px;width:64px;height:64px;cursor:pointer";
        var gambar = document.createElement("img");
        gambar.src = "/static/img/chesspieces/" + (warna === "b" ? "b" : "w") + b.huruf + ".png";
        gambar.alt = b.nama;
        gambar.style.cssText = "width:52px;height:52px";
        tombol.appendChild(gambar);
        tombol.addEventListener("click", function () { tutup(b.kode); });
        kotak.appendChild(tombol);
      });

      var ket = document.createElement("div");
      ket.textContent = "Esc = batal";
      ket.style.cssText = "margin-top:6px;font-size:12px;color:var(--teks-redup,#6b7280)";
      kotak.appendChild(ket);

      latar.appendChild(kotak);
      document.body.appendChild(latar);
      terbuka = { latar: latar, selesai: selesai };
      document.addEventListener("keydown", saatTombol, true);
      var pertama = kotak.querySelector("button");
      if (pertama) pertama.focus();
    });
  }

  function tunda(permainan, dari, ke, lanjut) {
    if (!perlu(permainan, dari, ke)) return false;
    var fen = permainan.fen();
    tanya(permainan.turn()).then(function (p) {
      if (p && permainan.fen() === fen) lanjut(p);
    });
    return true;
  }

  return { perlu: perlu, tanya: tanya, tunda: tunda };
})();
