/* Latihan dari kesalahan sendiri (Phase 7): posisi tepat sebelum blunder atau
   kesalahan di partai Ray yang sudah dianalisis. Tugasnya menemukan langkah
   terbaik menurut engine. Semua data dari endpoint yang sudah ada:
   /api/pemain (akun), /api/partai?dianalisis=true (partai),
   /api/partai/{id}/analisis (klasifikasi + langkah terbaik), /api/partai/{id}
   (PGN untuk membangun posisi). Tidak ada yang disimpan. */

(function () {
  "use strict";

  var papan = null;
  var permainan = null;      // chess.js pada posisi soal
  var soalSemua = [];        // {partai_id, ply, warna, san, terbaik_uci, terbaik_san, variasi, klasifikasi, kehilangan}
  var urutan = [];           // indeks soal yang belum dikerjakan, acak
  var soal = null;
  var dijawab = false;       // soal sekarang sudah dinilai; papan dikunci sampai soal berikutnya
  var benar = 0, dikerjakan = 0;
  var cachePgn = {};
  var klik = null;           // KlikPapan (web/klik-papan.js), klik-untuk-jalan
  var terpilih = null;
  /* Sesudah dijawab papan dibuka lagi untuk MENELUSUR: langkah boleh dimainkan
     bebas dan tidak dinilai, engine menilai tiap posisi. Tanpa ini "kenapa
     langkahku salah" cuma bisa dijawab dengan membaca satu baris variasi. */
  var telusur = false;
  /* Soal hanya dari posisi yang menurut ENGINE belum kalah.

     Ketentuan Ray (13 Sep 2026): "perhitungan bukan dari materi tapi dari
     engine; jika putih dan hitam batasnya 0, karena jika di bawah ini maka
     sudah dipastikan posisi sedang kalah." Jadi ambangnya SKOR ENGINE 0,00,
     sama untuk kedua warna — bukan hitungan materi, dan kalah kualitas dengan
     skor masih plus TETAP jadi soal.

     `eval_sebelum_cp` = penilaian engine atas posisi itu sendiri (hasil bila
     dimainkan terbaik), dari sudut pandang yang jalan. Skor persis 0,00 = masih
     imbang, jadi ikut masuk; yang dibuang hanya yang di BAWAH 0.
     Menyisakan 7.733 dari 12.818 soal. */
  var BATAS_MIN_SKOR = 0;
  /* Pita "imbang" menurut Ray (13 Sep 2026): skor engine -1,00 sampai +1,00.
     Dipakai HANYA untuk penamaan, tidak mengubah soal mana yang muncul. Karena
     ambang bawah sudah 0, yang terpakai praktis cuma sisi plusnya. Ditampilkan
     di kartu JAWABAN, bukan di soal — menyebut "kamu sedang unggul" sebelum
     dijawab sama saja membocorkan bahwa ada langkah menang. */
  var PITA_IMBANG = 100;
  /* Kesalahan saat sedang menang baru "tidak apa-apa" kalau SESUDAHNYA masih
     menang telak. Ray, 13 Sep 2026: "kalau 5 menjadi 2 itu fatal harus ditulis;
     kalau masih di atas 6 mungkin bisa ditoleransi." Jadi yang dibuang hanya
     kalau skor sesudah >= +6,00; +5,00 → +2,00 tetap jadi soal.
     Membuang 123 dari 1.949 soal partai sendiri. */
  var TOLERANSI_MASIH_MENANG = 600;
  function namaKedudukan(cp) {
    if (cp === null || cp === undefined) return "";
    if (cp >= PITA_IMBANG) return "unggul";
    if (cp <= -PITA_IMBANG) return "kalah";
    return "imbang";
  }
  function skorTampil(cp) {
    if (cp === null || cp === undefined) return "?";
    return (cp > 0 ? "+" : "") + (cp / 100).toFixed(2).replace(".", ",");
  }
  function ambangTampil() { return (BATAS_MIN_SKOR / 100).toFixed(2).replace(".", ","); }
  var dilewatiKalah = 0;
  var dilewatiMenang = 0;
  var fenSoal = null;        // posisi awal soal, untuk tombol "Posisi soal"
  var permintaanMesin = null;   // AbortController evaluasi yang sedang berjalan
  var timerMesin = null;
  /* ?partai_id= : latihan dari satu partai saja (tautan "latih →" di halaman partai). */
  var partaiTunggal = parseInt(new URLSearchParams(window.location.search).get("partai_id"), 10) || null;
  var akunPribadi = [];      // id akun tanpa gelar/FIDE ID, ≥100 partai

  /* Catatan jawaban, pengiriman ke server, dan kartu Perkembangan ada di
     web/latihan-catatan.js (LatihanCatatan). Di berkas ini tinggal pemakaiannya. */
  var Catatan = window.LatihanCatatan;
  var JEDA_ULANG_HARI = Catatan.JEDA_ULANG_HARI;
  function bacaProgres() { return Catatan.baca(); }
  function kunciSoal(s) { return Catatan.kunciSoal(s); }
  function alasanTunda(p, s) { return Catatan.alasanTunda(p, s); }
  function tampilkanPerkembangan() { return Catatan.gambarPerkembangan(el.perkembangan, mode); }
  function muatCatatan() { return Catatan.muat(galat); }
  /* hasil: "benar" | "salah" | "menyerah" | "pilihan" */
  function ingatHasil(s, hasil, uci) { return Catatan.ingat(s, hasil, uci, tampilkanPerkembangan); }

  var mode = "kesalahan";
  var ringkasanPembukaan = null;

  /* Hanya soal yang boleh dikerjakan yang masuk antrean: yang sudah benar
     dibuang, yang salah menunggu JEDA_ULANG_HARI. */
  function susunUrutan() {
    var p = bacaProgres();
    var siap = [];
    soalSemua.forEach(function (s, i) { if (!alasanTunda(p, s)) siap.push(i); });
    return acak(siap);
  }
  /* Pesan kosong harus menyebut penyebabnya. Kalau semua kesalahan berasal dari
     posisi yang sudah kalah, "tidak ada kesalahan" itu menyesatkan. */
  function kosong(tempat) {
    if (dilewatiKalah) {
      return "Tidak ada soal " + tempat + ": " + dilewatiKalah + " kesalahan ada, tapi "
        + "semuanya dari posisi yang SKOR ENGINE-nya sudah di bawah " + ambangTampil()
        + ", artinya sudah kalah. Ini penilaian engine, bukan hitungan materi: "
        + "kalah kualitas tapi skornya masih plus tetap jadi soal. "
        + "Longgarkan saringan di atas: coba \"semua lawan\", format lain, atau akun lain.";
    }
    return "Tidak ada kesalahan jenis ini " + tempat + ".";
  }

  function hitungTunda() { return Catatan.hitung(soalSemua); }

  var el = {
    galat: document.getElementById("galat"),
    status: document.getElementById("status"),
    akun: document.getElementById("akun"),
    jenis: document.getElementById("jenis"),
    lawan: document.getElementById("lawan"),
    format: document.getElementById("format"),
    skor: document.getElementById("skor"),
    nomor: document.getElementById("nomor"),
    soal: document.getElementById("soal"),
    kartuJawab: document.getElementById("kartu-jawab"),
    jawab: document.getElementById("jawab"),
    lewati: document.getElementById("lewati"),
    menyerah: document.getElementById("menyerah"),
    kartuMesin: document.getElementById("kartu-mesin"),
    mesinSkor: document.getElementById("telusur-skor"),
    mesinMeta: document.getElementById("telusur-meta"),
    mesinGaris: document.getElementById("telusur-garis"),
    mesinStatus: document.getElementById("telusur-status"),
    mesinUlang: document.getElementById("telusur-ulang"),
    labelAkun: document.getElementById("label-akun"),
    ulangSemua: document.getElementById("ulang-semua"),
    mode: document.getElementById("mode"),
    perkembangan: document.getElementById("perkembangan")
  };

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
  function acak(arr) {
    for (var i = arr.length - 1; i > 0; i--) {
      var j = Math.floor(Math.random() * (i + 1));
      var t = arr[i]; arr[i] = arr[j]; arr[j] = t;
    }
    return arr;
  }

  /* ---------- akun ---------- */

  /* Akun sendiri dari modul bersama (web/akun-pribadi.js): pilihan pertama
     "semua akun" menggabungkan seluruh akun Lichess dan Chess.com. */
  function isiAkun() {
    return AkunPribadi.muat().then(function (akun) {
      akunPribadi = akun.map(function (p) { return p.id; });
      el.akun.textContent = "";
      if (akun.length > 1) {
        var semua = document.createElement("option");
        semua.value = "semua";
        semua.textContent = "semua akun (" + akun.length + ")";
        el.akun.appendChild(semua);
      }
      akun.forEach(function (p) {
        var o = document.createElement("option");
        o.value = p.id;
        o.textContent = p.nama + (p.sumber ? " · " + p.sumber : "") + " (" + p.jumlah_partai + ")";
        el.akun.appendChild(o);
      });
      if (!akun.length && !partaiTunggal) throw new Error("Tidak ada akun pribadi di database (impor dulu: python impor_pgn.py --semua).");
    });
  }

  /* Saringan lawan & format: aturan bersama di web/alat-catur.js. */
  function lolosLawan(g, warna) { return AlatCatur.lolosLawan(g, warna, el.lawan.value); }

  /* Detail partai (PGN + kontrol waktu), sekali per partai. */
  var cacheDetail = {};
  function ambilDetail(partaiId) {
    if (cacheDetail[partaiId]) return Promise.resolve(cacheDetail[partaiId]);
    return ambil("/api/partai/" + partaiId).then(function (g) {
      cacheDetail[partaiId] = g;
      cachePgn[partaiId] = g.pgn;
      return g;
    });
  }

  /* Partai teranalisis satu akun, dengan warna akun itu di tiap partai.
     Saringan format dikerjakan server (kolom `format`, Permintaan API #8). */
  function partaiAkun(id, batas) {
    var q = "/api/partai?pemain_id=" + id + "&dianalisis=true&per_halaman=100&urut=terbaru";
    if (el.format.value !== "semua") q += "&format=" + el.format.value;
    return ambil(q).then(function (d) {
      return d.partai.map(function (g) { return { g: g, warna: g.putih_id === id ? "putih" : "hitam" }; })
        .filter(function (x) { return lolosLawan(x.g, x.warna); })
        .slice(0, batas);
    });
  }

  /* Soal dari satu partai + analisisnya. warnaDiambil null = kedua warna. */
  function tambahSoal(g, a, warnaDiambil, jenis) {
    a.langkah.forEach(function (l) {
      if (warnaDiambil && l.warna !== warnaDiambil) return;
      if (jenis.indexOf(l.klasifikasi) < 0) return;
      if (!l.langkah_terbaik_uci || l.langkah_terbaik_uci === l.uci) return;
      if (l.eval_sebelum_cp !== null && l.eval_sebelum_cp !== undefined &&
          l.eval_sebelum_cp < BATAS_MIN_SKOR) {
        dilewatiKalah++;
        return;
      }
      /* Sesudah kesalahannya pun masih menang telak: tidak ada yang benar-benar
         hilang, jadi bukan bahan latihan. */
      if (l.eval_sesudah_cp !== null && l.eval_sesudah_cp !== undefined &&
          l.eval_sesudah_cp >= TOLERANSI_MASIH_MENANG) {
        dilewatiMenang++;
        return;
      }
      soalSemua.push({
        partai_id: g.id, ply: l.ply, warna: l.warna, san: l.san,
        terbaik_uci: l.langkah_terbaik_uci, terbaik_san: l.langkah_terbaik_san,
        variasi: l.variasi_terbaik || [], klasifikasi: l.klasifikasi,
        kehilangan: l.kehilangan_cp,
        sebelum: l.eval_sebelum_cp, sesudah: l.eval_sesudah_cp,
        format: AlatCatur.formatPartai(g), kontrol: AlatCatur.kontrolPartai(g),
        pemain: l.warna === "putih" ? g.putih : g.hitam,
        pemain_id: l.warna === "putih" ? g.putih_id : g.hitam_id,
        lawan: l.warna === "putih" ? g.hitam : g.putih,
        tanggal: g.tanggal
      });
    });
  }

  /* ---------- kumpulkan soal ---------- */

  function kumpulkan() {
    var semuaAkun = el.akun.value === "semua";
    var id = parseInt(el.akun.value, 10);
    var jenis = el.jenis.value.split(",");
    soalSemua = []; urutan = []; soal = null; benar = 0; dikerjakan = 0;
    dilewatiKalah = 0;
    dilewatiMenang = 0;
    el.status.textContent = "mengumpulkan soal…";
    el.soal.textContent = "";
    el.kartuJawab.hidden = true;
    el.skor.textContent = "";

    if (mode === "pembukaan" && !partaiTunggal) return kumpulkanPembukaan();

    if (partaiTunggal) {
      el.labelAkun.hidden = true;
      return Promise.all([ambil("/api/partai/" + partaiTunggal), ambil("/api/partai/" + partaiTunggal + "/analisis")])
        .then(function (r) {
          var g = r[0];
          /* Kalau salah satu pihak akun pribadi, hanya kesalahan pihak itu;
             kalau partai GM, kesalahan kedua pihak jadi soal. */
          var warna = akunPribadi.indexOf(g.putih_id) >= 0 ? "putih" : akunPribadi.indexOf(g.hitam_id) >= 0 ? "hitam" : null;
          tambahSoal(g, r[1], warna, jenis);
          if (!soalSemua.length) throw new Error(kosong("di partai " + partaiTunggal));
          urutan = susunUrutan();
          el.status.textContent = "";
          berikutnya();
        }).catch(function (e) {
          throw new Error(e.message.indexOf("404") >= 0 ? "Partai ini belum dianalisis." : e.message);
        });
    }

    var daftarId = semuaAkun ? akunPribadi : [id];
    /* Dulu 80 partai terbaru. Terlalu sedikit DAN bias: batch analisis dijalankan
       dengan --hasil kalah, jadi partai teranalisis terbaru hampir semuanya
       kekalahan (687 dari 833), dan blunder di partai kalah kebanyakan terjadi
       saat skornya sudah jeblok — hasilnya "tidak ada soal" padahal di seluruh
       database ada 529 blunder dengan skor engine 0 ke atas. */
    var batasPerAkun = semuaAkun ? Math.max(50, Math.ceil(300 / Math.max(1, daftarId.length))) : 250;
    return Promise.all(daftarId.map(function (x) { return partaiAkun(x, batasPerAkun); })).then(function (kelompok) {
      var partai = [];
      kelompok.forEach(function (k) { partai = partai.concat(k); });
      /* partai yang sama bisa muncul di dua akun (mis. akun sendiri melawan akun sendiri) */
      var terlihat = {};
      partai = partai.filter(function (x) { if (terlihat[x.g.id]) return false; terlihat[x.g.id] = true; return true; });
      if (!partai.length) throw new Error("Belum ada partai akun ini yang dianalisis. Jalankan: python analisis_partai.py (lihat README).");
      /* satu per satu supaya server tidak dibanjiri puluhan permintaan sekaligus */
      var rantai = Promise.resolve();
      partai.forEach(function (x) {
        rantai = rantai.then(function () {
          return ambil("/api/partai/" + x.g.id + "/analisis").then(function (a) {
            tambahSoal(x.g, a, x.warna, jenis);
          }).catch(function () { /* partai tanpa analisis dilewati */ });
        });
      });
      return rantai;
    }).then(function () {
      if (!soalSemua.length) throw new Error(kosong("di partai yang sudah dianalisis"));
      urutan = susunUrutan();
      el.status.textContent = "";
      berikutnya();
    });
  }

  /* Mode pembukaan: data dan tampilannya di web/latihan-pembukaan.js. */
  function kumpulkanPembukaan() {
    ringkasanPembukaan = null;
    el.status.textContent = "menghitung posisi pembukaan… (pertama kali ±10 detik)";
    return LatihanPembukaan.muat().then(function (d) {
      ringkasanPembukaan = d.ringkasan;
      d.soal.forEach(function (s) { soalSemua.push(s); });
      if (!soalSemua.length) {
        throw new Error("Tidak ada soal pembukaan: di " + (ringkasanPembukaan.partai_repertoire || 0) +
          " partai repertoire, tidak ada posisi tempat langkahmu berulang kali jarang dimainkan GM.");
      }
      urutan = susunUrutan();
      el.status.textContent = "";
      berikutnya();
    });
  }

  function tampilkanSoalPembukaan() {
    permainan = new Chess();
    for (var i = 0; i < soal.jalur.length; i++) {
      if (!permainan.move(soal.jalur[i])) throw new Error("Jalur soal pembukaan tidak bisa dimainkan");
    }
    papan.orientation(soal.warna === "putih" ? "white" : "black");
    fenSoal = permainan.fen();
    papan.position(fenSoal, false);
    el.status.textContent = (soal.warna === "putih" ? "Putih" : "Hitam") + " jalan · cari langkah yang dimainkan GM";
    LatihanPembukaan.gambarSoal(soal, el.soal);
  }

  function jawabPembukaan(uci, menyerah) {
    return LatihanPembukaan.gambarJawaban(soal, el.jawab, uci, menyerah);
  }

  /* ---------- posisi soal ---------- */

  function posisiSebelum(pgn, ply) {
    var penuh = new Chess();
    if (!penuh.load_pgn(pgn)) return null;
    var riwayat = penuh.history();
    var c = AlatCatur.permainanAwal(penuh);      // partai ber-[FEN] tidak mulai dari posisi standar
    for (var i = 0; i < ply && i < riwayat.length; i++) c.move(riwayat[i]);
    return c;
  }

  function ambilPgn(partaiId) {
    if (cachePgn[partaiId]) return Promise.resolve(cachePgn[partaiId]);
    return ambil("/api/partai/" + partaiId).then(function (g) { cachePgn[partaiId] = g.pgn; return g.pgn; });
  }

  function berikutnya() {
    el.kartuJawab.hidden = true;
    el.kartuMesin.hidden = true;
    dijawab = false;
    telusur = false;
    batalkanMesin();
    hapusPilihan();
    if (!urutan.length) {
      soal = null;
      el.soal.textContent = "";
      el.soal.appendChild(buat("div", "pesan kecil", "Semua soal selesai · " + benar + " dari " + dikerjakan + " benar"));
      el.status.textContent = "selesai";
      return;
    }
    soal = soalSemua[urutan.pop()];
    el.nomor.textContent = "soal ke-" + (dikerjakan + 1) + " · sisa " + urutan.length;
    perbaruiSkor();
    if (soal.jenis === "pembukaan") {
      try { tampilkanSoalPembukaan(); } catch (e) { galat(e.message); }
      return;
    }
    ambilPgn(soal.partai_id).then(function (pgn) {
      permainan = posisiSebelum(pgn, soal.ply);
      if (!permainan) throw new Error("PGN partai " + soal.partai_id + " tidak bisa dibaca");
      papan.orientation(soal.warna === "putih" ? "white" : "black");
      fenSoal = permainan.fen();
      papan.position(permainan.fen(), false);
      el.status.textContent = (soal.warna === "putih" ? "Putih" : "Hitam") + " jalan · cari langkah terbaik";
      el.soal.textContent = "";
      var pribadi = akunPribadi.indexOf(soal.pemain_id) >= 0;
      el.soal.appendChild(buat("div", null, (partaiTunggal ? soal.pemain + " melawan " : "Melawan ") + soal.lawan +
        (soal.format && soal.format !== "tanpa" ? " · " + soal.format + (soal.kontrol ? " " + soal.kontrol : "") : "") +
        (soal.tanggal ? " · " + soal.tanggal : "")));
      el.soal.appendChild(buat("div", "kecil redup", "Di partai ini " + (pribadi ? "kamu bermain" : "dimainkan") + " " + soal.san + " (" + (soal.klasifikasi === "blunder" ? "??" : "?") + ")."));
    }).catch(function (e) { galat(e.message); });
  }

  function perbaruiSkor() {
    var t = hitungTunda();
    el.skor.textContent = "";
    /* Angka tebal + keterangan redup; alasan soal dilewati di baris sendiri
       supaya tidak bercampur dengan skor sesi. */
    function angka(n, ket) {
      var s = buat("span", null, "");
      s.appendChild(buat("strong", null, n));
      s.appendChild(teks(" " + ket));
      el.skor.appendChild(s);
    }
    if (dikerjakan) angka(benar + "/" + dikerjakan, "benar");
    if (t.benar) angka(t.benar, "selesai");
    if (t.tunggu) angka(t.tunggu, "menunggu " + JEDA_ULANG_HARI + " hari");
    if (t.pilihan) angka(t.pilihan, "pilihan sendiri");
    if (mode === "pembukaan" && ringkasanPembukaan) {
      angka((ringkasanPembukaan.partai_repertoire || 0).toLocaleString("id-ID"), "partai repertoire");
    }
    var lewat = [];
    if (dilewatiKalah) lewat.push(dilewatiKalah + " karena skor engine < " + ambangTampil());
    if (dilewatiMenang) lewat.push(dilewatiMenang + " karena sesudahnya masih menang telak");
    if (lewat.length) el.skor.appendChild(buat("span", "dilewati", "Dilewati: " + lewat.join(" · ")));
  }

  /* `menyerah` = Ray tidak menebak, hanya ingin melihat jawabannya lalu
     menelusuri sendiri. Dihitung sebagai dikerjakan tapi tidak benar, dan
     soalnya tetap diulang di akhir sesi seperti jawaban salah. */
  function nilai(uci, menyerah) {
    if (dijawab) return;
    dijawab = true;
    dikerjakan++;
    el.jawab.textContent = "";
    var pembukaan = soal.jenis === "pembukaan";
    var cocok = pembukaan ? jawabPembukaan(uci, menyerah) : !menyerah &&
      (uci === soal.terbaik_uci || uci === soal.terbaik_uci.slice(0, 4));
    /* Benar = dibuang dari kumpulan. Salah atau menyerah = disimpan dan baru
       muncul lagi setelah JEDA_ULANG_HARI, bukan diulang di sesi yang sama. */
    if (cocok) benar++;
    ingatHasil(soal, cocok ? "benar" : (menyerah ? "menyerah" : "salah"), uci);
    perbaruiSkor();
    if (!pembukaan) {
    el.jawab.appendChild(buat("div", cocok ? "latihan-benar" : "latihan-salah",
      cocok ? "Benar" : (menyerah ? "Jawabannya" : "Bukan itu")));
    el.jawab.appendChild(buat("div", null, "Terbaik: " + soal.terbaik_san +
      (soal.variasi.length > 1 ? "  (" + soal.variasi.slice(0, 6).join(" ") + ")" : "")));
    if (soal.kehilangan) el.jawab.appendChild(buat("div", "kecil redup", "Langkah asli " + soal.san + " membuang " + soal.kehilangan + " cp"));
    }
    /* Inti pelajarannya: dari kedudukan apa ke kedudukan apa. "Imbang langsung
       jadi kalah" adalah kasus yang paling mahal. */
    if (soal.sebelum !== null && soal.sebelum !== undefined) {
      var dari = namaKedudukan(soal.sebelum), ke = namaKedudukan(soal.sesudah);
      var baris = "Kedudukan: " + dari + " (" + skorTampil(soal.sebelum) + ") → " +
                  ke + " (" + skorTampil(soal.sesudah) + ")";
      var fatal = (dari === "imbang" && ke === "kalah") ||
                  (soal.sebelum >= PITA_IMBANG && soal.sesudah < TOLERANSI_MASIH_MENANG &&
                   soal.sebelum - soal.sesudah >= 200);
      var el2 = buat("div", "kedudukan" + (fatal ? " fatal" : ""), baris);
      if (dari === "imbang" && ke === "kalah") el2.appendChild(buat("span", "catatan", "Fatal: satu langkah dari imbang ke kalah"));
      else if (fatal) el2.appendChild(buat("span", "catatan", "Fatal: keunggulan yang hilang sebanyak ini"));
      el.jawab.appendChild(el2);
    }
    el.kartuJawab.classList.toggle("jawab-benar", cocok);
    el.kartuJawab.classList.toggle("jawab-salah", !cocok);
    var tb = buat("div", "tautan-baris");
    var a = document.createElement("a");
    a.href = "/partai/" + soal.partai_id;
    a.target = "_blank";
    a.appendChild(teks(pembukaan ? "Partai terakhirmu di sini" : "Buka partai"));
    tb.appendChild(a);
    if (pembukaan) {
      var pilih = document.createElement("a");
      pilih.href = "#";
      pilih.title = "Langkahmu memang disengaja: posisi ini tidak ditanyakan lagi (kembali lewat \"ulang semua\")";
      pilih.appendChild(teks("Ini memang pilihanku"));
      pilih.addEventListener("click", function (ev) {
        ev.preventDefault();
        ingatHasil(soal, "pilihan");
        perbaruiSkor();
        berikutnya();
      });
      tb.appendChild(pilih);
    }
    /* Papan analisis penuh: susun posisi, ganti giliran, multipv sampai 5.
       Kartu engine di bawah cukup untuk menelusuri cepat; ini untuk yang serius. */
    var pa = document.createElement("a");
    pa.href = "/static/analisis.html?fen=" + encodeURIComponent(fenSoal || permainan.fen());
    pa.target = "_blank";
    pa.title = "Buka posisi ini di papan analisis penuh";
    pa.appendChild(teks("Papan analisis"));
    tb.appendChild(pa);
    var b = document.createElement("a");
    b.href = "#";
    b.className = "utama";
    b.appendChild(teks("Soal berikutnya ›"));
    b.addEventListener("click", function (ev) { ev.preventDefault(); berikutnya(); });
    tb.appendChild(b);
    el.jawab.appendChild(tb);
    el.kartuJawab.hidden = false;
    el.status.textContent = cocok ? "benar" : "salah";

    /* Papan dibuka lagi: dari sini langkah tidak dinilai, hanya ditelusuri. */
    telusur = true;
    el.kartuMesin.hidden = false;
    el.mesinStatus.textContent = "Mainkan langkah apa pun di papan untuk melihat "
      + "penilaian engine. Klik langkah di garis untuk mengikutinya.";
    jadwalkanMesin();
  }

  /* ---------- papan ---------- */

  function bolehGeser(source, piece) {
    hapusPilihan();
    if (!soal || !permainan) return false;
    if (dijawab && !telusur) return false;
    if (permainan.game_over()) return false;
    return piece.charAt(0) === permainan.turn();
  }

  /* ---------- klik-untuk-jalan (modul bersama klik-papan.js) ---------- */

  function hapusPilihan() {
    terpilih = null;
    if (klik) klik.hapusSorot();
  }

  function klikKotak(sq) {
    if (!sq || !soal || !permainan || (dijawab && !telusur)) { hapusPilihan(); return; }
    var bidak = permainan.get(sq);
    if (terpilih === null) {
      if (!bidak || bidak.color !== permainan.turn()) return;
      terpilih = sq;
      klik.sorotPilih(sq);
      permainan.moves({ square: sq, verbose: true }).forEach(function (m) { klik.sorotTarget(m.to, !!m.captured); });
      return;
    }
    if (sq === terpilih) { hapusPilihan(); return; }
    var dari = terpilih;
    if (PilihPromosi.tunda(permainan, dari, sq, function (p) { jalankan(dari, sq, p); })) {
      hapusPilihan();
      return;
    }
    if (jalankan(terpilih, sq)) {
      hapusPilihan();
      return;
    }
    hapusPilihan();
    if (bidak && bidak.color === permainan.turn()) klikKotak(sq);
  }

  function saatJatuh(source, target) {
    if (PilihPromosi.tunda(permainan, source, target, function (p) { jalankan(source, target, p); })) {
      return "snapback";
    }
    if (!jalankan(source, target)) return "snapback";
  }

  /* Satu langkah dari papan (klik, seret, atau pilihan promosi). Promosi
     selain ratu harus bisa dijawab: soal ber-jawaban =N/=R dulu mustahil
     benar karena semua papan memaksa ratu (audit 27 Sep 2026). */
  function jalankan(dari, ke, promosi) {
    var m = permainan.move({ from: dari, to: ke, promotion: promosi || "q" });
    if (!m) return null;
    papan.position(permainan.fen());
    if (telusur) jadwalkanMesin(); else nilai(m.from + m.to + (m.promotion || ""));
    return m;
  }


  /* ---------- engine saat menelusur ----------
     Memakai GET /api/evaluasi, endpoint yang sama dengan papan analisis:
     satu Stockfish profil hemat di server, ~1,5 detik per permintaan. */

  function batalkanMesin() {
    clearTimeout(timerMesin);
    if (permintaanMesin) { permintaanMesin.abort(); permintaanMesin = null; }
  }

  function jadwalkanMesin() {
    batalkanMesin();
    timerMesin = setTimeout(evaluasiSekarang, 250);
  }

  function formatSkor(cp, mat) {
    if (mat !== null && mat !== undefined) return (mat > 0 ? "+M" : "-M") + Math.abs(mat);
    if (cp === null || cp === undefined) return "?";
    return (cp > 0 ? "+" : "") + (cp / 100).toFixed(2).replace(".", ",");
  }

  function evaluasiSekarang() {
    if (!telusur || !permainan) return;
    var fen = permainan.fen();
    batalkanMesin();
    permintaanMesin = new AbortController();
    var sinyal = permintaanMesin.signal;
    el.mesinStatus.textContent = "Mengevaluasi…";
    fetch("/api/evaluasi?fen=" + encodeURIComponent(fen) + "&multipv=3", { signal: sinyal })
      .then(function (r) {
        return r.text().then(function (t) {
          var b = null;
          try { b = JSON.parse(t); } catch (e) { b = null; }
          return { ok: r.ok, status: r.status, body: b };
        });
      })
      .then(function (res) {
        if (!permainan || permainan.fen() !== fen) return;   // sudah pindah posisi
        if (!res.ok) {
          var d = res.body && res.body.detail ? String(res.body.detail) : "";
          el.mesinStatus.textContent = res.status === 409
            ? "Engine sedang dipakai batch analisis; coba lagi sebentar."
            : (res.status === 503 ? "Engine tidak tersedia di server."
               : "Evaluasi gagal (" + res.status + ")" + (d ? ": " + d : ""));
          return;
        }
        tampilkanMesin(res.body || {}, fen);
      })
      .catch(function (e) {
        if (e.name !== "AbortError") el.mesinStatus.textContent = "Gagal menghubungi server: " + e.message;
      })
      .then(function () {
        if (permintaanMesin && permintaanMesin.signal === sinyal) permintaanMesin = null;
      });
  }

  function tampilkanMesin(b, fen) {
    var garis = b.garis || [];
    var skor = b.skor || {};
    el.mesinGaris.textContent = "";

    if (!garis.length) {
      el.mesinSkor.textContent = skor.pemenang ? (skor.pemenang === "putih" ? "1-0" : "0-1") : "½";
      el.mesinGaris.appendChild(buat("div", "pesan kecil",
        skor.pemenang ? "Skakmat." : "Remis atau pat — tidak ada langkah."));
      el.mesinStatus.textContent = "Posisi sudah selesai.";
      return;
    }
    el.mesinSkor.textContent = formatSkor(garis[0].cp, garis[0].mate);

    var bagian = fen.split(/\s+/);
    var giliranHitam = bagian[1] === "b";
    var nomorAwal = parseInt(bagian[5], 10) || 1;

    garis.forEach(function (g) {
      var baris = buat("div", "pv");
      baris.appendChild(buat("span", "pv-skor " + ((g.mate || g.cp || 0) >= 0 ? "putih" : "hitam"),
        formatSkor(g.cp, g.mate)));
      (g.pv || []).slice(0, 10).forEach(function (san, j) {
        var nomor = nomorAwal + Math.floor((j + (giliranHitam ? 1 : 0)) / 2);
        var hitam = (j + (giliranHitam ? 1 : 0)) % 2 === 1;
        if (!hitam) baris.appendChild(buat("span", "nomor", nomor + "."));
        else if (j === 0) baris.appendChild(buat("span", "nomor", nomor + "..."));
        var sp = buat("span", "langkah", san);
        sp.title = "Mainkan sampai langkah ini";
        sp.addEventListener("click", function () {
          if (!permainan || permainan.fen() !== fen) return;
          for (var k = 0; k <= j; k++) { if (!permainan.move(g.pv[k])) break; }
          papan.position(permainan.fen());
          hapusPilihan();
          jadwalkanMesin();
        });
        baris.appendChild(sp);
        baris.appendChild(teks(" "));
      });
      baris.appendChild(buat("span", "redup kecil", "d" + (g.depth === undefined ? "?" : g.depth)));
      el.mesinGaris.appendChild(baris);
    });

    el.mesinMeta.textContent = "Stockfish · profil " + (b.profil || "hemat") +
      " · depth " + (b.depth || "?") +
      (b.durasi_ms ? " · " + (b.durasi_ms / 1000).toFixed(1).replace(".", ",") + " s" : "");
    el.mesinStatus.textContent = "Sudut pandang Putih. Mainkan langkah lain untuk membandingkan.";
  }

  papan = Chessboard("papan", {
    position: "start",
    draggable: true,
    pieceTheme: "/static/img/chesspieces/{piece}.png",
    onDragStart: bolehGeser,
    onDrop: saatJatuh,
    onSnapEnd: function () { if (permainan) papan.position(permainan.fen()); }
  });
  window.addEventListener("resize", function () { papan.resize(); });
  window.addEventListener("load", function () { setTimeout(function () { papan.resize(); }, 300); });
  if (window.KlikPapan) {
    klik = KlikPapan(document.getElementById("papan"), {
      orientasi: function () { return papan.orientation(); },
      saatKlik: klikKotak
    });
  }

  el.lewati.addEventListener("click", function () { if (soal) { dikerjakan++; perbaruiSkor(); berikutnya(); } });
  el.menyerah.addEventListener("click", function () {
    if (soal && !dijawab) nilai(null, true);
  });
  el.ulangSemua.addEventListener("click", function () {
    var namaMode = mode === "pembukaan" ? "latihan pembukaan" : "latihan kesalahan partai";
    if (!window.confirm("Lupakan semua catatan benar/salah " + namaMode + "? Semua soalnya muncul lagi.")) return;
    /* Hanya catatan mode yang sedang dibuka; mode lain tidak ikut terhapus,
       dan riwayat di server tetap dihitung di Perkembangan. */
    Catatan.ulangSemua(mode).then(function () {
      tampilkanPerkembangan();
      return kumpulkan();
    }).catch(function (e) { galat(e.message); });
  });
  el.mode.addEventListener("change", function () {
    mode = el.mode.value;
    el.galat.hidden = true;
    /* Saringan akun/lawan/format/jenis hanya berlaku untuk soal kesalahan. */
    ["akun", "lawan", "format", "jenis"].forEach(function (k) {
      var lab = el[k].closest("label");
      if (lab && mode === "pembukaan") lab.hidden = true;
    });
    if (mode !== "pembukaan" && adaSaringanUrl) munculkanSaringan();
    try {
      var u = new URL(window.location.href);
      if (mode === "pembukaan") u.searchParams.set("mode", "pembukaan"); else u.searchParams.delete("mode");
      window.history.replaceState(null, "", u);
    } catch (e) { /* URL tidak bisa diubah: tidak apa-apa */ }
    tampilkanPerkembangan();
    kumpulkan().catch(function (e) { el.status.textContent = ""; galat(e.message); });
  });
  el.akun.addEventListener("change", function () { kumpulkan().catch(function (e) { galat(e.message); }); });
  el.jenis.addEventListener("change", function () { kumpulkan().catch(function (e) { galat(e.message); }); });
  el.lawan.addEventListener("change", function () { kumpulkan().catch(function (e) { galat(e.message); }); });
  el.format.addEventListener("change", function () { kumpulkan().catch(function (e) { galat(e.message); }); });
  document.addEventListener("keydown", function (ev) {
    if (ev.target.tagName === "SELECT" || ev.target.tagName === "INPUT") return;
    if (ev.key === "n" || ev.key === "N") el.lewati.click();
  });

  /* Kait pengujian tanpa mouse: ?uji_jawab=terbaik menjawab soal pertama
     dengan langkah terbaik, ?uji_jawab=salah dengan langkah sah pertama yang
     bukan terbaik, ?uji_jawab=klik menjawab lewat dua klik (klik-papan.js). */
  var uji = new URLSearchParams(window.location.search).get("uji_jawab");
  if (uji) {
    var cek = setInterval(function () {
      if (!soal || !permainan || el.status.textContent.indexOf("cari") < 0) return;
      clearInterval(cek);
      var uci = soal.terbaik_uci;
      if (uji === "menyerah") { el.menyerah.click(); return; }
      if (uji === "klik") {
        klikKotak(uci.slice(0, 2));
        setTimeout(function () { klikKotak(uci.slice(2, 4)); }, 100);
        return;
      }
      if (uji === "salah") {
        var m = permainan.moves({ verbose: true }).filter(function (x) { return x.from + x.to !== uci.slice(0, 4); })[0];
        uci = m.from + m.to;
      }
      var mv = permainan.move({ from: uci.slice(0, 2), to: uci.slice(2, 4), promotion: uci.slice(4) || "q" });
      if (mv) nilai(mv.from + mv.to + (mv.promotion || ""));
    }, 300);
  }

  /* Saringan bisa diisi lewat URL (mis. tautan dari halaman lain, atau uji headless):
     ?akun=semua|<id>&lawan=ketentuan|2200|gelar|semua&format=bullet|blitz|rapid|klasik */
  /* Saringan dikunci pada satu kombinasi (Ray, 13 Sep 2026: "formatnya selalu
     ini dan dibuat fix tidak bisa diganti, jadinya akan habis jika sudah tidak
     ada soal lagi"): semua akun, semua lawan, semua format, blunder+kesalahan.
     Kendalinya disembunyikan di HTML, TAPI tetap ada — halaman Beranda dan
     Kelemahan menaut ke sini dengan ?akun=/?lawan=/?jenis=. Kalau tautan
     seperti itu dipakai, kendalinya dimunculkan lagi supaya jelas kenapa
     soalnya berbeda dan bisa dikembalikan. */
  var qSaring = new URLSearchParams(window.location.search);
  var adaSaringanUrl = false;
  ["lawan", "format", "jenis"].forEach(function (k) {
    if (qSaring.get(k) && el[k].querySelector('option[value="' + CSS.escape(qSaring.get(k)) + '"]')) {
      el[k].value = qSaring.get(k);
      adaSaringanUrl = true;
    }
  });
  function munculkanSaringan() {
    ["akun", "lawan", "format", "jenis"].forEach(function (k) {
      var lab = el[k].closest("label");
      if (lab) lab.hidden = false;
    });
  }
  /* ?mode=pembukaan membuka latihan pembukaan langsung (juga dipakai uji headless). */
  if (qSaring.get("mode") === "pembukaan" && !partaiTunggal) {
    mode = "pembukaan";
    el.mode.value = "pembukaan";
  }
  /* Catatan dari server dulu: tanpa itu soal yang sudah benar ikut muncul lagi. */
  muatCatatan().then(isiAkun).then(function () {
    tampilkanPerkembangan();
    var a = qSaring.get("akun");
    if (a && el.akun.querySelector('option[value="' + CSS.escape(a) + '"]')) {
      el.akun.value = a;
      adaSaringanUrl = true;
    }
    if (adaSaringanUrl && mode !== "pembukaan") munculkanSaringan();
    return kumpulkan();
  }).catch(function (e) { el.status.textContent = ""; el.soal.textContent = ""; galat(e.message); });
})();
