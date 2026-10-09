/* Navigasi "partai sebelumnya / berikutnya" di halaman partai.

   Dipisah dari papan.js 25 Sep 2026 (audit nomor 8). Isinya tidak menyentuh
   papan, langkah, maupun analisis sama sekali — hanya membaca ulang daftar
   partai dari ?daftar=<query> dan menyusun tautan tetangganya. Satu-satunya
   sambungan ke halaman adalah id partai yang sedang dibuka.

   Dipakai web/partai.html lewat window.NavigasiPartai; harus dimuat SEBELUM
   papan.js. */

window.NavigasiPartai = (function () {
  "use strict";

  function teks(isi) {
    return document.createTextNode(isi === null || isi === undefined ? "" : String(isi));
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

  function siapkan(idSekarang) {
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

  return {
    siapkan: siapkan,
    keSebelumnya: function () { keTetangga(tetangga.sebelum); },
    keBerikutnya: function () { keTetangga(tetangga.sesudah); }
  };
})();
