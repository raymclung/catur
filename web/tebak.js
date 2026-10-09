/* Tebak langkah GM: Ray memegang bidak GM di partai aslinya dan menebak tiap
   langkahnya. Tujuan awal proyek ini belajar gaya dan langkah GM; halaman
   Latihan hanya memakai partai sendiri, jadi ini jalan pertama dari database
   partai GM ke latihan harian.

   Hanya memakai endpoint yang sudah ada:
     /api/pemain             daftar GM (terbanyak partai)
     /api/partai             partai acak GM (sumber=gm, warna, hasil)
     /api/partai/{id}        PGN
     /api/partai/{id}/analisis  label langkah GM dari analisis tersimpan
     /api/evaluasi           menilai langkah yang BERBEDA dari GM

   Kenapa langkah yang berbeda dinilai ulang lewat /api/evaluasi, bukan dari
   analisis tersimpan: analisis hanya menyimpan evaluasi langkah yang benar-
   benar dimainkan (dan langkah terbaik engine), bukan langkah lain. Posisi
   sebelum dan sesudah langkahmu dinilai engine yang SAMA supaya selisihnya
   sebanding; mencampur depth 10 tersimpan dengan 1,5 detik server akan
   menghasilkan selisih palsu. Risiko yang diterima: 1,5 detik lebih dangkal
   dari analisis partai, jadi label "bagus/meleset" di sini perkiraan.

   Ambang klasifikasi disalin dari src/catur_analysis/partai.py
   (klasifikasikan, aturan Lichess). Kalau ambang di sana berubah, ubah juga
   di sini. */

