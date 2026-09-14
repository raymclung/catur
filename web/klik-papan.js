/* Interaksi mouse untuk papan chessboard.js, dipakai bersama oleh halaman
   partai, papan analisis, dan latihan.

   Mengikuti kebiasaan Lichess dan Chess.com:
     klik KIRI         pilih bidak lalu klik kotak tujuan (klik-untuk-jalan);
                       sekaligus menghapus semua panah dan tanda kotak
     klik KANAN        tanda lingkaran pada satu kotak (klik lagi = hapus)
     seret KANAN       panah dari kotak awal ke kotak tujuan (ulangi = hapus)
     + Shift / Alt / Ctrl  warna merah / biru / kuning; tanpa tombol = hijau

   Kenapa perlu dibedakan sendiri: chessboard.js memulai seret pada mousedown
   APA PUN tombolnya, dan pendeteksi klik lama juga tidak memeriksa tombol —
   akibatnya klik kanan berperilaku sama dengan klik kiri (dilaporkan Ray,
   13 Sep 2026). Tombol selain kiri sekarang dihentikan di fase capture
   sebelum sampai ke chessboard.js.

   Klik kiri dideteksi dari pointerdown lalu pointerup yang hampir tidak
   bergeser (<= 6 px). Kotaknya dihitung dari koordinat, karena saat bidak
   ditekan chessboard.js sudah memindahkannya ke elemen seret di luar papan.

   Panah dan tanda digambar di lapisan SVG di atas papan, dihitung dari posisi
   elemen kotak yang sebenarnya, jadi tetap benar saat papan dibalik atau
   ukurannya berubah. Semuanya dihapus otomatis kalau susunan bidak berubah
   (langkah dimainkan, soal berganti, posisi dimuat) — sama seperti Lichess,
   anotasi milik satu posisi.

   Pemakaian:
     var klik = KlikPapan(document.getElementById("papan"), {
       orientasi: function () { return papan.orientation(); },
       saatKlik:  function (kotak) { ... }        // kotak = "e4" atau null (di luar papan)
     });
     klik.sorotPilih("e2"); klik.sorotTarget("e4", false); klik.hapusSorot();
     klik.hapusAnotasi();

   Kelas CSS yang dipakai: .sorot-pilih, .sorot-target, .ada-bidak (gaya.css). */

