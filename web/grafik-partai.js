/* Dua grafik SVG halaman partai: sisa waktu (%clk) dan evaluasi engine.

   Dipisah dari papan.js 25 Sep 2026 (audit nomor 8: berkas itu 1.194 baris).
   Keduanya dipilih lebih dulu karena paling mandiri — isinya murni menggambar
   dari data yang diberikan, dan satu-satunya yang dibutuhkan dari halaman
   adalah "apa yang terjadi kalau grafiknya diklik". Itu disuntik lewat
   `saatKlik(indeks)`, jadi modul ini tidak tahu apa-apa soal papan, autoplay,
   maupun navigasi langkah.

   Dipakai oleh web/partai.html lewat window.GrafikPartai; harus dimuat
   SEBELUM papan.js. */

window.GrafikPartai = (function () {
  "use strict";

  function svgEl(tag, atribut) {
    var e = document.createElementNS("http://www.w3.org/2000/svg", tag);
    for (var k in atribut) e.setAttribute(k, atribut[k]);
    return e;
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

  /* Siapa yang jalan di ply 0 dan nomor langkahnya, dari tag [FEN]. Partai
     "From Position" bisa mulai dengan Hitam (1... e5) atau dari langkah ke-30;
     dulu grafik menganggap ply genap selalu Putih, jadi jam kedua pihak
     tertukar (audit 27 Sep 2026). */
  function awalPartai(pgn) {
    var fen = (AlatCatur.tagPgn(pgn).FEN || "").split(/\s+/);
    return { geser: fen[1] === "b" ? 1 : 0, nomor: parseInt(fen[5], 10) || 1 };
  }

  function gambarJam(g, saatKlik) {
    if (!elJam.kartu) return;
    jamPly = bacaJam(g.pgn || "");
    var awalan = awalPartai(g.pgn || "");
    /* ply i milik Putih kalau (i + geser) genap */
    var milik = function (i, k) { return (i + awalan.geser) % 2 === k; };
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
      for (var i = 0; i < n; i++) if (milik(i, k)) titik.push(x(i) + "," + y(jamPly[i]));
      svg.appendChild(svgEl("polyline", { points: titik.join(" "), fill: "none", stroke: sisi[1], "stroke-width": "1.4" }));
    });
    svg.appendChild(svgEl("line", { id: "penanda-jam", x1: 0, y1: 0, x2: 0, y2: T, stroke: "#2f6f4f", "stroke-width": "1.5" }));
    svg.onclick = function (ev) {
      var kotak = svg.getBoundingClientRect();
      saatKlik(Math.round(((ev.clientX - kotak.left) / kotak.width) * n));
    };

    /* keterangan: langkah pertama masing-masing pihak masuk zeitnot */
    var ket = [];
    [["Putih", 0], ["Hitam", 1]].forEach(function (s) {
      var pertama = null, akhir = null;
      for (var i = 0; i < n; i++) {
        if (!milik(i, s[1])) continue;
        if (pertama === null && jamPly[i] < ZEITNOT) pertama = i;
        akhir = i;
      }
      if (akhir === null) return;
      ket.push(s[0] + ": " + (pertama === null ? "tidak pernah < 10 s"
        : "< 10 s sejak langkah " + (awalan.nomor + Math.floor((pertama + awalan.geser) / 2))) +
        " · akhir " + formatDetik(jamPly[akhir]));
    });
    elJam.ket.textContent = ket.join("   ·   ");
    elJam.kartu.hidden = false;
  }

  function penandaJam(indeks) {
    var p = document.getElementById("penanda-jam");
    if (!p || !jamPly.length) return;
    var xx = (indeks / jamPly.length) * 600;
    p.setAttribute("x1", xx); p.setAttribute("x2", xx);
    if (elJam.ket && indeks > 0 && jamPly[indeks - 1] !== undefined) {
      elJam.ket.title = "Setelah langkah ini: " + formatDetik(jamPly[indeks - 1]) + " tersisa";
    }
  }

  /* ---------- grafik evaluasi (SVG inline, tanpa library) ---------- */

  var elEval = {
    kartu: document.getElementById("grafik-kartu"),
    grafik: document.getElementById("grafik")
  };
  var analisisTerakhir = null;
  var LEBAR = 600, TINGGI = 120, BATAS = 1000;

  function yDariCp(cp, mat) {
    var v = cp;
    if (mat !== null && mat !== undefined) v = mat === 0 ? cp : (mat > 0 ? BATAS : -BATAS);
    v = Math.max(-BATAS, Math.min(BATAS, v));
    return TINGGI / 2 - (v / BATAS) * (TINGGI / 2 - 4);
  }

  function gambarEval(analisis, saatKlik) {
    var g = elEval.grafik;
    analisisTerakhir = analisis;
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
      saatKlik(Math.round(((ev.clientX - kotak.left) / kotak.width) * n));
    };
    elEval.kartu.hidden = false;
  }

  function penandaEval(indeks) {
    var p = document.getElementById("penanda-grafik");
    if (!p || !analisisTerakhir) return;
    var x = (indeks / analisisTerakhir.langkah.length) * LEBAR;
    p.setAttribute("x1", x); p.setAttribute("x2", x);
  }

  return {
    gambarJam: gambarJam,
    penandaJam: penandaJam,
    gambarEval: gambarEval,
    penandaEval: penandaEval,
    /* dipakai tes dan halaman lain yang perlu membaca jam dari PGN */
    bacaJam: bacaJam,
    formatDetik: formatDetik
  };
})();
