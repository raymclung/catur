/* Kemajuan: apakah permainan Ray membaik dari waktu ke waktu.

   Semua dihitung dari daftar partai (`/api/partai`) saja — tanggal, Elo,
   hasil, akurasi engine, dan format sudah ada di sana sejak kolom `format`
   dipasang backend (Permintaan API #8). Tidak ada permintaan analisis per
   partai, jadi halaman ini cepat walau mencakup ribuan partai.

   Catatan jujur soal Elo: rating bullet, blitz, dan rapid adalah angka yang
   berbeda, jadi grafik rating menggambar satu garis per format, bukan satu
   garis campuran. Akurasi hanya dihitung dari partai yang sudah dianalisis;
   periode dengan sedikit partai teranalisis ditandai. */

(function () {
  "use strict";

  var el = {
    galat: document.getElementById("galat"),
    akun: document.getElementById("akun"),
    format: document.getElementById("format"),
    batas: document.getElementById("batas"),
    kelompok: document.getElementById("kelompok"),
    status: document.getElementById("status"),
    ikhtisar: document.getElementById("ikhtisar"),
    grafikElo: document.getElementById("grafik-elo"),
    ketElo: document.getElementById("ket-elo"),
    legendaElo: document.getElementById("legenda-elo"),
    grafikAkurasi: document.getElementById("grafik-akurasi"),
    ketAkurasi: document.getElementById("ket-akurasi"),
    baris: document.getElementById("baris")
  };

  var WARNA_FORMAT = { bullet: "#b45309", blitz: "#2f6f4f", rapid: "#2563eb", klasik: "#7c3aed", tanpa: "#6b7280" };
  var MIN_AKURASI = 5;      // periode dengan partai teranalisis lebih sedikit dari ini ditandai
  var akunSemua = [];
  var permintaanKe = 0;

  /* desimal koma seperti halaman lain; sebelumnya "56.9%" bercampur "54,9%" */
  function desimal(v) {
    return Number(v).toLocaleString("id-ID", { maximumFractionDigits: 1 });
  }

  function teks(isi) { return document.createTextNode(isi === null || isi === undefined ? "" : String(isi)); }
  function buat(tag, kelas, isi) {
    var e = document.createElement(tag);
    if (kelas) e.className = kelas;
    if (isi !== undefined) e.appendChild(teks(isi));
    return e;
  }
  function galat(p) { el.galat.textContent = p; el.galat.hidden = false; }
  function ambil(url) {
    return fetch(url).then(function (r) {
      if (!r.ok) throw new Error("Server menjawab " + r.status + " untuk " + url);
      return r.json();
    });
  }
  function svgEl(tag, atribut) {
    var e = document.createElementNS("http://www.w3.org/2000/svg", tag);
    for (var k in atribut) e.setAttribute(k, atribut[k]);
    return e;
  }

  /* ---------- ambil partai ---------- */

  function partaiAkun(akun, batas) {
    var dasar = "/api/partai?pemain_id=" + akun.id + "&per_halaman=100&urut=terbaru" +
      (el.format.value !== "semua" ? "&format=" + el.format.value : "");
    var kumpul = [];
    function halaman(n) {
      if (kumpul.length >= batas) return Promise.resolve(kumpul);
      return ambil(dasar + "&halaman=" + n).then(function (d) {
        d.partai.forEach(function (g) { kumpul.push({ g: g, warna: g.putih_id === akun.id ? "putih" : "hitam", akun: akun }); });
        el.status.textContent = "mengambil " + kumpul.length + " partai…";
        if (n >= d.jumlah_halaman || kumpul.length >= batas) return kumpul;
        return halaman(n + 1);
      });
    }
    return halaman(1);
  }

  /* ---------- pengelompokan ---------- */

  function kunciPeriode(tanggal, mode) {
    if (!tanggal) return null;
    var b = String(tanggal).replace(/-/g, ".").split(".");
    if (b.length < 2) return null;
    return mode === "tahun" ? b[0] : b[0] + "." + b[1];
  }

  function Periode(kunci) {
    this.kunci = kunci; this.partai = 0; this.menang = 0; this.remis = 0; this.kalah = 0;
    this.eloJumlah = {}; this.eloN = {}; this.akurasiJumlah = 0; this.akurasiN = 0;
  }
  Periode.prototype.tambah = function (x) {
    var g = x.g, warna = x.warna;
    this.partai++;
    var hasil = g.hasil;
    if (hasil === "1/2-1/2") this.remis++;
    else if ((hasil === "1-0") === (warna === "putih")) this.menang++;
    else if (hasil === "1-0" || hasil === "0-1") this.kalah++;
    var elo = warna === "putih" ? g.elo_putih : g.elo_hitam;
    var f = AlatCatur.formatPartai(g);
    if (elo) {
      this.eloJumlah[f] = (this.eloJumlah[f] || 0) + elo;
      this.eloN[f] = (this.eloN[f] || 0) + 1;
    }
    var ak = warna === "putih" ? g.akurasi_putih : g.akurasi_hitam;
    if (g.dianalisis && ak !== null && ak !== undefined) { this.akurasiJumlah += ak; this.akurasiN++; }
  };
  Periode.prototype.skor = function () {
    var selesai = this.menang + this.remis + this.kalah;
    return selesai ? Math.round(1000 * (this.menang + this.remis / 2) / selesai) / 10 : null;
  };
  Periode.prototype.elo = function (f) { return this.eloN[f] ? Math.round(this.eloJumlah[f] / this.eloN[f]) : null; };
  Periode.prototype.eloSemua = function () {
    var j = 0, n = 0;
    for (var f in this.eloJumlah) { j += this.eloJumlah[f]; n += this.eloN[f]; }
    return n ? Math.round(j / n) : null;
  };
  Periode.prototype.akurasi = function () { return this.akurasiN ? Math.round(10 * this.akurasiJumlah / this.akurasiN) / 10 : null; };

  /* ---------- grafik ---------- */

  var L = 600, T = 140, PAD = 10;

  function gambarGaris(svg, periode, nilai, warnaGaris, min, maks, judulTitik) {
    var n = periode.length;
    if (n < 2) return false;
    var x = function (i) { return PAD + (i / (n - 1)) * (L - 2 * PAD); };
    var y = function (v) { return T - PAD - ((v - min) / (maks - min || 1)) * (T - 2 * PAD); };
    var titik = [], ada = false;
    periode.forEach(function (p, i) {
      var v = nilai(p);
      if (v === null || v === undefined) { return; }
      ada = true;
      titik.push(x(i) + "," + y(v));
    });
    if (!ada || titik.length < 2) return false;
    svg.appendChild(svgEl("polyline", { points: titik.join(" "), fill: "none", stroke: warnaGaris, "stroke-width": "1.6" }));
    periode.forEach(function (p, i) {
      var v = nilai(p);
      if (v === null || v === undefined) return;
      var c = svgEl("circle", { cx: x(i), cy: y(v), r: 2.4, fill: warnaGaris });
      c.appendChild(svgEl("title", {})).textContent = p.kunci + ": " + judulTitik(p, v);
      svg.appendChild(c);
    });
    return true;
  }

  function siapkanSvg(svg) {
    svg.textContent = "";
    svg.appendChild(svgEl("rect", { x: 0, y: 0, width: L, height: T, fill: "#fafaf8" }));
  }

  function garisBantu(svg, min, maks, label) {
    [0.25, 0.5, 0.75].forEach(function (p) {
      var yy = PAD + p * (T - 2 * PAD);
      svg.appendChild(svgEl("line", { x1: PAD, y1: yy, x2: L - PAD, y2: yy, stroke: "#e2e2df", "stroke-width": "0.6" }));
    });
    var t = svgEl("text", { x: PAD + 2, y: PAD + 9, "font-size": "9", fill: "#6b7280" });
    t.textContent = label(maks);
    svg.appendChild(t);
    var b = svgEl("text", { x: PAD + 2, y: T - PAD - 2, "font-size": "9", fill: "#6b7280" });
    b.textContent = label(min);
    svg.appendChild(b);
  }

  function gambarElo(periode) {
    siapkanSvg(el.grafikElo);
    el.legendaElo.textContent = "";
    var format = {};
    periode.forEach(function (p) { for (var f in p.eloN) format[f] = true; });
    var daftarFormat = Object.keys(format).filter(function (f) {
      return periode.filter(function (p) { return p.elo(f) !== null; }).length >= 2;
    });
    if (!daftarFormat.length) { el.ketElo.textContent = "· tidak ada rating di data ini"; return; }
    var semuaNilai = [];
    daftarFormat.forEach(function (f) {
      periode.forEach(function (p) { var v = p.elo(f); if (v !== null) semuaNilai.push(v); });
    });
    var min = Math.min.apply(null, semuaNilai) - 20, maks = Math.max.apply(null, semuaNilai) + 20;
    garisBantu(el.grafikElo, min, maks, function (v) { return Math.round(v); });
    daftarFormat.forEach(function (f) {
      gambarGaris(el.grafikElo, periode, function (p) { return p.elo(f); }, WARNA_FORMAT[f] || "#6b7280", min, maks,
        function (p, v) { return f + " " + v + " (" + p.eloN[f] + " partai)"; });
      var k = buat("span", null, "■ " + f + "  ");
      k.style.color = WARNA_FORMAT[f] || "#6b7280";
      el.legendaElo.appendChild(k);
    });
    var awal = null, akhir = null;
    /* format dengan partai terbanyak, bukan yang kebetulan pertama di daftar:
       kalimat "bullet 2650 → 2700" tidak boleh datang dari format yang cuma
       dimainkan dua kali */
    var utama = daftarFormat.slice().sort(function (a, b) {
      var na = 0, nb = 0;
      periode.forEach(function (p) { na += p.eloN[a] || 0; nb += p.eloN[b] || 0; });
      return nb - na;
    })[0];
    periode.forEach(function (p) { var v = p.elo(utama); if (v !== null) { if (awal === null) awal = v; akhir = v; } });
    var nUtama = 0;
    periode.forEach(function (p) { nUtama += p.eloN[utama] || 0; });
    el.ketElo.textContent = "· rata-rata per periode" + (awal !== null && akhir !== null ?
      " · " + utama + " " + awal + " → " + akhir + " (" + (akhir - awal >= 0 ? "+" : "") + (akhir - awal) +
      ", " + nUtama.toLocaleString("id-ID") + " partai)" : "");
  }

  function gambarAkurasi(periode) {
    siapkanSvg(el.grafikAkurasi);
    var ada = periode.filter(function (p) { return p.akurasi() !== null; });
    if (ada.length < 2) {
      el.ketAkurasi.textContent = "Belum cukup partai yang dianalisis untuk menggambar tren. Jalankan analisis engine dari halaman Mesin, atau lihat PANDUAN-TERMINAL.md.";
      return;
    }
    var nilai = ada.map(function (p) { return p.akurasi(); });
    var min = Math.max(0, Math.min.apply(null, nilai) - 3), maks = Math.min(100, Math.max.apply(null, nilai) + 3);
    garisBantu(el.grafikAkurasi, min, maks, function (v) { return Math.round(v) + "%"; });
    gambarGaris(el.grafikAkurasi, periode, function (p) { return p.akurasi(); }, "#2f6f4f", min, maks,
      function (p, v) { return desimal(v) + "% dari " + p.akurasiN + " partai"; });
    var kurang = ada.filter(function (p) { return p.akurasiN < MIN_AKURASI; }).length;
    el.ketAkurasi.textContent = ada.length + " periode punya partai teranalisis" +
      (kurang ? " · " + kurang + " di antaranya kurang dari " + MIN_AKURASI + " partai, jangan ditarik kesimpulan" : "");
  }

  /* ---------- tabel & ikhtisar ---------- */

  function gambarTabel(periode, akun) {
    el.baris.textContent = "";
    var urut = periode.slice().reverse();   // terbaru dulu
    urut.forEach(function (p) {
      var tr = document.createElement("tr");
      tr.appendChild(buat("td", "mono", p.kunci));
      tr.appendChild(buat("td", "mono", p.partai));
      tr.appendChild(buat("td", "mono", p.skor() === null ? "—" : desimal(p.skor()) + "%"));
      tr.appendChild(buat("td", "mono kecil redup", "+" + p.menang + " =" + p.remis + " −" + p.kalah));
      tr.appendChild(buat("td", "mono", p.eloSemua() === null ? "—" : p.eloSemua()));
      var tdA = buat("td", "mono", p.akurasi() === null ? "—" : desimal(p.akurasi()) + "%");
      if (p.akurasi() !== null && p.akurasiN < MIN_AKURASI) {
        var w = buat("span", "redup kecil", " ⚠");
        w.title = "hanya " + p.akurasiN + " partai teranalisis";
        tdA.appendChild(w);
      }
      tr.appendChild(tdA);
      tr.appendChild(buat("td", "mono redup", p.akurasiN + " / " + p.partai));
      var tdL = buat("td", "kecil");
      tdL.style.whiteSpace = "nowrap";
      var a = document.createElement("a");
      var q = new URLSearchParams();
      if (akun.length === 1) q.set("pemain_id", akun[0].id);
      if (el.format.value !== "semua") q.set("format", el.format.value);
      a.href = "/" + (q.toString() ? "?" + q.toString() : "");
      a.title = "Daftar partai akun ini (daftar belum bisa disaring per periode)";
      a.appendChild(teks("partai →"));
      tdL.appendChild(a);
      tr.appendChild(tdL);
      tr.style.cursor = "default";
      el.baris.appendChild(tr);
    });
    if (!urut.length) {
      var tr0 = document.createElement("tr");
      var td = buat("td", "pesan kecil", "Tidak ada partai untuk saringan ini.");
      td.colSpan = 8; tr0.appendChild(td); el.baris.appendChild(tr0);
    }
  }

  function gambarIkhtisar(semua, periode) {
    var total = new Periode("semua");
    semua.forEach(function (x) { total.tambah(x); });
    var pertama = semua.length ? semua[semua.length - 1].g.tanggal : null;
    var terakhir = semua.length ? semua[0].g.tanggal : null;
    el.ikhtisar.style.gridTemplateColumns = "repeat(4, 1fr)";
    el.ikhtisar.textContent = "";
    /* Akurasi hanya ada untuk partai yang sudah dianalisis, dan partai yang
       dianalisis DIPILIH dari yang kalah lebih dulu — jadi angkanya tidak
       mewakili semua partai. Cakupannya ditulis di sebelah angkanya, bukan
       dibiarkan terbaca seperti akurasi keseluruhan. */
    var cakupan = total.partai ? Math.round(1000 * total.akurasiN / total.partai) / 10 : null;
    [["Partai dihitung", total.partai.toLocaleString("id-ID"), ""],
     ["Rentang", (pertama || "?") + " – " + (terakhir || "?"), ""],
     ["Skor keseluruhan", total.skor() === null ? "—" : desimal(total.skor()) + "%", "dari semua partai di rentang ini"],
     ["Akurasi rata-rata", total.akurasi() === null ? "—" : desimal(total.akurasi()) + "%",
      total.akurasiN ? "dari " + total.akurasiN.toLocaleString("id-ID") + " partai teranalisis (" +
        String(cakupan).replace(".", ",") + "%)" : "belum ada partai teranalisis"]
    ].forEach(function (x) {
      var k = buat("div");
      k.appendChild(buat("div", "label", x[0]));
      k.appendChild(buat("div", "angka-besar", x[1]));
      if (x[2]) k.appendChild(buat("div", "kecil redup", x[2]));
      el.ikhtisar.appendChild(k);
    });
    if (total.akurasiN && total.akurasiN < total.partai / 2) {
      var p = buat("div", "kecil redup");
      p.style.padding = "0 14px 12px";
      p.textContent = "Akurasi dihitung dari sebagian kecil partai, dan partai yang dianalisis " +
        "didahulukan yang kalah — jadi angkanya lebih rendah daripada permainanmu sebenarnya, " +
        "dan naik-turunnya belum tentu berarti membaik atau memburuk.";
      el.ikhtisar.parentNode.appendChild(p);
    }
  }

  /* ---------- muat ---------- */

  function muat() {
    var ke = ++permintaanKe;
    var daftarAkun = el.akun.value === "semua" ? akunSemua : akunSemua.filter(function (a) { return String(a.id) === el.akun.value; });
    var batas = parseInt(el.batas.value, 10);
    el.galat.hidden = true;
    el.status.textContent = "mengambil daftar…";

    Promise.all(daftarAkun.map(function (a) { return partaiAkun(a, batas); })).then(function (kelompok) {
      if (ke !== permintaanKe) return;
      var semua = [];
      kelompok.forEach(function (k) { semua = semua.concat(k); });
      var terlihat = {};
      semua = semua.filter(function (x) { if (terlihat[x.g.id]) return false; terlihat[x.g.id] = true; return true; });
      semua = semua.filter(function (x) { return x.g.tanggal; });
      semua.sort(function (a, b) { return (b.g.tanggal || "").localeCompare(a.g.tanggal || ""); });
      semua = semua.slice(0, batas);
      el.status.textContent = "";
      if (!semua.length) { galat("Tidak ada partai untuk saringan ini."); gambarTabel([], daftarAkun); return; }

      var mode = el.kelompok.value;
      if (mode === "auto") {
        var bulan = {};
        semua.forEach(function (x) { bulan[kunciPeriode(x.g.tanggal, "bulan")] = true; });
        mode = Object.keys(bulan).length > 30 ? "tahun" : "bulan";
      }
      var peta = {};
      semua.forEach(function (x) {
        var k = kunciPeriode(x.g.tanggal, mode);
        if (!k) return;
        (peta[k] || (peta[k] = new Periode(k))).tambah(x);
      });
      var periode = Object.keys(peta).sort().map(function (k) { return peta[k]; });

      gambarIkhtisar(semua, periode);
      gambarElo(periode);
      gambarAkurasi(periode);
      gambarTabel(periode, daftarAkun);
    }).catch(function (e) { galat("Gagal memuat: " + e.message); el.status.textContent = ""; });
  }

  /* ---------- mulai ---------- */

  var q = new URLSearchParams(window.location.search);
  ["format", "batas", "kelompok"].forEach(function (k) {
    if (q.get(k) && el[k].querySelector('option[value="' + q.get(k) + '"]')) el[k].value = q.get(k);
  });

  AkunPribadi.muat().then(function (akun) {
    akunSemua = akun;
    el.akun.textContent = "";
    akun.forEach(function (p) {
      var o = document.createElement("option");
      o.value = p.id;
      o.textContent = p.nama + (p.sumber ? " · " + p.sumber : "") + " (" + p.jumlah_partai + ")";
      el.akun.appendChild(o);
    });
    var semua = document.createElement("option");
    semua.value = "semua";
    semua.textContent = "semua akun (" + akun.length + ")";
    el.akun.appendChild(semua);
    if (q.get("akun") && el.akun.querySelector('option[value="' + q.get("akun") + '"]')) el.akun.value = q.get("akun");
    if (!akun.length) throw new Error("Tidak ada akun pribadi di database.");
    muat();
  }).catch(function (e) { galat(e.message); });

  [el.akun, el.format, el.batas, el.kelompok].forEach(function (s) { s.addEventListener("change", muat); });
})();
