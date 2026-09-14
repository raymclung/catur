/* Pola kelemahan (RENCANA Phase 7 "identify weaknesses"): dari partai
   teranalisis akun pribadi, di mana kesalahan menumpuk — fase permainan,
   warna, format waktu, dan pembukaan. Ukurannya kesalahan per 100 langkah
   sendiri (blunder + kesalahan), supaya kelompok besar dan kecil bisa
   dibandingkan; kelompok < 30 langkah ditandai sampel kecil.

   Semua dari endpoint yang ada: /api/partai (daftar teranalisis per akun),
   /api/partai/{id} (kontrol waktu → format), /api/partai/{id}/analisis. */

(function () {
  "use strict";

  var el = {
    galat: document.getElementById("galat"),
    akun: document.getElementById("akun"),
    lawan: document.getElementById("lawan"),
    format: document.getElementById("format"),
    batas: document.getElementById("batas"),
    status: document.getElementById("status"),
    ikhtisar: document.getElementById("ikhtisar"),
    perFase: document.getElementById("per-fase"),
    perWarnaFormat: document.getElementById("per-warna-format"),
    perPembukaan: document.getElementById("per-pembukaan")
  };

  var akunSemua = [];
  var permintaanKe = 0;
  var SAMPEL_KECIL = 30;

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

  /* ---------- saringan (sama dengan latihan & kekalahan) ---------- */

  /* Saringan lawan & format: aturan bersama di web/alat-catur.js. */
  function lolosLawan(g, warna) { return AlatCatur.lolosLawan(g, warna, el.lawan.value); }

  function fase(nomor) { return nomor <= 12 ? "pembukaan" : nomor <= 40 ? "tengah" : "akhir"; }
  function keluarga(g) {
    if (g.pembukaan) return g.pembukaan.split(":")[0].split(",")[0].trim();
    return g.eco ? "ECO " + g.eco : "tanpa ECO";
  }

  /* ---------- pengumpulan ---------- */

  function Kelompok() { this.langkah = 0; this.blunder = 0; this.mistake = 0; this.inaccuracy = 0; this.hilang = 0; this.partai = {}; }
  Kelompok.prototype.tambah = function (l, partaiId) {
    this.langkah++;
    if (l.klasifikasi === "blunder") this.blunder++;
    else if (l.klasifikasi === "mistake") this.mistake++;
    else if (l.klasifikasi === "inaccuracy") this.inaccuracy++;
    this.hilang += l.kehilangan_cp || 0;
    this.partai[partaiId] = true;
  };
  Kelompok.prototype.per100 = function () { return this.langkah ? 100 * (this.blunder + this.mistake) / this.langkah : 0; };
  Kelompok.prototype.jumlahPartai = function () { return Object.keys(this.partai).length; };

  function partaiAkun(akun, batas) {
    /* Saringan format dikerjakan server (kolom `format`, Permintaan API #8),
       jadi halaman ini tidak perlu mengambil detail tiap partai lagi. */
    var f = el.format.value !== "semua" ? "&format=" + el.format.value : "";
    return ambil("/api/partai?pemain_id=" + akun.id + "&dianalisis=true&per_halaman=100&urut=terbaru" + f).then(function (d) {
      return d.partai.map(function (g) { return { g: g, warna: g.putih_id === akun.id ? "putih" : "hitam", akun: akun }; })
        .filter(function (x) { return lolosLawan(x.g, x.warna); }).slice(0, batas);
    });
  }

  function muat() {
    var ke = ++permintaanKe;
    var daftarAkun = el.akun.value === "semua" ? akunSemua : akunSemua.filter(function (a) { return String(a.id) === el.akun.value; });
    var batas = parseInt(el.batas.value, 10);
    var formatDipilih = el.format.value;
    el.galat.hidden = true;
    el.status.textContent = "mengambil daftar…";
    [el.ikhtisar, el.perFase, el.perWarnaFormat, el.perPembukaan].forEach(function (e) { e.textContent = ""; });

    Promise.all(daftarAkun.map(function (a) { return partaiAkun(a, batas); })).then(function (kelompok) {
      var partai = [];
      kelompok.forEach(function (k) { partai = partai.concat(k); });
      var terlihat = {};
      partai = partai.filter(function (x) { if (terlihat[x.g.id]) return false; terlihat[x.g.id] = true; return true; });
      partai.sort(function (a, b) { return (b.g.tanggal || "").localeCompare(a.g.tanggal || ""); });
      partai = partai.slice(0, batas);
      if (!partai.length) throw new Error("Belum ada partai teranalisis untuk saringan ini.");

      var agg = { total: new Kelompok(), fase: {}, warna: {}, format: {}, pembukaan: {} };
      function kel(peta, kunci) { return peta[kunci] || (peta[kunci] = new Kelompok()); }
      var dipakai = 0;
      var rantai = Promise.resolve();
      partai.forEach(function (x, i) {
        rantai = rantai.then(function () {
          if (ke !== permintaanKe) return;
          el.status.textContent = "menghitung " + (i + 1) + " dari " + partai.length + "…";
          var fmt = AlatCatur.formatPartai(x.g);
          if (formatDipilih !== "semua" && fmt !== formatDipilih) return;
          return ambil("/api/partai/" + x.g.id + "/analisis").then(function (a) {
            dipakai++;
            a.langkah.forEach(function (l) {
              if (l.warna !== x.warna) return;
              agg.total.tambah(l, x.g.id);
              kel(agg.fase, fase(l.nomor_langkah)).tambah(l, x.g.id);
              kel(agg.warna, x.warna).tambah(l, x.g.id);
              kel(agg.format, fmt).tambah(l, x.g.id);
              kel(agg.pembukaan, keluarga(x.g)).tambah(l, x.g.id);
            });
          }).catch(function () { /* partai tanpa analisis dilewati */ });
        });
      });
      return rantai.then(function () { if (ke === permintaanKe) gambar(agg, dipakai); });
    }).catch(function (e) { galat(e.message); el.status.textContent = ""; });
  }

  /* ---------- tampilan ---------- */

  function tabel(wadah, judul, peta, urutan, catatan, tautan) {
    wadah.textContent = "";
    wadah.appendChild(buat("h2", "judul-kartu", judul));
    if (catatan) { var c = buat("div", "kecil redup", catatan); c.style.padding = "0 14px 8px"; wadah.appendChild(c); }
    var kunci = urutan || Object.keys(peta).sort(function (a, b) { return peta[b].per100() - peta[a].per100(); });
    var t = document.createElement("table");
    var thead = document.createElement("thead"), trh = document.createElement("tr");
    ["", "Partai", "Langkah", "??", "?", "?!", "Kesalahan / 100", "Rata loss"].forEach(function (k) { trh.appendChild(buat("th", null, k)); });
    thead.appendChild(trh); t.appendChild(thead);
    var tb = document.createElement("tbody");
    var terburuk = Math.max.apply(null, kunci.map(function (k) { return peta[k] ? peta[k].per100() : 0; }).concat([0.01]));
    kunci.forEach(function (k) {
      var g = peta[k];
      if (!g) return;
      var tr = document.createElement("tr");
      var td0 = document.createElement("td");
      if (tautan && tautan(k)) {
        var a = document.createElement("a"); a.href = tautan(k); a.title = "Latihan dari kelompok ini"; a.appendChild(teks(k)); td0.appendChild(a);
      } else td0.appendChild(teks(k));
      if (g.langkah < SAMPEL_KECIL) { var s = buat("span", "redup kecil", " ⚠"); s.title = "sampel kecil (< " + SAMPEL_KECIL + " langkah)"; td0.appendChild(s); }
      tr.appendChild(td0);
      tr.appendChild(buat("td", "mono", g.jumlahPartai()));
      tr.appendChild(buat("td", "mono", g.langkah));
      tr.appendChild(buat("td", "mono klas-blunder", g.blunder));
      tr.appendChild(buat("td", "mono klas-mistake", g.mistake));
      tr.appendChild(buat("td", "mono klas-inaccuracy", g.inaccuracy));
      var tdR = buat("td", "mono");
      var bar = buat("div", "pref-baris"); bar.style.gridTemplateColumns = "44px 1fr";
      bar.appendChild(buat("span", null, g.per100().toFixed(1)));
      var isiW = buat("div"); var isi = buat("div", "isi"); isi.style.width = Math.max(2, 100 * g.per100() / terburuk) + "%";
      isi.style.background = g.per100() >= 0.75 * terburuk ? "#b3261e" : "var(--aksen)"; isiW.appendChild(isi); bar.appendChild(isiW);
      tdR.appendChild(bar);
      tr.appendChild(tdR);
      tr.appendChild(buat("td", "mono redup", g.langkah ? Math.round(g.hilang / g.langkah) + " cp" : "—"));
      tr.style.cursor = "default";
      tb.appendChild(tr);
    });
    t.appendChild(tb);
    var gulir = buat("div", "gulir"); gulir.appendChild(t); wadah.appendChild(gulir);
  }

  function gambar(agg, dipakai) {
    el.status.textContent = "";
    var tot = agg.total;
    el.ikhtisar.style.gridTemplateColumns = "repeat(4, 1fr)";
    [["Partai dihitung", dipakai], ["Langkah sendiri", tot.langkah.toLocaleString("id-ID")],
     ["Kesalahan / 100 langkah", tot.per100().toFixed(1)], ["Rata loss per langkah", (tot.langkah ? Math.round(tot.hilang / tot.langkah) : 0) + " cp"]
    ].forEach(function (x) {
      var k = buat("div"); k.appendChild(buat("div", "label", x[0])); k.appendChild(buat("div", "angka-besar", x[1])); el.ikhtisar.appendChild(k);
    });

    var dasarUrl = "/static/latihan.html?akun=" + encodeURIComponent(el.akun.value) + "&lawan=" + el.lawan.value;
    tabel(el.perFase, "Per fase permainan", agg.fase, ["pembukaan", "tengah", "akhir"],
      "pembukaan = langkah 1–12, tengah = 13–40, akhir = 41+ · kesalahan = ?? + ?");
    var wf = {};
    Object.keys(agg.warna).forEach(function (k) { wf["sebagai " + (k === "putih" ? "Putih" : "Hitam")] = agg.warna[k]; });
    Object.keys(agg.format).forEach(function (k) { wf[k] = agg.format[k]; });
    tabel(el.perWarnaFormat, "Per warna dan format", wf, null, null, function (k) {
      return ["bullet", "blitz", "rapid", "klasik"].indexOf(k) >= 0 ? dasarUrl + "&format=" + k : null;
    });
    var urutPembukaan = Object.keys(agg.pembukaan)
      .filter(function (k) { return agg.pembukaan[k].langkah >= 15; })
      .sort(function (a, b) { return agg.pembukaan[b].per100() - agg.pembukaan[a].per100(); }).slice(0, 15);
    tabel(el.perPembukaan, "Per pembukaan (15 terburuk, minimal 15 langkah)", agg.pembukaan, urutPembukaan,
      "kelompok pembukaan diambil dari nama sebelum tanda titik dua");
  }

  /* ---------- mulai ---------- */

  var q = new URLSearchParams(window.location.search);
  ["lawan", "format", "batas"].forEach(function (k) {
    if (q.get(k) && el[k].querySelector('option[value="' + q.get(k) + '"]')) el[k].value = q.get(k);
  });

  AkunPribadi.muat().then(function (akun) {
    akunSemua = akun;
    el.akun.textContent = "";
    var semua = document.createElement("option");
    semua.value = "semua"; semua.textContent = "semua akun (" + akun.length + ")"; el.akun.appendChild(semua);
    akun.forEach(function (p) {
      var o = document.createElement("option"); o.value = p.id; o.textContent = p.nama + (p.sumber ? " · " + p.sumber : ""); el.akun.appendChild(o);
    });
    if (q.get("akun") && el.akun.querySelector('option[value="' + q.get("akun") + '"]')) el.akun.value = q.get("akun");
    if (!akun.length) throw new Error("Tidak ada akun pribadi di database.");
    muat();
  }).catch(function (e) { galat(e.message); });

  [el.akun, el.lawan, el.format, el.batas].forEach(function (s) { s.addEventListener("change", muat); });
})();
