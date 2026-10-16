/* Kamu vs GM dari posisi yang sama (16 Okt 2026).

   GET /api/banding-gm: tiap partai akun sendiri dipotong di titik keluar buku
   (posisi terdalam yang masih punya >= 20 partai GM), lalu dikelompokkan per
   titik. Tabel mengurutkan posisi menurut poin yang hilang DIBANDING
   RATA-RATA SENDIRI — bukan dibanding skor GM, karena format dan lawan
   berbeda (blitz vs klasik). Skor GM dipakai untuk memastikan posisinya
   sehat.

   Rincian satu posisi (langkah GM, kehilangan cp GM, contoh partai GM) mahal,
   jadi diambil hanya saat baris dibuka: GET /api/banding-gm/rincian. */

(function () {
  "use strict";

  var el = {
    galat: document.getElementById("galat"),
    akun: document.getElementById("akun"),
    lawan: document.getElementById("lawan"),
    format: document.getElementById("format"),
    status: document.getElementById("status"),
    ikhtisar: document.getElementById("ikhtisar"),
    keterangan: document.getElementById("keterangan"),
    daftar: document.getElementById("daftar"),
    keluarAwal: document.getElementById("keluar-awal")
  };

  var akunSemua = [];
  var permintaanKe = 0;
  var TAMPIL_AWAL = 25;

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
  function angka(n, d) {
    if (n === null || n === undefined) return "—";
    return Number(n).toLocaleString("id-ID", { minimumFractionDigits: d || 0, maximumFractionDigits: d || 0 });
  }
  function persen(n) { return n === null || n === undefined ? "—" : angka(n, 1) + "%"; }
  function bertanda(n) { return n === null || n === undefined ? "—" : (n > 0 ? "+" : "") + angka(n, 1); }

  /* "e4 c5 Nf3" -> "1.e4 c5 2.Nf3" */
  function jalurTeks(jalur) {
    return jalur.map(function (san, i) { return (i % 2 === 0 ? (i / 2 + 1) + "." : "") + san; }).join(" ");
  }
  function fenLengkap(fenKunci) { return fenKunci + " 0 1"; }

  function urlApi(jalan) {
    var q = new URLSearchParams();
    var ids = el.akun.value === "semua" ? akunSemua.map(function (a) { return a.id; }) : [el.akun.value];
    ids.forEach(function (id) { q.append("pemain_id", id); });
    q.set("lawan", el.lawan.value);
    if (el.format.value !== "semua") q.set("format", el.format.value);
    return jalan + "?" + q.toString();
  }

  /* ---------- muat ---------- */

  function muat() {
    var ke = ++permintaanKe;
    el.galat.hidden = true;
    el.status.textContent = "menghitung… (pertama kali beberapa detik)";
    [el.ikhtisar, el.keterangan, el.daftar, el.keluarAwal].forEach(function (e) { e.textContent = ""; });
    el.keluarAwal.hidden = true;
    ambil(urlApi("/api/banding-gm")).then(function (d) {
      if (ke !== permintaanKe) return;
      el.status.textContent = "";
      gambarIkhtisar(d.ringkasan);
      gambarDaftar(d);
      gambarKeluarAwal(d.ringkasan);
      /* Kait uji tanpa mouse: ?uji_buka=1 membuka rincian baris pertama
         (dump-dom peramban headless tidak bisa mengklik). */
      if (q.get("uji_buka")) {
        var baris1 = el.daftar.querySelector("tbody tr");
        if (baris1) baris1.click();
      }
    }).catch(function (e) { if (ke === permintaanKe) { galat(e.message); el.status.textContent = ""; } });
  }

  function gambarIkhtisar(r) {
    el.ikhtisar.style.gridTemplateColumns = "repeat(4, 1fr)";
    var eng = r.engine_saya || {};
    [["Partai dihitung", angka(r.partai_dihitung) + " dari " + angka(r.partai)],
     ["Skor rata-ratamu", persen(r.skor_rata_saya)],
     ["Kehilangan cp-mu sesudah buku", eng.rata_kehilangan === null || eng.rata_kehilangan === undefined ? "—" : angka(eng.rata_kehilangan, 1) + " cp"],
     ["Posisi yang sering kamu capai", angka(r.titik_ditampilkan)]
    ].forEach(function (x) {
      var k = buat("div"); k.appendChild(buat("div", "label", x[0])); k.appendChild(buat("div", "angka-besar", x[1])); el.ikhtisar.appendChild(k);
    });
    if (!r.ambang) return;
    el.keterangan.textContent =
      "Titik keluar buku = posisi terdalam di partaimu yang masih punya ≥ " + r.ambang.min_partai_gm +
      " partai GM (sejak " + r.ambang.gm_sejak + "). Hanya posisi yang kamu capai ≥ " + r.ambang.min_partai_saya +
      " kali yang ditampilkan. \"vs rata-rata\" = skormu di posisi itu dikurangi skor rata-ratamu sendiri; " +
      "skor GM tidak dibandingkan langsung karena GM main klasik melawan GM, kamu kebanyakan blitz. " +
      "Kehilangan cp = rata-rata di " + (r.ambang.jendela_ply / 2) + " langkahmu sesudah titik itu (hanya partai teranalisis). " +
      "Dihitung dalam " + angka(r.detik, 1) + " detik.";
  }

  function gambarDaftar(d) {
    var w = el.daftar;
    w.appendChild(buat("h2", "judul-kartu", "Posisi tempat kamu paling sering kehilangan poin"));
    if (!d.posisi.length) {
      var k = buat("div", "kecil redup", "Belum ada posisi yang dicapai cukup sering untuk saringan ini.");
      k.style.padding = "0 14px 14px";
      w.appendChild(k);
      return;
    }
    var t = document.createElement("table");
    var thead = document.createElement("thead"), trh = document.createElement("tr");
    ["Jalur sampai keluar buku", "Warna", "Partaimu", "Skormu", "vs rata-rata", "Skor GM", "cp-mu", ""]
      .forEach(function (k) { trh.appendChild(buat("th", null, k)); });
    thead.appendChild(trh); t.appendChild(thead);
    var tb = document.createElement("tbody");
    d.posisi.forEach(function (p, i) {
      var tr = document.createElement("tr");
      if (i >= TAMPIL_AWAL) tr.hidden = true;
      tr.style.cursor = "pointer";
      var td0 = buat("td", "mono kecil", jalurTeks(p.jalur));
      if (p.pembukaan) td0.appendChild(buat("span", "redup", " · " + p.pembukaan));
      if (!p.posisi_sehat) {
        var s = buat("span", "redup", " · sulit juga bagi GM");
        s.title = "Skor GM untuk warna ini di bawah 40%: masalahnya pilihan pembukaan, bukan cara memainkannya";
        td0.appendChild(s);
      }
      tr.appendChild(td0);
      tr.appendChild(buat("td", null, p.warna === "putih" ? "Putih" : "Hitam"));
      tr.appendChild(buat("td", "mono", p.saya.partai + " (" + p.saya.menang + "/" + p.saya.remis + "/" + p.saya.kalah + ")"));
      tr.appendChild(buat("td", "mono", persen(p.saya.skor)));
      var tdS = buat("td", "mono" + (p.selisih_dari_rata <= -15 ? " klas-blunder" : p.selisih_dari_rata >= 15 ? " klas-terbaik" : ""),
        bertanda(p.selisih_dari_rata));
      tr.appendChild(tdS);
      tr.appendChild(buat("td", "mono", persen(p.gm.skor) + " (" + angka(p.gm.partai) + ")"));
      tr.appendChild(buat("td", "mono", p.saya.engine.rata_kehilangan === null ? "—" : angka(p.saya.engine.rata_kehilangan, 1)));
      tr.appendChild(buat("td", "kecil", "rincian ▾"));
      var trR = document.createElement("tr");
      trR.hidden = true;
      var tdR = document.createElement("td");
      tdR.colSpan = 8;
      trR.appendChild(tdR);
      tr.addEventListener("click", function () {
        trR.hidden = !trR.hidden;
        tr.lastChild.textContent = trR.hidden ? "rincian ▾" : "tutup ▴";
        if (!trR.hidden && !tdR.firstChild) muatRincian(p, tdR);
      });
      tb.appendChild(tr);
      tb.appendChild(trR);
    });
    t.appendChild(tb);
    var gulir = buat("div", "gulir"); gulir.appendChild(t); w.appendChild(gulir);
    if (d.posisi.length > TAMPIL_AWAL) {
      var lagi = document.createElement("button");
      lagi.type = "button";
      lagi.style.margin = "8px 14px";
      lagi.appendChild(teks("tampilkan semua " + d.posisi.length + " posisi"));
      lagi.addEventListener("click", function () {
        Array.prototype.forEach.call(tb.children, function (tr, i) { if (i % 2 === 0) tr.hidden = false; });
        lagi.remove();
      });
      w.appendChild(lagi);
    }
  }

  /* ---------- rincian satu posisi ---------- */

  function tautan(href, isi, judul) {
    var a = document.createElement("a");
    a.href = href;
    a.target = "_blank";
    if (judul) a.title = judul;
    a.appendChild(teks(isi));
    return a;
  }

  function muatRincian(p, wadah) {
    wadah.appendChild(buat("div", "kecil redup", "mengambil langkah dan partai GM…"));
    ambil("/api/banding-gm/rincian?fen=" + encodeURIComponent(p.fen) + "&warna=" + p.warna).then(function (r) {
      wadah.textContent = "";
      var kotak = buat("div");
      kotak.style.display = "grid";
      kotak.style.gridTemplateColumns = "repeat(auto-fit, minmax(240px, 1fr))";
      kotak.style.gap = "12px";
      kotak.style.padding = "8px 4px";

      var siapa = p.giliran_saya ? "langkahmu" : "langkah lawanmu";
      var kGm = buat("div");
      kGm.appendChild(buat("strong", null, "GM memainkan"));
      r.langkah_gm.forEach(function (x) {
        kGm.appendChild(buat("div", "mono kecil", x.san + " · " + persen(x.porsi) + " partai · skor " + persen(x.skor) + " (yang jalan)"));
      });
      kotak.appendChild(kGm);

      var kSaya = buat("div");
      kSaya.appendChild(buat("strong", null, "Di partaimu (" + siapa + ")"));
      p.saya.langkah.forEach(function (x) {
        kSaya.appendChild(buat("div", "mono kecil", x.san + " · " + x.n + "× · skormu " + persen(x.skor)));
      });
      kotak.appendChild(kSaya);

      var kEng = buat("div");
      kEng.appendChild(buat("strong", null, "Kehilangan cp " + "15 langkah berikutnya"));
      kEng.appendChild(buat("div", "mono kecil", "kamu: " + (p.saya.engine.rata_kehilangan === null ? "—" : angka(p.saya.engine.rata_kehilangan, 1) + " cp") +
        " · " + angka(p.saya.engine.kesalahan_per_100, 1) + " kesalahan/100 (" + p.saya.engine.partai + " partai)"));
      kEng.appendChild(buat("div", "mono kecil", "GM: " + (r.engine_gm.rata_kehilangan === null ? "—" : angka(r.engine_gm.rata_kehilangan, 1) + " cp") +
        " · " + angka(r.engine_gm.kesalahan_per_100, 1) + " kesalahan/100 (" + r.engine_gm.partai + " partai)"));
      kEng.appendChild(buat("div", "kecil redup", "patokan saja: GM umumnya klasik, kamu umumnya blitz"));
      kotak.appendChild(kEng);

      var kContoh = buat("div");
      kContoh.appendChild(buat("strong", null, "Pelajari partai GM dari sini"));
      r.contoh_gm.forEach(function (g) {
        var baris = buat("div", "kecil");
        baris.appendChild(tautan("/partai/" + g.id, g.putih + " – " + g.hitam + " " + g.hasil,
          (g.turnamen || "") + " " + (g.tanggal || "") + " · " + (g.jumlah_langkah || "?") + " langkah"));
        baris.appendChild(buat("span", "redup", " " + (g.tanggal || "").slice(0, 4)));
        kContoh.appendChild(baris);
      });
      var lain = buat("div", "kecil");
      lain.style.marginTop = "6px";
      lain.appendChild(tautan("/jelajah?fen=" + encodeURIComponent(fenLengkap(p.fen)), "jelajahi posisi →", "Penjelajah pembukaan dari posisi ini"));
      lain.appendChild(teks(" · "));
      lain.appendChild(tautan("/partai/" + p.contoh_saya.partai_id, "partaimu terakhir →",
        (p.contoh_saya.tanggal || "") + " vs " + (p.contoh_saya.lawan || "")));
      kContoh.appendChild(lain);
      kotak.appendChild(kContoh);

      wadah.appendChild(kotak);
      wadah.appendChild(rencanaSaya(p));
    }).catch(function (e) { wadah.textContent = e.message; });
  }

  /* Rencana belajar posisi ini dari sisi partai sendiri (16 Okt 2026): partai
     yang melewati posisi ini (kalah dulu) dengan kesalahan pertamamu
     sesudahnya, dan tautan ke Latihan yang hanya berisi kesalahan itu. */
  var TAMPIL_PARTAI_SAYA = 10;
  function rencanaSaya(p) {
    var w = buat("div");
    w.style.padding = "4px 4px 10px";
    var judul = buat("div", null, "");
    judul.appendChild(buat("strong", null, "Partaimu dari posisi ini"));
    var adaKesalahan = (p.partai_saya || []).some(function (x) { return x.kesalahan_pertama; });
    if (adaKesalahan) {
      var q = new URLSearchParams();
      q.set("akun", el.akun.value);
      q.set("lawan", el.lawan.value);
      if (el.format.value !== "semua") q.set("format", el.format.value);
      q.set("fen", fenLengkap(p.fen));
      q.set("ply_dari", p.ply);
      q.set("warna", p.warna);
      q.set("nama", "sesudah " + jalurTeks(p.jalur).split(" ").slice(-4).join(" "));
      judul.appendChild(teks("  "));
      judul.appendChild(tautan("/static/latihan.html?" + q.toString(), "latih kesalahanmu dari posisi ini →",
        "Soal Latihan dari blunder/kesalahanmu sesudah posisi ini saja"));
    }
    w.appendChild(judul);
    var daftar = p.partai_saya || [];
    daftar.slice(0, TAMPIL_PARTAI_SAYA).forEach(function (x) {
      var baris = buat("div", "mono kecil");
      baris.appendChild(buat("span", x.hasil === "kalah" ? "klas-blunder" : x.hasil === "menang" ? "klas-terbaik" : "redup",
        x.hasil));
      baris.appendChild(teks(" · "));
      baris.appendChild(tautan("/partai/" + x.partai_id, (x.tanggal || "?") + " vs " + (x.lawan || "?")));
      var k = x.kesalahan_pertama;
      baris.appendChild(teks(" · " + (k ? "salah pertama " + k.nomor_langkah + (p.warna === "putih" ? ". " : "… ") + k.san +
        (k.klasifikasi === "blunder" ? "??" : "?") + " (−" + k.kehilangan_cp + " cp)"
        : x.dianalisis ? "tanpa blunder/kesalahan sesudahnya" : "belum dianalisis")));
      w.appendChild(baris);
    });
    if (daftar.length > TAMPIL_PARTAI_SAYA) {
      w.appendChild(buat("div", "kecil redup", "… dan " + (daftar.length - TAMPIL_PARTAI_SAYA) + " partai lain (" +
        p.saya.partai + " total; paling banyak 30 dikirim, kalah dulu)"));
    }
    return w;
  }

  function gambarKeluarAwal(r) {
    if (!r.keluar_sebelum_ply_min) return;
    var w = el.keluarAwal;
    w.hidden = false;
    w.appendChild(buat("h2", "judul-kartu", "Keluar buku GM sebelum langkah ke-4: " + angka(r.keluar_sebelum_ply_min) + " partai"));
    var c = buat("div", "kecil redup", "Partai ini tidak dihitung di atas: jalurnya sudah jarang dimainkan GM (< " +
      r.ambang.min_partai_gm + " partai) dalam tiga langkah pertama. Itu pilihan pembukaan, bukan kesalahan. Jalur terseringnya:");
    c.style.padding = "0 14px 6px";
    w.appendChild(c);
    var ul = buat("ul", "kecil");
    ul.style.margin = "0 14px 14px";
    (r.keluar_awal_teratas || []).forEach(function (x) {
      ul.appendChild(buat("li", "mono", jalurTeks(x[0].split(" ")) + " — " + x[1] + " partai"));
    });
    w.appendChild(ul);
  }

  /* ---------- mulai ---------- */

  var q = new URLSearchParams(window.location.search);
  ["lawan", "format"].forEach(function (k) {
    if (q.get(k) && el[k].querySelector('option[value="' + CSS.escape(q.get(k)) + '"]')) el[k].value = q.get(k);
  });

  AkunPribadi.muat().then(function (akun) {
    akunSemua = akun;
    el.akun.textContent = "";
    var semua = document.createElement("option");
    semua.value = "semua"; semua.textContent = "semua akun (" + akun.length + ")"; el.akun.appendChild(semua);
    akun.forEach(function (p) {
      var o = document.createElement("option"); o.value = p.id; o.textContent = p.nama + (p.sumber ? " · " + p.sumber : ""); el.akun.appendChild(o);
    });
    if (q.get("akun") && el.akun.querySelector('option[value="' + CSS.escape(q.get("akun")) + '"]')) el.akun.value = q.get("akun");
    if (!akun.length) throw new Error("Tidak ada akun pribadi di database.");
    muat();
  }).catch(function (e) { galat(e.message); });

  [el.akun, el.lawan, el.format].forEach(function (s) { s.addEventListener("change", muat); });
})();
