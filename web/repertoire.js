/* Repertoire vs GM: telusuri garis pembukaan yang paling sering dimainkan
   akun pribadi (cabang terbanyak, sampai kedalaman tertentu) dan di setiap
   giliran sendiri bandingkan langkah yang dipakai dengan praktik GM di posisi
   yang sama (/api/posisi sumber=gm). Yang ditandai:

     sama        : langkahmu = langkah utama GM
     dipakai GM  : langkahmu dimainkan GM (≥ 5 % dari partai GM di posisi itu), bukan yang utama
     jarang      : GM hampir tidak memainkannya (< 5 %) atau tidak pernah
     skor buruk  : skormu < 45 % dengan ≥ 10 partai, apa pun praktik GM

   Distribusi langkah sendiri diambil dari /api/posisi dengan pemain_id + warna
   (Permintaan API #5), semua sumber. Semua perhitungan di browser. */

(function () {
  "use strict";

  var el = {
    galat: document.getElementById("galat"),
    akun: document.getElementById("akun"),
    warna: document.getElementById("warna"),
    kedalaman: document.getElementById("kedalaman"),
    cabang: document.getElementById("cabang"),
    minimal: document.getElementById("minimal"),
    status: document.getElementById("status"),
    ikhtisar: document.getElementById("ikhtisar"),
    kartuTindakan: document.getElementById("kartu-tindakan"),
    tindakan: document.getElementById("tindakan"),
    baris: document.getElementById("baris")
  };

  var akunSemua = [];
  var permintaanKe = 0;
  var AMBANG_JARANG = 0.05, AMBANG_SKOR = 45, MIN_SKOR = 10;

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

  /* ---------- data posisi ---------- */

  var cacheGm = {};
  function posisiGm(fen) {
    if (cacheGm[fen]) return Promise.resolve(cacheGm[fen]);
    return ambil("/api/posisi?fen=" + encodeURIComponent(fen) + "&sumber=gm").then(function (d) { cacheGm[fen] = d; return d; });
  }

  /* Distribusi langkah di partai akun (digabung kalau semua akun).
     warnaAkun = warna yang dimainkan akun di partai-partai itu. */
  function posisiSaya(fen, daftarAkun, warnaAkun) {
    return Promise.all(daftarAkun.map(function (a) {
      return ambil("/api/posisi?fen=" + encodeURIComponent(fen) + "&sumber=semua&pemain_id=" + a.id + "&warna=" + warnaAkun)
        .catch(function () { return { total: 0, langkah: [] }; });
    })).then(function (hasil) {
      var peta = {}, total = 0;
      hasil.forEach(function (d) {
        total += d.total || 0;
        (d.langkah || []).forEach(function (l) {
          var p = peta[l.san] || (peta[l.san] = { san: l.san, uci: l.uci, n: 0, menang_putih: 0, remis: 0, menang_hitam: 0 });
          p.n += l.n; p.menang_putih += l.menang_putih; p.remis += l.remis; p.menang_hitam += l.menang_hitam;
        });
      });
      var langkah = Object.keys(peta).map(function (k) { return peta[k]; }).sort(function (a, b) { return b.n - a.n; });
      return { total: total, langkah: langkah };
    });
  }

  function skorSaya(l, warnaAkun) {
    var selesai = l.menang_putih + l.remis + l.menang_hitam;
    if (!selesai) return null;
    var poinPutih = l.menang_putih + l.remis / 2;
    var s = 100 * (warnaAkun === "putih" ? poinPutih : selesai - poinPutih) / selesai;
    return Math.round(s * 10) / 10;
  }

  function fenSetelah(fen, san) {
    var c = new Chess(fen);
    return c.move(san) ? c.fen() : null;
  }

  function labelGaris(sanList) {
    var t = [];
    for (var i = 0; i < sanList.length; i++) {
      if (i % 2 === 0) t.push((i / 2 + 1) + ".");
      else if (i === 0) t.push("1…");
      t.push(sanList[i]);
    }
    return t.join(" ");
  }

  /* ---------- penelusuran ---------- */

  function telusuri() {
    var ke = ++permintaanKe;
    var daftarAkun = el.akun.value === "semua" ? akunSemua : akunSemua.filter(function (a) { return String(a.id) === el.akun.value; });
    var warnaAkun = el.warna.value;
    var kedalaman = parseInt(el.kedalaman.value, 10);
    var cabang = parseInt(el.cabang.value, 10);
    var minimal = parseInt(el.minimal.value, 10);
    el.galat.hidden = true;
    el.baris.textContent = "";
    el.ikhtisar.textContent = "";
    var hasil = [];
    var dikunjungi = 0;

    /* antrean lebar-dulu supaya garis pendek muncul dulu */
    var antre = [{ fen: new Chess().fen(), garis: [], ply: 0 }];

    function proses() {
      if (ke !== permintaanKe) return Promise.resolve();
      if (!antre.length) return Promise.resolve();
      var node = antre.shift();
      dikunjungi++;
      el.status.textContent = "menelusuri posisi " + dikunjungi + " · antrean " + antre.length + "…";
      var giliranSaya = (node.ply % 2 === 0) === (warnaAkun === "putih");
      return posisiSaya(node.fen, daftarAkun, warnaAkun).then(function (saya) {
        if (!saya.total) return;
        var terbanyak = saya.langkah.filter(function (l) { return l.n >= minimal; }).slice(0, cabang);
        if (!terbanyak.length) return;
        if (!giliranSaya) {
          terbanyak.forEach(function (l) {
            var anak = fenSetelah(node.fen, l.san);
            if (anak && node.ply + 1 < kedalaman) antre.push({ fen: anak, garis: node.garis.concat([l.san]), ply: node.ply + 1 });
          });
          return;
        }
        return posisiGm(node.fen).then(function (gm) {
          var utama = gm.langkah.length ? gm.langkah[0] : null;
          terbanyak.forEach(function (l) {
            var diGm = null;
            gm.langkah.forEach(function (g) { if (g.san === l.san) diGm = g; });
            var porsiGm = diGm && gm.total ? diGm.n / gm.total : 0;
            var skor = skorSaya(l, warnaAkun);
            var status;
            if (skor !== null && skor < AMBANG_SKOR && l.n >= MIN_SKOR) status = "skor";
            else if (utama && utama.san === l.san) status = "sama";
            else if (porsiGm >= AMBANG_JARANG) status = "dipakai";
            else status = "jarang";
            hasil.push({
              fen: node.fen, garis: node.garis, san: l.san, n: l.n, porsi: saya.total ? l.n / saya.total : 0, skor: skor,
              gmN: diGm ? diGm.n : 0, gmPorsi: porsiGm, gmSkor: diGm ? (warnaAkun === "putih" ? diGm.skor_putih : (diGm.skor_putih === null ? null : Math.round((100 - diGm.skor_putih) * 10) / 10)) : null,
              utama: utama ? { san: utama.san, porsi: gm.total ? utama.n / gm.total : 0, skor: warnaAkun === "putih" ? utama.skor_putih : (utama.skor_putih === null ? null : Math.round((100 - utama.skor_putih) * 10) / 10) } : null,
              gmTotal: gm.total, status: status
            });
            var anak = fenSetelah(node.fen, l.san);
            if (anak && node.ply + 1 < kedalaman) antre.push({ fen: anak, garis: node.garis.concat([l.san]), ply: node.ply + 1 });
          });
        });
      }).catch(function () { /* posisi yang gagal dilewati */ }).then(proses);
    }

    proses().then(function () {
      if (ke !== permintaanKe) return;
      el.status.textContent = "";
      gambar(hasil, warnaAkun, daftarAkun);
    });
  }

  /* ---------- tampilan ---------- */

  var LABEL = { sama: "sama dengan GM", dipakai: "dipakai GM", jarang: "jarang di GM", skor: "skor buruk" };
  var BOBOT = { skor: 0, jarang: 1, dipakai: 2, sama: 3 };

  function persen(p) { return Math.round(100 * p) + "%"; }

  /* Daftar tindakan: penyimpangan yang paling layak diperbaiki — skor buruk
     atau jarang di GM, punya cukup partai, dan ada langkah GM yang jelas
     lebih baik. Diurutkan menurut jumlah partai supaya yang paling sering
     dimainkan lebih dulu. */
  function gambarTindakan(hasil, warnaAkun, daftarAkun) {
    var calon = hasil.filter(function (h) {
      if (h.status !== "skor" && h.status !== "jarang") return false;
      if (h.n < MIN_SKOR) return false;                     // cukup sering kamu mainkan
      if (!h.utama || h.utama.san === h.san) return false;
      if (h.utama.skor === null || h.skor === null) return false;
      if (h.skor >= 50) return false;                       // yang skornya sudah di atas imbang tidak diusik
      if (h.gmTotal < 20) return false;                     // sampel GM terlalu kecil untuk dijadikan saran
      return h.utama.skor - h.skor >= 5;                    // langkah GM setidaknya 5 poin lebih baik
    }).sort(function (a, b) { return b.n - a.n; }).slice(0, 5);

    el.tindakan.textContent = "";
    if (!calon.length) { el.kartuTindakan.hidden = true; return; }
    calon.forEach(function (h) {
      var baris = buat("div", "tersimpan-baris");
      var kiri = buat("div", "tersimpan-isi");
      var judul = buat("div", "tersimpan-judul");
      judul.appendChild(teks((h.garis.length ? labelGaris(h.garis) + " " : "posisi awal: ") + "ganti "));
      judul.appendChild(buat("span", "klas-blunder", h.san));
      judul.appendChild(teks(" dengan "));
      judul.appendChild(buat("span", "klas-terbaik", h.utama.san));
      kiri.appendChild(judul);
      kiri.appendChild(buat("div", "kecil redup",
        h.san + ": " + h.n + " partai, skormu " + h.skor + "%" +
        " · " + h.utama.san + ": dipakai GM " + persen(h.utama.porsi) + ", skor " + h.utama.skor + "%" +
        (h.gmTotal ? " (" + h.gmTotal.toLocaleString("id-ID") + " partai GM di posisi ini)" : "")));
      baris.appendChild(kiri);
      var kanan = buat("div", "tautan-baris");
      kanan.style.marginTop = "0";
      var q = "/jelajah?fen=" + encodeURIComponent(h.fen);
      var a = document.createElement("a");
      a.href = q + "&sumber=gm";
      a.title = "Lihat langkah GM dari posisi ini";
      a.appendChild(teks("praktik GM →"));
      kanan.appendChild(a);
      var b = document.createElement("a");
      b.href = q + (daftarAkun.length === 1 ? "&pemain_id=" + daftarAkun[0].id + "&warna=" + warnaAkun : "");
      b.title = "Partaimu sendiri dari posisi ini";
      b.appendChild(teks("partaiku →"));
      kanan.appendChild(b);
      baris.appendChild(kanan);
      el.tindakan.appendChild(baris);
    });
    el.kartuTindakan.hidden = false;
  }

  function gambar(hasil, warnaAkun, daftarAkun) {
    var hitung = { sama: 0, dipakai: 0, jarang: 0, skor: 0 };
    hasil.forEach(function (h) { hitung[h.status]++; });
    el.ikhtisar.style.gridTemplateColumns = "repeat(4, 1fr)";
    ["skor", "jarang", "dipakai", "sama"].forEach(function (s) {
      var k = buat("div");
      k.appendChild(buat("div", "label", LABEL[s]));
      k.appendChild(buat("div", "angka-besar status-" + s, hitung[s]));
      el.ikhtisar.appendChild(k);
    });

    gambarTindakan(hasil, warnaAkun, daftarAkun);

    el.baris.textContent = "";
    if (!hasil.length) {
      var tr0 = document.createElement("tr");
      var td0 = buat("td", "pesan kecil", "Tidak ada garis dengan partai sebanyak itu. Turunkan minimal partai.");
      td0.colSpan = 8; tr0.appendChild(td0); el.baris.appendChild(tr0);
      return;
    }
    hasil.sort(function (a, b) { return BOBOT[a.status] - BOBOT[b.status] || b.n - a.n; });
    hasil.forEach(function (h) {
      var tr = document.createElement("tr");
      tr.appendChild(buat("td", "kecil", h.garis.length ? labelGaris(h.garis) : "posisi awal"));
      tr.appendChild(buat("td", "mono", h.san));
      var tdN = buat("td", "mono", h.n);
      tdN.appendChild(buat("span", "redup kecil", " " + persen(h.porsi)));
      tr.appendChild(tdN);
      tr.appendChild(buat("td", "mono " + (h.skor !== null && h.skor < AMBANG_SKOR ? "klas-blunder" : ""), h.skor === null ? "—" : h.skor + "%"));
      var tdG = buat("td", "mono kecil", h.gmN ? h.gmN.toLocaleString("id-ID") + " (" + persen(h.gmPorsi) + ")" + (h.gmSkor !== null ? " · " + h.gmSkor + "%" : "") : "—");
      tr.appendChild(tdG);
      var tdU = buat("td", "mono kecil", h.utama ? h.utama.san + " " + persen(h.utama.porsi) + (h.utama.skor !== null ? " · " + h.utama.skor + "%" : "") : "—");
      if (h.utama && h.gmTotal < 20) { var w = buat("span", "redup", " ⚠"); w.title = "hanya " + h.gmTotal + " partai GM di posisi ini"; tdU.appendChild(w); }
      tdU.title = h.gmTotal ? h.gmTotal.toLocaleString("id-ID") + " partai GM di posisi ini" : "tidak ada partai GM di posisi ini";
      tr.appendChild(tdU);
      var tdS = document.createElement("td");
      tdS.appendChild(buat("span", "chip status-" + h.status, LABEL[h.status]));
      tr.appendChild(tdS);
      var tdA = buat("td", "kecil"); tdA.style.whiteSpace = "nowrap";
      var a = document.createElement("a");
      a.href = "/jelajah?fen=" + encodeURIComponent(h.fen) + (daftarAkun.length === 1 ? "&pemain_id=" + daftarAkun[0].id + "&warna=" + warnaAkun : "");
      a.title = "Buka posisi ini di penjelajah"; a.appendChild(teks("jelajah →")); tdA.appendChild(a);
      tr.appendChild(tdA);
      tr.style.cursor = "default";
      el.baris.appendChild(tr);
    });
  }

  /* ---------- mulai ---------- */

  var q = new URLSearchParams(window.location.search);
  ["warna", "kedalaman", "cabang", "minimal"].forEach(function (k) {
    if (q.get(k) && el[k].querySelector('option[value="' + q.get(k) + '"]')) el[k].value = q.get(k);
  });

  AkunPribadi.muat().then(function (akun) {
    akunSemua = akun;
    el.akun.textContent = "";
    var semua = document.createElement("option");
    semua.value = "semua"; semua.textContent = "semua akun (" + akun.length + ")"; el.akun.appendChild(semua);
    akun.forEach(function (p) {
      var o = document.createElement("option"); o.value = p.id; o.textContent = p.nama + (p.sumber ? " · " + p.sumber : ""); el.akun.appendChild(o);
    });
    if (q.get("akun") && el.akun.querySelector('option[value="' + q.get("akun") + '"]')) el.akun.value = q.get("akun");
    if (!akun.length) throw new Error("Tidak ada akun pribadi di database.");
    telusuri();
  }).catch(function (e) { galat(e.message); });

  [el.akun, el.warna, el.kedalaman, el.cabang, el.minimal].forEach(function (s) { s.addEventListener("change", telusuri); });
})();
