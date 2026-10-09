/* Kartu info halaman partai: kepala pertandingan (Putih · hasil · Hitam) dan
   daftar keterangan (turnamen, tanggal, pembukaan, kontrol waktu, sumber).

   Dipisah dari papan.js 27 Sep 2026 (F4: berkas itu 988 baris). Murni
   menggambar dari objek partai /api/partai/{id}; tidak tahu apa-apa soal
   papan maupun navigasi langkah.

   Dipakai oleh web/partai.html lewat window.InfoPartai; harus dimuat
   SEBELUM papan.js, dan SESUDAH alat-catur.js (AlatCatur.kontrolPartai). */

window.InfoPartai = (function () {
  "use strict";

  function teks(isi) {
    return document.createTextNode(isi === null || isi === undefined ? "" : String(isi));
  }

  function buat(tag, kelas, isi) {
    var e = document.createElement(tag);
    if (kelas) e.className = kelas;
    if (isi !== undefined) e.appendChild(teks(isi));
    return e;
  }

  function namaLengkap(nama, gelar, elo, terverifikasi, pemainId) {
    var f = document.createDocumentFragment();
    if (gelar) f.appendChild(buat("span", "gelar", gelar));
    if (pemainId) {
      var a = document.createElement("a");
      a.href = "/pemain/" + pemainId;
      a.appendChild(teks(nama));
      f.appendChild(a);
    } else {
      f.appendChild(teks(nama));
    }
    if (terverifikasi) {
      var v = buat("span", "tercentang", " ✓");
      v.title = "Identitas dipastikan lewat FIDE ID";
      f.appendChild(v);
    }
    if (elo) f.appendChild(buat("span", "redup mono", " (" + elo + ")"));
    return f;
  }

  function gambar(elInfo, g) {
    elInfo.textContent = "";

    /* Kepala pertandingan: Putih · hasil · Hitam, seperti papan skor turnamen. */
    var laga = buat("div", "laga");
    function sisi(nama, gelar, elo, terverifikasi, id, warna) {
      var s = buat("div", "laga-sisi " + warna);
      var n = buat("div", "laga-nama");
      n.appendChild(namaLengkap(nama, gelar, null, terverifikasi, id));
      s.appendChild(n);
      s.appendChild(buat("div", "kecil redup", (warna === "putih" ? "Putih" : "Hitam") + (elo ? " · " + elo : "")));
      return s;
    }
    laga.appendChild(sisi(g.putih, g.putih_gelar, g.elo_putih, g.putih_terverifikasi, g.putih_id, "putih"));
    var hasil = buat("div", "laga-hasil hasil " +
      (g.hasil === "1-0" ? "hasil-1-0" :
       g.hasil === "0-1" ? "hasil-0-1" :
       g.hasil === "*" ? "hasil-jalan" : "hasil-remis"),
      g.hasil === "*" ? "…" : g.hasil === "1/2-1/2" ? "½–½" : g.hasil.replace("-", "–"));
    hasil.title = g.hasil === "*" ? "sedang berjalan" : "hasil " + g.hasil;
    laga.appendChild(hasil);
    laga.appendChild(sisi(g.hitam, g.hitam_gelar, g.elo_hitam, g.hitam_terverifikasi, g.hitam_id, "hitam"));
    elInfo.appendChild(laga);

    var dl = document.createElement("dl");
    function baris(label, nilai, tautan) {
      if (!nilai) return;
      dl.appendChild(buat("dt", null, label));
      var dd = document.createElement("dd");
      if (tautan) {
        var a = document.createElement("a");
        a.href = nilai; a.target = "_blank"; a.rel = "noopener noreferrer";
        a.appendChild(teks("buka sumber"));
        dd.appendChild(a);
      } else {
        dd.appendChild(teks(nilai));
      }
      dl.appendChild(dd);
    }
    baris("Turnamen", g.turnamen);
    baris("Tanggal", g.tanggal);
    baris("Babak", g.babak);
    baris("Pembukaan", [g.eco, g.pembukaan].filter(Boolean).join(" — "));
    baris("Kontrol waktu", AlatCatur.kontrolPartai(g));
    baris("Jumlah langkah", g.jumlah_langkah);
    baris("Sumber", g.sumber);
    baris("Tautan", g.sumber_url, true);
    elInfo.appendChild(dl);
  }

  return { gambar: gambar };
})();
