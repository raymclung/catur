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
    baris: document.getElementById("baris"),
    jumlahPembukaan: document.getElementById("jumlah-pembukaan"),
    barisPembukaan: document.getElementById("baris-pembukaan")
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

    /* "batas" sengaja tidak dikirim: kartu pembukaan menghitung semua partai,
       bukan hanya N kekalahan terbaru — skor butuh menang dan remis juga. */
    var qPembukaan = q.replace(/&batas=[^&]*/, "");
    el.barisPembukaan.textContent = "";
    return Promise.all([
      ambil("/api/kekalahan?" + q),
      ambil("/api/kekalahan/pembukaan?" + qPembukaan)
    ]).then(function (hasil) {
      if (ke !== permintaanKe) return;
      gambar(hasil[0].partai.map(bentukBaris), hasil[0].ikhtisar, hasil[0].zeitnot);
      gambarPembukaan(hasil[1]);
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

  /* Kolom "Kalah di": tempat kekalahan hilang, terbanyak dulu, yang terbesar
     ditebalkan — supaya langsung terlihat apakah yang perlu diperbaiki teori
     pembukaan, middlegame, atau jam (Ray, 10 Okt 2026). */
  var LABEL_KALAH_DI = {
    pembukaan: "pembukaan", tengah: "tengah", akhir: "akhir", waktu: "waktu",
    dikalahkan: "dikalahkan", belum: "belum dianalisis"
  };
  var FASE_LATIH = { pembukaan: 1, tengah: 1, akhir: 1 };

  /* Fase punya blunder yang bisa dilatih: angkanya jadi tautan ke Latihan
     mode kesalahan, hanya partai keluarga ECO + warna ini, hanya kesalahan di
     fase itu, dengan saringan lawan/format yang sedang dipilih di sini. */
  function tautanLatihFase(p, fase) {
    var u = new URLSearchParams();
    u.set("eco_dari", p.eco_rentang[0]);
    u.set("eco_sampai", p.eco_rentang[1]);
    u.set("warna", p.warna);
    u.set("fase", fase);
    u.set("nama", p.keluarga);
    u.set("lawan", el.lawan.value);
    if (el.format.value !== "semua") u.set("format", el.format.value);
    if (el.akun.value !== "semua") u.set("akun", el.akun.value);
    return "/static/latihan.html?" + u.toString();
  }

  function selKalahDi(kd, p) {
    var td = buat("td", "kecil");
    var isi = Object.keys(LABEL_KALAH_DI).filter(function (k) { return kd[k]; })
      .sort(function (a, b) { return kd[b] - kd[a]; });
    isi.forEach(function (k, i) {
      if (i) td.appendChild(teks(" · "));
      var label = LABEL_KALAH_DI[k] + " " + kd[k];
      if (FASE_LATIH[k] && p && p.eco_rentang) {
        var a = document.createElement("a");
        a.href = tautanLatihFase(p, k);
        a.title = "Latih kesalahanmu di fase " + k + " dalam partai " + p.keluarga + " sebagai " +
          (p.warna === "putih" ? "Putih" : "Hitam");
        a.appendChild(i === 0 ? buat("strong", null, label) : teks(label));
        td.appendChild(a);
      } else {
        td.appendChild(buat(i === 0 ? "strong" : "span", null, label));
      }
    });
    if (!isi.length) td.appendChild(teks("—"));
    return td;
  }

  /* Pembukaan yang paling sering kalah (GET /api/kekalahan/pembukaan). Urutan
     dan tanda sampel kecil datang dari server; di sini hanya digambar. */
  function gambarPembukaan(d) {
    var t = d.total;
    el.jumlahPembukaan.textContent = t.partai
      ? "· " + t.partai + " partai, skor " + Math.round(100 * (t.menang + 0.5 * t.remis) / t.partai) + "%"
      : "";
    el.barisPembukaan.textContent = "";
    if (!d.pembukaan.length) {
      var tr0 = document.createElement("tr");
      var td0 = buat("td", "pesan kecil", "Tidak ada partai untuk saringan ini");
      td0.colSpan = 8;
      tr0.appendChild(td0);
      el.barisPembukaan.appendChild(tr0);
      return;
    }
    d.pembukaan.forEach(function (p) {
      var tr = document.createElement("tr");
      if (p.sampel_kecil) {
        tr.className = "redup";
        tr.title = "Kurang dari " + d.sampel_kecil_di_bawah + " partai";
      }
      tr.appendChild(buat("td", "kecil", p.warna === "putih" ? "Putih" : "Hitam"));
      var tdN = buat("td", "", p.keluarga);
      if (p.contoh_nama.length) tdN.appendChild(buat("div", "kecil redup", p.contoh_nama.join(" · ")));
      tr.appendChild(tdN);
      tr.appendChild(buat("td", "mono", p.partai));
      tr.appendChild(buat("td", "mono kecil", p.menang + " / " + p.remis + " / " + p.kalah));
      tr.appendChild(buat("td", "mono", p.skor_persen + "%"));
      tr.appendChild(buat("td", "mono", p.kalah + " (" + Math.round(p.kalah_persen) + "%)"));
      tr.appendChild(selKalahDi(p.kalah_di || {}, p));
      var tdA = buat("td", "kecil");
      tdA.style.whiteSpace = "nowrap";
      if (p.kalah_terakhir) {
        var a = document.createElement("a");
        a.href = "/partai/" + p.kalah_terakhir.id;
        a.title = "Kekalahan terakhir dengan pembukaan ini (" + (p.kalah_terakhir.tanggal || "?") + ")";
        a.appendChild(teks("kalah terakhir →"));
        tdA.appendChild(a);
      }
      /* Pembukaan repertoire: langsung ke soal posisi tempat Ray biasa keluar
         dari jalur GM, hanya untuk pembukaan ini (Ray, 10 Okt 2026). */
      if (p.latihan) {
        if (p.kalah_terakhir) tdA.appendChild(teks("  "));
        var l = document.createElement("a");
        l.href = "/static/latihan.html?mode=pembukaan&pembukaan=" + encodeURIComponent(p.latihan);
        l.title = "Soal pembukaan " + p.latihan + ": posisi tempat langkahmu jarang dimainkan GM";
        l.appendChild(teks("latih →"));
        tdA.appendChild(l);
      }
      tr.appendChild(tdA);
      el.barisPembukaan.appendChild(tr);
    });
  }

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
    if (q.get(k) && el[k].querySelector('option[value="' + CSS.escape(q.get(k)) + '"]')) el[k].value = q.get(k);
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
    if (q.get("akun") && el.akun.querySelector('option[value="' + CSS.escape(q.get("akun")) + '"]')) el.akun.value = q.get("akun");
    if (!akun.length) throw new Error("Tidak ada akun pribadi di database.");
    muat();
  }).catch(function (e) { galat(e.message); });

  [el.akun, el.lawan, el.format, el.batas].forEach(function (s) { s.addEventListener("change", muat); });
})();