(function () {
  "use strict";

  var SKOR_MAT = 100000;
  var BATAS_EVAL_CP = 1000;
  var MAT_RINGAN_CP = 999;
  var MAT_SEDANG_CP = 700;
  var AMBANG_BLUNDER = 0.3, AMBANG_MISTAKE = 0.2, AMBANG_INACCURACY = 0.1;

  var MIN_LANGKAH = 20;      // partai lebih pendek jarang berisi rencana; diambil ulang
  var MAKS_COBA = 6;
  var JEDA_LAWAN_MS = 450;

  var TANDA = { sama: "✓", bagus: "○", kurang: "~", meleset: "✗", takDinilai: "?", dilihat: "·" };
  var NAMA = {
    sama: "sama dengan GM", bagus: "beda, tapi bagus", kurang: "kurang tepat",
    meleset: "meleset", takDinilai: "tak dinilai", dilihat: "dilihat tanpa menebak"
  };
  var NAMA_KLAS = {
    terbaik: "terbaik", baik: "baik", inaccuracy: "kurang tepat (?!)",
    mistake: "kesalahan (?)", blunder: "blunder (??)"
  };

  var el = {};
  ["galat", "status", "petunjuk", "tunjukkan", "partai-lain", "gm", "warna", "hasil", "mulai",
   "skor", "nomor", "info", "kartu-nilai", "nilai", "riwayat"].forEach(function (id) {
    el[id.replace(/-(\w)/g, function (_, c) { return c.toUpperCase(); })] = document.getElementById(id);
  });

  var papan = null, klik = null, terpilih = null;
  var sesi = 0;              // naik tiap partai baru; callback lama yang terlambat diabaikan
  var partai = null;         // detail /api/partai/{id}
  var analisis = {};         // ply -> baris langkah analisis tersimpan
  var infoAnalisis = null;   // {engine, depth, profil}
  var langkahAsli = [];      // history verbose chess.js
  var permainan = null;      // posisi yang sedang tampil
  var ply = 0;               // jumlah ply partai asli yang sudah dimainkan
  var warnaGM = "w";
  var sibuk = true;          // menilai / memainkan langkah lawan
  var petunjukDipakai = false;
  var riwayat = [];          // tebakan di partai ini
  var nomorPartai = 0;
  var total = { sama: 0, bagus: 0, kurang: 0, meleset: 0, takDinilai: 0 };
  var fenTampil = "start";

  /* ---------- umum ---------- */

  function teks(isi) { return document.createTextNode(isi === null || isi === undefined ? "" : String(isi)); }
  function buat(tag, kelas, isi) {
    var e = document.createElement(tag);
    if (kelas) e.className = kelas;
    if (isi !== undefined && isi !== null) e.appendChild(teks(isi));
    return e;
  }
  function galat(p) { el.galat.textContent = p; el.galat.hidden = false; }
  function ambil(url) {
    return fetch(url).then(function (r) {
      if (!r.ok) {
        return r.text().then(function (t) {
          var d = "";
          try { d = JSON.parse(t).detail || ""; } catch (e) { d = ""; }
          throw new Error("Server menjawab " + r.status + (d ? ": " + d : ""));
        });
      }
      return r.json();
    });
  }
  function namaPendek(nama) { return String(nama || "?").split(",")[0]; }
  function namaGM() { return partai ? namaPendek(warnaGM === "w" ? partai.putih : partai.hitam) : "GM"; }
  function nomorLangkah(fen) {
    var b = fen.split(/\s+/);
    return (parseInt(b[5], 10) || 1) + (b[1] === "b" ? "..." : ".");
  }
  function formatSkor(cp) {
    if (cp >= SKOR_MAT) return "mat";
    if (cp <= -SKOR_MAT) return "dimat";
    return (cp > 0 ? "+" : "") + (cp / 100).toFixed(2).replace(".", ",");
  }

  /* ---------- penilaian (salinan aturan partai.py) ---------- */

  function peluangMenang(cp) {
    var c = Math.max(-BATAS_EVAL_CP, Math.min(BATAS_EVAL_CP, cp));
    return 0.5 + 0.5 * (2 / (1 + Math.exp(-0.00368208 * c)) - 1);
  }
  function turunPeluang(sebelum, sesudah) {
    return (2 * peluangMenang(sebelum) - 1) - (2 * peluangMenang(sesudah) - 1);
  }

  /* Kedua skor dari sudut pemain yang jalan; mat = ±SKOR_MAT. */
  function klasifikasikan(sebelum, sesudah, terbaik) {
    if (terbaik) return "terbaik";
    var matSebelum = Math.abs(sebelum) >= SKOR_MAT, matSesudah = Math.abs(sesudah) >= SKOR_MAT;
    if (!matSebelum && !matSesudah) {
      var turun = turunPeluang(sebelum, sesudah);
      if (turun >= AMBANG_BLUNDER) return "blunder";
      if (turun >= AMBANG_MISTAKE) return "mistake";
      if (turun >= AMBANG_INACCURACY) return "inaccuracy";
      return "baik";
    }
    if (!matSebelum && sesudah <= -SKOR_MAT) {                 // mat muncul
      if (sebelum < -MAT_RINGAN_CP) return "inaccuracy";
      return sebelum < -MAT_SEDANG_CP ? "mistake" : "blunder";
    }
    if (sebelum >= SKOR_MAT && sesudah < SKOR_MAT) {            // mat hilang
      if (matSesudah) return "blunder";
      if (sesudah > MAT_RINGAN_CP) return "inaccuracy";
      return sesudah > MAT_SEDANG_CP ? "mistake" : "blunder";
    }
    return "baik";
  }

  function kategori(klas) {
    if (klas === "terbaik" || klas === "baik") return "bagus";
    if (klas === "inaccuracy") return "kurang";
    return "meleset";
  }

  /* Satu garis /api/evaluasi (sudut Putih) -> skor sudut pemain `warna`. */
  function skorGaris(g, warna) {
    var s;
    if (g.mate !== null && g.mate !== undefined) s = g.mate > 0 ? SKOR_MAT : -SKOR_MAT;
    else s = g.cp || 0;
    return warna === "w" ? s : -s;
  }

  /* Skor posisi sesudah sebuah langkah, sudut pemain yang baru jalan. */
  function skorSesudah(b, warna) {
    var garis = b.garis || [];
    if (garis.length) return skorGaris(garis[0], warna);
    var pemenang = b.skor && b.skor.pemenang;
    if (!pemenang) return 0;                                    // pat / remis
    return pemenang === (warna === "w" ? "putih" : "hitam") ? SKOR_MAT : -SKOR_MAT;
  }

  function evaluasi(fen, multipv, sudahUlang) {
    return fetch("/api/evaluasi?fen=" + encodeURIComponent(fen) + "&multipv=" + multipv)
      .then(function (r) {
        return r.text().then(function (t) {
          var b = null;
          try { b = JSON.parse(t); } catch (e) { b = null; }
          return { ok: r.ok, status: r.status, body: b };
        });
      })
      .then(function (res) {
        /* 409 = engine server dipakai permintaan lain (sebentar); coba sekali lagi. */
        if (res.status === 409 && !sudahUlang) {
          return new Promise(function (ok) { setTimeout(ok, 1600); })
            .then(function () { return evaluasi(fen, multipv, true); });
        }
        return res;
      })
      .catch(function (e) { return { ok: false, status: 0, body: { detail: e.message } }; });
  }

  function alasanGagal(res) {
    if (res.status === 409) return "engine sedang dipakai permintaan lain";
    if (res.status === 503) return "engine tidak tersedia di server";
    var d = res.body && res.body.detail ? String(res.body.detail) : "";
    return "evaluasi gagal (" + (res.status || "jaringan") + ")" + (d ? ": " + d : "");
  }

  function nilaiBeda(fenSebelum, sanKamu, fenSesudah, sanGM, warna) {
    return evaluasi(fenSebelum, 3).then(function (r1) {
      if (!r1.ok) return { jenis: "takDinilai", alasan: alasanGagal(r1) };
      var garis = (r1.body && r1.body.garis) || [];
      if (!garis.length) return { jenis: "takDinilai", alasan: "engine tidak memberi garis" };
      var terbaik = skorGaris(garis[0], warna);
      var sanTerbaik = (garis[0].pv || [])[0];
      var gGM = garis.filter(function (g) { return (g.pv || [])[0] === sanGM; })[0];
      var gKamu = garis.filter(function (g) { return (g.pv || [])[0] === sanKamu; })[0];
      var dasar = {
        terbaik: terbaik, sanTerbaik: sanTerbaik, depth: r1.body.depth,
        skorGM: gGM ? skorGaris(gGM, warna) : null
      };
      var skor = gKamu
        ? Promise.resolve(skorGaris(gKamu, warna))
        : evaluasi(fenSesudah, 1).then(function (r2) {
            if (!r2.ok) { dasar.alasan = alasanGagal(r2); return null; }
            return skorSesudah(r2.body || {}, warna);
          });
      return skor.then(function (s) {
        if (s === null) { dasar.jenis = "takDinilai"; return dasar; }
        dasar.skorKamu = s;
        dasar.klas = klasifikasikan(terbaik, s, sanKamu === sanTerbaik);
        dasar.turun = (Math.abs(terbaik) < SKOR_MAT && Math.abs(s) < SKOR_MAT)
          ? Math.max(0, turunPeluang(terbaik, s)) : null;
        dasar.jenis = kategori(dasar.klas);
        return dasar;
      });
    });
  }

  /* ---------- memilih partai ---------- */

  function isiGM() {
    return ambil("/api/pemain?limit=80").then(function (d) {
      var gm = d.pemain.filter(function (p) { return p.gelar === "GM" && !p.akun_pribadi; }).slice(0, 40);
      var idUrl = parseInt(new URLSearchParams(window.location.search).get("pemain_id"), 10);
      var tambahan = (!idUrl || gm.some(function (p) { return p.id === idUrl; }))
        ? Promise.resolve(null)
        : ambil("/api/pemain/" + idUrl).catch(function () { return null; });
      return tambahan.then(function (p) {
        if (p && p.id) gm.unshift({ id: p.id, nama: p.nama, jumlah_partai: p.total && p.total.partai });
        el.gm.textContent = "";
        gm.forEach(function (p) {
          var o = buat("option", null, p.nama + (p.jumlah_partai ? " (" + p.jumlah_partai.toLocaleString("id-ID") + ")" : ""));
          o.value = p.id;
          el.gm.appendChild(o);
        });
        if (idUrl && el.gm.querySelector('option[value="' + idUrl + '"]')) el.gm.value = String(idUrl);
        if (!gm.length) throw new Error("Tidak ada pemain bergelar GM di database.");
      });
    });
  }

  function ambilAcak(coba, warnaTetap) {
    var gmId = el.gm.value;
    var warna = warnaTetap || (el.warna.value === "acak" ? (Math.random() < 0.5 ? "putih" : "hitam") : el.warna.value);
    var q = "/api/partai?pemain_id=" + encodeURIComponent(gmId) + "&warna=" + warna + "&sumber=gm&per_halaman=1";
    if (el.hasil.value === "menang") q += "&hasil=" + (warna === "putih" ? "1-0" : "0-1");
    return ambil(q + "&halaman=1").then(function (d) {
      if (!d.total) {
        if (el.warna.value === "acak" && !warnaTetap) return ambilAcak(coba, warna === "putih" ? "hitam" : "putih");
        throw new Error("Tidak ada partai " + el.gm.options[el.gm.selectedIndex].text +
          " sebagai " + warna + " dengan saringan ini.");
      }
      var h = 1 + Math.floor(Math.random() * d.total);
      return ambil(q + "&halaman=" + h).then(function (d2) {
        var g = d2.partai[0];
        var layak = g && g.hasil !== "*" && (g.jumlah_langkah || 0) >= MIN_LANGKAH;
        if (!layak && coba < MAKS_COBA) return ambilAcak(coba + 1, warnaTetap);
        if (!g) throw new Error("Partai tidak ditemukan.");
        return { id: g.id, warna: warna };
      });
    });
  }

  function partaiBaru(pilihan) {
    var ini = ++sesi;
    sibuk = true;
    el.galat.hidden = true;
    el.status.textContent = "mencari partai…";
    el.kartuNilai.hidden = true;
    var cari = pilihan ? Promise.resolve(pilihan) : ambilAcak(0, null);
    return cari.then(function (p) {
      return Promise.all([
        ambil("/api/partai/" + p.id),
        ambil("/api/partai/" + p.id + "/analisis").catch(function () { return null; }),
        p.warna
      ]);
    }).then(function (h) {
      if (ini !== sesi) return;
      siapkan(h[0], h[1], h[2]);
    }).catch(function (e) {
      if (ini !== sesi) return;
      el.status.textContent = "";
      galat(e.message);
    });
  }

  function siapkan(detail, a, warna) {
    var penuh = new Chess();
    if (!penuh.load_pgn(detail.pgn || "")) throw new Error("PGN partai #" + detail.id + " tidak bisa dibaca.");
    partai = detail;
    langkahAsli = penuh.history({ verbose: true });
    permainan = AlatCatur.permainanAwal(penuh);
    warnaGM = warna === "hitam" ? "b" : "w";
    analisis = {};
    infoAnalisis = null;
    if (a && a.langkah) {
      a.langkah.forEach(function (l) { analisis[l.ply] = l; });
      infoAnalisis = { engine: a.engine, depth: a.depth, profil: a.profil };
    }
    ply = 0;
    riwayat = [];
    nomorPartai++;

    /* Pembukaan sebelum "mulai dari" dimainkan otomatis. */
    var mulai = parseInt(el.mulai.value, 10) || 1;
    while (ply < langkahAsli.length) {
      var b = permainan.fen().split(/\s+/);
      if ((parseInt(b[5], 10) || 1) >= mulai && permainan.turn() === warnaGM) break;
      permainan.move(langkahAsli[ply].san);
      ply++;
    }

    papan.orientation(warnaGM === "w" ? "white" : "black");
    tampilkan(permainan.fen());
    tampilkanInfo();
    tampilkanRiwayat();
    perbaruiSkor();
    simpanUrl();
    giliran();
  }

  function simpanUrl() {
    try {
      var u = new URL(window.location.href);
      u.searchParams.set("pemain_id", el.gm.value);
      u.searchParams.delete("partai_id");
      window.history.replaceState(null, "", u);
    } catch (e) { /* URL tidak bisa diubah: tidak apa-apa */ }
  }

  /* ---------- jalannya partai ---------- */

  function tampilkan(fen) {
    fenTampil = fen;
    papan.position(fen);
  }

  function mainkanAsli() {
    permainan.move(langkahAsli[ply].san);
    ply++;
    tampilkan(permainan.fen());
  }

  function giliran() {
    var ini = sesi;
    if (ply >= langkahAsli.length) { selesaiPartai(); return; }
    if (permainan.turn() !== warnaGM) {
      sibuk = true;
      el.status.textContent = "lawan jalan…";
      setTimeout(function () {
        if (ini !== sesi) return;
        mainkanAsli();
        giliran();
      }, JEDA_LAWAN_MS);
      return;
    }
    sibuk = false;
    petunjukDipakai = false;
    el.status.textContent = "Giliranmu: " + nomorLangkah(permainan.fen()) + " apa yang dimainkan " + namaGM() + "?";
  }

  function jalankan(dari, ke, promosi) {
    if (sibuk || !permainan || permainan.turn() !== warnaGM) return null;
    var warna = permainan.turn();
    var fenSebelum = permainan.fen();
    var m = permainan.move({ from: dari, to: ke, promotion: promosi || "q" });
    if (!m) return null;
    var fenSesudah = permainan.fen();
    permainan.undo();

    var asli = langkahAsli[ply];
    var catatan = {
      ply: ply, nomor: nomorLangkah(fenSebelum), sanKamu: m.san, sanGM: asli.san,
      fen: fenSebelum, petunjuk: petunjukDipakai
    };
    hapusPilihan();

    if (m.from === asli.from && m.to === asli.to && (m.promotion || "") === (asli.promotion || "")) {
      catatan.jenis = "sama";
      catat(catatan);
      mainkanAsli();
      giliran();
      return m;
    }

    sibuk = true;
    tampilkan(fenSesudah);
    el.status.textContent = "Beda dengan GM — engine menilai langkahmu…";
    var ini = sesi;
    nilaiBeda(fenSebelum, m.san, fenSesudah, asli.san, warna).then(function (h) {
      if (ini !== sesi) return;
      for (var k in h) catatan[k] = h[k];
      catat(catatan);
      mainkanAsli();
      giliran();
    });
    return m;
  }

  function catat(c) {
    riwayat.push(c);
    if (total[c.jenis] !== undefined) total[c.jenis]++;
    tampilkanNilai(c);
    tampilkanRiwayat();
    perbaruiSkor();
  }

  function tunjukkan() {
    if (sibuk || !permainan || ply >= langkahAsli.length || permainan.turn() !== warnaGM) return;
    var asli = langkahAsli[ply];
    var c = { ply: ply, nomor: nomorLangkah(permainan.fen()), sanKamu: null, sanGM: asli.san,
              fen: permainan.fen(), jenis: "dilihat" };
    hapusPilihan();
    catat(c);
    mainkanAsli();
    giliran();
  }

  function petunjuk() {
    if (sibuk || !permainan || ply >= langkahAsli.length || permainan.turn() !== warnaGM) return;
    petunjukDipakai = true;
    hapusPilihan();
    if (klik) klik.sorotPilih(langkahAsli[ply].from);
    el.status.textContent = "Petunjuk: GM menggerakkan bidak di " + langkahAsli[ply].from + ".";
  }

  function selesaiPartai() {
    sibuk = true;
    el.status.textContent = "Partai selesai.";
    tampilkanInfo();
  }

  /* ---------- tampilan ---------- */

  function labelGM(c) {
    var a = analisis[c.ply];
    if (!a || !infoAnalisis) return null;
    var sumber = infoAnalisis.depth ? "depth " + infoAnalisis.depth : (infoAnalisis.engine || infoAnalisis.profil || "?");
    return (NAMA_KLAS[a.klasifikasi] || a.klasifikasi || "?") +
      (a.skor_sesudah_teks ? ", " + a.skor_sesudah_teks + " (sudut Putih)" : "") +
      " — analisis tersimpan, " + sumber;
  }

  function tampilkanNilai(c) {
    var k = el.nilai;
    k.textContent = "";
    el.kartuNilai.hidden = false;
    el.kartuNilai.classList.toggle("jawab-benar", c.jenis === "sama" || c.jenis === "bagus");
    el.kartuNilai.classList.toggle("jawab-salah", c.jenis === "meleset");

    var judul = buat("div", "tebak-judul tebak-" + c.jenis, TANDA[c.jenis] + " " + NAMA[c.jenis]);
    k.appendChild(judul);

    var baris = buat("div");
    if (c.sanKamu) {
      baris.appendChild(teks("Kamu: "));
      baris.appendChild(buat("b", null, c.nomor + " " + c.sanKamu));
      baris.appendChild(teks(" · "));
    }
    baris.appendChild(teks(namaGM() + ": "));
    baris.appendChild(buat("b", null, c.nomor + " " + c.sanGM));
    if (c.petunjuk) baris.appendChild(buat("span", "redup kecil", " (dengan petunjuk)"));
    k.appendChild(baris);

    if (c.jenis !== "sama" && c.jenis !== "dilihat") {
      if (c.skorKamu !== undefined) {
        var e = buat("div", "kecil");
        e.appendChild(teks("Engine (±1,5 s, depth " + (c.depth || "?") + ", sudut " + namaGM() + "): langkahmu " +
          formatSkor(c.skorKamu) + " — " + (NAMA_KLAS[c.klas] || c.klas) + ". Terbaik " +
          (c.sanTerbaik || "?") + " " + formatSkor(c.terbaik) +
          (c.turun !== null && c.turun !== undefined ? ", peluang turun " + c.turun.toFixed(2).replace(".", ",") : "") + "."));
        k.appendChild(e);
        if (c.skorGM !== null && c.skorGM !== undefined) {
          k.appendChild(buat("div", "kecil", "Langkah GM di engine yang sama: " + formatSkor(c.skorGM) +
            (c.skorKamu > c.skorGM ? " — langkahmu dinilai lebih tinggi." : "")));
        }
      } else if (c.alasan) {
        k.appendChild(buat("div", "kecil redup", "Tidak dinilai: " + c.alasan + ". Tidak dihitung salah."));
      }
    }
    var lg = labelGM(c);
    if (lg) k.appendChild(buat("div", "kecil redup", "Langkah GM: " + lg));

    var tautan = buat("div", "kecil");
    var a = buat("a", null, "Buka posisi ini di papan analisis ↗");
    a.href = "/static/analisis.html?fen=" + encodeURIComponent(c.fen);
    a.target = "_blank";
    a.rel = "noopener";
    tautan.appendChild(a);
    k.appendChild(tautan);
  }

  function tampilkanRiwayat() {
    var k = el.riwayat;
    k.textContent = "";
    if (!riwayat.length) { k.appendChild(buat("div", "pesan kecil", "belum ada")); return; }
    riwayat.forEach(function (c) {
      var s = buat("span", "tebak-item tebak-" + c.jenis);
      s.appendChild(buat("span", "redup", c.nomor));
      s.appendChild(teks(c.sanGM + " " + TANDA[c.jenis]));
      if (c.sanKamu && c.jenis !== "sama") s.appendChild(buat("span", "redup kecil", " (" + c.sanKamu + ")"));
      s.title = NAMA[c.jenis] + (c.petunjuk ? ", dengan petunjuk" : "") + (c.alasan ? " — " + c.alasan : "");
      k.appendChild(s);
    });
  }

  function hitungPartai() {
    var h = { sama: 0, bagus: 0, kurang: 0, meleset: 0, takDinilai: 0, dilihat: 0 };
    riwayat.forEach(function (c) { h[c.jenis]++; });
    return h;
  }

  function perbaruiSkor() {
    var s = el.skor;
    s.textContent = "";
    var dinilai = total.sama + total.bagus + total.kurang + total.meleset;
    if (!dinilai && !total.takDinilai) return;
    function angka(n, ket) {
      var sp = buat("span");
      sp.appendChild(buat("strong", null, n));
      sp.appendChild(teks(" " + ket));
      s.appendChild(sp);
    }
    angka(total.sama, "sama GM");
    angka(total.bagus, "bagus");
    angka(total.kurang, "kurang");
    angka(total.meleset, "meleset");
    if (dinilai) angka(Math.round(100 * total.sama / dinilai) + "%", "cocok");
    s.appendChild(buat("span", "dilewati", "sesi ini, " + nomorPartai + " partai" +
      (total.takDinilai ? " · " + total.takDinilai + " tak dinilai" : "")));
  }

  function tampilkanInfo() {
    var g = partai;
    var k = el.info;
    k.textContent = "";
    if (!g) return;
    var selesai = ply >= langkahAsli.length;
    el.nomor.textContent = "#" + g.id;

    var pemain = buat("div");
    var putih = buat("b", null, g.putih + (g.elo_putih ? " (" + g.elo_putih + ")" : ""));
    var hitam = buat("b", null, g.hitam + (g.elo_hitam ? " (" + g.elo_hitam + ")" : ""));
    pemain.appendChild(teks("⚪ ")); pemain.appendChild(putih);
    pemain.appendChild(teks(" – ⚫ ")); pemain.appendChild(hitam);
    k.appendChild(pemain);
    k.appendChild(buat("div", "kecil redup", [g.turnamen, g.babak ? "babak " + g.babak : null,
      g.tanggal ? String(g.tanggal).slice(0, 4) : null, g.eco ? g.eco + " " + (g.pembukaan || "") : null]
      .filter(Boolean).join(" · ")));
    k.appendChild(buat("div", "kecil", "Kamu memegang " + (warnaGM === "w" ? "Putih" : "Hitam") +
      " (" + namaGM() + "). " + (selesai ? "Hasil: " + g.hasil : "Hasil disembunyikan sampai partai selesai.")));

    if (selesai) {
      var h = hitungPartai();
      var dinilai = h.sama + h.bagus + h.kurang + h.meleset;
      var r = buat("div", "tebak-rangkum");
      r.appendChild(teks(riwayat.length + " langkah ditebak: " + h.sama + " sama GM" +
        (dinilai ? " (" + Math.round(100 * h.sama / dinilai) + "%)" : "") + ", " + h.bagus + " bagus, " +
        h.kurang + " kurang tepat, " + h.meleset + " meleset" +
        (h.takDinilai ? ", " + h.takDinilai + " tak dinilai" : "") +
        (h.dilihat ? ", " + h.dilihat + " dilihat" : "") + "."));
      k.appendChild(r);
      var t = buat("div", "kecil");
      var a = buat("a", null, "Tinjau partai ini lengkap dengan analisis ↗");
      a.href = "/partai/" + g.id;
      t.appendChild(a);
      k.appendChild(t);
    }
  }

  /* ---------- papan ---------- */

  function bolehGeser(source, piece) {
    hapusPilihan();
    if (sibuk || !permainan || permainan.turn() !== warnaGM) return false;
    return piece.charAt(0) === warnaGM;
  }

  function hapusPilihan() {
    terpilih = null;
    if (klik) klik.hapusSorot();
  }

  function klikKotak(sq) {
    if (!sq || sibuk || !permainan || permainan.turn() !== warnaGM) { hapusPilihan(); return; }
    var bidak = permainan.get(sq);
    if (terpilih === null) {
      if (!bidak || bidak.color !== warnaGM) return;
      terpilih = sq;
      klik.sorotPilih(sq);
      permainan.moves({ square: sq, verbose: true }).forEach(function (m) { klik.sorotTarget(m.to, !!m.captured); });
      return;
    }
    if (sq === terpilih) { hapusPilihan(); return; }
    var dari = terpilih;
    if (PilihPromosi.tunda(permainan, dari, sq, function (p) { jalankan(dari, sq, p); })) {
      hapusPilihan();
      return;
    }
    if (jalankan(dari, sq)) return;
    hapusPilihan();
    if (bidak && bidak.color === warnaGM) klikKotak(sq);
  }

  function saatJatuh(source, target) {
    if (PilihPromosi.tunda(permainan, source, target, function (p) { jalankan(source, target, p); })) {
      return "snapback";
    }
    if (!jalankan(source, target)) return "snapback";
  }

  papan = Chessboard("papan", {
    position: "start",
    draggable: true,
    pieceTheme: "/static/img/chesspieces/{piece}.png",
    onDragStart: bolehGeser,
    onDrop: saatJatuh,
    onSnapEnd: function () { papan.position(fenTampil); }
  });
  window.addEventListener("resize", function () { papan.resize(); });
  window.addEventListener("load", function () { setTimeout(function () { papan.resize(); }, 300); });
  if (window.KlikPapan) {
    klik = KlikPapan(document.getElementById("papan"), {
      orientasi: function () { return papan.orientation(); },
      saatKlik: klikKotak
    });
  }

  /* ---------- kendali ---------- */

  function gantiPartai() { partaiBaru(null); }
  el.partaiLain.addEventListener("click", gantiPartai);
  el.tunjukkan.addEventListener("click", tunjukkan);
  el.petunjuk.addEventListener("click", petunjuk);
  ["gm", "warna", "hasil", "mulai"].forEach(function (k) { el[k].addEventListener("change", gantiPartai); });
  document.addEventListener("keydown", function (ev) {
    if (ev.target.tagName === "SELECT" || ev.target.tagName === "INPUT") return;
    var t = (ev.key || "").toLowerCase();
    if (t === "n") gantiPartai();
    else if (t === "s") tunjukkan();
    else if (t === "h") petunjuk();
  });

  /* URL: ?pemain_id=<GM>&warna=putih|hitam|acak&hasil=menang|semua&mulai=1|8|15
     dan ?partai_id=<id> untuk memainkan satu partai tertentu (pemain_id wajib
     ikut supaya warna GM diketahui).
     Kait uji tanpa mouse: ?uji_tebak=gm memainkan langkah GM (=semua: sampai
     partai habis), ?uji_tebak=beda
     memainkan langkah sah pertama yang BUKAN langkah GM (memicu /api/evaluasi). */
  var q = new URLSearchParams(window.location.search);
  [["warna", "warna"], ["hasil", "hasil"], ["mulai", "mulai"]].forEach(function (p) {
    var v = q.get(p[0]);
    if (v && el[p[1]].querySelector('option[value="' + CSS.escape(v) + '"]')) el[p[1]].value = v;
  });

  isiGM().then(function () {
    var idPartai = parseInt(q.get("partai_id"), 10);
    if (!idPartai) return partaiBaru(null);
    return ambil("/api/partai/" + idPartai).then(function (g) {
      var gm = parseInt(el.gm.value, 10);
      var warna = g.hitam_id === gm ? "hitam" : (g.putih_id === gm ? "putih" : (el.warna.value === "hitam" ? "hitam" : "putih"));
      return partaiBaru({ id: idPartai, warna: warna });
    });
  }).catch(function (e) { el.status.textContent = ""; el.info.textContent = ""; galat(e.message); });

  var uji = q.get("uji_tebak");
  if (uji) {
    var cek = setInterval(function () {
      if (sibuk || !permainan || ply >= langkahAsli.length || permainan.turn() !== warnaGM) return;
      if (uji !== "semua") clearInterval(cek);
      var asli = langkahAsli[ply];
      if (uji === "gm" || uji === "semua") { jalankan(asli.from, asli.to, asli.promotion); return; }
      var lain = permainan.moves({ verbose: true }).filter(function (m) { return m.san !== asli.san; })[0];
      if (lain) jalankan(lain.from, lain.to, lain.promotion);
    }, 300);
  }
})();
