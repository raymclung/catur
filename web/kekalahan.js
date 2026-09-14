/* Penyelidikan kekalahan: partai kalah akun pribadi digolongkan menurut
   penyebabnya — kalah waktu, blunder cepat (missclick), blunder, dikalahkan,
   atau belum dianalisis.

   ATURANNYA ADA DI SERVER: `GET /api/kekalahan` (app/sebab_kalah.py). Halaman
   ini hanya menampilkan. Sebelum 13 Sep 2026 penggolongannya dihitung di sini
   dan butuh dua permintaan per partai (80 partai = 160 permintaan), dan karena
   aturannya cuma hidup di berkas ini, halaman Ringkasan/Kemajuan tidak bisa
   memakai angka yang sama. */

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
    jumlah: document.getElementById("jumlah"),
    baris: document.getElementById("baris")
  };

  var akunSemua = [];
  var permintaanKe = 0;

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

  var LABEL = {
    waktu: "kalah waktu", missclick: "blunder cepat (missclick?)", blunder: "blunder",
    dikalahkan: "dikalahkan", belum: "belum dianalisis"
  };

  var ARTI = {
    waktu: "Jam habis waktu posisinya masih seimbang atau unggul — ini soal pengelolaan waktu",
    missclick: "Blunder ≥ 300 cp yang dimainkan ≤ 2 detik",
    blunder: "Blunder ≥ 300 cp dengan waktu pikir normal",
    dikalahkan: "Tidak ada blunder besar; kalah perlahan atau dimatikan",
    belum: "Belum dianalisis engine"
  };

  /* ---------- pengumpulan ----------

     Penggolongan sebab kekalahan sekarang dikerjakan SERVER
     (`GET /api/kekalahan`, app/sebab_kalah.py), bukan di sini. Sebelumnya
     halaman ini mengambil PGN dan analisis satu per satu — 80 partai berarti
     160 permintaan — dan aturannya hanya hidup di berkas ini, jadi halaman
     lain tidak bisa memakai angka yang sama (Ray, 13 Sep: "perbaiki di
     backendnya juga"). */

  function muat() {
    var ke = ++permintaanKe;
    var daftarAkun = el.akun.value === "semua" ? akunSemua
      : akunSemua.filter(function (a) { return String(a.id) === el.akun.value; });
    el.galat.hidden = true;
    el.status.textContent = "menyelidiki…";
    el.baris.textContent = "";
    el.ikhtisar.textContent = "";
    if (!daftarAkun.length) { el.status.textContent = ""; return Promise.resolve(); }

    var q = daftarAkun.map(function (a) { return "pemain_id=" + a.id; }).join("&") +
      "&lawan=" + encodeURIComponent(el.lawan.value) +
      "&batas=" + encodeURIComponent(el.batas.value) +
      (el.format.value !== "semua" ? "&format=" + encodeURIComponent(el.format.value) : "");

    return ambil("/api/kekalahan?" + q).then(function (d) {
      if (ke !== permintaanKe) return;
      gambar(d.partai.map(bentukBaris), d.ikhtisar, d.zeitnot);
    }).catch(function (e) {
      galat("Gagal memuat: " + e.message);
      el.status.textContent = "";
    });
  }

  /* Bentuk lama {x:{g,warna,akun}, det, format, gol} dipertahankan supaya
     penggambaran tabel tidak perlu ditulis ulang. */
  function bentukBaris(p) {
    var g = { id: p.id, tanggal: p.tanggal };
    if (p.warna === "putih") {
      g.putih = p.akun; g.hitam = p.lawan;
      g.hitam_gelar = p.lawan_gelar; g.elo_hitam = p.lawan_elo;
    } else {
      g.hitam = p.akun; g.putih = p.lawan;
      g.putih_gelar = p.lawan_gelar; g.elo_putih = p.lawan_elo;
    }
    return {
      x: { g: g, warna: p.warna, akun: { nama: p.akun } },
      det: { kontrol_waktu: p.kontrol_waktu },
      format: p.format || "tanpa",
      gol: { sebab: p.sebab, ket: p.keterangan, ply: p.ply,
             zeitnot: p.zeitnot, habisWaktu: p.habis_waktu }
    };
  }

  /* ---------- tampilan ---------- */

  function gambar(hasil, ikhtisar, zeitnot) {
    el.status.textContent = "";
    var urutSebab = ["waktu", "missclick", "blunder", "dikalahkan", "belum"];
    var hitung = {};
    urutSebab.forEach(function (s) { hitung[s] = ikhtisar && ikhtisar[s] ? ikhtisar[s] : 0; });
    el.ikhtisar.textContent = "";
    el.ikhtisar.style.gridTemplateColumns = "repeat(" + (urutSebab.length + 1) + ", 1fr)";
    urutSebab.forEach(function (s) {
      var k = buat("div");
      k.title = ARTI[s] || "";
      k.appendChild(buat("div", "label", LABEL[s]));
      k.appendChild(buat("div", "angka-besar sebab-" + s, hitung[s]));
      k.appendChild(buat("div", "kecil redup", hasil.length ? Math.round(100 * hitung[s] / hasil.length) + "%" : "—"));
      el.ikhtisar.appendChild(k);
    });
    var kz = buat("div");
    kz.appendChild(buat("div", "label", "dalam zeitnot (< 10 s)"));
    kz.appendChild(buat("div", "angka-besar sebab-waktu", zeitnot));
    kz.appendChild(buat("div", "kecil redup", hasil.length ? Math.round(100 * zeitnot / hasil.length) + "% dari kekalahan" : "—"));
    kz.title = "Jam sendiri di bawah 10 detik saat blunder penentu atau di akhir partai";
    el.ikhtisar.appendChild(kz);

    el.jumlah.textContent = hasil.length ? "· " + hasil.length + " partai" : "";
    el.baris.textContent = "";
    if (!hasil.length) {
      var tr0 = document.createElement("tr");
      var td0 = buat("td", "pesan kecil", "Tidak ada partai kalah untuk saringan ini");
      td0.colSpan = 7;
      tr0.appendChild(td0);
      el.baris.appendChild(tr0);
      return;
    }

    hasil.forEach(function (h) {
      var g = h.x.g, warna = h.x.warna;
      var tr = document.createElement("tr");
      tr.appendChild(buat("td", "redup kecil mono", g.tanggal || "-"));
      tr.appendChild(buat("td", "kecil", h.x.akun.nama + " · " + (warna === "putih" ? "Putih" : "Hitam")));
      var lawanNama = warna === "putih" ? g.hitam : g.putih;
      var lawanGelar = warna === "putih" ? g.hitam_gelar : g.putih_gelar;
      var lawanElo = warna === "putih" ? g.elo_hitam : g.elo_putih;
      var tdL = document.createElement("td");
      if (lawanGelar) tdL.appendChild(buat("span", "gelar", lawanGelar));
      tdL.appendChild(teks(lawanNama));
      if (lawanElo) tdL.appendChild(buat("span", "redup mono", " " + lawanElo));
      tr.appendChild(tdL);
      var tcNormal = AlatCatur.kontrolPartai(h.det);
      tr.appendChild(buat("td", "kecil", h.format + (tcNormal ? " " + tcNormal : "")));
      var tdS = document.createElement("td");
      tdS.appendChild(buat("span", "chip sebab-" + h.gol.sebab, LABEL[h.gol.sebab]));
      if (h.gol.zeitnot) { var z = buat("span", "kecil", " ⏱"); z.title = "dalam zeitnot (< 10 s)"; tdS.appendChild(z); }
      /* partainya memang berakhir karena jam habis, tapi sebabnya bukan itu —
         tetap ditandai supaya tidak terlihat hilang dari hitungan */
      if (h.gol.habisWaktu && h.gol.sebab !== "waktu") {
        var w = buat("span", "kecil redup", " habis waktu");
        w.title = "Jam habis, tapi posisinya sudah kalah — sebabnya digolongkan dari permainannya";
        tdS.appendChild(w);
      }
      tr.appendChild(tdS);
      tr.appendChild(buat("td", "kecil", h.gol.ket));
      var tdA = buat("td", "kecil");
      tdA.style.whiteSpace = "nowrap";
      var a = document.createElement("a");
      a.href = "/partai/" + g.id;
      a.appendChild(teks("partai →"));
      tdA.appendChild(a);
      if (h.gol.sebab === "blunder" || h.gol.sebab === "missclick") {
        tdA.appendChild(teks("  "));
        var b = document.createElement("a");
        b.href = "/static/latihan.html?partai_id=" + g.id;
        b.appendChild(teks("latih →"));
        tdA.appendChild(b);
      }
      tr.appendChild(tdA);
      tr.addEventListener("click", function (ev) { if (ev.target.tagName !== "A") window.location.href = "/partai/" + g.id; });
      el.baris.appendChild(tr);
    });
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
    semua.value = "semua";
    semua.textContent = "semua akun (" + akun.length + ")";
    el.akun.appendChild(semua);
    akun.forEach(function (p) {
      var o = document.createElement("option");
      o.value = p.id;
      o.textContent = p.nama + (p.sumber ? " · " + p.sumber : "");
      el.akun.appendChild(o);
    });
    if (q.get("akun") && el.akun.querySelector('option[value="' + q.get("akun") + '"]')) el.akun.value = q.get("akun");
    if (!akun.length) throw new Error("Tidak ada akun pribadi di database.");
    muat();
  }).catch(function (e) { galat(e.message); });

  [el.akun, el.lawan, el.format, el.batas].forEach(function (s) { s.addEventListener("change", muat); });
})();
