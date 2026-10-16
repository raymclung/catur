/* Halaman profil pemain: rating, hasil per warna, pembukaan, partai terbaru. */

(function () {
  "use strict";

  var el = {
    profil: document.getElementById("profil"),
    putih: document.getElementById("pembukaan-putih"),
    hitam: document.getElementById("pembukaan-hitam"),
    baris: document.getElementById("baris-partai"),
    tautan: document.getElementById("tautan-semua"),
    preferensi: document.getElementById("preferensi"),
    galat: document.getElementById("galat")
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
      if (r.status === 404) throw new Error("Pemain ini tidak ada di database.");
      if (!r.ok) throw new Error("Server menjawab " + r.status);
      return r.json();
    });
  }
  function idDariUrl() {
    var b = window.location.pathname.split("/").filter(Boolean);
    return b[b.length - 1];
  }
  function angka(v, sfx) { return v === null || v === undefined ? "—" : v + (sfx || ""); }

  function gambarProfil(p) {
    el.profil.textContent = "";
    var h = buat("h2");
    if (p.gelar) h.appendChild(buat("span", "gelar", p.gelar));
    h.appendChild(teks(p.nama));
    if (p.terverifikasi) {
      var v = buat("span", "tercentang", " ✓");
      v.title = "Identitas dipastikan lewat FIDE ID";
      h.appendChild(v);
    }
    /* Halaman Banding (16 Okt 2026) dengan pemain ini sebagai A; B dipilih di sana. */
    var banding = document.createElement("a");
    banding.href = "/static/banding.html?a=" + p.id;
    banding.className = "kecil";
    banding.style.marginLeft = "12px";
    banding.title = "Bandingkan pemain ini dengan pemain lain, berdampingan";
    banding.appendChild(teks("bandingkan dengan… →"));
    h.appendChild(banding);
    el.profil.appendChild(h);
    document.title = p.nama + " — McLung ChessBase";

    var dl = document.createElement("dl");
    function baris(label, nilai) {
      if (nilai === null || nilai === undefined || nilai === "") return;
      dl.appendChild(buat("dt", null, label));
      dl.appendChild(buat("dd", null, nilai));
    }
    baris("Elo di partai terakhir", p.elo_terakhir);
    baris("FIDE ID", p.fide_id ? p.fide_id : "—");
    baris("Negara", p.negara);
    baris("Partai di database", p.total.partai);
    baris("Sudah dianalisis", p.partai_dianalisis);
    baris("Sumber", p.per_sumber.map(function (s) { return s[0] + " (" + s[1] + ")"; }).join(", "));
    baris("Per tahun", p.per_tahun.map(function (s) { return s[0] + ": " + s[1]; }).join("  ·  "));
    el.profil.appendChild(dl);

    var kotak = buat("div", "akurasi-kotak");
    kotak.style.gridTemplateColumns = "repeat(3, 1fr)";
    [["Semua", p.total, null], ["Sebagai Putih", p.putih, "putih"], ["Sebagai Hitam", p.hitam, "hitam"]].forEach(function (x) {
      var k = buat("div");
      k.appendChild(buat("div", "label", x[0]));
      k.appendChild(buat("div", "angka-besar", angka(x[1].skor_persen, "%")));
      k.appendChild(barWdl(x[1]));
      k.appendChild(barisWdl(x[1], p.id, x[2]));
      kotak.appendChild(k);
    });
    el.profil.appendChild(kotak);

    /* Jembatan ke penjelajah pembukaan dengan saringan pemain + warna terisi. */
    var tb = buat("div", "tautan-baris");
    [["putih", "Repertoire sebagai Putih"], ["hitam", "Repertoire sebagai Hitam"]].forEach(function (x) {
      var a = document.createElement("a");
      a.href = "/jelajah?pemain_id=" + p.id + "&warna=" + x[0];
      a.title = "Langkah yang dimainkan " + p.nama + " sebagai " + (x[0] === "putih" ? "Putih" : "Hitam") + ", dari posisi mana pun";
      a.appendChild(teks(x[1]));
      tb.appendChild(a);
    });
    el.profil.appendChild(tb);
  }

  /* Bar menang / remis / kalah, dari sudut pandang pemain ini. */
  function barWdl(t) {
    var total = t.menang + t.remis + t.kalah;
    var bar = buat("div", "bar-wdl");
    if (!total) return bar;
    [["menang", t.menang], ["remis", t.remis], ["kalah", t.kalah]].forEach(function (x) {
      var s = buat("div", x[0]);
      s.style.width = (100 * x[1] / total) + "%";
      s.title = x[0] + " " + x[1];
      bar.appendChild(s);
    });
    return bar;
  }

  /* Preferensi pembukaan (RENCANA "GM Profile"): langkah pertama sebagai
     Putih, jawaban terhadap 1.e4 dan 1.d4 sebagai Hitam. Dihitung dari indeks
     posisi lewat /api/posisi dengan saringan pemain + warna, semua sumber. */
  var FEN_AWAL = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1";
  var FEN_E4 = "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq e3 0 1";
  var FEN_D4 = "rnbqkbnr/pppppppp/8/8/3P4/8/PPP1PPPP/RNBQKBNR b KQkq d3 0 1";

  function fenSetelah(fen, san) {
    var c = new Chess(fen);
    return c.move(san) ? c.fen() : null;
  }

  function kolomPreferensi(judul, fen, warna, pemainId) {
    var kolom = buat("div", "preferensi-kolom");
    kolom.appendChild(buat("h3", null, judul));
    var q = new URLSearchParams({ fen: fen, sumber: "semua", pemain_id: pemainId, warna: warna });
    ambil("/api/posisi?" + q.toString()).then(function (d) {
      if (!d.total) { kolom.appendChild(buat("div", "kecil redup", "tidak ada partai")); return; }
      d.langkah.slice(0, 6).forEach(function (l) {
        var b = buat("div", "pref-baris");
        var a = document.createElement("a");
        var anak = fenSetelah(fen, l.san);
        a.href = "/jelajah?fen=" + encodeURIComponent(anak || fen) + "&pemain_id=" + pemainId + "&warna=" + warna;
        a.title = "Lanjutkan di penjelajah";
        a.appendChild(teks(l.san));
        b.appendChild(a);
        var isi = buat("div", "isi");
        isi.style.width = Math.max(2, 100 * l.n / d.total) + "%";
        isi.title = Math.round(100 * l.n / d.total) + "%";
        var wadah = buat("div");
        wadah.appendChild(isi);
        b.appendChild(wadah);
        b.appendChild(buat("div", "n", Math.round(100 * l.n / d.total) + "%"));
        var skor = l.skor_putih === null ? null : (warna === "putih" ? l.skor_putih : Math.round((100 - l.skor_putih) * 10) / 10);
        var sk = buat("div", "skor redup", skor === null ? "—" : skor + "%");
        sk.title = "Skor " + (warna === "putih" ? "Putih" : "Hitam") + " dari " + l.n + " partai";
        b.appendChild(sk);
        kolom.appendChild(b);
      });
    }).catch(function (e) {
      kolom.appendChild(buat("div", "kecil redup", "Gagal dimuat: " + e.message));
    });
    return kolom;
  }

  function muatPreferensi(p) {
    el.preferensi.textContent = "";
    el.preferensi.appendChild(buat("h2", "judul-kartu", "Preferensi pembukaan"));
    var g = buat("div", "preferensi-grid");
    g.appendChild(kolomPreferensi("Sebagai Putih · langkah pertama", FEN_AWAL, "putih", p.id));
    g.appendChild(kolomPreferensi("Sebagai Hitam · lawan 1.e4", FEN_E4, "hitam", p.id));
    g.appendChild(kolomPreferensi("Sebagai Hitam · lawan 1.d4", FEN_D4, "hitam", p.id));
    el.preferensi.appendChild(g);
    el.preferensi.hidden = false;
  }

  /* Kartu yang gagal dimuat memberi pesannya sendiri; kartu lain tetap tampil. */
  function pesanKartu(wadah, judul, pesan) {
    wadah.textContent = "";
    if (judul) wadah.appendChild(buat("h2", "judul-kartu", judul));
    wadah.appendChild(buat("div", "pesan kecil", pesan));
  }

  /* Tautan ke daftar partai dengan saringan hasil terbawa. "Menang" untuk
     pemain ini berarti 1-0 kalau ia Putih dan 0-1 kalau ia Hitam; tanpa warna
     tidak ada satu nilai `hasil` yang mewakili menang/kalah, jadi di kotak
     "Semua" hanya remis dan "menang-kalah saja" yang jadi tautan. */
  function tautanDaftar(label, pemainId, warna, hasil, judul) {
    var a = document.createElement("a");
    var q = new URLSearchParams();
    q.set("pemain_id", pemainId);
    if (warna) q.set("warna", warna);
    if (hasil) q.set("hasil", hasil);
    a.href = "/?" + q.toString();
    a.title = judul;
    a.appendChild(teks(label));
    return a;
  }

  function barisWdl(t, pemainId, warna) {
    var d = buat("div", "kecil redup");
    var menang = warna === "hitam" ? "0-1" : warna === "putih" ? "1-0" : null;
    var kalah  = warna === "hitam" ? "1-0" : warna === "putih" ? "0-1" : null;
    function bagian(label, hasil, judul) {
      if (hasil) d.appendChild(tautanDaftar(label, pemainId, warna, hasil, judul));
      else d.appendChild(teks(label));
    }
    bagian("+" + t.menang, menang, "partai yang dimenangkan");
    d.appendChild(teks(" "));
    bagian("=" + t.remis, "1/2-1/2", "partai remis");
    d.appendChild(teks(" "));
    bagian("−" + t.kalah, kalah, "partai yang kalah");
    if (t.belum_selesai) d.appendChild(teks("  (" + t.belum_selesai + " berjalan)"));
    d.appendChild(teks("  · "));
    d.appendChild(tautanDaftar("tanpa remis", pemainId, warna, "menang-kalah", "hanya partai menang-kalah (remis disembunyikan, tidak dihapus)"));
    return d;
  }

  function gambarPembukaan(wadah, judul, daftar, pemainId, warna) {
    wadah.textContent = "";
    wadah.appendChild(buat("h2", "judul-kartu", judul));
    if (daftar.length === 0) {
      wadah.appendChild(buat("div", "pesan kecil", "Belum ada partai dengan kode ECO"));
      return;
    }
    var t = document.createElement("table");
    var thead = document.createElement("thead");
    var trh = document.createElement("tr");
    ["Keluarga", "Partai", "%", "Skor", "ECO terbanyak"].forEach(function (k) { trh.appendChild(buat("th", null, k)); });
    thead.appendChild(trh);
    t.appendChild(thead);
    var tb = document.createElement("tbody");
    daftar.forEach(function (d) {
      var tr = document.createElement("tr");
      var td = document.createElement("td");
      var a = document.createElement("a");
      /* eco_terbanyak diurutkan menurut frekuensi, bukan kode; rentang harus
         kode terkecil..terbesar, kalau tidak BETWEEN di server kosong. */
      var kode = d.eco_terbanyak.map(function (e) { return e[0]; }).sort();
      a.href = "/?pemain_id=" + pemainId + "&warna=" + warna +
        (kode.length ? "&eco_dari=" + kode[0] + "&eco_sampai=" + kode[kode.length - 1] : "");
      a.appendChild(teks(d.keluarga));
      td.appendChild(a);
      if (d.contoh_nama.length) td.appendChild(buat("div", "kecil redup", d.contoh_nama[0][0]));
      tr.appendChild(td);
      tr.appendChild(buat("td", "mono", d.partai));
      tr.appendChild(buat("td", "mono", d.persen + "%"));
      tr.appendChild(buat("td", "mono", angka(d.skor_persen, "%")));
      tr.appendChild(buat("td", "mono kecil", d.eco_terbanyak.map(function (e) { return e[0] + "×" + e[1]; }).join("  ")));
      tr.style.cursor = "default";
      tb.appendChild(tr);
    });
    t.appendChild(tb);
    var g = buat("div", "gulir");
    g.appendChild(t);
    wadah.appendChild(g);
  }

  function gambarPartai(d, pemainId) {
    el.baris.textContent = "";
    d.partai.forEach(function (g) {
      var tr = document.createElement("tr");
      tr.appendChild(buat("td", "redup kecil mono", g.tanggal || "-"));
      tr.appendChild(buat("td", null, (g.putih_gelar ? g.putih_gelar + " " : "") + g.putih));
      tr.appendChild(buat("td", null, (g.hitam_gelar ? g.hitam_gelar + " " : "") + g.hitam));
      tr.appendChild(buat("td", "hasil", g.hasil));
      tr.appendChild(buat("td", "mono kecil", g.eco || "-"));
      tr.appendChild(buat("td", "kecil redup", g.turnamen || "-"));
      tr.appendChild(buat("td", "mono kecil", g.dianalisis ? (g.akurasi_putih || 0).toFixed(0) + "/" + (g.akurasi_hitam || 0).toFixed(0) : "—"));
      tr.addEventListener("click", function () { window.location.href = "/partai/" + g.id; });
      el.baris.appendChild(tr);
    });
    el.tautan.textContent = "";
    el.tautan.appendChild(tautanDaftar("Lihat semua " + d.total + " partai →", pemainId, null, null, "daftar partai pemain ini"));
  }

  /* Tiga permintaan berjalan sendiri-sendiri: profil yang gagal = halaman
     gagal; pembukaan atau daftar partai yang gagal hanya mengosongkan
     kartunya sendiri dengan pesan, profil tetap tampil. */
  var id = idDariUrl();
  ambil("/api/pemain/" + id).then(function (p) {
    gambarProfil(p);
    muatPreferensi(p);

    ambil("/api/pemain/" + id + "/pembukaan").then(function (r) {
      gambarPembukaan(el.putih, "Pembukaan sebagai Putih", r.putih, id, "putih");
      gambarPembukaan(el.hitam, "Pembukaan sebagai Hitam", r.hitam, id, "hitam");
    }).catch(function (e) {
      pesanKartu(el.putih, "Pembukaan sebagai Putih", "Gagal dimuat: " + e.message);
      pesanKartu(el.hitam, "Pembukaan sebagai Hitam", "Gagal dimuat: " + e.message);
    });

    ambil("/api/partai?pemain_id=" + id + "&per_halaman=15").then(function (d) {
      gambarPartai(d, id);
    }).catch(function (e) {
      el.baris.textContent = "";
      var tr = document.createElement("tr");
      var td = buat("td", "pesan kecil", "Gagal dimuat: " + e.message);
      td.colSpan = 7;
      tr.appendChild(td);
      el.baris.appendChild(tr);
    });
  }).catch(function (e) {
    el.profil.textContent = "";
    galat(e.message);
  });
})();
