/* Bandingkan dua pemain berdampingan (RENCANA §10, 16 Okt 2026).

   GET /api/banding memberi gaya, pembukaan, dan partai di antara keduanya.
   Angka engine diambil terpisah dari /api/ringkasan per pemain, karena untuk
   pemain dengan 10 ribu partai itu butuh beberapa detik: tabel lain tampil
   dulu, baris engine menyusul.

   Alamat halaman menyimpan pilihan (?a=&b=&format=), jadi perbandingan bisa
   ditautkan dari halaman pemain atau dibagikan. */

(function () {
  "use strict";

  var el = {
    galat: document.getElementById("galat"),
    status: document.getElementById("status"),
    cariA: document.getElementById("cari-a"),
    cariB: document.getElementById("cari-b"),
    daftarA: document.getElementById("daftar-a"),
    daftarB: document.getElementById("daftar-b"),
    tukar: document.getElementById("tukar"),
    format: document.getElementById("format"),
    petunjuk: document.getElementById("petunjuk"),
    ringkasan: document.getElementById("ringkasan"),
    pembukaan: document.getElementById("pembukaan"),
    pembukaanPutih: document.getElementById("pembukaan-putih"),
    pembukaanHitam: document.getElementById("pembukaan-hitam"),
    h2h: document.getElementById("h2h")
  };

  var pilihan = { a: null, b: null };   // {id, nama}
  var permintaanKe = 0;
  var H2H_TAMPIL = 30;
  var PEMBUKAAN_MAKS = 8;

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
      if (!r.ok) {
        return r.json().catch(function () { return {}; }).then(function (j) {
          throw new Error(typeof j.detail === "string" ? j.detail : "Server menjawab " + r.status + " untuk " + url);
        });
      }
      return r.json();
    });
  }
  function angka(n, desimal) {
    if (n === null || n === undefined) return "—";
    return Number(n).toLocaleString("id-ID", { minimumFractionDigits: desimal || 0, maximumFractionDigits: desimal || 0 });
  }
  function persen(n) { return n === null || n === undefined ? "—" : angka(n, 1) + "%"; }
  function namaPemain(p) { return p.nama + (p.gelar ? " (" + p.gelar + ")" : ""); }

  /* ---------- pemilihan pemain ---------- */

  /* Nilai opsi datalist berakhiran "#id" supaya pilihan bisa dibaca tanpa
     menebak dari nama (nama kembar memang ada di database). */
  function labelOpsi(p) {
    return p.nama + (p.gelar ? " · " + p.gelar : "") + (p.elo_terakhir ? " · " + p.elo_terakhir : "") +
      " · " + angka(p.jumlah_partai) + " partai #" + p.id;
  }

  function pasangPencari(input, daftar, kunci) {
    var tunda = null, terakhir = "";
    input.addEventListener("input", function () {
      var m = /#(\d+)$/.exec(input.value);
      if (m) {
        pilihan[kunci] = { id: parseInt(m[1], 10), nama: input.value.split(" · ")[0] };
        input.value = pilihan[kunci].nama;
        muat();
        return;
      }
      var q = input.value.trim();
      if (q.length < 2 || q === terakhir) return;
      clearTimeout(tunda);
      tunda = setTimeout(function () {
        terakhir = q;
        ambil("/api/pemain?limit=15&cari=" + encodeURIComponent(q)).then(function (d) {
          daftar.textContent = "";
          d.pemain.forEach(function (p) {
            var o = document.createElement("option");
            o.value = labelOpsi(p);
            daftar.appendChild(o);
          });
        }).catch(function (e) { galat(e.message); });
      }, 250);
    });
  }

  function simpanAlamat() {
    var q = new URLSearchParams();
    if (pilihan.a) q.set("a", pilihan.a.id);
    if (pilihan.b) q.set("b", pilihan.b.id);
    if (el.format.value !== "semua") q.set("format", el.format.value);
    try { history.replaceState(null, "", "?" + q.toString()); } catch (e) { /* tanpa riwayat tetap jalan */ }
  }

  /* ---------- muat ---------- */

  function muat() {
    simpanAlamat();
    if (!pilihan.a || !pilihan.b) return;
    if (pilihan.a.id === pilihan.b.id) { galat("Pilih dua pemain yang berbeda."); return; }
    var ke = ++permintaanKe;
    el.galat.hidden = true;
    el.status.textContent = "menghitung…";
    var f = el.format.value !== "semua" ? "&format=" + el.format.value : "";
    ambil("/api/banding?a=" + pilihan.a.id + "&b=" + pilihan.b.id + f).then(function (d) {
      if (ke !== permintaanKe) return;
      pilihan.a.nama = d.a.nama; pilihan.b.nama = d.b.nama;
      el.cariA.value = d.a.nama; el.cariB.value = d.b.nama;
      el.status.textContent = "angka engine menyusul…";
      el.petunjuk.hidden = true;
      gambarRingkasan(d);
      gambarPembukaan(d);
      gambarH2h(d);
      return Promise.all([d.a, d.b].map(function (p) {
        return ambil("/api/ringkasan?pemain_id=" + p.id + f).catch(function () { return null; });
      })).then(function (r) {
        if (ke !== permintaanKe) return;
        gambarEngine(r[0], r[1]);
        el.status.textContent = "";
      });
    }).catch(function (e) { if (ke === permintaanKe) { galat(e.message); el.status.textContent = ""; } });
  }

  /* ---------- tampilan: ringkasan ---------- */

  /* Baris tabel A | B. `lebih` = "tinggi"/"rendah" menebalkan angka yang lebih
     baik; null = tidak ada yang "lebih baik" (gaya, bukan kekuatan). */
  function baris(tb, label, a, b, lebih, nilaiA, nilaiB) {
    var tr = document.createElement("tr");
    tr.style.cursor = "default";
    tr.appendChild(buat("td", "redup", label));
    var tdA = buat("td", "mono", a), tdB = buat("td", "mono", b);
    if (lebih && nilaiA !== null && nilaiB !== null && nilaiA !== undefined && nilaiB !== undefined && nilaiA !== nilaiB) {
      var aMenang = lebih === "tinggi" ? nilaiA > nilaiB : nilaiA < nilaiB;
      (aMenang ? tdA : tdB).style.fontWeight = "700";
    }
    tr.appendChild(tdA); tr.appendChild(tdB);
    tb.appendChild(tr);
    return tr;
  }

  var tbEngine = null;

  function gambarRingkasan(d) {
    var w = el.ringkasan;
    w.hidden = false;
    w.textContent = "";
    w.appendChild(buat("h2", "judul-kartu", "Berdampingan" + (d.format !== "semua" ? " · format " + d.format : "")));
    var t = document.createElement("table");
    var thead = document.createElement("thead"), trh = document.createElement("tr");
    trh.appendChild(buat("th", null, ""));
    [d.a, d.b].forEach(function (p) {
      var th = document.createElement("th");
      var a = document.createElement("a");
      a.href = "/pemain/" + p.id;
      a.appendChild(teks(namaPemain(p)));
      th.appendChild(a);
      trh.appendChild(th);
    });
    thead.appendChild(trh); t.appendChild(thead);
    var tb = document.createElement("tbody");
    var A = d.a.gaya, B = d.b.gaya;

    baris(tb, "Elo terakhir", angka(d.a.elo_terakhir), angka(d.b.elo_terakhir), null);
    baris(tb, "Partai selesai", angka(A.partai), angka(B.partai), null);
    baris(tb, "Rentang tahun", (A.tahun_dari || "?") + "–" + (A.tahun_sampai || "?"), (B.tahun_dari || "?") + "–" + (B.tahun_sampai || "?"), null);
    baris(tb, "Skor", persen(A.skor_persen), persen(B.skor_persen), "tinggi", A.skor_persen, B.skor_persen);
    baris(tb, "Menang / remis / kalah", A.menang + " / " + A.remis + " / " + A.kalah, B.menang + " / " + B.remis + " / " + B.kalah, null);
    baris(tb, "Porsi remis", persen(A.remis_persen), persen(B.remis_persen), null);
    baris(tb, "Rata-rata panjang partai", angka(A.rata_langkah, 1) + " langkah", angka(B.rata_langkah, 1) + " langkah", null);
    baris(tb, "Menang cepat (≤ 30 langkah, dari kemenangan)", persen(A.menang_cepat_persen), persen(B.menang_cepat_persen), null);
    baris(tb, "Partai panjang (≥ 60 langkah)", persen(A.panjang_persen), persen(B.panjang_persen), null);
    baris(tb, "Rata-rata Elo lawan", angka(A.rata_elo_lawan) + " (" + angka(A.partai_ber_elo) + " partai)",
      angka(B.rata_elo_lawan) + " (" + angka(B.partai_ber_elo) + " partai)", null);
    baris(tb, "Skor melawan Elo lebih tinggi", persen(A.lawan_lebih_kuat.skor_persen) + " (" + angka(A.lawan_lebih_kuat.partai) + ")",
      persen(B.lawan_lebih_kuat.skor_persen) + " (" + angka(B.lawan_lebih_kuat.partai) + ")", "tinggi",
      A.lawan_lebih_kuat.skor_persen, B.lawan_lebih_kuat.skor_persen);
    baris(tb, "Skor melawan Elo lebih rendah", persen(A.lawan_lebih_lemah.skor_persen) + " (" + angka(A.lawan_lebih_lemah.partai) + ")",
      persen(B.lawan_lebih_lemah.skor_persen) + " (" + angka(B.lawan_lebih_lemah.partai) + ")", "tinggi",
      A.lawan_lebih_lemah.skor_persen, B.lawan_lebih_lemah.skor_persen);
    function pertama(g) {
      var lp = g.langkah_pertama;
      if (!lp.terindeks) return "—";
      return lp.langkah.slice(0, 3).map(function (x) { return "1." + x.san + " " + angka(x.persen, 0) + "%"; }).join(" · ");
    }
    baris(tb, "Langkah pertama sebagai Putih", pertama(A), pertama(B), null);
    function fmt(g) { return g.per_format.map(function (x) { return x[0] + " " + angka(x[1]); }).join(" · ") || "—"; }
    baris(tb, "Format", fmt(A), fmt(B), null);

    var sela = buat("tr");
    var tdSela = buat("td", "kecil redup", "Engine (hanya partai yang sudah dianalisis)");
    tdSela.colSpan = 3;
    tdSela.style.paddingTop = "12px";
    sela.appendChild(tdSela);
    tb.appendChild(sela);
    tbEngine = tb;
    var tunggu = baris(tb, "Akurasi", "memuat…", "memuat…", null);
    tunggu.className = "baris-engine";

    t.appendChild(tb);
    var gulir = buat("div", "gulir"); gulir.appendChild(t); w.appendChild(gulir);
    var c = buat("div", "kecil redup", d.catatan.join(" ") +
      " Format \"tanpa\" = PGN tanpa kontrol waktu (hampir semua partai TWIC), jadi saringan format jarang berguna untuk partai GM.");
    c.style.padding = "8px 14px";
    w.appendChild(c);
  }

  function gambarEngine(ra, rb) {
    if (!tbEngine) return;
    Array.prototype.slice.call(tbEngine.querySelectorAll(".baris-engine")).forEach(function (tr) { tr.remove(); });
    function tot(r) { return r && r.total && r.total.partai ? r.total : null; }
    var A = tot(ra), B = tot(rb);
    function cakupan(r) {
      if (!r || !r.cakupan || r.cakupan.partai_total === undefined) return "—";
      return angka(r.cakupan.partai_dianalisis) + " dari " + angka(r.cakupan.partai_total);
    }
    function nilai(x, k, d) { return x ? angka(x[k], d) : "—"; }
    var daftar = [
      ["Partai dianalisis", cakupan(ra), cakupan(rb), null],
      ["Akurasi", A ? angka(A.akurasi, 1) + "%" : "—", B ? angka(B.akurasi, 1) + "%" : "—", "tinggi", "akurasi"],
      ["Rata-rata kehilangan per langkah", A ? angka(A.rata_kehilangan, 1) + " cp" : "—", B ? angka(B.rata_kehilangan, 1) + " cp" : "—", "rendah", "rata_kehilangan"],
      ["Blunder per partai", nilai(A, "blunder_per_partai", 2), nilai(B, "blunder_per_partai", 2), "rendah", "blunder_per_partai"],
      ["Mistake per partai", nilai(A, "mistake_per_partai", 2), nilai(B, "mistake_per_partai", 2), "rendah", "mistake_per_partai"]
    ];
    daftar.forEach(function (x) {
      var tr = baris(tbEngine, x[0], x[1], x[2], A && B ? x[3] : null, A && x[4] ? A[x[4]] : null, B && x[4] ? B[x[4]] : null);
      tr.className = "baris-engine";
    });
    var condong = [ra, rb].some(function (r) { return r && r.cakupan && r.cakupan.condong_ke_kalah; });
    if (condong) {
      var tr = buat("tr"); tr.className = "baris-engine";
      var td = buat("td", "kecil redup", "Partai yang dianalisis condong ke partai kalah (kekalahan didahulukan), jadi angka engine di sini lebih buruk dari permainan sesungguhnya.");
      td.colSpan = 3; tr.appendChild(td); tbEngine.appendChild(tr);
    }
  }

  /* ---------- tampilan: pembukaan ---------- */

  function gambarPembukaan(d) {
    el.pembukaan.hidden = false;
    [["putih", el.pembukaanPutih, "Pembukaan sebagai Putih"], ["hitam", el.pembukaanHitam, "Pembukaan sebagai Hitam"]].forEach(function (x) {
      var warna = x[0], w = x[1];
      w.textContent = "";
      w.appendChild(buat("h2", "judul-kartu", x[2]));
      var peta = {};
      [["a", d.a.pembukaan[warna]], ["b", d.b.pembukaan[warna]]].forEach(function (y) {
        y[1].forEach(function (k) { (peta[k.keluarga] = peta[k.keluarga] || {})[y[0]] = k; });
      });
      var kunci = Object.keys(peta).sort(function (p, q) {
        function n(k) { return (peta[k].a ? peta[k].a.persen : 0) + (peta[k].b ? peta[k].b.persen : 0); }
        return n(q) - n(p);
      }).slice(0, PEMBUKAAN_MAKS);
      if (!kunci.length) { w.appendChild(buat("div", "kecil redup", "Belum ada partai ber-ECO.")); return; }
      var t = document.createElement("table");
      var thead = document.createElement("thead"), trh = document.createElement("tr");
      ["Keluarga ECO", "A: porsi · skor", "B: porsi · skor"].forEach(function (k) { trh.appendChild(buat("th", null, k)); });
      thead.appendChild(trh); t.appendChild(thead);
      var tb = document.createElement("tbody");
      kunci.forEach(function (k) {
        var tr = document.createElement("tr");
        tr.style.cursor = "default";
        var td0 = buat("td", null, k);
        if (peta[k].a && peta[k].b) { var s = buat("span", "kecil redup", " · keduanya"); td0.appendChild(s); }
        tr.appendChild(td0);
        ["a", "b"].forEach(function (s) {
          var v = peta[k][s];
          tr.appendChild(buat("td", "mono", v ? persen(v.persen) + " · " + persen(v.skor_persen) + " (" + angka(v.partai) + ")" : "—"));
        });
        tb.appendChild(tr);
      });
      t.appendChild(tb);
      var gulir = buat("div", "gulir"); gulir.appendChild(t); w.appendChild(gulir);
    });
  }

  /* ---------- tampilan: head-to-head ---------- */

  function hasilDariA(g) {
    if (g.hasil === "1/2-1/2") return "½";
    if (g.hasil !== "1-0" && g.hasil !== "0-1") return g.hasil;
    return (g.hasil === "1-0") === (g.warna_a === "putih") ? "1" : "0";
  }

  function gambarH2h(d) {
    var h = d.head_to_head, w = el.h2h;
    w.hidden = false;
    w.textContent = "";
    w.appendChild(buat("h2", "judul-kartu", "Partai di antara keduanya"));
    if (!h.total) {
      var k = buat("div", "kecil redup", "Belum ada partai di antara keduanya di database" +
        (d.format !== "semua" ? " untuk format " + d.format : "") + ".");
      k.style.padding = "0 14px 14px";
      w.appendChild(k);
      return;
    }
    var skor = buat("div", null, "");
    skor.style.padding = "0 14px 8px";
    skor.appendChild(buat("strong", null, d.a.nama + " " + angka(h.skor_a, 1).replace(",0", "") + " – " +
      angka(h.selesai - h.skor_a, 1).replace(",0", "") + " " + d.b.nama));
    skor.appendChild(teks("  (" + h.menang_a + " menang, " + h.remis + " remis, " + h.menang_b + " kalah dari sudut A · " +
      angka(h.total) + " partai)"));
    w.appendChild(skor);

    var t = document.createElement("table");
    var thead = document.createElement("thead"), trh = document.createElement("tr");
    ["Tanggal", "Turnamen", "A main", "Hasil A", "ECO", "Pembukaan", "Langkah"].forEach(function (x) { trh.appendChild(buat("th", null, x)); });
    thead.appendChild(trh); t.appendChild(thead);
    var tb = document.createElement("tbody");
    h.partai.forEach(function (g, i) {
      var tr = document.createElement("tr");
      if (i >= H2H_TAMPIL) tr.hidden = true;
      tr.addEventListener("click", function () { window.open("/partai/" + g.id, "_blank"); });
      tr.appendChild(buat("td", "mono", g.tanggal || "—"));
      tr.appendChild(buat("td", null, g.turnamen || "—"));
      tr.appendChild(buat("td", null, g.warna_a === "putih" ? "Putih" : "Hitam"));
      var hasil = hasilDariA(g);
      tr.appendChild(buat("td", "mono" + (hasil === "1" ? " klas-terbaik" : hasil === "0" ? " klas-blunder" : ""), hasil));
      tr.appendChild(buat("td", "mono", g.eco || "—"));
      tr.appendChild(buat("td", "kecil", g.pembukaan || ""));
      tr.appendChild(buat("td", "mono", g.jumlah_langkah || "—"));
      tb.appendChild(tr);
    });
    t.appendChild(tb);
    var gulir = buat("div", "gulir"); gulir.appendChild(t); w.appendChild(gulir);
    if (h.partai.length > H2H_TAMPIL) {
      var lagi = document.createElement("button");
      lagi.type = "button";
      lagi.style.margin = "8px 14px";
      lagi.appendChild(teks("tampilkan semua " + h.partai.length + " partai"));
      lagi.addEventListener("click", function () {
        Array.prototype.forEach.call(tb.children, function (tr) { tr.hidden = false; });
        lagi.remove();
      });
      w.appendChild(lagi);
    }
    if (h.dipotong) {
      var c = buat("div", "kecil redup", "Hanya " + h.partai.length + " partai terbaru yang ditampilkan; skor di atas dari seluruh " + angka(h.total) + " partai.");
      c.style.padding = "0 14px 8px";
      w.appendChild(c);
    }
  }

  /* ---------- mulai ---------- */

  pasangPencari(el.cariA, el.daftarA, "a");
  pasangPencari(el.cariB, el.daftarB, "b");
  el.format.addEventListener("change", muat);
  el.tukar.addEventListener("click", function () {
    var x = pilihan.a; pilihan.a = pilihan.b; pilihan.b = x;
    var v = el.cariA.value; el.cariA.value = el.cariB.value; el.cariB.value = v;
    muat();
  });

  var q = new URLSearchParams(window.location.search);
  if (q.get("format") && el.format.querySelector('option[value="' + CSS.escape(q.get("format")) + '"]')) el.format.value = q.get("format");
  ["a", "b"].forEach(function (k) {
    var id = parseInt(q.get(k) || "", 10);
    if (id > 0) pilihan[k] = { id: id, nama: "#" + id };
  });
  if (pilihan.a && !pilihan.b) el.cariB.focus();
  if (pilihan.a && pilihan.b) muat();
  else if (pilihan.a) {
    ambil("/api/pemain/" + pilihan.a.id).then(function (p) { el.cariA.value = p.nama; pilihan.a.nama = p.nama; })
      .catch(function (e) { galat(e.message); });
  }
})();