window.KlikPapan = function (wadah, opsi) {
  "use strict";

  var SVG_NS = "http://www.w3.org/2000/svg";
  var JEDA_SNAPBACK_MS = 80;   // tunggu chessboard.js menyelesaikan snapback klik-tanpa-geser
  var WARNA = { hijau: "#15781b", merah: "#882020", biru: "#003088", kuning: "#e68f00" };

  var tekan = null;            // klik kiri yang sedang ditekan
  var kanan = null;            // {dari, warna} selama tombol kanan ditekan
  var sementara = null;        // pratinjau panah saat menyeret kanan
  var anotasi = [];            // [{dari, ke|null, warna}]
  var svg = null;
  var tandaSusunan = null;     // susunan bidak saat anotasi terakhir digambar
  var sedangGambar = false;

  if (window.getComputedStyle(wadah).position === "static") {
    wadah.style.position = "relative";
  }

  /* ---------- kotak ---------- */

  function elemenKotak(kotak) {
    return wadah.querySelector('[data-square="' + kotak + '"]');
  }

  function kotakDariTitik(x, y) {
    var b = wadah.querySelector(".board-b72b1");
    if (!b) return null;
    var r = b.getBoundingClientRect();
    if (x < r.left || x >= r.right || y < r.top || y >= r.bottom) return null;
    var kol = Math.floor((x - r.left) / (r.width / 8));
    var bar = Math.floor((y - r.top) / (r.height / 8));
    if (opsi.orientasi() === "black") { kol = 7 - kol; bar = 7 - bar; }
    return "abcdefgh".charAt(kol) + (8 - bar);
  }

  function hapusSorot() {
    var semua = wadah.querySelectorAll(".sorot-pilih, .sorot-target");
    for (var i = 0; i < semua.length; i++) {
      semua[i].classList.remove("sorot-pilih", "sorot-target", "ada-bidak");
    }
  }

  function sorotPilih(kotak) {
    var e = elemenKotak(kotak);
    if (e) e.classList.add("sorot-pilih");
  }

  function sorotTarget(kotak, tangkap) {
    var e = elemenKotak(kotak);
    if (!e) return;
    e.classList.add("sorot-target");
    if (tangkap) e.classList.add("ada-bidak");
  }

  /* ---------- anotasi: panah & lingkaran ---------- */

  function warnaDariTombol(ev) {
    if (ev.shiftKey) return "merah";
    if (ev.altKey) return "biru";
    if (ev.ctrlKey || ev.metaKey) return "kuning";
    return "hijau";
  }

  /* Klik ulang anotasi yang sama menghapusnya; warna lain menggantinya. */
  function tukarAnotasi(dari, ke, warna) {
    for (var i = 0; i < anotasi.length; i++) {
      var a = anotasi[i];
      if (a.dari === dari && a.ke === ke) {
        anotasi.splice(i, 1);
        if (a.warna !== warna) anotasi.push({ dari: dari, ke: ke, warna: warna });
        gambar();
        return;
      }
    }
    anotasi.push({ dari: dari, ke: ke, warna: warna });
    gambar();
  }

  function hapusAnotasi() {
    if (!anotasi.length && !sementara) return;
    anotasi = [];
    sementara = null;
    gambar();
  }

  /* Susunan bidak saat ini, untuk tahu kapan posisi berganti. */
  function susunanBidak() {
    var img = wadah.querySelectorAll("[data-square] img[data-piece]");
    var bagian = [];
    for (var i = 0; i < img.length; i++) {
      bagian.push(img[i].parentNode.getAttribute("data-square") + img[i].getAttribute("data-piece"));
    }
    return bagian.sort().join(",");
  }

  function pastikanSvg() {
    if (svg && svg.isConnected) return svg;
    svg = document.createElementNS(SVG_NS, "svg");
    svg.setAttribute("class", "lapisan-anotasi");
    svg.style.cssText = "position:absolute;left:0;top:0;pointer-events:none;z-index:5;overflow:visible";
    wadah.appendChild(svg);
    return svg;
  }

  function pusatKotak(kotak, asal) {
    var e = elemenKotak(kotak);
    if (!e) return null;
    var r = e.getBoundingClientRect();
    return { x: r.left - asal.left + r.width / 2, y: r.top - asal.top + r.height / 2, s: r.width };
  }

  function elemenSvg(tag, atribut) {
    var e = document.createElementNS(SVG_NS, tag);
    Object.keys(atribut).forEach(function (k) { e.setAttribute(k, atribut[k]); });
    return e;
  }

  function gambarSatu(lapisan, a, asal, redup) {
    var p = pusatKotak(a.dari, asal);
    if (!p) return;
    var warna = WARNA[a.warna] || WARNA.hijau;
    var buram = redup ? 0.45 : 0.8;

    if (!a.ke || a.ke === a.dari) {
      lapisan.appendChild(elemenSvg("circle", {
        cx: p.x, cy: p.y, r: p.s * 0.44, fill: "none",
        stroke: warna, "stroke-width": p.s * 0.075, opacity: buram
      }));
      return;
    }

    var q = pusatKotak(a.ke, asal);
    if (!q) return;
    var dx = q.x - p.x, dy = q.y - p.y;
    var panjang = Math.sqrt(dx * dx + dy * dy) || 1;
    var ux = dx / panjang, uy = dy / panjang;       // arah
    var nx = -uy, ny = ux;                          // tegak lurus
    var s = p.s;
    var ujung = { x: q.x - ux * s * 0.12, y: q.y - uy * s * 0.12 };
    var kepala = s * 0.42, lebar = s * 0.38;
    var dasar = { x: ujung.x - ux * kepala, y: ujung.y - uy * kepala };

    lapisan.appendChild(elemenSvg("line", {
      x1: p.x, y1: p.y, x2: dasar.x, y2: dasar.y,
      stroke: warna, "stroke-width": s * 0.15, "stroke-linecap": "round", opacity: buram
    }));
    lapisan.appendChild(elemenSvg("polygon", {
      points: [ujung.x + "," + ujung.y,
               (dasar.x + nx * lebar / 2) + "," + (dasar.y + ny * lebar / 2),
               (dasar.x - nx * lebar / 2) + "," + (dasar.y - ny * lebar / 2)].join(" "),
      fill: warna, opacity: buram
    }));
  }

  function gambar() {
    sedangGambar = true;
    try {
      var lapisan = pastikanSvg();
      while (lapisan.firstChild) lapisan.removeChild(lapisan.firstChild);
      lapisan.setAttribute("width", wadah.offsetWidth);
      lapisan.setAttribute("height", wadah.offsetHeight);
      var asal = wadah.getBoundingClientRect();
      anotasi.forEach(function (a) { gambarSatu(lapisan, a, asal, false); });
      if (sementara) gambarSatu(lapisan, sementara, asal, true);
      tandaSusunan = susunanBidak();
    } finally {
      sedangGambar = false;
    }
  }

  /* Diproses LANGSUNG, bukan lewat requestAnimationFrame: rAF tidak menjamin
     kapan dijalankan (terbukti goyah saat diuji — kadang panah tidak ikut
     pindah setelah papan dibalik). MutationObserver sendiri sudah asinkron
     dan menggabungkan banyak perubahan menjadi satu panggilan. */
  function perbarui() {
    /* Susunan bidak berubah = posisi lain: anotasi milik posisi lama dibuang. */
    if (anotasi.length && tandaSusunan !== null && susunanBidak() !== tandaSusunan) {
      anotasi = [];
      sementara = null;
    }
    gambar();
  }

  /* Papan dibangun ulang, dibalik, atau bidaknya berpindah: gambar ulang.
     Perubahan di dalam lapisan SVG sendiri diabaikan supaya tidak berulang. */
  if (window.MutationObserver) {
    new MutationObserver(function (daftar) {
      if (sedangGambar) return;
      for (var i = 0; i < daftar.length; i++) {
        var m = daftar[i];
        if (svg && (m.target === svg || svg.contains(m.target))) continue;
        var hanyaSvg = m.target === wadah && svg &&
          Array.prototype.every.call(m.addedNodes, function (n) { return n === svg; }) &&
          Array.prototype.every.call(m.removedNodes, function (n) { return n === svg; }) &&
          (m.addedNodes.length || m.removedNodes.length);
        if (hanyaSvg) continue;
        perbarui();
        return;
      }
    }).observe(wadah, { childList: true, subtree: true });
  }
  window.addEventListener("resize", perbarui);

  /* ---------- tombol mouse ---------- */

  /* Tombol selain kiri tidak boleh sampai ke chessboard.js, karena pustaka itu
     memulai seret untuk tombol apa pun. */
  wadah.addEventListener("mousedown", function (ev) {
    if (ev.button !== 0) { ev.stopPropagation(); ev.preventDefault(); }
  }, true);

  wadah.addEventListener("contextmenu", function (ev) { ev.preventDefault(); });

  wadah.addEventListener("pointerdown", function (ev) {
    if (ev.button === 2) {
      var dari = kotakDariTitik(ev.clientX, ev.clientY);
      if (!dari) return;
      ev.preventDefault();        // cegah event mouse turunan (mousedown) sampai ke chessboard.js
      ev.stopPropagation();
      kanan = { dari: dari, warna: warnaDariTombol(ev) };
      sementara = null;
      return;
    }
    if (ev.button !== 0) return;
    /* Lichess: klik kiri di papan membersihkan semua anotasi. */
    if (kotakDariTitik(ev.clientX, ev.clientY)) hapusAnotasi();
    tekan = { x: ev.clientX, y: ev.clientY };
  }, true);

  document.addEventListener("pointermove", function (ev) {
    if (!kanan) return;
    var ke = kotakDariTitik(ev.clientX, ev.clientY);
    var baru = ke && ke !== kanan.dari ? { dari: kanan.dari, ke: ke, warna: kanan.warna } : null;
    var lama = sementara;
    if ((baru && lama && baru.ke === lama.ke) || (!baru && !lama)) return;
    sementara = baru;
    gambar();
  }, true);

  document.addEventListener("pointerup", function (ev) {
    if (ev.button === 2 && kanan) {
      var awal = kanan;
      kanan = null;
      sementara = null;
      var ke = kotakDariTitik(ev.clientX, ev.clientY);
      if (!ke) { gambar(); return; }          // dilepas di luar papan = batal
      tukarAnotasi(awal.dari, ke === awal.dari ? null : ke, awal.warna);
      return;
    }
    if (ev.button !== 0 || !tekan) return;
    var mula = tekan;
    tekan = null;
    if (Math.abs(ev.clientX - mula.x) > 6 || Math.abs(ev.clientY - mula.y) > 6) return;
    var kotak = kotakDariTitik(ev.clientX, ev.clientY);
    setTimeout(function () { opsi.saatKlik(kotak); }, JEDA_SNAPBACK_MS);
  }, true);

  /* Menu klik kanan juga ditahan kalau seret kanan dilepas di luar papan. */
  document.addEventListener("contextmenu", function (ev) {
    if (kanan || wadah.contains(ev.target)) ev.preventDefault();
  }, true);

  return {
    elemenKotak: elemenKotak,
    kotakDariTitik: kotakDariTitik,
    hapusSorot: hapusSorot,
    sorotPilih: sorotPilih,
    sorotTarget: sorotTarget,
    hapusAnotasi: hapusAnotasi,
    anotasi: function () { return anotasi.slice(); }
  };
};
