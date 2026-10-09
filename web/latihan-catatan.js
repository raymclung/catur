/* Catatan jawaban latihan: status tiap soal, pengiriman ke server, dan kartu
   Perkembangan. Dipecah dari web/latihan.js (1.007 baris) 25 Sep 2026 —
   berkas itu memuat tiga urusan sekaligus: soal kesalahan partai, mode
   pembukaan, dan catatan/kemajuan.

   Sumber kebenarannya DATABASE (tabel latihan_jawaban, rute /api/latihan/*):
   tiap jawaban satu baris, jadi riwayatnya bisa dihitung per minggu dan tidak
   hilang kalau data situs dibersihkan. localStorage tinggal dua peran:
   salinan kalau server tidak bisa dihubungi, dan antrean jawaban yang belum
   terkirim. */

window.LatihanCatatan = (function () {
  "use strict";

  var KUNCI_PROGRES = "latihan.dikuasai";
  var KUNCI_TERTUNDA = "latihan.tertunda";

  /* Aturan Ray 13 Sep 2026: soal yang BENAR dibuang dari kumpulan, soal yang
     SALAH disimpan dan baru muncul lagi setelah minimal 3 hari — "biar sudah
     lupa". Karena itu tidak ada lagi pengulangan di sesi yang sama.
     Simpanan: { "partai:ply": {h: "b"|"s"|"a", w: "YYYY-MM-DD"} }; nilai lama
     berupa teks (waktu jawaban benar) tetap dibaca sebagai benar. */
  var JEDA_ULANG_HARI = 3;

  var progres = {};

  function teks(isi) { return document.createTextNode(isi === null || isi === undefined ? "" : String(isi)); }
  function buat(tag, kelas, isi) {
    var e = document.createElement(tag);
    if (kelas) e.className = kelas;
    if (isi !== undefined) e.appendChild(teks(isi));
    return e;
  }
  function ambil(url) {
    return fetch(url).then(function (r) {
      if (!r.ok) throw new Error("Server menjawab " + r.status + " untuk " + url);
      return r.json();
    });
  }
  function bacaLokal(kunci, awal) {
    try { return JSON.parse(localStorage.getItem(kunci) || "null") || awal; } catch (e) { return awal; }
  }
  function tulisLokal(kunci, nilai) {
    try { localStorage.setItem(kunci, JSON.stringify(nilai)); } catch (e) { /* penyimpanan tidak tersedia */ }
  }
  function hariIni() {
    var d = new Date();
    return d.getFullYear() + "-" + ("0" + (d.getMonth() + 1)).slice(-2) + "-" + ("0" + d.getDate()).slice(-2);
  }
  function persenTeks(x) { return x === null || x === undefined ? "–" : String(x).replace(".", ",") + "%"; }
  function tanggalPendek(iso) {
    var b = iso.split("-");
    return parseInt(b[2], 10) + " " + ["Jan", "Feb", "Mar", "Apr", "Mei", "Jun", "Jul",
                                       "Agu", "Sep", "Okt", "Nov", "Des"][parseInt(b[1], 10) - 1];
  }

  /* ---------- kunci & status satu soal ---------- */

  /* Soal pembukaan dikunci per POSISI, bukan per partai: posisi yang sama
     dicapai di puluhan partai dan cukup satu catatan. */
  function kunciSoal(s) { return s.jenis === "pembukaan" ? "buka:" + s.fen : s.partai_id + ":" + s.ply; }

  /* Babak kesalahan dari nomor langkah — kasar tapi jujur, jadi labelnya
     menyebut nomor langkah, bukan "pembukaan/tengah/akhir". */
  function kelompokSoal(s) {
    if (s.jenis === "pembukaan") return s.pembukaan;
    var langkah = Math.floor(s.ply / 2) + 1;
    return langkah <= 12 ? "langkah 1–12" : langkah <= 30 ? "langkah 13–30" : "langkah 31+";
  }

  function catatanSoal(p, s) {
    var v = p[kunciSoal(s)];
    if (!v) return null;
    if (typeof v === "string") return { h: "b", w: v.slice(0, 10) };   // bentuk lama
    return v;
  }

  function hariSejak(tanggal) {
    var t = Date.parse(tanggal + "T00:00:00");
    return isNaN(t) ? 9999 : Math.floor((Date.now() - t) / 86400000);
  }

  /* null = boleh dikerjakan sekarang; teks = alasan ditunda. */
  function alasanTunda(p, s) {
    var c = catatanSoal(p, s);
    if (!c) return null;
    if (c.h === "b") return "sudah benar";
    /* "Ini memang pilihanku": langkah jarang GM yang Ray mainkan dengan sengaja
       (mis. sistem d3 di English). Tidak ditanyakan lagi, kecuali "ulang semua". */
    if (c.h === "a") return "pilihan sendiri";
    var sisa = JEDA_ULANG_HARI - hariSejak(c.w);
    return sisa > 0 ? "salah, ulang " + sisa + " hari lagi" : null;
  }

  function hitung(daftarSoal) {
    var p = progres, benar = 0, tunggu = 0, pilihan = 0;
    daftarSoal.forEach(function (s) {
      var a = alasanTunda(p, s);
      if (a === "sudah benar") benar++;
      else if (a === "pilihan sendiri") pilihan++;
      else if (a) tunggu++;
    });
    return { benar: benar, tunggu: tunggu, pilihan: pilihan };
  }

  /* ---------- pengiriman ke server ---------- */

  function kirimJawaban(isi) {
    return fetch("/api/latihan/jawaban", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(isi)
    }).then(function (r) { if (!r.ok) throw new Error("server " + r.status); });
  }

  /* Jawaban yang gagal terkirim (server mati, database sedang dikunci impor)
     diantrekan dan dikirim ulang saat halaman dibuka berikutnya. */
  function kirimTertunda() {
    var antre = bacaLokal(KUNCI_TERTUNDA, []);
    if (!antre.length) return Promise.resolve();
    var sisa = [];
    return antre.reduce(function (rantai, isi) {
      return rantai.then(function () { return kirimJawaban(isi).catch(function () { sisa.push(isi); }); });
    }, Promise.resolve()).then(function () { tulisLokal(KUNCI_TERTUNDA, sisa); });
  }

  /* Catatan dari server. Pertama kali: catatan lama di browser dipindahkan
     (server melewati kunci yang sudah punya baris, jadi aman diulang).
     `saatGagal` dipanggil dengan pesan kalau servernya tidak bisa dihubungi;
     halaman yang memutuskan cara menampilkannya. */
  function muat(saatGagal) {
    var lama = bacaLokal(KUNCI_PROGRES, {});
    var impor = Object.keys(lama).length
      ? fetch("/api/latihan/catatan/impor", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ catatan: lama })
        }).catch(function () { /* dicoba lagi lain kali */ })
      : Promise.resolve();
    return impor.then(kirimTertunda).then(function () { return ambil("/api/latihan/catatan"); })
      .then(function (c) { simpan(c); })
      .catch(function () {
        progres = lama;
        if (saatGagal) {
          saatGagal("Catatan latihan tidak bisa diambil dari server; sementara memakai salinan di browser.");
        }
      });
  }

  function baca() { return progres; }
  function simpan(p) { progres = p; tulisLokal(KUNCI_PROGRES, p); }

  /* hasil: "benar" | "salah" | "menyerah" | "pilihan".
     `sesudah` dipanggil setelah jawabannya benar-benar tersimpan di server. */
  function ingat(s, hasil, uci, sesudah) {
    var p = baca();
    p[kunciSoal(s)] = { h: hasil === "benar" ? "b" : hasil === "pilihan" ? "a" : "s", w: hariIni() };
    simpan(p);
    var isi = { kunci: kunciSoal(s), hasil: hasil, kelompok: kelompokSoal(s) };
    if (uci && /^[a-h][1-8][a-h][1-8][qrbn]?$/.test(uci)) isi.langkah_uci = uci;
    return kirimJawaban(isi).then(function () { if (sesudah) sesudah(); }).catch(function () {
      var antre = bacaLokal(KUNCI_TERTUNDA, []);
      antre.push(isi);
      tulisLokal(KUNCI_TERTUNDA, antre);
    });
  }

  /* "Ulang semua": riwayat di server TIDAK dihapus (tetap dihitung di
     Perkembangan), hanya status soal mode ini yang dilupakan. */
  function ulangSemua(mode) {
    return fetch("/api/latihan/catatan?mode=" + mode, { method: "DELETE" }).then(function (r) {
      if (!r.ok) throw new Error("Server menjawab " + r.status + " saat mengulang catatan");
      var p = baca();
      Object.keys(p).forEach(function (k) {
        if ((k.indexOf("buka:") === 0) === (mode === "pembukaan")) delete p[k];
      });
      simpan(p);
    });
  }

  /* ---------- kartu Perkembangan (GET /api/latihan/ringkasan) ---------- */

  function gambarPerkembangan(isi, mode) {
    if (!isi) return Promise.resolve();
    return ambil("/api/latihan/ringkasan").then(function (semua) {
      var r = semua[mode];
      isi.textContent = "";
      if (!r) return;
      var status = buat("div", "latihan-strip");
      [[r.selesai, "selesai"], [r.menunggu, "menunggu " + semua.jeda_ulang_hari + " hari"],
       [r.siap_ulang, "siap diulang"], [r.pilihan, "pilihan sendiri"]].forEach(function (x) {
        var sp = buat("span");
        sp.appendChild(buat("strong", null, x[0]));
        sp.appendChild(teks(" " + x[1]));
        status.appendChild(sp);
      });
      isi.appendChild(status);
      if (!r.dijawab) {
        isi.appendChild(buat("div", "kecil redup", "Belum ada jawaban di mode ini."));
        return;
      }
      isi.appendChild(buat("div", "kecil", "Semua jawaban: " + r.benar + " dari " + r.dijawab +
        " benar (" + persenTeks(r.persen_benar) + ")"));

      var tMinggu = document.createElement("table");
      var kepala = document.createElement("tr");
      ["Minggu mulai", "Dijawab", "Benar"].forEach(function (j) { kepala.appendChild(buat("th", null, j)); });
      tMinggu.appendChild(kepala);
      r.per_minggu.forEach(function (m) {
        var tr = document.createElement("tr");
        var redup = m.dijawab ? null : "redup";
        tr.appendChild(buat("td", redup, tanggalPendek(m.mulai)));
        tr.appendChild(buat("td", redup, m.dijawab));
        tr.appendChild(buat("td", redup, m.dijawab ? m.benar + " (" + persenTeks(m.persen_benar) + ")" : "–"));
        tMinggu.appendChild(tr);
      });
      isi.appendChild(tMinggu);

      if (r.per_kelompok.length) {
        var tKel = document.createElement("table");
        var k2 = document.createElement("tr");
        [mode === "pembukaan" ? "Pembukaan" : "Babak", "Salah", "Benar", "% benar"].forEach(function (j) {
          k2.appendChild(buat("th", null, j));
        });
        tKel.appendChild(k2);
        r.per_kelompok.forEach(function (k) {
          var tr = document.createElement("tr");
          tr.appendChild(buat("td", null, k.kelompok));
          tr.appendChild(buat("td", null, k.salah));
          tr.appendChild(buat("td", null, k.benar));
          tr.appendChild(buat("td", null, persenTeks(k.persen_benar)));
          tKel.appendChild(tr);
        });
        isi.appendChild(tKel);
      }
    }).catch(function () {
      isi.textContent = "";
      isi.appendChild(buat("div", "kecil redup", "Ringkasan tidak bisa diambil dari server."));
    });
  }

  return {
    JEDA_ULANG_HARI: JEDA_ULANG_HARI,
    muat: muat,
    baca: baca,
    simpan: simpan,
    ingat: ingat,
    ulangSemua: ulangSemua,
    kunciSoal: kunciSoal,
    kelompokSoal: kelompokSoal,
    catatanSoal: catatanSoal,
    alasanTunda: alasanTunda,
    hitung: hitung,
    hariIni: hariIni,
    gambarPerkembangan: gambarPerkembangan
  };
})();
