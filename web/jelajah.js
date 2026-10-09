/* Penjelajah pembukaan: papan yang bisa dimainkan + langkah GM dari database. */

(function () {
  "use strict";

  var permainan = new Chess();
  var AWAL_FEN = permainan.fen();
  var papan = null;
  var riwayat = [];   // SAN, untuk baris langkah & undo
  var langkahTampil = [];   // baris tabel langkah terakhir, untuk keyboard (→, 1–9)
  var permintaanKe = 0;

  var el = {
    galat: document.getElementById("galat"),
    sumber: document.getElementById("sumber"),
    minElo: document.getElementById("min-elo"),
    total: document.getElementById("total"),
    tabel: document.getElementById("baris-langkah-tabel"),
    contoh: document.getElementById("baris-contoh"),
    barisLangkah: document.getElementById("baris-langkah"),
    fen: document.getElementById("fen"),
    tautanPosisi: document.getElementById("tautan-posisi"),
    cariPemain: document.getElementById("cari-pemain"),
    pemain: document.getElementById("pemain"),
    warna: document.getElementById("warna"),
    thSkor: document.getElementById("th-skor")
  };

  /* FastAPI mengabaikan parameter yang tidak dikenalnya, jadi saringan
     pemain yang belum dipasang backend akan diam-diam mengembalikan statistik
     SEMUA pemain. Cek /openapi.json dulu (SESI.md bagian 3 #5). */
  var dukungPemain = null;

  function cekDukungPemain() {
    if (dukungPemain !== null) return Promise.resolve(dukungPemain);
    return fetch("/openapi.json").then(function (r) { return r.json(); }).then(function (d) {
      var param = (((d.paths || {})["/api/posisi"] || {}).get || {}).parameters || [];
      dukungPemain = param.some(function (x) { return x.name === "pemain_id"; });
      return dukungPemain;
    }).catch(function () { dukungPemain = false; return false; });
  }

  /* Pilihan pemain di dropdown; nama diambil dari /api/pemain. */
  function tambahOpsiPemain(p, pilih) {
    var ada = el.pemain.querySelector('option[value="' + p.id + '"]');
    if (!ada) {
      ada = document.createElement("option");
      ada.value = p.id;
      ada.textContent = (p.gelar ? p.gelar + " " : "") + p.nama + (p.jumlah_partai ? " (" + p.jumlah_partai + ")" : "");
      el.pemain.appendChild(ada);
    }
    if (pilih) el.pemain.value = p.id;
  }

  function cariPemain() {
    var q = el.cariPemain.value.trim();
    if (!q) return;
    fetch("/api/pemain?cari=" + encodeURIComponent(q) + "&limit=20")
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (d.pemain.length === 0) { galat("Tidak ada pemain bernama \"" + q + "\"."); return; }
        d.pemain.forEach(function (p, i) { tambahOpsiPemain(p, i === 0); });
        muatLangkah();
      }).catch(function (e) { galat("Gagal mencari pemain: " + e.message); });
  }

  function tulisUrl() {
    var u = new URL(window.location.href);
    u.searchParams.set("fen", permainan.fen());
    ["pemain_id", "warna"].forEach(function (k) { u.searchParams.delete(k); });
    if (el.pemain.value) {
      u.searchParams.set("pemain_id", el.pemain.value);
      if (el.warna.value) u.searchParams.set("warna", el.warna.value);
    }
    window.history.replaceState(null, "", u.toString());
  }

  /* Tautan ke halaman daftar dengan saringan posisi (parameter fen). */
  function urlDaftar(fen) { return "/?fen=" + encodeURIComponent(fen); }

  /* FEN setelah satu langkah SAN dari posisi sekarang, tanpa mengubah papan. */
  function fenSetelah(san) {
    var c = new Chess(permainan.fen());
    return c.move(san) ? c.fen() : null;
  }

  function teks(isi) { return document.createTextNode(isi === null || isi === undefined ? "" : String(isi)); }
  function buat(tag, kelas, isi) {
    var e = document.createElement(tag);
    if (kelas) e.className = kelas;
    if (isi !== undefined) e.appendChild(teks(isi));
    return e;
  }
  function galat(p) { el.galat.textContent = p; el.galat.hidden = false; }

  /* ---------- papan ---------- */

  function bolehGeser(source, piece) {
    if (permainan.game_over()) return false;
    var giliran = permainan.turn();
    return (giliran === "w" && piece[0] === "w") || (giliran === "b" && piece[0] === "b");
  }

  function saatJatuh(source, target) {
    if (PilihPromosi.tunda(permainan, source, target, function (p) {
      var mp = permainan.move({ from: source, to: target, promotion: p });
      if (!mp) return;
      riwayat.push(mp.san);
      papan.position(permainan.fen());
      setelahBerubah();
    })) return "snapback";
    var m = permainan.move({ from: source, to: target, promotion: "q" });
    if (m === null) return "snapback";
    riwayat.push(m.san);
    setelahBerubah();
  }

  function saatSelesaiGeser() { papan.position(permainan.fen()); }

  function mainkanSan(san) {
    var m = permainan.move(san);
    if (m === null) return;
    riwayat.push(m.san);
    papan.position(permainan.fen());
    setelahBerubah();
  }

  function undo() {
    if (permainan.undo()) {
      riwayat.pop();
      papan.position(permainan.fen());
      setelahBerubah();
    }
  }

  function reset(fen) {
    if (fen) {
      if (!permainan.load(fen)) { galat("FEN tidak sah: " + fen); permainan.reset(); }
    } else {
      permainan.reset();
    }
    riwayat = [];
    papan.position(permainan.fen());
    setelahBerubah();
  }

  function setelahBerubah() {
    var t = [];
    for (var i = 0; i < riwayat.length; i++) {
      if (i % 2 === 0) t.push((i / 2 + 1) + ".");
      t.push(riwayat[i]);
    }
    if (t.length) {
      el.barisLangkah.textContent = t.join(" ");
    } else if (permainan.fen() === AWAL_FEN) {
      el.barisLangkah.textContent = "Posisi awal";
    } else {
      /* Dibuka lewat ?fen=: urutan langkah menuju posisi ini tidak diketahui. */
      el.barisLangkah.textContent = "Posisi dari FEN · " + (permainan.turn() === "w" ? "Putih" : "Hitam") + " jalan";
    }
    el.fen.textContent = permainan.fen();
    el.tautanPosisi.href = urlDaftar(permainan.fen());
    tulisUrl();
    muatLangkah();
  }

  /* ---------- data ---------- */

  function tabelPesan(pesan) {
    el.tabel.textContent = "";
    el.contoh.textContent = "";
    el.total.textContent = "";
    var tr0 = document.createElement("tr");
    var td0 = buat("td", "pesan", pesan);
    td0.colSpan = 7;
    tr0.appendChild(td0);
    el.tabel.appendChild(tr0);
  }

  function muatLangkah() {
    var ke = ++permintaanKe;
    var p = new URLSearchParams();
    p.set("fen", permainan.fen());
    p.set("sumber", el.sumber.value);
    if (el.minElo.value) p.set("min_elo", el.minElo.value);
    el.galat.hidden = true;

    if (el.pemain.value) {
      p.set("pemain_id", el.pemain.value);
      if (el.warna.value) p.set("warna", el.warna.value);
      cekDukungPemain().then(function (ok) {
        if (ke !== permintaanKe) return;
        if (ok) return ambilLangkah(p, ke);
        tabelPesan("Saringan pemain belum didukung server; tabel tidak ditampilkan supaya tidak menyesatkan (menunggu backend, SESI.md bagian 3 #5).");
      });
      return;
    }
    ambilLangkah(p, ke);
  }

  function ambilLangkah(p, ke) {
    fetch("/api/posisi?" + p.toString()).then(function (r) {
      if (!r.ok) throw new Error("Server menjawab " + r.status);
      return r.json();
    }).then(function (d) {
      if (ke !== permintaanKe) return;   // sudah ada permintaan yang lebih baru
      gambarLangkah(d);
      gambarContoh(d.partai);
    }).catch(function (e) { galat("Gagal memuat langkah: " + e.message); });
  }

  function gambarLangkah(d) {
    el.tabel.textContent = "";
    langkahTampil = d.langkah;
    el.total.textContent = d.total ? "· " + d.total.toLocaleString("id-ID") + " partai" : "";
    /* Skor dari server selalu sudut pandang Putih; di sini dibalik untuk
       Hitam supaya angka yang dibaca = peluang pihak yang sedang memilih. */
    var hitamJalan = permainan.turn() === "b";
    el.thSkor.textContent = hitamJalan ? "Skor Hitam" : "Skor Putih";
    function skor(l) {
      if (l.skor_putih === null) return "—";
      return (hitamJalan ? Math.round((100 - l.skor_putih) * 10) / 10 : l.skor_putih) + "%";
    }
    if (d.langkah.length === 0) {
      var tr0 = document.createElement("tr");
      var td0 = buat("td", "pesan", "Tidak ada partai untuk saringan ini");
      td0.colSpan = 7;
      tr0.appendChild(td0);
      el.tabel.appendChild(tr0);
      return;
    }
    d.langkah.forEach(function (l) {
      var tr = document.createElement("tr");
      tr.title = "Mainkan " + l.san;
      tr.addEventListener("click", function () { mainkanSan(l.san); });
      tr.appendChild(buat("td", "mono", l.san));
      var tdN = buat("td", "mono", l.n.toLocaleString("id-ID"));
      tdN.appendChild(buat("span", "redup kecil", "  " + Math.round(100 * l.n / d.total) + "%"));
      tr.appendChild(tdN);
      tr.appendChild(buat("td", "mono", skor(l)));
      var bar = document.createElement("td");
      var selesai = l.menang_putih + l.remis + l.menang_hitam;
      if (selesai) {
        var w = document.createElement("div");
        w.style.cssText = "display:flex;height:12px;width:96px;border-radius:3px;overflow:hidden;border:1px solid #ccc";
        [[l.menang_putih, "#f4f4f2"], [l.remis, "#a5a5a0"], [l.menang_hitam, "#3a3a3a"]].forEach(function (x) {
          var s = document.createElement("div");
          s.style.cssText = "width:" + (100 * x[0] / selesai) + "%;background:" + x[1];
          s.title = x[0];
          w.appendChild(s);
        });
        bar.appendChild(w);
        bar.appendChild(buat("span", "redup kecil", " " + l.menang_putih + "/" + l.remis + "/" + l.menang_hitam));
      }
      tr.appendChild(bar);
      tr.appendChild(buat("td", "mono", l.elo_rata || "—"));
      tr.appendChild(buat("td", "mono kecil redup", l.terakhir || "—"));
      var tdD = buat("td", "kecil");
      tdD.style.whiteSpace = "nowrap";
      var fenAnak = fenSetelah(l.san);
      if (fenAnak) {
        var a = document.createElement("a");
        a.href = urlDaftar(fenAnak);
        a.title = "Daftar partai yang mencapai posisi setelah " + l.san;
        a.appendChild(teks("daftar →"));
        a.addEventListener("click", function (ev) { ev.stopPropagation(); });
        tdD.appendChild(a);
      }
      tr.appendChild(tdD);
      el.tabel.appendChild(tr);
    });
  }

  function gambarContoh(daftar) {
    el.contoh.textContent = "";
    /* Partai contoh dibuka dengan saringan yang sama (posisi, sumber, pemain)
       supaya halaman partai bisa menawarkan sebelumnya/berikutnya. */
    var q = new URLSearchParams();
    q.set("fen", permainan.fen());
    if (el.sumber.value && el.sumber.value !== "semua") q.set("sumber", el.sumber.value);
    if (el.pemain.value) {
      q.set("pemain_id", el.pemain.value);
      if (el.warna.value) q.set("warna", el.warna.value);
    }
    q.set("urut", "elo");
    q.set("halaman", "1");
    q.set("per_halaman", "25");
    var daftarParam = encodeURIComponent(q.toString());

    daftar.forEach(function (g) {
      var tr = document.createElement("tr");
      tr.addEventListener("click", function () { window.open("/partai/" + g.id + "?daftar=" + daftarParam, "_blank"); });
      tr.appendChild(buat("td", "redup kecil mono", g.tanggal || "-"));
      tr.appendChild(buat("td", null, (g.putih_gelar ? g.putih_gelar + " " : "") + g.putih + (g.elo_putih ? " (" + g.elo_putih + ")" : "")));
      tr.appendChild(buat("td", null, (g.hitam_gelar ? g.hitam_gelar + " " : "") + g.hitam + (g.elo_hitam ? " (" + g.elo_hitam + ")" : "")));
      tr.appendChild(buat("td", "hasil", g.hasil));
      tr.appendChild(buat("td", "mono", g.langkah));
      tr.appendChild(buat("td", "kecil redup", g.turnamen || "-"));
      el.contoh.appendChild(tr);
    });
  }

  /* ---------- mulai ---------- */

  papan = Chessboard("papan", {
    position: "start",
    draggable: true,
    pieceTheme: "/static/img/chesspieces/{piece}.png",
    onDragStart: bolehGeser,
    onDrop: saatJatuh,
    onSnapEnd: saatSelesaiGeser
  });
  window.addEventListener("resize", function () { papan.resize(); });
  /* Papan dibuat sebelum tata letak akhir; ukur ulang sekali setelah termuat
     supaya tidak meluber di layar sempit. */
  window.addEventListener("load", function () { setTimeout(function () { papan.resize(); }, 300); });

  document.getElementById("undo").addEventListener("click", undo);
  document.getElementById("reset").addEventListener("click", function () { reset(); });
  document.getElementById("balik").addEventListener("click", function () { papan.flip(); });
  el.sumber.addEventListener("change", muatLangkah);
  el.minElo.addEventListener("change", muatLangkah);
  el.pemain.addEventListener("change", function () { tulisUrl(); muatLangkah(); });
  el.warna.addEventListener("change", function () { tulisUrl(); muatLangkah(); });
  el.cariPemain.addEventListener("keydown", function (ev) {
    if (ev.key === "Enter") { ev.preventDefault(); cariPemain(); }
  });
  document.addEventListener("keydown", function (ev) {
    if (ev.target.tagName === "SELECT" || ev.target.tagName === "INPUT") return;
    if (ev.key === "ArrowLeft") { undo(); ev.preventDefault(); }
    if (ev.key === "ArrowRight" && langkahTampil.length) { mainkanSan(langkahTampil[0].san); ev.preventDefault(); }
    if (ev.key >= "1" && ev.key <= "9" && langkahTampil[ev.key - 1]) mainkanSan(langkahTampil[ev.key - 1].san);
    if (ev.key === "f" || ev.key === "F") papan.flip();
  });

  var qAwal = new URLSearchParams(window.location.search);

  /* Kait pengujian tanpa keyboard (Chrome headless): ?uji_tombol=ArrowRight,2
     mengirim keydown berurutan setelah tabel langkah termuat. Tidak berpengaruh
     kalau parameter tidak ada. */
  if (qAwal.get("uji_tombol")) {
    qAwal.get("uji_tombol").split(",").forEach(function (k, i) {
      setTimeout(function () {
        document.dispatchEvent(new KeyboardEvent("keydown", { key: k.trim(), bubbles: true }));
      }, 1500 + 1200 * i);
    });
  }
  if (qAwal.get("warna")) el.warna.value = qAwal.get("warna");
  if (qAwal.get("pemain_id")) {
    /* Nama diambil dulu supaya dropdown tidak menampilkan angka; kalau gagal,
       saringan pemain dilepas dan ditampilkan tanpa saringan. */
    fetch("/api/pemain/" + qAwal.get("pemain_id"))
      .then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); })
      .then(function (p) { tambahOpsiPemain(p, true); reset(qAwal.get("fen")); })
      .catch(function () { reset(qAwal.get("fen")); });
  } else {
    reset(qAwal.get("fen"));
  }
})();
