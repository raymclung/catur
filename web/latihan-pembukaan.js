/* Mode latihan PEMBUKAAN (A37): posisi repertoire tempat langkah Ray jarang
   dimainkan GM, dari GET /api/latihan/pembukaan. Jawaban benar = langkah GM
   mana pun dengan porsi >= 5 %, bukan langkah terbaik engine — jadi kartu
   jawabannya menampilkan statistik GM, bukan garis engine.

   Dipecah dari web/latihan.js 25 Sep 2026. Modul ini TIDAK menyentuh papan
   maupun keadaan sesi: ia hanya mengubah data server jadi daftar soal, dan
   mengisi dua elemen (kartu soal, kartu jawaban) yang diberikan pemanggil. */

window.LatihanPembukaan = (function () {
  "use strict";

  function teks(isi) { return document.createTextNode(isi === null || isi === undefined ? "" : String(isi)); }
  function buat(tag, kelas, isi) {
    var e = document.createElement(tag);
    if (kelas) e.className = kelas;
    if (isi !== undefined) e.appendChild(teks(isi));
    return e;
  }
  function persen(x) { return x === null || x === undefined ? "–" : String(x).replace(".", ",") + "%"; }

  /* Soal dari server, sudah dibentuk seperti soal latihan biasa supaya
     halaman memperlakukan keduanya sama (kunci, urutan acak, penilaian). */
  function muat() {
    return fetch("/api/latihan/pembukaan").then(function (r) {
      if (!r.ok) throw new Error("Server menjawab " + r.status + " untuk /api/latihan/pembukaan");
      return r.json();
    }).then(function (d) {
      var soal = (d.soal || []).map(function (s) {
        var biasa = s.langkah_ray[0] || {};
        return {
          jenis: "pembukaan", fen: s.fen, warna: s.warna, pembukaan: s.pembukaan,
          dicapai: s.dicapai, menyimpang: s.menyimpang, langkah_ray: s.langkah_ray,
          partai_gm: s.partai_gm, diterima: s.diterima, gm: s.gm,
          jalur: s.contoh.jalur, partai_id: s.contoh.partai_id,
          san: biasa.san, terbaik_uci: s.gm[0].uci, terbaik_san: s.gm[0].san
        };
      });
      return { soal: soal, ringkasan: d.ringkasan || {} };
    });
  }

  /* "1.c4 e5 2.Nc3 Nf6 3.?" dari jalur SAN menuju posisi soal. */
  function tulisJalur(jalur) {
    var keluar = [];
    jalur.forEach(function (san, i) {
      keluar.push((i % 2 === 0 ? (i / 2 + 1) + "." : "") + san);
    });
    keluar.push(jalur.length % 2 === 0 ? (jalur.length / 2 + 1) + ".?" : Math.ceil(jalur.length / 2) + "...?");
    return keluar.join(" ");
  }

  function gambarSoal(soal, elSoal) {
    elSoal.textContent = "";
    elSoal.appendChild(buat("div", null, soal.pembukaan + " · " + tulisJalur(soal.jalur)));
    /* Menyebut langkah kebiasaan Ray bukan membocorkan jawaban — justru itu
       yang mau diganti. */
    elSoal.appendChild(buat("div", "kecil redup", "Posisi ini kamu capai " + soal.dicapai + " kali. " +
      soal.menyimpang + " kali kamu memainkan langkah yang jarang dimainkan GM, paling sering " +
      soal.san + "."));
  }

  /* Isi kartu jawaban; kembalikan true kalau langkahnya diterima. */
  function gambarJawaban(soal, elJawab, uci, menyerah) {
    var cocok = false;
    if (!menyerah && uci) {
      soal.diterima.forEach(function (d) { if (uci === d || uci.slice(0, 4) === d.slice(0, 4)) cocok = true; });
    }
    var kebiasaan = !cocok && uci && soal.langkah_ray.some(function (r) {
      return r.uci.slice(0, 4) === uci.slice(0, 4);
    });
    elJawab.appendChild(buat("div", cocok ? "latihan-benar" : "latihan-salah",
      cocok ? "Benar" : (menyerah ? "Jawabannya" : (kebiasaan ? "Itu kebiasaanmu" : "Bukan itu"))));
    elJawab.appendChild(buat("div", "kecil redup", "Dari " + soal.partai_gm.toLocaleString("id-ID") +
      " partai GM sejak 2000 · diterima: langkah dengan porsi ≥ 5% · skor dari sudut yang jalan"));

    var tabel = document.createElement("table");
    var kepala = document.createElement("tr");
    ["Langkah GM", "Porsi", "Skor", "Partai"].forEach(function (j) { kepala.appendChild(buat("th", null, j)); });
    tabel.appendChild(kepala);
    soal.gm.forEach(function (g) {
      var tr = document.createElement("tr");
      var diterima = soal.diterima.indexOf(g.uci) >= 0;
      var dimainkan = uci && uci.slice(0, 4) === g.uci.slice(0, 4);
      tr.appendChild(buat("td", diterima ? null : "redup", g.san + (dimainkan ? "  ← kamu" : "")));
      tr.appendChild(buat("td", diterima ? null : "redup", persen(g.porsi)));
      tr.appendChild(buat("td", diterima ? null : "redup", persen(g.skor)));
      tr.appendChild(buat("td", diterima ? null : "redup", g.n.toLocaleString("id-ID")));
      tabel.appendChild(tr);
    });
    elJawab.appendChild(tabel);

    elJawab.appendChild(buat("div", "kecil", "Di partaimu: " + soal.langkah_ray.slice(0, 4).map(function (r) {
      return r.san + " " + r.n + "×" + (r.skor !== null && r.skor !== undefined ? " (skor " + persen(r.skor) + ")" : "");
    }).join(" · ")));
    elJawab.appendChild(buat("div", "kecil redup", "Jarang dimainkan GM belum tentu salah. " +
      "Nilai sendiri dengan engine di bawah; kalau memang pilihanmu, tandai supaya tidak ditanyakan lagi."));
    return cocok;
  }

  return { muat: muat, tulisJalur: tulisJalur, gambarSoal: gambarSoal, gambarJawaban: gambarJawaban };
})();
