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
    rencana: document.getElementById("rencana"),
    rencanaMinggu: document.getElementById("rencana-minggu"),
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

  var LABEL = { waktu: "kalah waktu", missclick: "blunder cepat", blunder: "blunder", dikalahkan: "dikalahkan", belum: "belum dianalisis" };

  /* Sebab kekalahan diambil dari server (GET /api/kekalahan, app/sebab_kalah.py),
     aturan yang sama dengan halaman Kekalahan. Dulu Beranda punya salinan
     aturan lama sendiri di sini (tanpa "jam habis tapi posisi memang sudah
     kalah"), jadi partai yang sama bisa berbunyi "kalah waktu" di Beranda dan
     "blunder" di Kekalahan (audit 27 Sep 2026). lawan=semua: Beranda
     menampilkan kekalahan terbaru apa adanya, tanpa saringan lawan. */
  function muatKekalahan(akun) {
    var q = akun.map(function (a) { return "pemain_id=" + a.id; }).join("&") +
      "&lawan=semua&batas=" + JUMLAH_KEKALAHAN;
    /* /api/kekalahan wajib punya pemain_id; tanpa akun = daftar kosong. */
    return (akun.length ? ambil("/api/kekalahan?" + q) : Promise.resolve({ partai: [] })).then(function (d) {
      return d.partai.map(function (p) {
        var g = { id: p.id, tanggal: p.tanggal, format: p.format, kontrol_waktu: p.kontrol_waktu };
        if (p.warna === "putih") { g.hitam = p.lawan; g.elo_hitam = p.lawan_elo; }
        else { g.putih = p.lawan; g.elo_putih = p.lawan_elo; }
        return { x: { g: g, warna: p.warna, akun: { nama: p.akun } },
                 gol: { sebab: p.sebab, ket: p.keterangan } };
      });
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

  /* ---------- latihan: catatan dari database (A38), bukan localStorage ----------
     Dulu menghitung kunci di localStorage; sejak catatan pindah ke server,
     angka itu cuma milik satu browser dan bisa berbeda dari kenyataan. */

  function gambarLatihan() {
    el.latihan.textContent = "";
    ambil("/api/latihan/ringkasan").then(function (r) {
      var k = r.kesalahan || {}, p = r.pembukaan || {};
      var selesai = (k.selesai || 0) + (p.selesai || 0);
      el.latihan.textContent = "";
      el.latihan.appendChild(buat("div", "label", "soal selesai"));
      el.latihan.appendChild(buat("div", "angka-besar", selesai));
      var rincian = [];
      if ((k.menunggu || 0) + (p.menunggu || 0)) rincian.push(((k.menunggu || 0) + (p.menunggu || 0)) + " menunggu " + (r.jeda_ulang_hari || 3) + " hari");
      if ((k.siap_ulang || 0) + (p.siap_ulang || 0)) rincian.push(((k.siap_ulang || 0) + (p.siap_ulang || 0)) + " siap diulang");
      if (p.selesai) rincian.push(p.selesai + " dari pembukaan");
      el.latihan.appendChild(buat("div", "kecil redup",
        rincian.length ? rincian.join(" · ") : "Belum ada. Mulai dari blunder di partai kalah."));
      tautanLatihan();
    }).catch(function () {
      el.latihan.textContent = "";
      el.latihan.appendChild(buat("div", "kecil redup", "Catatan latihan tidak bisa diambil dari server."));
      tautanLatihan();
    });
  }

  function tautanLatihan() {
    var tb = buat("div", "tautan-baris");
    tb.appendChild(tautan("latih blunder →", "/static/latihan.html?jenis=blunder", "Hanya blunder (??)"));
    tb.appendChild(tautan("semua kesalahan →", "/static/latihan.html", "Blunder + kesalahan"));
    tb.appendChild(tautan("pembukaan →", "/static/latihan.html?mode=pembukaan",
      "Posisi repertoire tempat langkahmu jarang dimainkan GM"));
    el.latihan.appendChild(tb);
  }

  /* ---------- rencana minggu ini ---------- */

  function gambarRencana(akun) {
    if (!akun.length) {
      el.rencana.textContent = "";
      el.rencana.appendChild(buat("div", "pesan kecil", "Belum ada akun pribadi di database."));
      return Promise.resolve();
    }
    var q = akun.map(function (a) { return "pemain_id=" + a.id; }).join("&");
    return ambil("/api/rencana?" + q).then(function (d) {
      el.rencana.textContent = "";
      el.rencanaMinggu.textContent = "· mulai " + d.minggu_mulai;

      if (!d.kelemahan.length) {
        el.rencana.appendChild(buat("div", "pesan kecil",
          "Belum ada kelemahan yang selisihnya cukup besar untuk disebut."));
      }
      d.kelemahan.forEach(function (k, i) {
        var baris = buat("div", "kartu");
        baris.style.margin = i ? "10px 0 0" : "0";
        baris.style.padding = "10px 12px";
        baris.appendChild(buat("div", null, (i + 1) + ". " + k.judul));
        baris.appendChild(buat("div", "kecil", k.angka));
        baris.appendChild(buat("div", "kecil redup", k.saran));
        var tb = buat("div", "tautan-baris");
        k.tautan.forEach(function (t) { tb.appendChild(tautan(t.teks + " →", t.url)); });
        baris.appendChild(tb);
        el.rencana.appendChild(baris);
      });

      /* Minggu ini vs minggu lalu: supaya kelihatan latihannya jalan atau tidak. */
      var l = d.latihan || {};
      var garis = ["kesalahan", "pembukaan"].map(function (m) {
        var a = (l[m] || {}).minggu_ini || {}, b = (l[m] || {}).minggu_lalu || {};
        return m + ": " + (a.dijawab || 0) + " soal minggu ini (" + (a.benar || 0) + " benar)" +
               ", minggu lalu " + (b.dijawab || 0);
      });
      var ringkas = buat("div", "kecil");
      ringkas.style.marginTop = "10px";
      ringkas.appendChild(teks(garis.join(" · ")));
      el.rencana.appendChild(ringkas);

      var cat = buat("div", "kecil redup");
      (d.catatan || []).forEach(function (c) { cat.appendChild(buat("div", null, "· " + c)); });
      el.rencana.appendChild(cat);
    }).catch(function (e) {
      el.rencana.textContent = "";
      el.rencana.appendChild(buat("div", "kecil redup", "Rencana tidak bisa dihitung: " + e.message));
    });
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
    gambarRencana(akun);
    return Promise.all(akun.map(function (a) {
      return ambil("/api/pemain/" + a.id).catch(function () { return null; });
    })).then(function (profil) {
      gambarAkun(profil);
      return muatKekalahan(akun);
    });
  }).catch(function (e) { galat("Gagal memuat: " + e.message); });
})();
