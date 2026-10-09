/* Halaman Mesin: jalankan analisis engine tanpa membuka terminal.

   Server tidak menganalisis di dalam request — ia meluncurkan
   analisis_partai.py sebagai proses tersendiri. Halaman ini yang menunjukkan
   kemajuannya, dibaca dari berkas log batch itu.

   Kait uji headless:
     ?uji_pratinjau=1   tekan "Lihat dulu" begitu halaman siap
     ?uji_mulai=1       tekan "Mulai analisa" (dipakai untuk menguji penolakan
                        saat masih ada batch lain yang berjalan)
     ?uji_kelompok=gm   pilih kelompok partai (saya | gm | id) */

(function () {
  "use strict";

  var el = {
    galat: document.getElementById("galat"),
    hasil: document.getElementById("hasil"),
    format: document.getElementById("format"),
    lawanMinElo: document.getElementById("lawan-min-elo"),
    sumber: document.getElementById("sumber"),
    pemain: document.getElementById("pemain"),
    pemainKetik: document.getElementById("pemain-ketik"),
    bungkusPemainKetik: document.getElementById("bungkus-pemain-ketik"),
    minElo: document.getElementById("min-elo"),
    idPartai: document.getElementById("id-partai"),
    terbaru: document.getElementById("terbaru"),
    profil: document.getElementById("profil"),
    ulang: document.getElementById("ulang"),
    repertoire: document.getElementById("repertoire"),
    lihat: document.getElementById("lihat"),
    mulai: document.getElementById("mulai"),
    catatanMulai: document.getElementById("catatan-mulai"),
    statusSlot: document.getElementById("status-slot"),
    pratinjau: document.getElementById("pratinjau"),
    angka: document.getElementById("angka-analisis"),
    bar: document.getElementById("bar-analisis"),
    catatanKemajuan: document.getElementById("catatan-kemajuan"),
    tugas: document.getElementById("tugas"),
    riwayat: document.getElementById("riwayat"),
    bungkusRiwayat: document.getElementById("bungkus-riwayat"),
    jumlahRiwayat: document.getElementById("jumlah-riwayat"),
    jumlahBerjalan: document.getElementById("jumlah-berjalan"),
    antrekan: document.getElementById("antrekan"),
    daftarAntrean: document.getElementById("daftar-antrean"),
    ringkasAntrean: document.getElementById("ringkas-antrean"),
    jaga: document.getElementById("jaga"),
    maksPenjaga: document.getElementById("maks-penjaga"),
    batasCpu: document.getElementById("batas-cpu"),
    ketCpu: document.getElementById("ket-cpu"),
    statusPenjaga: document.getElementById("status-penjaga"),
    jam: document.getElementById("jam"),
    bagian: {
      belum: document.getElementById("bagian-belum"),
      saya: document.getElementById("bagian-saya"),
      gm: document.getElementById("bagian-gm"),
      id: document.getElementById("bagian-id")
    },
    ringkasBelum: document.getElementById("ringkas-belum"),
    /* Riwayat ANTREAN, beda dengan riwayat BATCH (#riwayat) di atas. Dulu
       keduanya memakai kunci dan id yang sama, jadi baris batch masuk ke
       daftar antrean, lalu dihapus lagi oleh gambarRiwayat(): tombol
       Lanjutkan dan riwayat batch tidak pernah terlihat (audit 27 Sep 2026). */
    bungkusRiwayatAntrean: document.getElementById("bungkus-riwayat-antrean"),
    ringkasRiwayat: document.getElementById("ringkas-riwayat"),
    riwayatAntrean: document.getElementById("daftar-riwayat")
  };

  var penyegar = null;
  var uji = new URLSearchParams(location.search);

  function teks(isi) { return document.createTextNode(isi === null || isi === undefined ? "" : String(isi)); }
  function buat(tag, kelas, isi) {
    var e = document.createElement(tag);
    if (kelas) e.className = kelas;
    if (isi !== undefined) e.appendChild(teks(isi));
    return e;
  }
  function angka(n) { return (n || 0).toLocaleString("id-ID"); }
  function galat(p) {
    el.galat.textContent = p || "";
    el.galat.hidden = !p;
  }
  function bilangWaktu(menit) {
    if (!menit) return "kurang dari semenit";
    if (menit < 90) return menit + " menit";
    var jam = Math.floor(menit / 60);
    var sisa = menit % 60;
    return jam + " jam" + (sisa ? " " + sisa + " menit" : "");
  }

  /* ---------- kelompok partai ---------- */

  function kelompok() {
    var t = document.querySelector('input[name="kelompok"]:checked');
    return t ? t.value : "belum";
  }
  function tampilkanBagian() {
    var k = kelompok();
    el.bagian.belum.hidden = k !== "belum";
    el.bagian.saya.hidden = k !== "saya";
    el.bagian.gm.hidden = k !== "gm";
    el.bagian.id.hidden = k !== "id";
    el.pratinjau.hidden = true;
  }
  Array.prototype.forEach.call(document.querySelectorAll('input[name="kelompok"]'), function (r) {
    r.addEventListener("change", function () {
      tampilkanBagian();
      if (typeof muatPemain === "function") muatPemain();
    });
  });

  function bilanganAtauNol(nilai) {
    var n = parseInt(String(nilai).replace(/[^0-9]/g, ""), 10);
    return isNaN(n) ? 0 : n;
  }

  function saringan() {
    /* Kotak kosong = pakai bawaan yang sama dengan HTML (2.000), bukan 25:
       kalau beda, batch diam-diam jadi jauh lebih kecil dari yang terlihat. */
    var s = { terbaru: bilanganAtauNol(el.terbaru.value) || 2000, profil: el.profil.value };
    if (el.ulang.checked) s.ulang = true;
    var k = kelompok();
    /* "Belum dianalisis" (Ray, 21 Sep 2026: "pilihan utama dengan langsung
       analisa partai yang belum dianalisa"): TIDAK ada saringan sama sekali —
       partai mana pun yang belum pernah dianalisis, yang terbaru dulu.
       `pilih_partai()` memang sudah membuang partai yang punya baris analisis,
       jadi cukup tidak mengirim saringan apa pun. */
    if (k === "belum") {
      return s;
    }
    if (k === "saya") {
      if (el.hasil.value) s.hasil = el.hasil.value;
      if (el.format.value) s.format = el.format.value;
      var lawan = bilanganAtauNol(el.lawanMinElo.value);
      if (lawan) s.lawan_min_elo = lawan;
      /* tanpa satu pun saringan di atas, "partai saya" tidak punya penanda apa
         pun — batas rating lawan yang membuatnya jadi partai Ray. */
      if (!s.hasil && !s.lawan_min_elo) s.lawan_min_elo = 2201;
    } else if (k === "gm") {
      if (el.repertoire.checked) s.repertoire = true;
      /* "semua" di kelompok ini berarti semua sumber GM (label `gm` di
         analisis_partai.py), BUKAN tanpa saringan: tanpa sumber, batch
         Partai GM ikut memilih partai akun Ray (diuji 13 Sep: pratinjau
         kosong memberi #266820 rayylung - AkibaRubi). */
      s.sumber = el.sumber.value || "gm";
      /* Nama dikirim UTUH, tidak dipecah per koma: nama pemain sendiri memuat
         koma ("Nakamura, Hikaru"), jadi memecahnya malah mencocokkan dua nama
         terpisah — Nakamura ATAU Hikaru. */
      var siapa = el.pemain.value === KETIK ? el.pemainKetik.value.trim() : el.pemain.value;
      if (siapa) s.pemain = [siapa];
      var elo = bilanganAtauNol(el.minElo.value);
      if (elo) s.min_elo = elo;
    } else {
      var nomor = (el.idPartai.value.match(/\d+/g) || []).map(Number);
      if (nomor.length) s.id = nomor;
    }
    return s;
  }

  function kirim(url, badan) {
    return fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(badan)
    }).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (d) {
        if (r.ok) return d;
        var p = d && d.detail;
        if (Array.isArray(p)) {
          p = p.map(function (x) {
            return (x.loc || []).slice(1).join(".") + ": " + (x.msg || "tidak sah");
          }).join("; ");
        }
        throw new Error(typeof p === "string" && p ? p : "Server menjawab " + r.status);
      });
    });
  }

  /* Baris perintah proses memuat jalur lengkap python.exe. Yang berguna dibaca
     hanya nama skrip dan saringannya. */
  function ringkasPerintah(p) {
    var i = String(p || "").indexOf("analisis_partai.py");
    return i < 0 ? p : "python " + String(p).slice(i);
  }

  function sibuk(aktif, tombol, label) {
    tombol.disabled = aktif;
    if (aktif) { tombol.dataset.label = tombol.textContent; tombol.textContent = label; }
    else if (tombol.dataset.label) tombol.textContent = tombol.dataset.label;
  }

  /* ---------- pratinjau ---------- */

  function tampilkanPratinjau(d) {
    el.pratinjau.textContent = "";
    el.pratinjau.hidden = false;

    var atas = buat("div", "pratinjau-atas");
    atas.appendChild(buat("strong", null, angka(d.jumlah) + " partai"));
    atas.appendChild(buat("span", "redup", " · perkiraan " + bilangWaktu(d.perkiraan_menit) +
      " (" + d.dasar_perkiraan + ")"));
    atas.appendChild(buat("div", "kecil redup", "profil " + d.profil + " · depth " + d.depth +
      " · RAM ~" + d.ram_mb + " MB · " + d.detik_per_ply + " detik per setengah langkah"));
    el.pratinjau.appendChild(atas);

    /* Batch kembar sedang jalan: server memutar batch ini ke ujung yang
       lain (terlama dulu) supaya keduanya tidak mengerjakan partai yang sama. */
    if (d.catatan) el.pratinjau.appendChild(buat("div", "kecil", d.catatan));

    if (d.contoh && d.contoh.length) {
      var pre = buat("pre", "daftar-partai");
      pre.appendChild(teks(d.contoh.join("\n")));
      el.pratinjau.appendChild(pre);
      if (d.jumlah > d.contoh.length) {
        el.pratinjau.appendChild(buat("div", "kecil redup",
          "…dan " + angka(d.jumlah - d.contoh.length) + " partai lain"));
      }
    }
    var p = buat("div", "kecil redup", "perintah setara di terminal:");
    var kode = buat("code", null, d.perintah);
    p.appendChild(document.createElement("br"));
    p.appendChild(kode);
    el.pratinjau.appendChild(p);
  }

  /* Partai GM boleh tanpa saringan apa pun (Ray: "saya ingin analisa
     semuanya"; backend menerimanya sejak commit ad7d35c) — sumbernya tetap
     dibatasi ke sumber GM oleh saringan(). Yang wajib diisi hanya nomor
     partai di kelompok "id". */
  function kurang(s) {
    var k = kelompok();
    if (k === "id" && !s.id) {
      return "Isi dulu nomor partainya, misalnya 11669 11657.";
    }
    return "";
  }

  el.lihat.addEventListener("click", function () {
    galat("");
    var belum = kurang(saringan());
    if (belum) { galat(belum); return; }
    sibuk(true, el.lihat, "menghitung…");
    kirim("/api/analisa/pratinjau", saringan())
      .then(tampilkanPratinjau)
      .catch(function (e) { galat(e.message); el.pratinjau.hidden = true; })
      .then(function () { sibuk(false, el.lihat); });
  });

  el.mulai.addEventListener("click", function () {
    galat("");
    var belum = kurang(saringan());
    if (belum) { galat(belum); return; }
    sibuk(true, el.mulai, "menjalankan…");
    kirim("/api/analisa/mulai", saringan()).then(function (d) {
      tampilkanPratinjau(d.pratinjau);
      el.catatanMulai.textContent = "batch jalan (PID " + d.tugas.pid + ")" +
        (d.tugas.saringan && d.tugas.saringan.terlama ? " · dari partai terlama" : "");
      muatStatus();
    }).catch(function (e) {
      galat(e.message);
    }).then(function () {
      sibuk(false, el.mulai);
      muatStatus();          // slot penuh = tombol dimatikan lagi, bukan menunggu 30 detik
    });
  });

  /* ---------- status ---------- */

  function barisTugas(t, dariTerminal) {
    var b = buat("div", "tersimpan-baris");
    var isi = buat("div", "tersimpan-isi");
    isi.style.flex = "1";

    var judul = buat("div", "tersimpan-judul");
    judul.appendChild(teks(dariTerminal ? "Batch dari terminal" : (t.jumlah ? angka(t.jumlah) + " partai" : "batch")));
    /* Batch yang mati di tengah TIDAK boleh terlihat sama dengan yang beres:
       lencananya dibedakan dan alasannya ditampilkan. */
    var gagal = !t.berjalan && (t.status === "gagal" || t.status === "dihentikan");
    var lencana = buat("span", "chip " +
      (t.berjalan ? "status-dipakai" : (gagal ? "status-gagal" : "status-sama")),
      t.berjalan ? "berjalan" : (t.status || "selesai"));
    lencana.style.marginLeft = "8px";
    judul.appendChild(lencana);
    isi.appendChild(judul);
    if (gagal && t.alasan) {
      isi.appendChild(buat("div", "kecil", "Berhenti sebelum selesai: " + t.alasan));
    }

    isi.appendChild(buat("div", "kecil redup",
      "PID " + t.pid + (t.mulai ? " · mulai " + t.mulai : "") +
      (t.profil ? " · profil " + t.profil : "")));

    if (t.dari) {
      var sudah = t.selesai || 0;
      var persen = Math.min(100, Math.round(sudah / t.dari * 100));
      var bar = buat("div", "kemajuan");
      var isiBar = buat("div", "kemajuan-isi");
      isiBar.style.width = persen + "%";
      bar.appendChild(isiBar);
      isi.appendChild(bar);
      isi.appendChild(buat("div", "kecil redup", angka(sudah) + " dari " + angka(t.dari) +
        " partai (" + persen + "%)" + (t.berjalan && t.sisa_menit ? " · sisa ~" + bilangWaktu(t.sisa_menit) : "")));
    }

    if (t.perintah) {
      var kode = buat("code", "kecil", ringkasPerintah(t.perintah));
      kode.style.display = "block";
      kode.style.marginTop = "6px";
      kode.style.wordBreak = "break-all";
      isi.appendChild(kode);
    }
    if (dariTerminal && !t.dari) {
      isi.appendChild(buat("div", "kecil redup",
        "Kemajuannya tidak terbaca (batch versi lama, lognya tidak ditemukan); " +
        "angka di atas tetap ikut bertambah."));
    }
    if (t.ekor && t.ekor.length) {
      var pre = buat("pre", "log-ekor");
      pre.appendChild(teks(t.ekor.join("\n")));
      isi.appendChild(pre);
    }
    b.appendChild(isi);

    if (t.berjalan) {
      var stop = buat("button", null, "Hentikan");
      stop.addEventListener("click", function () {
        /* batch dari terminal bisa saja milik pekerjaan lain yang sedang jalan
           berjam-jam; jangan hentikan hanya karena tombolnya tersenggol. */
        if (!window.confirm("Hentikan batch PID " + t.pid + "? Partai yang sudah selesai tetap tersimpan.")) return;
        stop.disabled = true;
        kirim("/api/analisa/hentikan", { pid: t.pid })
          .then(function (d) { el.catatanMulai.textContent = d.catatan; muatStatus(); })
          .catch(function (e) { galat(e.message); stop.disabled = false; });
      });
      b.appendChild(stop);
    } else if (gagal) {
      /* Tidak ada "pause" sungguhan: hasil disimpan tiap partai selesai dan
         partai yang sudah dianalisis tidak dipilih lagi, jadi menjalankan
         saringan yang sama = melanjutkan dari tempat berhenti. */
      var lanjut = buat("button", "utama", "Lanjutkan");
      lanjut.title = "Jalankan lagi dengan saringan yang sama; partai yang sudah " +
                     "dianalisis dilewati";
      lanjut.addEventListener("click", function () {
        lanjut.disabled = true;
        kirim("/api/analisa/lanjutkan", { pid: t.pid })
          .then(function (d) {
            el.catatanMulai.textContent =
              "Dilanjutkan sebagai PID " + d.tugas.pid + " · " + angka(d.tugas.jumlah) +
              " partai · perkiraan " + bilangWaktu(d.tugas.perkiraan_menit);
            muatStatus();
          })
          .catch(function (e) { galat(e.message); lanjut.disabled = false; });
      });
      b.appendChild(lanjut);
    }
    return b;
  }

  /* Batas batch engine adalah aturan kita, bukan batas mesin, dan angkanya
     ditentukan server (MAKS_BATCH). Halaman hanya melaporkan sisa slot. */
  function tampilkanSlot(d) {
    var maks = d.maks_batch || 0;
    var jalan = d.batch_berjalan || 0;
    if (!maks) {                       // server lama tanpa angka slot
      el.mulai.disabled = !!d.sibuk;
      el.statusSlot.textContent = d.sibuk ? "engine sedang dipakai batch lain" : "";
      return;
    }
    el.mulai.disabled = !!d.sibuk;
    var pesan = jalan + " dari " + maks + " slot engine dipakai";
    var kelas = "kecil redup";
    if (d.sibuk) {
      pesan += " — tunggu satu selesai, atau hentikan dari daftar di kanan";
      kelas = "kecil klas-blunder";
    } else if (jalan === maks - 1) {
      pesan += " — ini slot terakhir, mesin akan ramai";
      kelas = "kecil slot-hampir";
    }
    el.statusSlot.textContent = pesan;
    el.statusSlot.className = kelas;
  }

  function kotakAngka(nilai, label, kelas) {
    var k = buat("div");
    k.appendChild(buat("div", "angka-besar " + (kelas || ""), nilai));
    k.appendChild(buat("div", "label", label));
    return k;
  }

  function tampilkanAngka(d, persen) {
    el.angka.textContent = "";
    el.angka.appendChild(kotakAngka(angka(d.partai_dianalisis), "partai sudah dianalisis"));
    if (el.ringkasBelum) {
      var sisa = (d.belum_saya || 0) + (d.belum_gm || 0);
      el.ringkasBelum.textContent = sisa
        ? "Sisa " + angka(sisa) + " partai belum dianalisis (" + angka(d.belum_saya) +
          " punyamu, " + angka(d.belum_gm) + " GM). Yang terbaru dikerjakan dulu, tanpa saringan."
        : "Semua partai sudah pernah dianalisis. Untuk memperdalam yang masih depth 10, "
          + "pakai kelompok lain dengan profil hemat.";
    }
    el.angka.appendChild(kotakAngka(angka(d.belum_saya), "partai saya belum",
      d.belum_saya ? "status-jarang" : ""));
    el.angka.appendChild(kotakAngka(angka(d.belum_gm), "partai GM belum",
      d.belum_gm ? "status-dipakai" : ""));
    el.bar.style.width = Math.max(persen, persen > 0 ? 0.4 : 0) + "%";
    el.catatanKemajuan.textContent =
      persen.toLocaleString("id-ID", { maximumFractionDigits: 1 }) + "% dari " +
      angka(d.partai_total) + " partai · " + angka(d.langkah_dianalisis) + " langkah dinilai engine";
  }

  /* ---------- daftar pemain ----------

     Isinya ikut Sumber yang sedang dipilih, karena "belum dianalisis" beda per
     sumber: Nakamura 10.288 kalau semua sumber, 8.139 kalau pgnmentor saja.
     Angka yang tertera harus jumlah yang benar-benar akan dianalisis. */


  var KETIK = "__ketik__";
  var pemainTerakhir = "";
  /* ?pemain=<nama> dari tautan: dipilih begitu daftarnya selesai dimuat, dan
     kalau namanya tidak ada di sumber itu, jatuh ke kotak ketik. */
  var pemainAwal = uji.get("pemain") || "";

  function isiPemain(daftar) {
    var dipilih = pemainAwal || el.pemain.value || "";
    pemainAwal = "";
    el.pemain.textContent = "";
    var semua = buat("option", null, "semua pemain");
    semua.value = "";
    el.pemain.appendChild(semua);
    (daftar || []).forEach(function (p) {
      var o = buat("option", null,
        (p.gelar ? p.gelar + " " : "") + p.nama + " — " + angka(p.belum) + " belum dianalisis");
      o.value = p.nama;
      el.pemain.appendChild(o);
    });
    var ketik = buat("option", null, "(ketik sendiri)");
    ketik.value = KETIK;
    el.pemain.appendChild(ketik);

    /* pilihan lama dipertahankan kalau pemainnya masih ada di sumber baru;
       kalau hilang, jatuh ke "ketik sendiri" supaya tidak diam-diam berubah
       jadi "semua pemain". */
    if (!dipilih) return;
    var masih = Array.prototype.some.call(el.pemain.options, function (o) { return o.value === dipilih; });
    if (masih) {
      el.pemain.value = dipilih;
    } else if (dipilih !== KETIK) {
      el.pemainKetik.value = dipilih;
      el.pemain.value = KETIK;
    } else {
      el.pemain.value = KETIK;
    }
    tampilkanKetik();
  }

  function tampilkanKetik() {
    el.bungkusPemainKetik.hidden = el.pemain.value !== KETIK;
  }

  function muatPemain() {
    var sumber = el.sumber.value;
    /* kelompok dikirim ke server: dia yang tahu akun mana milik Ray
       (pemain.akun_pribadi), jadi tidak ada daftar nama di berkas ini */
    var kel = kelompok() === "saya" ? "saya" : "gm";
    var kunci = kel + "|" + sumber;
    if (kunci === pemainTerakhir && el.pemain.options.length > 2) return Promise.resolve();
    pemainTerakhir = kunci;
    var url = "/api/analisa/pemain?batas=40&kelompok=" + kel +
              (sumber ? "&sumber=" + encodeURIComponent(sumber) : "");
    return fetch(url).then(function (r) {
      if (!r.ok) throw new Error("Server menjawab " + r.status);
      return r.json();
    }).then(function (d) {
      isiPemain(d.pemain);
    }).catch(function () {
      /* server lama tanpa /api/analisa/pemain: biarkan mengetik nama sendiri */
      el.pemain.value = KETIK;
      tampilkanKetik();
    });
  }

  el.pemain.addEventListener("change", tampilkanKetik);
  el.sumber.addEventListener("change", muatPemain);

  /* Daftar sumber datang dari database, bukan ditulis di halaman: label impor
     boleh dikarang sendiri di halaman Impor. */
  function isiSumber(daftar) {
    /* Sumber akun sendiri tidak ditawarkan di kelompok "Partai GM" (Ray,
       12 Sep): partai itu sudah punya kelompoknya sendiri di sebelah kiri,
       dan memilihnya di sini cuma bikin salah pilih. Penandanya `pribadi`
       datang dari server, jadi tidak ada daftar nama sumber di berkas ini. */
    var pakai = (daftar || []).filter(function (d) { return !d.pribadi; });
    var dipilih = el.sumber.value || uji.get("sumber") || "";
    var sekarang = Array.prototype.map.call(el.sumber.options, function (o) { return o.value; }).join("|");
    var mau = [""].concat(pakai.map(function (d) { return d.nama; })).join("|");
    if (sekarang === mau) return;
    el.sumber.textContent = "";
    var semua = buat("option", null, "semua sumber GM (TWIC, broadcast, PGN Mentor)");
    semua.value = "";
    el.sumber.appendChild(semua);
    pakai.forEach(function (d) {
      var o = buat("option", null, d.nama + " — " + angka(d.belum) + " belum dianalisis");
      o.value = d.nama;
      el.sumber.appendChild(o);
    });
    el.sumber.value = dipilih;
  }

  /* ---------- antrean otomatis ---------- */

  var WARNA_STATUS = { menunggu: "status-dipakai", jalan: "status-sama",
                       selesai: "status-sama", gagal: "status-skor", batal: "status-jarang" };

  var SELESAI = ["selesai", "gagal", "batal"];

  /* Satu berkas antrean memuat dua hal: pekerjaan yang akan dikerjakan dan
     riwayat yang sudah lewat. Ray, 25 Sep 2026: "kenapa yang sudah beres bisa
     dibuang?" — tombolnya memang cuma menghapus BARIS CATATAN, hasil analisis
     di tabel `analisis` tidak tersentuh, tapi satu daftar campur membuatnya
     terbaca seperti membuang pekerjaannya. Sekarang dipisah, dan nama
     tombolnya ikut keadaan. */
  function gambarAntrean(d) {
    var semua = d.antrean || [];
    var daftar = semua.filter(function (k) { return SELESAI.indexOf(k.status) < 0; });
    var riwayat = semua.filter(function (k) { return SELESAI.indexOf(k.status) >= 0; });
    el.daftarAntrean.textContent = "";
    if (!daftar.length) {
      el.daftarAntrean.appendChild(buat("div", "pesan kecil",
        "Antrean kosong. Susun saringan di kiri, lalu tekan “+ Antrekan”."));
    }
    gambarBaris(daftar, el.daftarAntrean);
    gambarRiwayat(riwayat);
    gambarSisaAntrean(d);
  }

  function gambarBaris(daftar, wadah) {
    daftar.slice().reverse().forEach(function (k) {
      var b = buat("div", "tersimpan-baris");
      var isi = buat("div", "tersimpan-isi");
      isi.style.flex = "1";
      var judul = buat("div", "tersimpan-judul", (k.jumlah ? angka(k.jumlah) + " partai" : "pekerjaan"));
      var lencana = buat("span", "chip " + (WARNA_STATUS[k.status] || "status-sama"), k.status);
      lencana.style.marginLeft = "8px";
      judul.appendChild(lencana);
      isi.appendChild(judul);
      isi.appendChild(buat("div", "kecil redup",
        (k.perkiraan_menit ? "~" + bilangWaktu(k.perkiraan_menit) + " · " : "") +
        "ditambah " + (k.ditambah || "?") + (k.pid ? " · PID " + k.pid : "")));
      var ket = buat("code", "kecil", k.keterangan || "");
      ket.style.display = "block";
      ket.style.marginTop = "4px";
      ket.style.wordBreak = "break-all";
      isi.appendChild(ket);
      if (k.alasan) isi.appendChild(buat("div", "kecil klas-blunder", k.alasan));
      b.appendChild(isi);
      if (k.status !== "jalan") {
        var lewat = SELESAI.indexOf(k.status) >= 0;
        var buang = buat("button", null, lewat ? "Hapus dari daftar" : "Buang");
        buang.title = lewat
          ? "Hanya menghapus catatan ini dari daftar. Hasil analisisnya tetap tersimpan di database."
          : "Pekerjaan ini batal dikerjakan.";
        buang.addEventListener("click", function () {
          buang.disabled = true;
          fetch("/api/analisa/antrean/" + k.id, { method: "DELETE" })
            .then(function (r) { if (!r.ok) throw new Error("gagal"); muatAntrean(); })
            .catch(function () { buang.disabled = false; });
        });
        b.appendChild(buang);
      }
      wadah.appendChild(b);
    });
  }

  function gambarRiwayat(riwayat) {
    if (!el.riwayatAntrean) return;
    el.riwayatAntrean.textContent = "";
    el.bungkusRiwayatAntrean.hidden = !riwayat.length;
    if (!riwayat.length) return;
    el.ringkasRiwayat.textContent = "Riwayat (" + riwayat.length + " pekerjaan yang sudah lewat)";
    var catatan = buat("div", "kecil redup",
      "Sudah tidak akan dikerjakan lagi. “Hapus dari daftar” hanya membuang catatannya; " +
      "hasil analisisnya tetap tersimpan di database.");
    catatan.style.margin = "0 0 8px";
    el.riwayatAntrean.appendChild(catatan);
    var bersih = buat("button", null, "Bersihkan riwayat");
    bersih.title = "Hapus semua catatan yang sudah selesai, gagal, atau batal sekaligus";
    bersih.addEventListener("click", function () {
      if (!window.confirm("Hapus " + riwayat.length + " catatan pekerjaan yang sudah lewat? " +
                          "Hasil analisisnya tidak ikut terhapus.")) return;
      bersih.disabled = true;
      fetch("/api/analisa/antrean", { method: "DELETE" })
        .then(function (r) { if (!r.ok) throw new Error("gagal"); muatAntrean(); })
        .catch(function () { bersih.disabled = false; });
    });
    el.riwayatAntrean.appendChild(bersih);
    gambarBaris(riwayat, el.riwayatAntrean);
  }

  function gambarSisaAntrean(d) {
    var h = d.hitung || {};
    el.ringkasAntrean.textContent = (h.menunggu || 0) + " menunggu" +
      (d.perkiraan_menit_sisa ? " · ~" + bilangWaktu(d.perkiraan_menit_sisa) : "");
    var jalan = !!d.penjaga_pid;
    el.jaga.textContent = jalan ? "Matikan penjaga" : "Hidupkan penjaga";
    el.statusPenjaga.textContent = jalan
      ? "penjaga jalan (PID " + d.penjaga_pid + ") — pekerjaan berikutnya dimulai sendiri"
      : ((h.menunggu || 0) ? "penjaga mati — antrean tidak akan jalan sendiri" : "penjaga mati");
    el.statusPenjaga.className = "kecil " + (jalan || !(h.menunggu || 0) ? "redup" : "slot-hampir");
    el.maksPenjaga.disabled = jalan;     // ubah batas = matikan dulu, lalu hidupkan lagi
  }

  /* Dulu satu kali gagal (mis. server sedang di-restart) mematikan tombol
     Antrekan dan penjaga sampai halaman dimuat ulang, dan jawaban 500
     terbaca "antrean kosong". Sekarang hanya server tanpa rute antrean (404)
     yang mematikannya; gagal lain ditulis apa adanya dan pulih sendiri di
     penyegaran berikutnya. */
  function muatAntrean() {
    return fetch("/api/analisa/antrean").then(function (r) {
      if (r.status === 404) throw { tanpaAntrean: true };
      if (!r.ok) throw new Error("server menjawab " + r.status);
      return r.json();
    }).then(function (d) {
      el.antrekan.disabled = false;
      el.jaga.disabled = false;
      gambarAntrean(d);
    }).catch(function (e) {
      el.daftarAntrean.textContent = "";
      el.daftarAntrean.appendChild(buat("div", "pesan kecil", e && e.tanpaAntrean
        ? "Server ini belum punya antrean otomatis."
        : "Antrean gagal dibaca (" + (e && e.message ? e.message : "tidak terhubung") + "); dicoba lagi sebentar."));
      el.jaga.disabled = el.antrekan.disabled = !!(e && e.tanpaAntrean);
    });
  }

  el.antrekan.addEventListener("click", function () {
    galat("");
    var belum = kurang(saringan());
    if (belum) { galat(belum); return; }
    sibuk(true, el.antrekan, "menambahkan…");
    kirim("/api/analisa/antrean", { saringan: saringan(), keterangan: "" }).then(function (d) {
      tampilkanPratinjau(d.pratinjau);
      el.catatanMulai.textContent = "masuk antrean: " + angka(d.kerja.jumlah) + " partai";
      muatAntrean();
    }).catch(function (e) { galat(e.message); })
      .then(function () { sibuk(false, el.antrekan); });
  });

  el.jaga.addEventListener("click", function () {
    var hidupkan = el.jaga.textContent.indexOf("Hidupkan") >= 0;
    el.jaga.disabled = true;
    /* batas penjaga ikut dikirim: makin besar makin cepat antrean habis, tapi
       makin berat laptopnya — satu batch ≈ 2 utas engine */
    fetch("/api/analisa/antrean/jaga?jalan=" + (hidupkan ? "true" : "false") +
          "&maks=" + encodeURIComponent(el.maksPenjaga.value), { method: "POST" })
      .then(function (r) { if (!r.ok) throw new Error("server menjawab " + r.status); return r.json(); })
      .then(function () { el.jaga.disabled = false; muatAntrean(); })
      .catch(function (e) {
        el.jaga.disabled = false;
        galat("Penjaga gagal di" + (hidupkan ? "hidupkan" : "matikan") + ": " + e.message);
      });
  });

  /* ---------- batas CPU ----------
     Engine dikurung di sebagian inti (CPU affinity), jadi sisanya selalu kosong
     untuk aplikasi lain. Berlaku seketika untuk engine yang sedang jalan. */

  function tulisKetCpu(d) {
    el.ketCpu.textContent = d.inti + " dari " + d.inti_total + " inti untuk engine, " +
      (d.inti_total - d.inti) + " inti selalu kosong untukmu" +
      (d.proses !== undefined ? " · diterapkan ke " + d.berhasil + " proses" : "");
  }

  function muatBatasCpu() {
    fetch("/api/analisa/cpu").then(function (r) { return r.json(); }).then(function (d) {
      el.batasCpu.value = String(d.persen);
      tulisKetCpu(d);
    }).catch(function () { el.batasCpu.disabled = true; el.ketCpu.textContent = "server lama"; });
  }

  el.batasCpu.addEventListener("change", function () {
    el.batasCpu.disabled = true;
    fetch("/api/analisa/cpu?persen=" + encodeURIComponent(el.batasCpu.value), { method: "POST" })
      .then(function (r) { if (!r.ok) throw new Error("server menjawab " + r.status); return r.json(); })
      .then(tulisKetCpu)
      .catch(function (e) { galat("Batas CPU gagal diubah: " + e.message); })
      .then(function () { el.batasCpu.disabled = false; });
  });

  function muatStatus() {
    return fetch("/api/analisa/status").then(function (r) { return r.json(); }).then(function (d) {
      var persen = d.partai_total ? (d.partai_dianalisis / d.partai_total * 100) : 0;
      tampilkanAngka(d, persen);
      isiSumber(d.sumber);

      /* Yang berjalan dan yang sudah selesai dipisah: angka slot di kiri
         menghitung yang BERJALAN saja, jadi dua daftar yang bercampur bikin
         angkanya seperti salah. */
      el.tugas.textContent = "";
      el.riwayat.textContent = "";
      var berjalan = 0;
      (d.batch_lain || []).forEach(function (b) {
        berjalan++;
        /* `selesai`/`dari`/`sisa_menit` ikut dikirim server sejak 13 Sep, jadi
           batch dari terminal dapat bilah kemajuan yang sama dengan batch dari
           halaman ini; kalimat "tidak terbaca" hanya untuk yang tanpa angka. */
        el.tugas.appendChild(barisTugas({
          pid: b.pid, mulai: b.mulai, berjalan: true, perintah: b.perintah,
          selesai: b.selesai, dari: b.dari, sisa_menit: b.sisa_menit, ekor: b.ekor
        }, true));
      });
      (d.tugas || []).forEach(function (t) {
        if (t.berjalan) {
          berjalan++;
          el.tugas.appendChild(barisTugas(t, false));
        } else {
          el.riwayat.appendChild(barisTugas(t, false));
        }
      });
      if (!berjalan) {
        el.tugas.appendChild(buat("div", "pesan kecil", "Tidak ada batch yang sedang berjalan."));
      }
      var jumlahSelesai = el.riwayat.childNodes.length;
      el.bungkusRiwayat.hidden = !jumlahSelesai;
      el.jumlahRiwayat.textContent = angka(jumlahSelesai);
      el.jumlahBerjalan.textContent = berjalan ? angka(berjalan) + ' batch berjalan' : '';

      tampilkanSlot(d);
      tampilkanJeda(d.jeda);
      el.jam.textContent = "diperbarui " + new Date().toLocaleTimeString("id-ID");

      muatAntrean();
      muatPembantu();
      /* `sibuk` = SEMUA slot penuh; kemajuan satu batch pun perlu segar. */
      atur(d.batch_berjalan || d.sibuk ? 5000 : 30000);
      return d;
    }).catch(function () {
      el.catatanKemajuan.textContent = "gagal membaca status server";
      atur(30000);
    });
  }

  /* ---------- pembantu tablet/HP (app/pembantu.py) ----------
     Alamat dan kode yang diketik di tablet/HP, plus perangkat yang sedang
     membantu. Ikut disegarkan bersama status. */

  function muatPembantu() {
    var wadah = document.getElementById("daftar-pembantu");
    var peringatan = document.getElementById("pembantu-peringatan");
    return fetch("/api/pembantu/info").then(function (r) {
      if (!r.ok) throw new Error("HTTP " + r.status);
      return r.json();
    }).then(function (d) {
      document.getElementById("pembantu-kode").textContent = d.kode;
      document.getElementById("pembantu-alamat").textContent =
        d.alamat.length ? d.alamat.join("  atau  ") : "laptop ini tidak tersambung ke WiFi";
      var masalah = [];
      if (!d.engine_ada) masalah.push("Engine untuk browser belum diunduh di laptop ini.");
      if (d.alamat.length && !d.terbuka) {
        masalah.push("Server ini masih hanya bisa dibuka dari laptop, jadi tablet/HP belum bisa masuk. " +
          "Server perlu dinyalakan dengan --host 0.0.0.0 dan Python diizinkan di Windows Firewall.");
      }
      peringatan.textContent = masalah.join(" ");
      peringatan.hidden = !masalah.length;

      wadah.textContent = "";
      var aktif = 0;
      (d.pembantu || []).forEach(function (p) {
        if (p.aktif) aktif++;
        var baris = buat("div", "kecil" + (p.aktif ? "" : " redup"),
          p.nama + " — " + angka(p.selesai) + " partai selesai" +
          (p.posisi_per_detik ? " · " + p.posisi_per_detik.toLocaleString("id-ID") + " posisi/detik" : "") +
          (p.aktif ? (p.partai_id ? " · sedang partai #" + p.partai_id : "") :
            " · terakhir " + bilangWaktu(Math.max(1, Math.round(p.detik_lalu / 60))) + " lalu"));
        wadah.appendChild(baris);
      });
      if (!(d.pembantu || []).length) {
        wadah.appendChild(buat("div", "kecil redup", "Belum ada perangkat yang membantu sejak server dinyalakan."));
      }
      document.getElementById("ringkas-pembantu").textContent = aktif ? aktif + " aktif" : "";
    }).catch(function () {
      document.getElementById("pembantu-alamat").textContent = "server ini belum punya fitur pembantu";
    });
  }

  document.getElementById("pembantu-ganti-kode").addEventListener("click", function () {
    if (!confirm("Ganti kode? Tablet/HP yang sedang membantu harus memasukkan kode baru.")) return;
    fetch("/api/pembantu/kode", { method: "POST" }).then(muatPembantu);
  });

  /* ---------- jeda semua engine (app/jeda_mesin.py) ----------
     Stockfish dibekukan: CPU kosong seketika, batch melanjutkan dari posisi
     yang sama begitu dicairkan. RAM tidak dibebaskan. */

  var elJeda = document.getElementById("jeda-semua");
  var elKetJeda = document.getElementById("ket-jeda");

  function tampilkanJeda(j) {
    if (!j) return;
    elJeda.disabled = false;
    elJeda.dataset.jeda = j.jeda ? "1" : "";
    elJeda.textContent = j.jeda ? "▶ Lanjutkan" : "⏸ Jeda semua";
    elJeda.className = j.jeda ? "utama" : "";
    elKetJeda.textContent = j.jeda
      ? "Dijeda sejak " + (j.sejak || "tadi") + " — engine di laptop ini dibekukan dan tidak ada batch baru. " +
        "Pembantu tablet/komputer lain tetap jalan."
      : "Engine bisa dijeda kalau laptop mau dipakai untuk yang lain.";
    elKetJeda.className = "kecil " + (j.jeda ? "slot-hampir" : "redup");
  }

  elJeda.addEventListener("click", function () {
    var jedakan = !elJeda.dataset.jeda;
    elJeda.disabled = true;
    fetch("/api/analisa/jeda?aktif=" + (jedakan ? "true" : "false"), { method: "POST" })
      .then(function (r) { if (!r.ok) throw new Error("HTTP " + r.status); return r.json(); })
      .then(function (d) {
        tampilkanJeda(d);
        galat("");
        muatStatus();
      })
      .catch(function (e) { elJeda.disabled = false; galat("Gagal " + (jedakan ? "menjeda" : "melanjutkan") + ": " + e.message); });
  });

  function atur(jeda) {
    if (penyegar) clearInterval(penyegar);
    penyegar = null;
    /* Permintaan yang sudah terbang saat tab disembunyikan tidak boleh
       menyalakan penyegar lagi di tab yang tersembunyi. */
    if (document.hidden) return;
    penyegar = setInterval(muatStatus, jeda);
  }

  document.addEventListener("visibilitychange", function () {
    if (document.hidden) { if (penyegar) clearInterval(penyegar); penyegar = null; }
    else muatStatus();
  });

  /* Halaman Impor menautkan ke sini sesudah impor selesai, mis.
     /static/mesin.html?kelompok=gm&sumber=unggahan&jumlah=12 */
  function prasetel() {
    var pil = uji.get("kelompok");
    if (pil) {
      var radio = document.getElementById("kel-" + pil);
      if (radio) radio.checked = true;
    }
    var sumber = uji.get("sumber");
    if (sumber) {
      var opsi = buat("option", null, sumber);      // diganti isiSumber() begitu status datang
      opsi.value = sumber;
      el.sumber.appendChild(opsi);
      el.sumber.value = sumber;
      pemainTerakhir = null;          // paksa muat ulang untuk sumber ini
      if (!pil) document.getElementById("kel-gm").checked = true;
    }
    /* ?id=11669 datang dari tombol "Analisa partai ini" di halaman partai,
       jadi nomornya tidak perlu diketik sendiri. */
    var nomor = uji.get("id");
    if (nomor) {
      el.idPartai.value = (nomor.match(/\d+/g) || []).join(" ");
      document.getElementById("kel-id").checked = true;
    }
    /* ?ulang=1&profil=hemat datang dari tautan "Analisa ulang lebih teliti" di
       halaman partai yang sudah dianalisis kasar. */
    if (uji.get("ulang") === "1") el.ulang.checked = true;
    var profil = uji.get("profil");
    if (profil && Array.prototype.some.call(el.profil.options, function (o) { return o.value === profil; })) {
      el.profil.value = profil;
    }
    var jumlah = parseInt(uji.get("jumlah"), 10);
    if (jumlah > 0) el.terbaru.value = Math.min(jumlah, 2000);
    if (uji.get("hasil")) el.hasil.value = uji.get("hasil");
    if (uji.get("format")) el.format.value = uji.get("format");
  }

  /* Isian berubah = hasil "Lihat dulu" tidak berlaku lagi. Dulu hanya
     ganti kelompok yang menyembunyikannya, jadi Ray bisa melihat
     "2.000 partai · 3 jam" untuk profil cepat, mengganti ke dalam, lalu
     menekan Mulai dengan perkiraan yang salah (audit 27 Sep 2026). */
  (function () {
    var kartu = el.lihat.closest(".mesin-isi");
    if (!kartu) return;
    function basi(ev) { if (ev.target !== el.batasCpu) el.pratinjau.hidden = true; }
    kartu.addEventListener("input", basi);
    kartu.addEventListener("change", basi);
  })();

  prasetel();
  tampilkanBagian();
  muatBatasCpu();
  muatPembantu();      /* tidak menunggu status, yang bisa beberapa detik */
  muatPemain();
  muatStatus().then(function () {
    var kel = uji.get("uji_kelompok");
    if (kel) {
      var radio = document.getElementById("kel-" + kel);
      if (radio) { radio.checked = true; tampilkanBagian(); }
    }
    /* Datang dari tombol "Analisa partai ini": langsung tunjukkan partai mana
       dan berapa lama, supaya tinggal menekan Mulai. */
    if (uji.get("id") || uji.get("uji_pratinjau")) el.lihat.click();
    if (uji.get("uji_mulai")) { el.mulai.disabled = false; el.mulai.click(); }
  });
})();
