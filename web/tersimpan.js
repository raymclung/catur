/* Daftar posisi tersimpan (Phase 6). Data dari GET /api/posisi-tersimpan,
   hapus lewat DELETE /api/posisi-tersimpan/{id} (SESI.md Permintaan API #6).
   Sebelum backend memasangnya, halaman menjelaskan itu, bukan menampilkan
   daftar kosong seolah tidak ada yang tersimpan. */

(function () {
  "use strict";

  var el = {
    galat: document.getElementById("galat"),
    daftar: document.getElementById("daftar"),
    jumlah: document.getElementById("jumlah")
  };

  function teks(isi) { return document.createTextNode(isi === null || isi === undefined ? "" : String(isi)); }
  function buat(tag, kelas, isi) {
    var e = document.createElement(tag);
    if (kelas) e.className = kelas;
    if (isi !== undefined) e.appendChild(teks(isi));
    return e;
  }
  function galat(p) { el.galat.textContent = p; el.galat.hidden = false; }
  function pesan(p) { el.daftar.textContent = ""; el.daftar.appendChild(buat("div", "pesan kecil", p)); }

  function tautan(label, href, judul) {
    var a = document.createElement("a");
    a.href = href;
    if (judul) a.title = judul;
    a.appendChild(teks(label));
    return a;
  }

  function labelFen(fen) {
    var b = fen.split(" ");
    return (b[1] === "b" ? "Hitam" : "Putih") + " jalan";
  }

  function barisPosisi(p) {
    var baris = buat("div", "tersimpan-baris");
    var kiri = buat("div", "tersimpan-isi");
    kiri.appendChild(buat("div", "tersimpan-judul", p.judul || "(tanpa judul)"));
    var meta = [labelFen(p.fen)];
    if (p.asal) meta.push("dari " + p.asal);
    if (p.dibuat_pada) meta.push(String(p.dibuat_pada).slice(0, 16).replace("T", " "));
    kiri.appendChild(buat("div", "kecil redup", meta.join(" · ")));
    if (p.catatan) kiri.appendChild(buat("div", "kecil", p.catatan));
    var fen = buat("div", "eval-fen kecil", p.fen);
    fen.style.padding = "4px 0 0";
    fen.style.borderTop = "0";
    kiri.appendChild(fen);
    baris.appendChild(kiri);

    var kanan = buat("div", "tautan-baris");
    kanan.style.marginTop = "0";
    var q = encodeURIComponent(p.fen);
    kanan.appendChild(tautan("jelajah", "/jelajah?fen=" + q, "Buka di penjelajah pembukaan"));
    kanan.appendChild(tautan("analisis", "/static/analisis.html?fen=" + q, "Buka di papan analisis"));
    kanan.appendChild(tautan("partai", "/?fen=" + q, "Daftar partai yang mencapai posisi ini"));
    if (p.partai_id) kanan.appendChild(tautan("asal", "/partai/" + p.partai_id, "Partai asal posisi ini"));
    var hapus = buat("button", null, "✕");
    hapus.title = "Hapus posisi ini";
    hapus.addEventListener("click", function () {
      if (!window.confirm("Hapus \"" + (p.judul || p.fen) + "\"?")) return;
      fetch("/api/posisi-tersimpan/" + p.id, { method: "DELETE" }).then(function (r) {
        if (!r.ok) throw new Error("Server menjawab " + r.status);
        baris.remove();
        perbaruiJumlah(-1);
      }).catch(function (e) { galat("Gagal menghapus: " + e.message); });
    });
    kanan.appendChild(hapus);
    baris.appendChild(kanan);
    return baris;
  }

  var jumlah = 0;
  function perbaruiJumlah(delta) {
    jumlah += delta;
    el.jumlah.textContent = jumlah ? "· " + jumlah : "";
    if (!jumlah) {
      pesan("Belum ada yang disimpan. Tekan tombol ☆ Simpan waktu membuka posisi — "
            + "ada di halaman Jelajah, papan Analisis, dan di halaman partai (di bawah papan).");
    }
  }

  fetch("/openapi.json").then(function (r) { return r.json(); }).then(function (d) {
    if (!d.paths || !d.paths["/api/posisi-tersimpan"]) {
      pesan("Penyimpanan posisi belum dipasang server (menunggu backend, SESI.md bagian 3 #6).");
      return;
    }
    return fetch("/api/posisi-tersimpan").then(function (r) {
      if (!r.ok) throw new Error("Server menjawab " + r.status);
      return r.json();
    }).then(function (data) {
      var daftar = Array.isArray(data) ? data : (data.posisi || data.tersimpan || []);
      el.daftar.textContent = "";
      daftar.forEach(function (p) { el.daftar.appendChild(barisPosisi(p)); });
      perbaruiJumlah(daftar.length);
    });
  }).catch(function (e) { galat("Gagal memuat: " + e.message); });
})();
