/* Halaman ringkasan lintas partai: di mana poin hilang, dan kapan. */

(function () {
  "use strict";

  var el = {
    formCari: document.getElementById("form-cari"),
    cari: document.getElementById("cari-pemain"),
    hasilCari: document.getElementById("hasil-cari"),
    akun: document.getElementById("daftar-akun"),
    format: document.getElementById("format"),
    muat: document.getElementById("muat"),
    isi: document.getElementById("isi"),
    galat: document.getElementById("galat")
  };

  function teks(isi) {
    return document.createTextNode(isi === null || isi === undefined ? "" : String(isi));
  }
  function buat(tag, kelas, isi) {
    var e = document.createElement(tag);
    if (kelas) e.className = kelas;
    if (isi !== undefined) e.appendChild(teks(isi));
    return e;
  }
  function galat(p) { el.galat.textContent = p; el.galat.hidden = false; }
  function ambil(url) {
    return fetch(url).then(function (r) {
      if (!r.ok) throw new Error("Server menjawab " + r.status);
      return r.json();
    });
  }
  /* Angka gaya Indonesia: desimal koma, ribuan titik. Sebelumnya nilai dipakai
     apa adanya, jadi dalam satu kotak bisa muncul "15.1%" dan "54,9%". */
  function angka(v, sfx) {
    if (v === null || v === undefined) return "—";
    var n = Number(v);
    return (isNaN(n) ? v : n.toLocaleString("id-ID", { maximumFractionDigits: 1 })) + (sfx || "");
  }

  /* ---------- pilihan pemain ---------- */

  /* Satu kotak centang per pemain. Mengembalikan false kalau sudah ada. */
  function tambahPilihan(p, tercentang) {
    if (el.akun.querySelector('input[value="' + p.id + '"]')) return false;
    var lab = document.createElement("label");
    lab.style.marginRight = "10px";
    var cb = document.createElement("input");
    cb.type = "checkbox";
    cb.value = p.id;
    cb.checked = !!tercentang;
    lab.appendChild(cb);
    var nama = (p.gelar ? p.gelar + " " : "") + p.nama + " (" + p.jumlah_partai + ")";
    lab.title = nama;  /* kisi memotong nama panjang dengan "…" */
    lab.appendChild(teks(" " + nama));
    el.akun.appendChild(lab);
    return true;
  }

  /* Akun sendiri (web/akun-pribadi.js) dicentang otomatis dan selalu ada di
     daftar; sisanya 40 pemain terbanyak (GM) tidak tercentang supaya angka
     Ray tidak tercampur dengan angka GM. */
  function isiDaftarAkun() {
    Promise.all([AkunPribadi.muat(), ambil("/api/pemain?limit=40")]).then(function (r) {
      el.akun.textContent = "";
      r[0].forEach(function (p) { tambahPilihan({ id: p.id, nama: p.nama + (p.sumber ? " · " + p.sumber : ""), jumlah_partai: p.jumlah_partai }, true); });
      r[1].pemain.forEach(function (p) { tambahPilihan(p, false); });
      muat();
    }).catch(function (e) { galat("Gagal memuat daftar pemain: " + e.message); });
  }

  /* Pemain di luar 12 teratas (mis. GM dari TWIC) dicari lewat nama, lalu
     ditambahkan ke daftar centang tanpa tercentang; pengguna memilih sendiri. */
  function cariPemain() {
    var q = el.cari.value.trim();
    if (!q) return;
    el.hasilCari.textContent = "…";
    ambil("/api/pemain?cari=" + encodeURIComponent(q) + "&limit=30").then(function (d) {
      var baru = 0;
      d.pemain.forEach(function (p) { if (tambahPilihan(p, false)) baru++; });
      el.hasilCari.textContent = d.pemain.length === 0 ? "tidak ditemukan" : baru + " ditambahkan";
    }).catch(function (e) { galat("Gagal mencari pemain: " + e.message); });
  }

  function idTerpilih() {
    var hasil = [];
    el.akun.querySelectorAll("input:checked").forEach(function (cb) { hasil.push(cb.value); });
    return hasil;
  }

  /* ---------- tabel generik ---------- */

  function angkaID(n) { return (n || 0).toLocaleString("id-ID"); }
  function persenID(v) {
    return v === null || v === undefined ? "?" : Number(v).toLocaleString("id-ID", { maximumFractionDigits: 1 });
  }

  /* Skor sesungguhnya dari SELURUH partai (bukan cuma yang dianalisis), supaya
     ada pembanding jujur untuk angka yang condong. */
  function skorDariHasil(h) {
    if (!h) return null;
    var menang = h.menang || 0, remis = h.remis || 0, kalah = h.kalah || 0;
    var n = menang + remis + kalah;
    return n ? Math.round(1000 * (menang + remis / 2) / n) / 10 : null;
  }

  function tabel(judul, kolom, baris, catatan) {
    var kartu = buat("div", "kartu");
    kartu.style.marginBottom = "16px";
    kartu.appendChild(buat("h2", "judul-kartu", judul));
    if (catatan) {
      var c = buat("div", "kecil redup", catatan);
      c.style.padding = "0 14px 8px";
      kartu.appendChild(c);
    }
    var gulir = buat("div", "gulir");
    var t = document.createElement("table");
    var thead = document.createElement("thead");
    var trh = document.createElement("tr");
    kolom.forEach(function (k) { trh.appendChild(buat("th", null, k.label)); });
    thead.appendChild(trh);
    t.appendChild(thead);
    var tbody = document.createElement("tbody");
    baris.forEach(function (b) {
      var tr = document.createElement("tr");
      tr.style.cursor = "default";
      if (b.sampel_kecil) tr.title = "Sampel kecil — jangan ditarik kesimpulan";
      kolom.forEach(function (k) {
        var td = buat("td", k.kelas || null, k.nilai(b));
        if (b.sampel_kecil && k.kelas !== "nama") td.classList.add("redup");
        tr.appendChild(td);
      });
      tbody.appendChild(tr);
    });
    t.appendChild(tbody);
    gulir.appendChild(t);
    kartu.appendChild(gulir);
    return kartu;
  }

  var KOLOM_UMUM = [
    { label: "Partai", nilai: function (b) { return b.partai + (b.sampel_kecil ? " ⚠" : ""); }, kelas: "mono" },
    { label: "Skor", nilai: function (b) { return angka(b.skor_persen, "%"); }, kelas: "mono" },
    { label: "Akurasi", nilai: function (b) { return angka(b.akurasi, "%"); }, kelas: "mono" },
    { label: "Rata loss (cp)", nilai: function (b) { return angka(b.rata_kehilangan); }, kelas: "mono" },
    { label: "?? / partai", nilai: function (b) { return angka(b.blunder_per_partai); }, kelas: "mono" },
    { label: "? / partai", nilai: function (b) { return angka(b.mistake_per_partai); }, kelas: "mono" }
  ];

  /* Ringkasan hanya menghitung partai yang sudah dianalisis engine. Supaya
     tidak disangka datanya sedikit, tampilkan berapa dari total partai pemain
     terpilih yang sudah dianalisis. */
  function gambarCakupan(profil) {
    var ada = profil.filter(Boolean);
    if (!ada.length) return null;
    var dianalisis = 0, total = 0;
    ada.forEach(function (p) { dianalisis += p.partai_dianalisis || 0; total += (p.total && p.total.partai) || 0; });
    var w = buat("div", "kecil redup");
    w.style.marginTop = "8px";
    w.appendChild(teks("Dianalisis " + dianalisis.toLocaleString("id-ID") + " dari " + total.toLocaleString("id-ID") + " partai " +
      ada.map(function (p) { return p.nama; }).join(", ") + ". "));
    if (dianalisis < total) w.appendChild(teks("Sisanya belum dianalisis engine (python analisis_partai.py); ringkasan ini hanya menghitung yang sudah."));
    return w;
  }

  function gambar(d, profil) {
    el.isi.textContent = "";
    if (!d.total || d.total.partai === 0) {
      el.isi.appendChild(buat("div", "pesan",
        "Belum ada partai yang dianalisis untuk pemain ini. " +
        "Jalankan: python analisis_partai.py --gelar-lawan GM  (atau saringan lain)"));
      return;
    }

    /* total */
    var tot = buat("div", "kartu info-partai");
    tot.style.marginBottom = "16px";

    /* Peringatan bias DULU, baru angkanya: partai yang dianalisis dipilih dari
       yang kalah lebih dulu, jadi "Skor" di bawah bukan skor sebenarnya. */
    var c = d.cakupan || {};
    if (c.condong_ke_kalah) {
      var p = buat("div", "galat");
      p.style.margin = "12px 14px 0";
      p.appendChild(buat("div", null,
        "Angka di bawah dihitung dari " + angkaID(c.partai_dianalisis) + " partai yang sudah dianalisis " +
        "(" + persenID(c.persen_dianalisis) + "% dari " + angkaID(c.partai_total) + "), dan partai kalah " +
        "sengaja didahulukan: " + persenID(c.porsi_kalah_dianalisis) + "% di antaranya kalah, padahal dari " +
        "seluruh partai cuma " + persenID(c.porsi_kalah_semua) + "%."));
      p.appendChild(buat("div", "kecil",
        "Jadi Skor dan Akurasi di bawah lebih buruk daripada permainanmu sebenarnya. Yang tetap sah dibaca: " +
        "pola kesalahannya — di fase mana, lawan sekuat apa, pembukaan mana."));
      tot.appendChild(p);
    }

    var kotak = buat("div", "akurasi-kotak");
    kotak.style.gridTemplateColumns = "repeat(4, 1fr)";
    var skorAsli = skorDariHasil(c.hasil_semua);
    [["Partai dianalisis", d.total.partai,
      c.partai_total ? "dari " + angkaID(c.partai_total) + " (" + persenID(c.persen_dianalisis) + "%)" : ""],
     ["Skor di partai teranalisis", angka(d.total.skor_persen, "%"),
      skorAsli === null ? "" : "seluruh partai: " + persenID(skorAsli) + "%"],
     ["Akurasi rata-rata", angka(d.total.akurasi, "%"), "di partai teranalisis saja"],
     ["Rata loss per langkah", angka(d.total.rata_kehilangan, " cp"), ""]
    ].forEach(function (x) {
      var k = buat("div");
      k.appendChild(buat("div", "label", x[0]));
      k.appendChild(buat("div", "angka-besar", x[1]));
      if (x[2]) k.appendChild(buat("div", "kecil redup", x[2]));
      kotak.appendChild(k);
    });
    tot.appendChild(kotak);
    var cakupan = gambarCakupan(profil || []);
    if (cakupan) tot.appendChild(cakupan);
    el.isi.appendChild(tot);

    /* per fase */
    el.isi.appendChild(tabel("Per fase permainan", [
      { label: "Fase", nilai: function (b) { return b.fase; }, kelas: "nama" },
      { label: "Langkah", nilai: function (b) { return b.langkah; }, kelas: "mono" },
      { label: "Rata loss (cp)", nilai: function (b) { return b.rata_kehilangan; }, kelas: "mono" },
      { label: "??", nilai: function (b) { return b.blunder; }, kelas: "mono klas-blunder" },
      { label: "?", nilai: function (b) { return b.mistake; }, kelas: "mono klas-mistake" },
      { label: "?!", nilai: function (b) { return b.inaccuracy; }, kelas: "mono klas-inaccuracy" }
    ], d.per_fase, "pembukaan = langkah 1–12, tengah = 13–40, akhir = 41+ (pembagian kasar berdasarkan nomor langkah)"));

    /* per kekuatan lawan */
    el.isi.appendChild(tabel("Per kekuatan lawan (selisih rating lawan − rating sendiri)",
      [{ label: "Lawan", nilai: function (b) { return b.nama; }, kelas: "nama" }]
        .concat(KOLOM_UMUM)
        .concat([
          { label: "Kesalahan besar pertama rata-rata di langkah", nilai: function (b) { return angka(b.kesalahan_pertama_rata_langkah); }, kelas: "mono" },
          { label: "Partai tanpa ??/?", nilai: function (b) { return b.partai_tanpa_kesalahan_besar; }, kelas: "mono" }
        ]),
      d.per_kekuatan_lawan,
      "Kolom terakhir menjawab: apakah kesalahan muncul lebih awal saat lawan lebih kuat?"));

    /* per pembukaan */
    el.isi.appendChild(tabel("Per pembukaan (15 terbanyak)",
      [{ label: "Pembukaan", nilai: function (b) { return b.nama; }, kelas: "nama" }].concat(KOLOM_UMUM),
      d.per_pembukaan));

    var cat = buat("div", "kecil redup");
    d.catatan.forEach(function (c) { cat.appendChild(buat("div", null, "· " + c)); });
    el.isi.appendChild(cat);
  }

  function muat() {
    var ids = idTerpilih();
    el.galat.hidden = true;
    if (ids.length === 0) {
      el.isi.textContent = "";
      el.isi.appendChild(buat("div", "pesan", "Pilih minimal satu pemain"));
      return;
    }
    var p = new URLSearchParams();
    ids.forEach(function (id) { p.append("pemain_id", id); });
    if (el.format && el.format.value !== "semua") p.set("format", el.format.value);
    Promise.all([ambil("/api/ringkasan?" + p.toString())].concat(ids.map(function (id) {
      return ambil("/api/pemain/" + id).catch(function () { return null; });
    }))).then(function (r) { gambar(r[0], r.slice(1)); })
      .catch(function (e) { galat("Gagal memuat ringkasan: " + e.message); });
  }

  el.muat.addEventListener("click", muat);
  if (el.format) el.format.addEventListener("change", muat);
  el.formCari.addEventListener("submit", function (ev) { ev.preventDefault(); cariPemain(); });
  isiDaftarAkun();
})();
