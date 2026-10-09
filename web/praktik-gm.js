/* Dua kartu halaman partai yang membandingkan partai dengan praktik GM:

   - "Praktik GM": di langkah mana partai ini keluar dari database GM
     (RENCANA Phase 7: bandingkan dengan GM), plus apa yang GM lain mainkan.
   - Penjelajah ringkas: langkah-langkah GM dari posisi papan saat ini.

   Dipisah dari papan.js 27 Sep 2026 (F4: berkas itu 988 baris). Modul ini
   hanya memanggil /api/posisi dan menggambar; yang ia butuhkan dari halaman
   disuntik: `lompat(i)` (pindah ke titik i), `mainkan(i, uci)` (pindah ke
   titik i lalu mainkan uci sebagai variasi), dan `dimainkan(l)` (apakah
   langkah GM l adalah langkah yang dimainkan di partai).

   Dipakai oleh web/partai.html lewat window.PraktikGm; harus dimuat SEBELUM
   papan.js, dan SESUDAH vendor/chess.js. */

window.PraktikGm = (function () {
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

  /* ---------- keluar dari praktik GM ----------
     Berjalan ply demi ply lewat /api/posisi?sumber=gm sampai posisi tidak ada
     lagi di indeks GM (paling jauh 40 ply). Partai yang sendiri bersumber GM
     (TWIC/broadcast) selalu menghitung dirinya, jadi ambangnya 1, bukan 0. */

  var elT = document.getElementById("teori-gm");
  var MAKS_PLY_TEORI = 40;

  /* langkah = history verbose chess.js, posisi = FEN tiap titik,
     elDaftar = daftar langkah (untuk menandai langkah yang keluar teori). */
  function cariKeluarTeori(g, langkah, posisi, elDaftar, aksi) {
    if (!elT || !langkah.length) return;
    var sendiri = (g.sumber === "twic" || g.sumber === "lichess-broadcast") ? 1 : 0;
    var maks = Math.min(MAKS_PLY_TEORI, langkah.length);
    var i = 0;
    var terakhir = null;   // {i, d}: posisi terakhir yang masih ada di praktik GM

    function selesai() { gambarTeori(terakhir, i, maks, sendiri, langkah, posisi, elDaftar, aksi); }
    function periksa() {
      if (i > maks) { selesai(); return; }
      fetch("/api/posisi?fen=" + encodeURIComponent(posisi[i]) + "&sumber=gm")
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (d) {
          if (!d || d.total - sendiri <= 0) { selesai(); return; }
          terakhir = { i: i, d: d };
          i++;
          periksa();
        })
        .catch(selesai);
    }
    periksa();
  }

  function gambarTeori(terakhir, iKeluar, maks, sendiri, langkah, posisi, elDaftar, aksi) {
    elT.textContent = "";
    var h = buat("h2", null, "Praktik GM");
    elT.appendChild(h);

    if (!terakhir) {
      elT.appendChild(buat("div", "kecil redup", "Posisi awal tidak ada di indeks GM (indeks belum dibangun?)."));
      elT.hidden = false;
      return;
    }
    if (iKeluar > maks) {
      elT.appendChild(buat("div", "kecil redup", maks + " langkah pertama semuanya ada dalam praktik GM."));
      elT.hidden = false;
      return;
    }

    /* langkah[terakhir.i] = langkah yang pertama kali keluar dari praktik GM */
    var l = langkah[terakhir.i];
    var nomor = Math.floor(terakhir.i / 2) + 1;
    var label = (terakhir.i % 2 === 0 ? nomor + "." : nomor + "…") + l.san;
    var baris = buat("div", null);
    baris.appendChild(buat("span", null, sendiri ? "Langkah baru di luar database GM: " : "Keluar dari praktik GM di "));
    var tautan = buat("span", "langkah", label);
    tautan.title = "Lompat ke langkah ini";
    tautan.addEventListener("click", function () { aksi.lompat(terakhir.i + 1); });
    baris.appendChild(tautan);
    baris.appendChild(buat("span", "redup kecil", "  · posisi sebelumnya ada di " + (terakhir.d.total - sendiri).toLocaleString("id-ID") + " partai GM lain"));
    elT.appendChild(baris);

    /* Partai bersumber GM ikut terhitung di indeks: kurangi langkahnya sendiri
       supaya "GM memainkan" hanya berisi partai lain. */
    var alternatif = terakhir.d.langkah.map(function (m) {
      return sendiri && m.san === l.san ? { san: m.san, uci: m.uci, n: m.n - 1, skor_putih: m.skor_putih } : m;
    }).filter(function (m) { return m.n > 0; });
    var alt = buat("div", "teori-langkah");
    alt.appendChild(buat("span", "redup kecil", alternatif.length ? "GM lain memainkan:" : "Tidak ada partai GM lain dari posisi ini."));
    alternatif.slice(0, 4).forEach(function (m) {
      var s = buat("span", "langkah", m.san);
      s.title = "Mainkan sebagai variasi";
      s.addEventListener("click", function () { aksi.mainkan(terakhir.i, m.uci); });
      alt.appendChild(s);
      alt.appendChild(buat("span", "n", m.n.toLocaleString("id-ID") + "× · " + (m.skor_putih === null ? "—" : m.skor_putih + "%")));
    });
    var j = document.createElement("a");
    j.href = "/jelajah?fen=" + encodeURIComponent(posisi[terakhir.i]);
    j.className = "kecil";
    j.appendChild(teks("jelajah →"));
    alt.appendChild(j);
    elT.appendChild(alt);

    var tanda = elDaftar.querySelector('.langkah[data-indeks="' + (terakhir.i + 1) + '"]');
    if (tanda) {
      tanda.classList.add("keluar-teori");
      tanda.title = (tanda.title ? tanda.title + " · " : "") + "keluar dari praktik GM";
    }
    elT.hidden = false;
  }

  /* ---------- penjelajah pembukaan (mengikuti posisi papan) ----------
     Ditunda sedikit supaya menahan → tidak memicu satu permintaan per ply. */

  var elJ = {
    tabel: document.getElementById("penjelajah-tabel"),
    tautan: document.getElementById("tautan-jelajah"),
    tautanDaftar: document.getElementById("tautan-daftar-posisi")
  };

  /* Bar + = − seperti di /jelajah, versi ringkas. */
  function selBarHasil(l) {
    var td = document.createElement("td");
    var selesai = l.menang_putih + l.remis + l.menang_hitam;
    if (!selesai) return td;
    var w = document.createElement("div");
    w.style.cssText = "display:flex;height:12px;width:110px;border-radius:3px;overflow:hidden;border:1px solid #ccc";
    [[l.menang_putih, "#f4f4f2"], [l.remis, "#a5a5a0"], [l.menang_hitam, "#3a3a3a"]].forEach(function (x) {
      var s = document.createElement("div");
      s.style.cssText = "width:" + (100 * x[0] / selesai) + "%;background:" + x[1];
      s.title = x[0];
      w.appendChild(s);
    });
    td.appendChild(w);
    td.appendChild(buat("span", "redup kecil", " " + l.menang_putih + "/" + l.remis + "/" + l.menang_hitam));
    return td;
  }

  /* Tautan ke daftar partai yang mencapai posisi SETELAH langkah san. */
  function selTautanDaftar(fen, san) {
    var td = buat("td", "kecil");
    td.style.whiteSpace = "nowrap";
    var c = new Chess(fen);
    if (!c.move(san)) return td;
    var a = document.createElement("a");
    a.href = "/?fen=" + encodeURIComponent(c.fen());
    a.title = "Daftar partai yang mencapai posisi setelah " + san;
    a.appendChild(teks("daftar →"));
    td.appendChild(a);
    return td;
  }
  var timerJelajah = null;
  var permintaanJelajah = 0;

  function perbaruiPenjelajah(fen, dimainkan) {
    if (!elJ.tabel) return;
    elJ.tautan.href = "/jelajah?fen=" + encodeURIComponent(fen);
    if (elJ.tautanDaftar) elJ.tautanDaftar.href = "/?fen=" + encodeURIComponent(fen);
    clearTimeout(timerJelajah);
    timerJelajah = setTimeout(function () {
      var ke = ++permintaanJelajah;
      fetch("/api/posisi?fen=" + encodeURIComponent(fen) + "&sumber=gm")
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (d) {
          if (ke !== permintaanJelajah) return;
          elJ.tabel.textContent = "";
          if (!d || d.langkah.length === 0) {
            var tr0 = document.createElement("tr");
            var td0 = buat("td", "pesan kecil", d ? "Tidak ada partai GM dari posisi ini." : "Indeks posisi belum dibangun (python indeks_posisi.py).");
            td0.colSpan = 6;
            tr0.appendChild(td0);
            elJ.tabel.appendChild(tr0);
            return;
          }
          d.langkah.slice(0, 8).forEach(function (l) {
            var tr = document.createElement("tr");
            tr.style.cursor = "default";
            var td = buat("td", "mono", l.san);
            if (dimainkan(l)) td.appendChild(buat("span", "redup kecil", "  ← dimainkan"));
            tr.appendChild(td);
            tr.appendChild(buat("td", "mono", l.n + "  (" + Math.round(100 * l.n / d.total) + "%)"));
            tr.appendChild(buat("td", "mono", l.skor_putih === null ? "—" : l.skor_putih + "%"));
            tr.appendChild(selBarHasil(l));
            tr.appendChild(buat("td", "mono", l.elo_rata || "—"));
            tr.appendChild(selTautanDaftar(fen, l.san));
            elJ.tabel.appendChild(tr);
          });
        })
        .catch(function () { /* kartu penjelajah opsional */ });
    }, 250);
  }

  return { cariKeluarTeori: cariKeluarTeori, perbaruiPenjelajah: perbaruiPenjelajah };
})();
