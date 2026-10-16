/* Mentor: belajar dari satu GM (RENCANA §16, 16 Okt 2026).

   GET /api/mentor?gm=<id> memberi empat minggu, masing-masing satu tema
   (pembukaan repertoire, konversi keunggulan, tekanan posisional, teknik
   endgame) dengan partai GM yang dipilih menurut kriteria terukur dan
   alasannya. Urutan minggu = kesalahan berlebihmu terhadap GM itu.

   Tiap partai bisa dibuka di halaman partai, atau dimainkan di Tebak GM
   (memegang bidak GM dan menebak langkahnya). Pilihan GM disimpan di alamat
   (?gm=), jadi kurikulum bisa dibuka lagi minggu depan. */

(function () {
  "use strict";

  var el = {
    galat: document.getElementById("galat"),
    status: document.getElementById("status"),
    cari: document.getElementById("cari-gm"),
    daftar: document.getElementById("daftar-gm"),
    lawan: document.getElementById("lawan"),
    saran: document.getElementById("saran"),
    saranGm: document.getElementById("saran-gm"),
    ringkas: document.getElementById("ringkas"),
    minggu: document.getElementById("minggu")
  };

  var gmId = null;
  var permintaanKe = 0;

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
  function angka(n, d) {
    if (n === null || n === undefined) return "—";
    return Number(n).toLocaleString("id-ID", { minimumFractionDigits: d || 0, maximumFractionDigits: d || 0 });
  }
  function tautan(href, isi, judul) {
    var a = document.createElement("a");
    a.href = href;
    a.target = "_blank";
    if (judul) a.title = judul;
    a.appendChild(teks(isi));
    return a;
  }

  /* ---------- pilih GM ---------- */

  function labelOpsi(p) {
    return p.nama + (p.gelar ? " · " + p.gelar : "") + " · " + angka(p.jumlah_partai) + " partai #" + p.id;
  }

  function pilih(id) {
    gmId = id;
    try { history.replaceState(null, "", "?gm=" + id + (el.lawan.value !== "ketentuan" ? "&lawan=" + el.lawan.value : "")); } catch (e) { /* tetap jalan */ }
    muat();
  }

  var tunda = null, terakhir = "";
  el.cari.addEventListener("input", function () {
    var m = /#(\d+)$/.exec(el.cari.value);
    if (m) {
      el.cari.value = el.cari.value.split(" · ")[0];
      pilih(parseInt(m[1], 10));
      return;
    }
    var q = el.cari.value.trim();
    if (q.length < 2 || q === terakhir) return;
    clearTimeout(tunda);
    tunda = setTimeout(function () {
      terakhir = q;
      ambil("/api/pemain?pribadi=false&limit=15&cari=" + encodeURIComponent(q)).then(function (d) {
        el.daftar.textContent = "";
        d.pemain.forEach(function (p) {
          var o = document.createElement("option");
          o.value = labelOpsi(p);
          el.daftar.appendChild(o);
        });
      }).catch(function (e) { galat(e.message); });
    }, 250);
  });
  el.lawan.addEventListener("change", function () { if (gmId) pilih(gmId); });

  /* Saran: pemain dengan partai terbanyak di database (GM teratas). */
  function isiSaran() {
    return ambil("/api/pemain?pribadi=false&limit=10").then(function (d) {
      el.saranGm.textContent = "";
      d.pemain.forEach(function (p, i) {
        if (i) el.saranGm.appendChild(teks(" · "));
        var a = document.createElement("a");
        a.href = "?gm=" + p.id;
        a.appendChild(teks(p.nama));
        a.addEventListener("click", function (ev) { ev.preventDefault(); el.cari.value = p.nama; pilih(p.id); });
        el.saranGm.appendChild(a);
      });
    });
  }

  /* ---------- muat & gambar ---------- */

  function muat() {
    var ke = ++permintaanKe;
    el.galat.hidden = true;
    el.status.textContent = "menyusun kurikulum… (pertama kali beberapa detik)";
    el.ringkas.hidden = true;
    el.minggu.textContent = "";
    ambil("/api/mentor?gm=" + gmId + "&lawan=" + el.lawan.value).then(function (d) {
      if (ke !== permintaanKe) return;
      el.status.textContent = "";
      el.cari.value = d.gm.nama;
      gambar(d);
    }).catch(function (e) { if (ke === permintaanKe) { galat(e.message); el.status.textContent = ""; } });
  }

  function gambar(d) {
    var w = el.ringkas;
    w.hidden = false;
    w.textContent = "";
    w.appendChild(buat("h2", "judul-kartu", "Kurikulum dari " + d.gm.nama + (d.gm.gelar ? " (" + d.gm.gelar + ")" : "")));
    var ket = buat("div", "kecil redup",
      angka(d.ukuran_gm.partai) + " partai " + d.gm.nama + " dan " + angka(d.ukuran_saya.partai) +
      " partaimu yang sudah dianalisis. Keunggulan yang gagal dimenangkan: kamu " +
      angka(d.ukuran_saya.konversi.persen_gagal, 1) + "% dari " + angka(d.ukuran_saya.konversi.pernah_unggul) +
      " partai, " + d.gm.nama + " " + angka(d.ukuran_gm.konversi.persen_gagal, 1) + "% dari " +
      angka(d.ukuran_gm.konversi.pernah_unggul) + ".");
    ket.style.padding = "0 14px 6px";
    w.appendChild(ket);
    if (!d.cukup) {
      var k = buat("div", "kecil", "Partai " + d.gm.nama + " yang sudah dianalisis kurang dari " + d.ambang.min_partai_gm +
        "; kurikulumnya akan terlalu tipis. Pilih GM lain, atau analisis partainya dulu di halaman Mesin.");
      k.style.padding = "0 14px 14px";
      w.appendChild(k);
      return;
    }
    (d.catatan || []).forEach(function (c) {
      var x = buat("div", "kecil redup", c);
      x.style.padding = "0 14px 6px";
      w.appendChild(x);
    });

    d.minggu.forEach(function (m) {
      var kartu = buat("div", "kartu");
      kartu.style.marginTop = "16px";
      kartu.appendChild(buat("h2", "judul-kartu", "Minggu " + m.minggu + ": " + m.nama));
      var ukur = buat("div", "kecil", "");
      ukur.style.padding = "0 14px 8px";
      ukur.appendChild(buat("span", "redup", m.ember + ": "));
      ukur.appendChild(buat("strong", null, "kamu " + angka(m.saya, 2) + " kesalahan per 100 langkah"));
      ukur.appendChild(teks(" · " + d.gm.nama.split(",")[0] + " " + angka(m.gm, 2) + " · " +
        angka(m.porsi_langkah_saya, 1) + "% langkahmu ada di sini · kesalahan berlebih " +
        angka(m.berlebih, 2) + " per 100 langkahmu"));
      kartu.appendChild(ukur);

      if (!m.partai.length) {
        var kos = buat("div", "kecil redup", "Tidak ada partai " + d.gm.nama + " yang memenuhi kriteria tema ini.");
        kos.style.padding = "0 14px 14px";
        kartu.appendChild(kos);
        el.minggu.appendChild(kartu);
        return;
      }
      var t = document.createElement("table");
      var thead = document.createElement("thead"), trh = document.createElement("tr");
      ["Partai", "Tahun", "Kenapa dipilih", ""].forEach(function (k) { trh.appendChild(buat("th", null, k)); });
      thead.appendChild(trh); t.appendChild(thead);
      var tb = document.createElement("tbody");
      m.partai.forEach(function (p) {
        var tr = document.createElement("tr");
        tr.style.cursor = "default";
        var td0 = buat("td");
        td0.appendChild(tautan("/partai/" + p.partai_id,
          (p.warna_gm === "putih" ? "Putih" : "Hitam") + " vs " + p.lawan + (p.elo_lawan ? " (" + p.elo_lawan + ")" : ""),
          (p.turnamen || "") + " · " + p.hasil + " · " + (p.jumlah_langkah || "?") + " langkah"));
        tr.appendChild(td0);
        tr.appendChild(buat("td", "mono", (p.tanggal || "").slice(0, 4)));
        tr.appendChild(buat("td", "kecil", p.alasan));
        var tdT = buat("td", "kecil");
        tdT.appendChild(tautan("/static/tebak.html?pemain_id=" + d.gm.id + "&partai_id=" + p.partai_id,
          "tebak langkahnya →", "Pegang bidak " + d.gm.nama + " di partai ini dan tebak tiap langkahnya"));
        tr.appendChild(tdT);
        tb.appendChild(tr);
      });
      t.appendChild(tb);
      var gulir = buat("div", "gulir"); gulir.appendChild(t); kartu.appendChild(gulir);
      var kand = buat("div", "kecil redup", angka(m.kandidat) + " partai " + d.gm.nama.split(",")[0] +
        " memenuhi kriteria tema ini; yang tampil lawan terkuat, paling banyak dua per lawan.");
      kand.style.padding = "6px 14px 12px";
      kartu.appendChild(kand);
      el.minggu.appendChild(kartu);
    });
  }

  /* ---------- mulai ---------- */

  var q = new URLSearchParams(window.location.search);
  if (q.get("lawan") && el.lawan.querySelector('option[value="' + CSS.escape(q.get("lawan")) + '"]')) el.lawan.value = q.get("lawan");
  isiSaran().catch(function (e) { galat(e.message); });
  var awal = parseInt(q.get("gm") || "", 10);
  if (awal > 0) pilih(awal);
})();
