/* Halaman Impor: unggah berkas PGN, atau tempel tautan broadcast Lichess,
   tanpa membuka terminal.

   Berkas dibaca di browser lalu isinya dikirim sebagai JSON ke
   /api/impor/pgn — bukan multipart, supaya server tidak perlu dependensi
   tambahan (python-multipart tidak terpasang).

   Beberapa berkas sekaligus dikirim satu per satu dan berurutan: server
   memegang kunci penulis selama impor, jadi dua permintaan bersamaan pasti
   saling tolak dengan 409. Penarikan akun juga satu akun per permintaan,
   supaya hasilnya muncul bertahap (Chess.com dibaca per bulan, satu akun bisa
   setengah menit).

   Kait uji headless: ?uji_tarik=<akun> menekan tombol Tarik akun itu;
   ?uji_tarik=semua menekan 'Tarik semua akun'. */

(function () {
  "use strict";

  var MAKS = 8000000;

  var el = {
    galat: document.getElementById("galat"),
    daftarAkun: document.getElementById("daftar-akun"),
    catatanAkun: document.getElementById("catatan-akun"),
    tarikSemua: document.getElementById("tarik-semua"),
    semuaRiwayat: document.getElementById("semua-riwayat"),
    statusTarik: document.getElementById("status-tarik"),
    tarikAnalisis: document.getElementById("tarik-analisis"),
    jatuhkan: document.getElementById("jatuhkan"),
    berkas: document.getElementById("berkas"),
    pilih: document.getElementById("pilih"),
    daftarBerkas: document.getElementById("daftar-berkas"),
    teks: document.getElementById("teks"),
    sumber: document.getElementById("sumber"),
    kirimPgn: document.getElementById("kirim-pgn"),
    url: document.getElementById("url"),
    seluruhTurnamen: document.getElementById("seluruh-turnamen"),
    pakaiEval: document.getElementById("pakai-eval"),
    kirimUrl: document.getElementById("kirim-url"),
    hasil: document.getElementById("hasil")
  };

  var antre = [];      // File[] yang dipilih atau dijatuhkan

  function teks(isi) { return document.createTextNode(isi === null || isi === undefined ? "" : String(isi)); }
  function buat(tag, kelas, isi) {
    var e = document.createElement(tag);
    if (kelas) e.className = kelas;
    if (isi !== undefined) e.appendChild(teks(isi));
    return e;
  }
  function galat(p) {
    el.galat.textContent = p || "";
    el.galat.hidden = !p;
    if (p) el.galat.scrollIntoView({ block: "nearest" });
  }
  function angka(n) { return (n || 0).toLocaleString("id-ID"); }

  /* ---------- tampilan hasil ---------- */

  function barisHasil(judul, isi, kelas) {
    if (el.hasil.querySelector(".pesan")) el.hasil.textContent = "";
    var b = buat("div", "tersimpan-baris");
    var kiri = buat("div", "tersimpan-isi");
    kiri.appendChild(buat("div", "tersimpan-judul " + (kelas || ""), judul));
    if (isi) kiri.appendChild(buat("div", "kecil redup", isi));
    b.appendChild(kiri);
    el.hasil.insertBefore(b, el.hasil.firstChild);
    return b;
  }

  function ringkasPgn(d) {
    var bagian = [angka(d.baru) + " baru", angka(d.duplikat) + " duplikat"];
    if (d.diperbarui) bagian.push(angka(d.diperbarui) + " diperbarui");
    if (d.dilewati) bagian.push(angka(d.dilewati) + " dilewati");
    bagian.push(angka(d.diindeks) + " diindeks");
    return angka(d.diproses) + " partai diproses · " + bagian.join(" · ");
  }

  /* FastAPI menolak badan yang tidak sah dengan detail berupa daftar objek.
     Yang berguna bagi pembaca hanya nama ruasnya, jadi ambil itu saja. */
  function pesanGalat(d, status) {
    var p = d && d.detail;
    if (typeof p === "string") return p;
    if (Array.isArray(p)) {
      return p.map(function (x) {
        var ruas = (x.loc || []).slice(1).join(".");
        return (ruas ? ruas + ": " : "") + (x.msg || "tidak sah");
      }).join("; ");
    }
    return "Server menjawab " + status;
  }

  function kirim(url, badan) {
    return fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(badan)
    }).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (d) {
        if (r.ok) return d;
        throw new Error(pesanGalat(d, r.status));
      });
    });
  }

  /* ---------- berkas ---------- */

  function tampilkanAntre() {
    el.daftarBerkas.textContent = "";
    antre.forEach(function (f) {
      el.daftarBerkas.appendChild(buat("div", null, f.name + " (" + angka(Math.round(f.size / 1024)) + " KB)"));
    });
  }

  function tambahBerkas(daftar) {
    var terlalu = [];
    Array.prototype.forEach.call(daftar, function (f) {
      if (f.size > MAKS) { terlalu.push(f.name); return; }
      antre.push(f);
    });
    tampilkanAntre();
    galat(terlalu.length ? "Terlalu besar untuk halaman ini (batas 8 MB): " + terlalu.join(", ") +
                           ". Pakai: python impor_pgn.py <berkas> --sumber <label>" : "");
  }

  function bacaBerkas(f) {
    return new Promise(function (selesai, gagal) {
      var r = new FileReader();
      r.onload = function () { selesai(String(r.result)); };
      r.onerror = function () { gagal(new Error("gagal membaca " + f.name)); };
      r.readAsText(f);
    });
  }

  function sibuk(aktif, tombol, labelSibuk) {
    tombol.disabled = aktif;
    if (aktif) { tombol.dataset.label = tombol.textContent; tombol.textContent = labelSibuk; }
    else if (tombol.dataset.label) tombol.textContent = tombol.dataset.label;
  }

  /* Impor TIDAK menganalisis dengan engine — itu pekerjaan berjam-jam yang
     harus diminta sendiri. Jadi sesudah ada partai baru, tawarkan jalannya. */
  /* `sumber` null = partai akun sendiri: di halaman Mesin partai itu ada di
     kelompok "Partai saya", bukan di kelompok GM (sumber akun sendiri sengaja
     tidak ditawarkan di sana). */
  function tautanAnalisa(baris, sumber, jumlah) {
    if (!jumlah) return;
    var a = document.createElement("a");
    a.className = "kecil";
    a.href = sumber
      ? "/static/mesin.html?kelompok=gm&sumber=" + encodeURIComponent(sumber) + "&jumlah=" + jumlah
      : "/static/mesin.html?kelompok=saya&jumlah=" + jumlah;
    a.appendChild(teks("Analisa partai ini dengan engine →"));
    var bungkus = buat("div", "kecil");
    bungkus.style.marginTop = "4px";
    bungkus.appendChild(a);
    baris.querySelector(".tersimpan-isi").appendChild(bungkus);
  }

  function imporSatu(nama, isi) {
    var sumber = el.sumber.value.trim() || "unggahan";
    return kirim("/api/impor/pgn", { pgn: isi, sumber: sumber, nama: nama })
      .then(function (d) {
        tautanAnalisa(barisHasil(nama || "tempelan", ringkasPgn(d)), sumber, d.baru);
      })
      .catch(function (e) { barisHasil(nama || "tempelan", "GAGAL: " + e.message, "klas-blunder"); throw e; });
  }

  /* ---------- partai sendiri (Lichess & Chess.com) ----------

     Dipanggil SATU AKUN PER PERMINTAAN, bukan sekaligus: Chess.com dibaca per
     bulan, jadi menarik semua akun dalam satu permintaan bisa beberapa menit
     tanpa ada yang terlihat. Per akun, hasilnya muncul bertahap. */

  var barisAkun = {};        // username -> {status, tombol}
  var statusAkunTerakhir = {};   // bertahan waktu daftar digambar ulang

  function tanggal(t) { return t ? String(t).replace(/\./g, "-") : "belum ada"; }

  function gambarAkun(daftar) {
    el.daftarAkun.textContent = "";
    barisAkun = {};
    daftar.forEach(function (a) {
      var b = buat("div", "akun-baris");

      var kiri = buat("div", "akun-isi");
      var judul = buat("div", "tersimpan-judul", a.akun);
      var plat = buat("span", "chip " + (a.platform === "lichess" ? "status-sama" : "status-dipakai"),
        a.platform === "lichess" ? "Lichess" : "Chess.com");
      plat.style.marginLeft = "8px";
      judul.appendChild(plat);
      kiri.appendChild(judul);
      kiri.appendChild(buat("div", "kecil redup",
        angka(a.partai) + " partai · terakhir " + tanggal(a.terakhir) +
        (a.saring_turnamen ? " · hanya turnamen " + a.saring_turnamen : "")));
      var status = buat("div", "kecil akun-status");
      kiri.appendChild(status);
      b.appendChild(kiri);

      var tombol = buat("button", null, "Tarik");
      tombol.addEventListener("click", function () { tarikSatu(a.akun); });
      b.appendChild(tombol);

      barisAkun[a.akun] = { status: status, tombol: tombol };
      el.daftarAkun.appendChild(b);
      var lama = statusAkunTerakhir[a.akun];
      if (lama) statusAkun(a.akun, lama.pesan, lama.kelas);
    });
    el.catatanAkun.textContent = daftar.length + " akun";
  }

  function kunciTombolAkun(kunci) {
    el.tarikSemua.disabled = kunci;
    Object.keys(barisAkun).forEach(function (n) { barisAkun[n].tombol.disabled = kunci; });
  }

  function statusAkun(nama, pesan, kelas) {
    statusAkunTerakhir[nama] = { pesan: pesan, kelas: kelas };
    var b = barisAkun[nama];
    if (!b) return;
    b.status.textContent = pesan || "";
    b.status.className = "kecil akun-status " + (kelas || "redup");
  }

  function ringkasTarik(r) {
    if (r.galat) return "gagal: " + r.galat;
    if (!r.diunduh) return "tidak ada partai baru sejak " + tanggal(r.sejak);
    var bagian = [angka(r.baru) + " baru"];
    if (r.duplikat) bagian.push(angka(r.duplikat) + " sudah ada");
    if (r.dilewati) bagian.push(angka(r.dilewati) + " dilewati");
    return angka(r.diunduh) + " diunduh · " + bagian.join(" · ");
  }

  function tarikSatu(nama) {
    kunciTombolAkun(true);
    statusAkun(nama, "mengambil…", "");
    var mulai = Date.now();
    return kirim("/api/impor/akun", {
      akun: [nama],
      semua_riwayat: el.semuaRiwayat.checked
    }).then(function (d) {
      var r = (d.akun || [])[0] || {};
      var detik = Math.round((Date.now() - mulai) / 1000);
      statusAkun(nama, ringkasTarik(r) + (detik >= 1 ? " · " + detik + " detik" : ""),
        r.galat ? "klas-blunder" : (r.baru ? "tercentang" : "redup"));
      if (r.baru) {
        var baris = barisHasil(nama, ringkasTarik(r));
        tautanAnalisa(baris, null, r.baru);        // partai sendiri → kelompok "Partai saya"
        muatAkun();            // jumlah partai & tanggal terakhir ikut berubah
      }
      return r;
    }).catch(function (e) {
      statusAkun(nama, "gagal: " + e.message, "klas-blunder");
      galat(e.message);
      throw e;
    }).then(function (r) {
      kunciTombolAkun(false);
      return r;
    }, function (e) {
      kunciTombolAkun(false);
      throw e;
    });
  }

  el.tarikSemua.addEventListener("click", function () {
    var nama = Object.keys(barisAkun);
    if (!nama.length) return;
    galat("");
    nama.forEach(function (n) { statusAkun(n, "menunggu giliran…"); });
    var rantai = Promise.resolve();
    nama.forEach(function (n, i) {
      rantai = rantai.then(function () {
        el.statusTarik.textContent = "akun " + (i + 1) + " dari " + nama.length + "…";
        return tarikSatu(n).catch(function () { });   // satu akun gagal, sisanya lanjut
      });
    });
    rantai.then(function () { el.statusTarik.textContent = "selesai"; });
  });

  /* Analisis server Lichess: hanya ada untuk partai yang pernah dimintakan
     "Request computer analysis" di Lichess, tapi yang ada langsung jadi —
     termasuk langkah terbaik, jadi Latihan ikut bisa memakainya. */
  el.tarikAnalisis.addEventListener("click", function () {
    galat("");
    sibuk(true, el.tarikAnalisis, "mengambil…");
    el.statusTarik.textContent = "memeriksa 900 partai Lichess terbaru…";
    kirim("/api/impor/analisis-lichess", { batas: 900 }).then(function (d) {
      var ket = angka(d.calon) + " partai diperiksa · " + angka(d.punya_analisis) +
        " punya analisis Lichess · " + angka(d.dianalisis) + " langsung dianalisis";
      barisHasil("Analisis Lichess", ket);
      el.statusTarik.textContent = ket;
    }).catch(function (e) {
      galat(e.message);
      el.statusTarik.textContent = "";
    }).then(function () { sibuk(false, el.tarikAnalisis); });
  });

  function muatAkun() {
    fetch("/api/impor/akun").then(function (r) {
      if (!r.ok) throw new Error("Server menjawab " + r.status);
      return r.json();
    }).then(function (d) {
      gambarAkun(d.akun || []);
    }).catch(function () {
      el.daftarAkun.textContent = "";
      el.daftarAkun.appendChild(buat("div", "pesan kecil",
        "Server ini belum punya /api/impor/akun — perbarui server dulu."));
      el.tarikSemua.disabled = true;
    });
  }

  muatAkun();

  /* Kait uji headless: ?uji_tarik=<akun> menekan tombol Tarik akun itu begitu
     daftarnya selesai dimuat. */
  (function () {
    var minta = new URLSearchParams(location.search).get("uji_tarik");
    if (!minta) return;
    var coba = setInterval(function () {
      if (!Object.keys(barisAkun).length) return;
      clearInterval(coba);
      if (minta === "semua") el.tarikSemua.click();
      else if (barisAkun[minta]) barisAkun[minta].tombol.click();
    }, 200);
  })();

  el.pilih.addEventListener("click", function () { el.berkas.click(); });
  el.berkas.addEventListener("change", function () { tambahBerkas(el.berkas.files); el.berkas.value = ""; });

  ["dragenter", "dragover"].forEach(function (n) {
    el.jatuhkan.addEventListener(n, function (ev) {
      ev.preventDefault(); el.jatuhkan.classList.add("aktif");
    });
  });
  ["dragleave", "drop"].forEach(function (n) {
    el.jatuhkan.addEventListener(n, function (ev) {
      ev.preventDefault(); el.jatuhkan.classList.remove("aktif");
    });
  });
  el.jatuhkan.addEventListener("drop", function (ev) {
    if (ev.dataTransfer && ev.dataTransfer.files) tambahBerkas(ev.dataTransfer.files);
  });

  el.kirimPgn.addEventListener("click", function () {
    var tempelan = el.teks.value.trim();
    if (!antre.length && !tempelan) { galat("Pilih berkas dulu, atau tempel isi PGN."); return; }
    var label = el.sumber.value.trim() || "unggahan";
    if (!/^[a-z0-9-]{2,20}$/.test(label)) {
      galat("Label sumber hanya boleh huruf kecil, angka, dan tanda hubung (2-20 aksara). "
            + "Contoh: unggahan, turnamen-sekolah.");
      el.sumber.focus();
      return;
    }
    galat("");
    sibuk(true, el.kirimPgn, "mengimpor…");

    /* berurutan: server memegang kunci penulis selama satu impor */
    var rantai = Promise.resolve();
    var daftar = antre.slice();
    daftar.forEach(function (f) {
      rantai = rantai.then(function () {
        return bacaBerkas(f).then(function (isi) { return imporSatu(f.name, isi); });
      });
    });
    if (tempelan) {
      rantai = rantai.then(function () { return imporSatu(null, tempelan); });
    }
    rantai.then(function () {
      antre = []; tampilkanAntre(); el.teks.value = "";
    }).catch(function (e) {
      galat(e.message);
    }).then(function () {
      sibuk(false, el.kirimPgn);
    });
  });

  /* ---------- broadcast ---------- */

  el.kirimUrl.addEventListener("click", function () {
    var u = el.url.value.trim();
    if (!u) { galat("Tempel tautan broadcast Lichess dulu."); return; }
    galat("");
    sibuk(true, el.kirimUrl, "mengambil…");
    kirim("/api/impor/broadcast", {
      url: u,
      seluruh_turnamen: el.seluruhTurnamen.checked,
      pakai_evaluasi_lichess: el.pakaiEval.checked
    }).then(function (d) {
      var judul = d.turnamen || "broadcast";
      var b = barisHasil(judul, angka(d.baru) + " baru · " + angka(d.duplikat) + " duplikat" +
        (d.diperbarui ? " · " + angka(d.diperbarui) + " diperbarui" : "") +
        " · " + angka(d.diindeks) + " diindeks" +
        (d.dianalisis_lichess ? " · " + angka(d.dianalisis_lichess) + " langsung dianalisis dari evaluasi Lichess" : "") +
        (d.evaluasi_tidak_lengkap ? " · " + angka(d.evaluasi_tidak_lengkap) + " evaluasinya tidak lengkap" : ""));
      tautanAnalisa(b, "lichess-broadcast", d.baru);
      var dilewati = (d.ronde || []).filter(function (r) { return /dilewati/.test(r.status || ""); }).length;
      if (dilewati) b.querySelector(".tersimpan-isi").appendChild(
        buat("div", "kecil redup", angka(dilewati) + " ronde sudah lengkap sebelumnya, dilewati"));
      (d.ronde || []).filter(function (r) { return !/dilewati/.test(r.status || ""); }).forEach(function (r) {
        var t = r.ronde + " — " + (r.status || "");
        if (r.partai !== undefined) t += " (" + angka(r.partai) + " partai, " + angka(r.baru) + " baru)";
        b.querySelector(".tersimpan-isi").appendChild(buat("div", "kecil redup", t));
      });
      el.url.value = "";
    }).catch(function (e) {
      barisHasil("broadcast", "GAGAL: " + e.message, "klas-blunder");
      galat(e.message);
    }).then(function () {
      sibuk(false, el.kirimUrl);
    });
  });

  el.url.addEventListener("keydown", function (ev) {
    if (ev.key === "Enter") { ev.preventDefault(); el.kirimUrl.click(); }
  });
})();
