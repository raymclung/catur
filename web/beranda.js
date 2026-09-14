/* Beranda: titik awal yang menjawab "apa keadaannya sekarang, apa yang perlu
   dikerjakan". Ringkas saja — perinciannya ada di halaman masing-masing.

     Akun saya         : Elo terakhir, jumlah partai, berapa yang dianalisis, skor
     Kekalahan terbaru : 6 kekalahan terakhir + sebabnya (waktu / blunder cepat /
                         blunder / dikalahkan), sama seperti halaman Kekalahan
     Latihan           : progres dari localStorage (kunci "latihan.dikuasai")
     Lanjutkan         : tautan dengan saringan yang sudah terisi

   Semua dari endpoint yang ada; tidak ada yang ditulis ke database. */

(function () {
  "use strict";

  var el = {
    galat: document.getElementById("galat"),
    ringkasan: document.getElementById("ringkasan"),
    statusAkun: document.getElementById("status-akun"),
    barisAkun: document.getElementById("baris-akun"),
    kekalahan: document.getElementById("kekalahan-ringkas"),
    latihan: document.getElementById("latihan-ringkas"),
    pintasan: document.getElementById("pintasan")
  };

  var JUMLAH_KEKALAHAN = 6;

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
  function tautan(label, href, judul) {
    var a = document.createElement("a");
    a.href = href;
    if (judul) a.title = judul;
    a.appendChild(teks(label));
    return a;
  }

  /* ---------- statistik database ---------- */

  ambil("/api/statistik").then(function (s) {
    el.ringkasan.textContent = s.partai.toLocaleString("id-ID") + " partai · " +
      s.analisis.partai_dianalisis.toLocaleString("id-ID") + " dianalisis";
  }).catch(function () { el.ringkasan.textContent = ""; });

  /* ---------- akun ---------- */

  function gambarAkun(profil) {
    el.barisAkun.textContent = "";
    profil.forEach(function (p) {
      if (!p) return;
      var tr = document.createElement("tr");
      var td0 = document.createElement("td");
      td0.appendChild(tautan(p.nama, "/pemain/" + p.id, "Profil pemain"));
      tr.appendChild(td0);
      tr.appendChild(buat("td", "kecil redup", (p.per_sumber || []).map(function (s) {
        return ({ "lichess-user": "Lichess", "chesscom": "Chess.com" })[s[0]] || s[0];
      }).join(", ")));
      tr.appendChild(buat("td", "mono", p.elo_terakhir || "—"));
      tr.appendChild(buat("td", "mono", (p.total && p.total.partai || 0).toLocaleString("id-ID")));
      var tdA = buat("td", "mono", (p.partai_dianalisis || 0).toLocaleString("id-ID"));
      var porsi = p.total && p.total.partai ? Math.round(100 * (p.partai_dianalisis || 0) / p.total.partai) : 0;
      tdA.appendChild(buat("span", "redup kecil", " " + porsi + "%"));
      tr.appendChild(tdA);
      tr.appendChild(buat("td", "mono", p.total && p.total.skor_persen !== null && p.total.skor_persen !== undefined ? p.total.skor_persen + "%" : "—"));
      var tdL = buat("td", "kecil");
      tdL.style.whiteSpace = "nowrap";
      tdL.appendChild(tautan("latih →", "/static/latihan.html?akun=" + p.id, "Latihan dari kesalahan akun ini"));
      tr.appendChild(tdL);
      tr.style.cursor = "default";
      el.barisAkun.appendChild(tr);
    });
    if (!el.barisAkun.children.length) {
      var tr0 = document.createElement("tr");
      var td = buat("td", "pesan kecil", "Tidak ada akun pribadi di database.");
      td.colSpan = 7; tr0.appendChild(td); el.barisAkun.appendChild(tr0);
    }
  }

  /* ---------- kekalahan terbaru (penggolongan ringkas) ---------- */

  var tagPgn = AlatCatur.tagPgn, jamPerPly = AlatCatur.jamPerPly, detikDipakai = AlatCatur.detikDipakai;

  var LABEL = { waktu: "kalah waktu", missclick: "blunder cepat", blunder: "blunder", dikalahkan: "dikalahkan", belum: "belum dianalisis" };

  function golongkan(warna, det, analisis) {
    var tags = tagPgn(det.pgn || ""), jam = jamPerPly(det.pgn || "");
    if (AlatCatur.kalahWaktu(tags)) return { sebab: "waktu", ket: "" };
    if (!analisis) return { sebab: "belum", ket: "" };
    var punya = analisis.langkah.filter(function (l) { return l.warna === warna && (l.kehilangan_cp || 0) >= 300; });
    punya.sort(function (a, b) { return b.kehilangan_cp - a.kehilangan_cp; });
    if (!punya.length) return { sebab: "dikalahkan", ket: "tanpa blunder besar" };
    var cepat = punya.filter(function (l) { var d = detikDipakai(jam, l.ply, det.kontrol_waktu); return d !== null && d <= 2; });
    var b = cepat.length ? cepat[0] : punya[0];
    var d = detikDipakai(jam, b.ply, det.kontrol_waktu);
    return {
      sebab: cepat.length ? "missclick" : "blunder",
      ket: (b.warna === "putih" ? b.nomor_langkah + "." : b.nomor_langkah + "…") + b.san +
        " −" + b.kehilangan_cp + " cp" + (d !== null ? " · " + d + " s" : "")
    };
  }

  function muatKekalahan(akun) {
    return Promise.all(akun.map(function (a) {
      return Promise.all([
        ambil("/api/partai?pemain_id=" + a.id + "&warna=putih&hasil=0-1&per_halaman=8&urut=terbaru").catch(function () { return { partai: [] }; }),
        ambil("/api/partai?pemain_id=" + a.id + "&warna=hitam&hasil=1-0&per_halaman=8&urut=terbaru").catch(function () { return { partai: [] }; })
      ]).then(function (r) {
        var d = [];
        r[0].partai.forEach(function (g) { d.push({ g: g, warna: "putih", akun: a }); });
        r[1].partai.forEach(function (g) { d.push({ g: g, warna: "hitam", akun: a }); });
        return d;
      });
    })).then(function (kelompok) {
      var semua = [];
      kelompok.forEach(function (k) { semua = semua.concat(k); });
      semua.sort(function (a, b) { return (b.g.tanggal || "").localeCompare(a.g.tanggal || ""); });
      semua = semua.slice(0, JUMLAH_KEKALAHAN);
      return Promise.all(semua.map(function (x) {
        return ambil("/api/partai/" + x.g.id).then(function (det) {
          return fetch("/api/partai/" + x.g.id + "/analisis").then(function (r) { return r.ok ? r.json() : null; })
            .then(function (a) { return { x: x, det: det, gol: golongkan(x.warna, det, a) }; });
        }).catch(function () { return null; });
      }));
    }).then(function (hasil) {
      el.kekalahan.textContent = "";
      hasil = hasil.filter(Boolean);
      if (!hasil.length) { el.kekalahan.appendChild(buat("div", "pesan kecil", "Tidak ada kekalahan terbaru.")); return; }
      hasil.forEach(function (h) {
        var g = h.x.g, warna = h.x.warna;
        var baris = buat("div", "tersimpan-baris");
        var kiri = buat("div", "tersimpan-isi");
        var lawan = warna === "putih" ? g.hitam : g.putih;
        var elo = warna === "putih" ? g.elo_hitam : g.elo_putih;
        kiri.appendChild(buat("div", null, h.x.akun.nama + " vs " + lawan + (elo ? " (" + elo + ")" : "")));
        var meta = buat("div", "kecil redup");
        meta.appendChild(teks((g.tanggal || "") + " · " + AlatCatur.formatPartai(g) + " · "));
        meta.appendChild(buat("span", "chip sebab-" + h.gol.sebab, LABEL[h.gol.sebab]));
        if (h.gol.ket) meta.appendChild(teks(" " + h.gol.ket));
        kiri.appendChild(meta);
        baris.appendChild(kiri);
        var kanan = buat("div", "tautan-baris");
        kanan.style.marginTop = "0";
        kanan.appendChild(tautan("partai →", "/partai/" + g.id));
        baris.appendChild(kanan);
        el.kekalahan.appendChild(baris);
      });
    });
  }

  /* ---------- latihan: progres dari localStorage ---------- */

  function gambarLatihan() {
    var dikuasai = 0;
    try { dikuasai = Object.keys(JSON.parse(localStorage.getItem("latihan.dikuasai") || "{}")).length; } catch (e) { dikuasai = 0; }
    el.latihan.textContent = "";
    var besar = buat("div", "angka-besar", dikuasai);
    el.latihan.appendChild(buat("div", "label", "posisi dikuasai (browser ini)"));
    el.latihan.appendChild(besar);
    el.latihan.appendChild(buat("div", "kecil redup", dikuasai ? "Soal yang sudah benar muncul paling belakang." : "Belum ada. Mulai dari blunder di partai kalah."));
    var tb = buat("div", "tautan-baris");
    tb.appendChild(tautan("latih blunder →", "/static/latihan.html?jenis=blunder", "Hanya blunder (??)"));
    tb.appendChild(tautan("semua kesalahan →", "/static/latihan.html", "Blunder + kesalahan"));
    el.latihan.appendChild(tb);
  }

  /* ---------- pintasan ---------- */

  function gambarPintasan(akun) {
    var satu = akun.length ? akun[0] : null;
    [
      ["Kelemahan saya", "/static/kelemahan.html", "Kesalahan per fase, warna, format, pembukaan"],
      ["Repertoire vs GM", "/static/repertoire.html", "Langkah pembukaanmu dibandingkan praktik GM"],
      ["Kekalahan bullet", "/static/kekalahan.html?format=bullet", "Kekalahan di bullet saja"],
      ["Jelajah pembukaan", "/jelajah", "Papan + statistik langkah GM"],
      ["Papan analisis", "/static/analisis.html", "Susun posisi, evaluasi engine"],
      ["Posisi tersimpan", "/static/tersimpan.html", "Posisi yang kamu simpan"]
    ].forEach(function (x) { el.pintasan.appendChild(tautan(x[0], x[1], x[2])); });
    if (satu) el.pintasan.appendChild(tautan("Profil " + satu.nama, "/pemain/" + satu.id, "Profil dan preferensi pembukaan"));
  }

  /* ---------- mulai ---------- */

  gambarLatihan();

  AkunPribadi.muat().then(function (akun) {
    el.statusAkun.textContent = akun.length ? "· " + akun.length + " akun" : "";
    gambarPintasan(akun);
    return Promise.all(akun.map(function (a) {
      return ambil("/api/pemain/" + a.id).catch(function () { return null; });
    })).then(function (profil) {
      gambarAkun(profil);
      return muatKekalahan(akun);
    });
  }).catch(function (e) { galat("Gagal memuat: " + e.message); });
})();
