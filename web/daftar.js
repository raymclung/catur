/* Halaman daftar partai: pencarian, penyaringan, dan paginasi. */

(function () {
  "use strict";

  var halamanSekarang = 1;
  var paramDaftar = "";   // query /api/partai terakhir, dibawa ke halaman partai

  var el = {
    cari: document.getElementById("cari"),
    eco: document.getElementById("eco"),
    sumber: document.getElementById("sumber"),
    tanpaSaya: document.getElementById("tanpa-saya"),
    labelTanpaSaya: document.getElementById("label-tanpa-saya"),
    hasil: document.getElementById("hasil"),
    gelar: document.getElementById("gelar"),
    dianalisis: document.getElementById("dianalisis"),
    pemainId: document.getElementById("pemain_id"),
    warna: document.getElementById("warna"),
    ecoDari: document.getElementById("eco_dari"),
    ecoSampai: document.getElementById("eco_sampai"),
    fen: document.getElementById("fen"),
    repertoire: document.getElementById("repertoire"),
    saringanAktif: document.getElementById("saringan-aktif"),
    urut: document.getElementById("urut"),
    form: document.getElementById("form-saring"),
    reset: document.getElementById("reset"),
    baris: document.getElementById("baris-partai"),
    paginasi: document.getElementById("paginasi"),
    ringkasan: document.getElementById("ringkasan"),
    galat: document.getElementById("galat")
  };

  /* Menyisipkan teks dari database ke HTML harus lewat createTextNode atau
     textContent, tidak pernah lewat innerHTML. Nama pemain berasal dari file
     PGN yang bisa berisi karakter apa saja. */
  function teks(isi) {
    return document.createTextNode(isi === null || isi === undefined ? "" : String(isi));
  }

  function buat(tag, kelas, isi) {
    var e = document.createElement(tag);
    if (kelas) e.className = kelas;
    if (isi !== undefined) e.appendChild(teks(isi));
    return e;
  }

  function tampilkanGalat(pesan) {
    el.galat.textContent = pesan;
    el.galat.hidden = false;
  }

  function ambil(url) {
    return fetch(url).then(function (r) {
      if (!r.ok) throw new Error("Server menjawab " + r.status + " untuk " + url);
      return r.json();
    });
  }

  /* ---------- baris tabel ---------- */

  function selPemain(nama, gelar, elo, terverifikasi, pemainId) {
    var td = document.createElement("td");
    if (gelar) td.appendChild(buat("span", "gelar", gelar));
    var a = document.createElement("a");
    a.href = "/pemain/" + pemainId;
    a.appendChild(teks(nama));
    a.addEventListener("click", function (ev) { ev.stopPropagation(); });
    td.appendChild(a);
    if (terverifikasi) {
      var v = buat("span", "tercentang", " ✓");
      v.title = "Identitas dipastikan lewat FIDE ID";
      td.appendChild(v);
    }
    if (elo) td.appendChild(buat("span", "redup mono", " " + elo));
    return td;
  }

  function kelasHasil(hasil) {
    if (hasil === "1-0") return "hasil hasil-1-0";
    if (hasil === "0-1") return "hasil hasil-0-1";
    if (hasil === "*") return "hasil hasil-jalan";
    return "hasil hasil-remis";
  }

  function barisPartai(g) {
    var tr = document.createElement("tr");
    tr.appendChild(buat("td", "redup kecil mono", g.tanggal || "-"));
    tr.appendChild(selPemain(g.putih, g.putih_gelar, g.elo_putih, g.putih_terverifikasi, g.putih_id));
    tr.appendChild(selPemain(g.hitam, g.hitam_gelar, g.elo_hitam, g.hitam_terverifikasi, g.hitam_id));
    tr.appendChild(buat("td", kelasHasil(g.hasil), g.hasil === "*" ? "jalan" : g.hasil));
    tr.appendChild(buat("td", "mono kecil", g.eco || "-"));
    tr.appendChild(buat("td", "kecil", g.pembukaan || "-"));
    tr.appendChild(buat("td", "kecil redup", g.turnamen || "-"));
    tr.appendChild(buat("td", "mono kecil", g.jumlah_langkah || "-"));
    var tdA = buat("td", "mono kecil" + (g.dianalisis ? "" : " redup"));
    if (g.dianalisis) {
      tdA.appendChild(teks(
        (g.akurasi_putih !== null ? g.akurasi_putih.toFixed(0) : "—") + " / " +
        (g.akurasi_hitam !== null ? g.akurasi_hitam.toFixed(0) : "—")));
      tdA.title = "Akurasi Putih / Hitam";
    } else {
      tdA.appendChild(teks("—"));
    }
    tr.appendChild(tdA);
    tr.addEventListener("click", function () {
      /* Bawa saringan + halaman supaya halaman partai bisa menawarkan
         partai sebelumnya/berikutnya dalam daftar yang sama. */
      window.location.href = "/partai/" + g.id + "?daftar=" + encodeURIComponent(paramDaftar);
    });
    return tr;
  }

  /* ---------- paginasi ---------- */

  function gambarPaginasi(data) {
    el.paginasi.textContent = "";
    if (data.total === 0) return;

    function tombol(label, halaman, nonaktif) {
      var b = buat("button", null, label);
      b.disabled = !!nonaktif;
      if (!nonaktif) {
        b.addEventListener("click", function () {
          halamanSekarang = halaman;
          muat();
          window.scrollTo(0, 0);
        });
      }
      return b;
    }

    el.paginasi.appendChild(tombol("«", 1, data.halaman <= 1));
    el.paginasi.appendChild(tombol("‹", data.halaman - 1, data.halaman <= 1));
    el.paginasi.appendChild(
      buat("span", "redup kecil",
           " Halaman " + data.halaman + " dari " + data.jumlah_halaman +
           "  ·  " + data.total.toLocaleString("id-ID") + " partai ")
    );
    el.paginasi.appendChild(
      tombol("›", data.halaman + 1, data.halaman >= data.jumlah_halaman));
    el.paginasi.appendChild(
      tombol("»", data.jumlah_halaman, data.halaman >= data.jumlah_halaman));
  }

  /* ---------- muat data ---------- */

  /* FastAPI diam-diam mengabaikan parameter yang tidak dikenalnya, jadi
     `fen` yang belum didukung server akan mengembalikan SEMUA partai seolah
     tersaring. Sebelum memakai `fen`, cek dulu /openapi.json apakah
     /api/partai mendaftarkannya. Sampai backend memasangnya (SESI.md bagian 3
     #3), daftar tidak ditampilkan. */
  var dukungFen = null;   // null = belum dicek, true/false setelah dicek

  function cekDukungFen() {
    if (dukungFen !== null) return Promise.resolve(dukungFen);
    return ambil("/openapi.json").then(function (d) {
      var param = (((d.paths || {})["/api/partai"] || {}).get || {}).parameters || [];
      dukungFen = param.some(function (x) { return x.name === "fen"; });
      return dukungFen;
    }).catch(function () { dukungFen = false; return false; });
  }

  function muat() {
    if (el.fen.value) {
      cekDukungFen().then(function (ok) {
        if (ok) return muatSungguh();
        el.baris.textContent = "";
        el.paginasi.textContent = "";
        tampilkanSaringanAktif();
        tulisUrl(new URLSearchParams({ fen: el.fen.value }));
        tampilkanGalat("Saringan posisi (fen) belum didukung server; daftar tidak ditampilkan supaya tidak menyesatkan (menunggu backend, SESI.md bagian 3 #3).");
      });
      return;
    }
    muatSungguh();
  }

  function muatSungguh() {
    var p = new URLSearchParams();
    if (el.cari.value.trim()) p.set("cari", el.cari.value.trim());
    if (el.eco.value.trim()) p.set("eco", el.eco.value.trim());
    if (el.tanpaSaya.checked) p.set("sumber", "gm");
    else if (el.sumber.value) p.set("sumber", el.sumber.value);
    if (el.hasil.value) p.set("hasil", el.hasil.value);
    if (el.gelar.value) p.set("gelar", el.gelar.value);
    if (el.dianalisis.value) p.set("dianalisis", el.dianalisis.value);
    if (el.pemainId.value) p.set("pemain_id", el.pemainId.value);
    if (el.warna.value) p.set("warna", el.warna.value);
    if (el.ecoDari.value && el.ecoSampai.value) {
      p.set("eco_dari", el.ecoDari.value);
      p.set("eco_sampai", el.ecoSampai.value);
    }
    if (el.fen.value) p.set("fen", el.fen.value);
    p.set("urut", el.urut.value);
    p.set("halaman", halamanSekarang);
    p.set("per_halaman", 25);
    tampilkanSaringanAktif();
    tulisUrl(p);
    paramDaftar = p.toString();

    el.galat.hidden = true;

    ambil("/api/partai?" + p.toString()).then(function (data) {
      el.baris.textContent = "";
      if (data.partai.length === 0) {
        var tr = document.createElement("tr");
        var td = buat("td", "pesan", "Tidak ada partai untuk saringan ini");
        td.colSpan = 9;
        tr.appendChild(td);
        el.baris.appendChild(tr);
      } else {
        data.partai.forEach(function (g) { el.baris.appendChild(barisPartai(g)); });
      }
      gambarPaginasi(data);
    }).catch(function (e) {
      tampilkanGalat("Gagal memuat daftar partai: " + e.message);
    });
  }

  function muatStatistik() {
    ambil("/api/statistik").then(function (s) {
      el.ringkasan.textContent =
        s.partai.toLocaleString("id-ID") + " partai · " +
        s.pemain.toLocaleString("id-ID") + " pemain · " +
        s.turnamen.toLocaleString("id-ID") + " turnamen";

      s.sumber.forEach(function (x) {
        var o = document.createElement("option");
        o.value = x.sumber;
        o.textContent = x.sumber + " (" + x.jumlah + ")";
        el.sumber.appendChild(o);
      });
    }).catch(function (e) {
      el.ringkasan.textContent = "";
      tampilkanGalat("Gagal memuat statistik: " + e.message);
    });
  }

  el.form.addEventListener("submit", function (ev) {
    ev.preventDefault();
    halamanSekarang = 1;
    muat();
  });

  /* Dropdown langsung mencari saat berubah: dua klik cukup untuk menyembunyikan
     remis (buka dropdown Hasil, pilih "menang-kalah saja"). Kotak teks tetap
     menunggu Enter/Cari supaya tidak memukul server tiap ketukan. */
  [el.sumber, el.hasil, el.gelar, el.dianalisis, el.urut].forEach(function (sel) {
    sel.addEventListener("change", function () {
      halamanSekarang = 1;
      muat();
    });
  });

  el.tanpaSaya.addEventListener("change", function () {
    terapkanTanpaSaya(true);
    halamanSekarang = 1;
    muat();
  });

  el.reset.addEventListener("click", function () {
    el.cari.value = "";
    el.eco.value = "";
    el.sumber.value = "";
    el.hasil.value = "";
    el.gelar.value = "";
    el.dianalisis.value = "";
    el.pemainId.value = "";
    el.warna.value = "";
    el.ecoDari.value = "";
    el.ecoSampai.value = "";
    el.fen.value = "";
    el.urut.value = "terbaru";
    /* centang "Tanpa partai saya" sengaja tidak ikut direset: itu pilihan tetap */
    halamanSekarang = 1;
    muat();
  });

  /* ---------- saringan tersembunyi: pemain & rentang ECO ---------- */

  /* Nama pemain untuk teks "Saringan aktif". Diambil sekali per id; sebelum
     jawaban datang (atau kalau gagal) tampil nomornya saja. */
  var namaPemain = { id: null, nama: null };

  function labelPemain() {
    var id = el.pemainId.value;
    var label = namaPemain.id === id && namaPemain.nama ? namaPemain.nama : "pemain #" + id;
    return label + (el.warna.value ? " sebagai " + el.warna.value : "");
  }

  function muatNamaPemain() {
    var id = el.pemainId.value;
    if (!id || namaPemain.id === id) return;
    namaPemain = { id: id, nama: null };
    ambil("/api/pemain/" + id).then(function (p) {
      if (namaPemain.id !== id) return;
      namaPemain.nama = (p.gelar ? p.gelar + " " : "") + p.nama;
      tampilkanSaringanAktif();
    }).catch(function () { /* nomor saja sudah cukup */ });
  }

  /* FEN utuh terlalu panjang untuk satu baris; cukup susunan bidak + giliran. */
  function labelFen(fen) {
    var b = fen.split(" ");
    return "posisi · " + (b[1] === "b" ? "Hitam" : "Putih") + " jalan";
  }

  /* Saringan tersembunyi (pemain, rentang ECO, posisi) ditampilkan sebagai
     chip; satu tombol menghapus semuanya. */
  function chip(isi, href, judul) {
    var c = buat("span", "chip");
    if (href) {
      var a = document.createElement("a");
      a.href = href;
      a.title = judul || "";
      a.appendChild(teks(isi));
      c.appendChild(a);
    } else {
      c.appendChild(teks(isi));
      if (judul) c.title = judul;
    }
    return c;
  }

  function tampilkanSaringanAktif() {
    var daftarChip = [];
    if (el.pemainId.value) {
      muatNamaPemain();
      daftarChip.push(chip(labelPemain(), "/pemain/" + el.pemainId.value, "Profil pemain"));
    }
    if (el.ecoDari.value && el.ecoSampai.value) daftarChip.push(chip("ECO " + el.ecoDari.value + "–" + el.ecoSampai.value));
    if (el.fen.value) daftarChip.push(chip(labelFen(el.fen.value), "/jelajah?fen=" + encodeURIComponent(el.fen.value), el.fen.value));
    el.saringanAktif.textContent = "";
    el.saringanAktif.hidden = daftarChip.length === 0;
    if (!daftarChip.length) return;
    daftarChip.forEach(function (c) { el.saringanAktif.appendChild(c); });
    var hapus = buat("button", null, "✕");
    hapus.type = "button";
    hapus.title = "Hapus semua saringan";
    hapus.addEventListener("click", function () { el.reset.click(); });
    el.saringanAktif.appendChild(hapus);
  }

  function muatRepertoire() {
    ambil("/api/repertoire").then(function (d) {
      d.repertoire.forEach(function (r) {
        var b = buat("button", null, r.nama);
        b.type = "button";
        b.title = "ECO " + r.eco_dari + "–" + r.eco_sampai;
        b.addEventListener("click", function () {
          el.ecoDari.value = r.eco_dari;
          el.ecoSampai.value = r.eco_sampai;
          halamanSekarang = 1;
          muat();
        });
        el.repertoire.appendChild(b);
      });
    }).catch(function () { /* tombol repertoire opsional */ });
  }

  /* "Tanpa partai saya" = hanya sumber GM (TWIC + broadcast); partai akun
     online sendiri hanya ada di lichess-user dan chesscom, jadi mematikan
     keduanya cukup. Pilihannya diingat browser ini (localStorage) supaya
     tidak perlu dicentang ulang tiap kali. Dropdown Sumber dimatikan selama
     centang aktif supaya tidak ada dua saringan yang bertabrakan. */
  var KUNCI_TANPA_SAYA = "daftar.tanpaPartaiSaya";

  function terapkanTanpaSaya(simpan) {
    var aktif = el.tanpaSaya.checked;
    el.sumber.disabled = aktif;
    el.labelTanpaSaya.classList.toggle("centang-aktif", aktif);
    if (simpan) {
      try { localStorage.setItem(KUNCI_TANPA_SAYA, aktif ? "1" : "0"); } catch (e) { /* penyimpanan tidak tersedia */ }
    }
  }

  /* Saringan aktif ditulis balik ke URL supaya bisa disalin/dibagikan dan
     tautan dari halaman lain (mis. profil pemain, "kembali ke daftar" di
     halaman partai) bisa diperiksa. Nilai bawaan (urut terbaru, halaman 1)
     tidak ditulis supaya URL tetap pendek. */
  var KUNCI_URL = ["cari", "eco", "sumber", "hasil", "gelar", "dianalisis",
                   "pemain_id", "warna", "eco_dari", "eco_sampai", "fen", "urut", "halaman"];

  function tulisUrl(p) {
    var q = new URLSearchParams();
    KUNCI_URL.forEach(function (k) {
      if (!p.has(k)) return;
      if (k === "urut" && p.get(k) === "terbaru") return;
      if (k === "halaman" && p.get(k) === "1") return;
      q.set(k, p.get(k));
    });
    var baru = window.location.pathname + (q.toString() ? "?" + q.toString() : "");
    window.history.replaceState(null, "", baru);
  }

  /* Parameter dari URL (mis. tautan dari halaman pemain). Nilai yang tidak ada
     di dropdown diabaikan oleh browser, jadi tidak perlu divalidasi di sini. */
  (function bacaUrl() {
    var q = new URLSearchParams(window.location.search);
    KUNCI_URL.forEach(function (k) {
      var e = document.getElementById(k);
      if (e && q.get(k)) e.value = q.get(k);
    });
    var h = parseInt(q.get("halaman") || "1", 10);
    if (h > 1) halamanSekarang = h;

    /* Pilihan tersimpan dipakai kecuali URL sudah menyebut sumber atau
       pemain tertentu — kalau tidak, tautan ke partai sendiri akan kosong. */
    var tersimpan = "0";
    try { tersimpan = localStorage.getItem(KUNCI_TANPA_SAYA) || "0"; } catch (e) { tersimpan = "0"; }
    el.tanpaSaya.checked = tersimpan === "1" && !q.get("sumber") && !q.get("pemain_id");
    terapkanTanpaSaya(false);
  })();

  /* Kait pengujian tanpa mouse (Chrome headless): ?uji_tanpa_saya=1 mencentang
     "Tanpa partai saya" seperti klik pengguna, termasuk menyimpannya. */
  (function () {
    var u = new URLSearchParams(window.location.search).get("uji_tanpa_saya");
    if (u === null) return;
    el.tanpaSaya.checked = u === "1";
    terapkanTanpaSaya(true);
  })();

  muatStatistik();
  muatRepertoire();
  muat();
})();
