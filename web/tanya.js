/* Tanya: pertanyaan bahasa Indonesia -> SQL -> jawaban dari database.

   Dua hal yang halaman ini sengaja TIDAK sembunyikan, karena keduanya adalah
   satu-satunya cara jawaban LLM bisa dipercaya:

   1. SQL yang dipakai selalu tersedia (kotak "SQL yang dipakai"). Kesalahan
      LLM yang paling berbahaya bukan yang error — itu langsung terlihat —
      melainkan SQL yang jalan mulus tapi angkanya salah. Backend sudah
      menandai pola seperti itu di `peringatan`, dan peringatan itu ditaruh
      DI ATAS jawaban, bukan di bawah.
   2. Keadaan Ollama dibaca lebih dulu lewat /api/tanya/status, karena satu
      pertanyaan bisa memakan 20-60 detik di mesin ini. Tanpa itu, Ollama yang
      mati baru terasa setelah menunggu semenit. */

(function () {
  "use strict";

  var el = {
    galat: document.getElementById("galat"),
    keadaan: document.getElementById("keadaan"),
    form: document.getElementById("form-tanya"),
    pertanyaan: document.getElementById("pertanyaan"),
    mode: document.getElementById("mode"),
    tombol: document.getElementById("tombol-tanya"),
    menunggu: document.getElementById("menunggu"),
    jawaban: document.getElementById("jawaban"),
    peringatan: document.getElementById("peringatan"),
    isi: document.getElementById("isi-jawaban"),
    kotakSql: document.getElementById("kotak-sql"),
    sql: document.getElementById("sql"),
    kotakPeta: document.getElementById("kotak-peta"),
    peta: document.getElementById("peta")
  };

  var sedangTanya = false;

  function teks(isi) {
    return document.createTextNode(isi === null || isi === undefined ? "" : String(isi));
  }
  function buat(tag, kelas, isi) {
    var e = document.createElement(tag);
    if (kelas) e.className = kelas;
    if (isi !== undefined) e.appendChild(teks(isi));
    return e;
  }
  function kosongkan(e) { while (e.firstChild) e.removeChild(e.firstChild); }
  function galat(p) { el.galat.textContent = p; el.galat.hidden = false; }
  function bersihGalat() { el.galat.textContent = ""; el.galat.hidden = true; }

  /* Pesan `detail` dari FastAPI lebih berguna daripada nomor statusnya:
     backend sudah menuliskan cara memperbaikinya (jalankan ollama serve, dst). */
  function ambilJson(url, opsi) {
    return fetch(url, opsi).then(function (r) {
      return r.json().then(function (d) {
        if (!r.ok) throw new Error((d && d.detail) || ("Server menjawab " + r.status));
        return d;
      }, function () {
        throw new Error("Server menjawab " + r.status + " tanpa penjelasan");
      });
    });
  }

  /* ---------- keadaan Ollama ---------- */

  function periksaKeadaan() {
    return ambilJson("/api/tanya/status").then(function (d) {
      if (!d.hidup) {
        el.keadaan.textContent = "Ollama tidak berjalan. " + (d.alasan || "");
        matikanForm();
        return;
      }
      if (!d.siap) {
        el.keadaan.textContent = "Ollama berjalan tapi model " + d.model_bawaan +
          " belum ada. Jalankan: ollama pull " + d.model_bawaan +
          (d.model.length ? " (yang ada sekarang: " + d.model.join(", ") + ")" : "");
        matikanForm();
        return;
      }
      el.keadaan.textContent = "Model " + d.model_bawaan + " siap di komputer ini. Memuat ke memori…";
      panaskan(d.model_bawaan);
    }).catch(function (e) {
      el.keadaan.textContent = "Keadaan Ollama tidak bisa dibaca: " + e.message;
      matikanForm();
    });
  }

  /* Model 4,7 GB dimuat dari SSD selagi kamu mengetik, bukan sesudah Tanya
     ditekan. Gagal di sini tidak fatal: pertanyaannya tetap bisa dikirim,
     hanya lebih lambat, jadi form tidak dimatikan. */
  function panaskan(model) {
    ambilJson("/api/tanya/panaskan", { method: "POST" }).then(function (d) {
      if (d.siap) {
        el.keadaan.textContent = "Model " + model + " sudah di memori (" +
          (d.durasi_ms / 1000).toFixed(1).replace(".", ",") + " s).";
      } else {
        el.keadaan.textContent = "Model " + model + " belum termuat: " + (d.alasan || "") +
          " Pertanyaan pertama akan lebih lambat.";
      }
    }).catch(function () {
      el.keadaan.textContent = "Model " + model + " siap di komputer ini.";
    });
  }

  function matikanForm() {
    el.tombol.disabled = true;
    el.pertanyaan.disabled = true;
  }

  /* ---------- tampilkan jawaban ---------- */

  function tampilPeringatan(daftar) {
    kosongkan(el.peringatan);
    if (!daftar || !daftar.length) return;
    var kotak = buat("div", "galat");
    kotak.appendChild(buat("strong", null, "Periksa dulu:"));
    var ul = buat("ul");
    daftar.forEach(function (p) { ul.appendChild(buat("li", null, p)); });
    kotak.appendChild(ul);
    el.peringatan.appendChild(kotak);
  }

  function tampilTabel(kolom, baris, terpotong, jumlah, kalimat) {
    kosongkan(el.isi);
    /* Kalimatnya disusun templat di server dari baris yang sama, bukan oleh
       model, jadi angkanya pasti sama dengan tabel di bawahnya. */
    if (kalimat) el.isi.appendChild(buat("p", "kalimat-jawaban", kalimat));
    if (!baris.length) {
      el.isi.appendChild(buat("p", "kecil redup", "Tidak ada baris yang cocok."));
      return;
    }
    var tabel = buat("table");
    var thead = buat("thead");
    var trh = buat("tr");
    kolom.forEach(function (k) { trh.appendChild(buat("th", null, k)); });
    thead.appendChild(trh);
    tabel.appendChild(thead);
    var tbody = buat("tbody");
    baris.forEach(function (r) {
      var tr = buat("tr");
      kolom.forEach(function (k, i) {
        /* Baris bisa datang sebagai objek atau sebagai deret nilai; keduanya
           ditangani supaya halaman tidak pecah kalau bentuknya berubah. */
        var nilai = Array.isArray(r) ? r[i] : r[k];
        tr.appendChild(buat("td", null, nilai === null || nilai === undefined ? "—" : nilai));
      });
      tbody.appendChild(tr);
    });
    tabel.appendChild(tbody);
    el.isi.appendChild(tabel);
    var ket = jumlah + (jumlah === 1 ? " baris" : " baris");
    if (terpotong) ket += " (dipotong di batas; pertanyaannya perlu lebih sempit)";
    el.isi.appendChild(buat("p", "kecil redup", ket));
  }

  function tampilJawabanAplikasi(d) {
    kosongkan(el.isi);
    /* Sengaja teks biasa, bukan HTML: isinya karangan model, jadi tidak boleh
       ikut jadi markup di halaman ini. */
    var p = buat("p");
    p.style.whiteSpace = "pre-wrap";
    p.appendChild(teks(d.jawaban || "(model tidak menjawab apa pun)"));
    el.isi.appendChild(p);
    el.isi.appendChild(buat("p", "kecil redup",
      "Jawaban ini tidak bisa diperiksa otomatis. Peta yang dibacakan ke model " +
      "ada di bawah, jadi kamu bisa mencocokkannya sendiri."));
  }

  function tampil(d) {
    el.jawaban.hidden = false;
    tampilPeringatan(d.peringatan);
    if (d.mode === "aplikasi") {
      tampilJawabanAplikasi(d);
      el.kotakSql.hidden = true;
      el.kotakPeta.hidden = false;
      el.peta.textContent = d.peta || "";
    } else {
      tampilTabel(d.kolom || [], d.baris || [], d.terpotong, d.jumlah || 0, d.kalimat);
      if (typeof d.durasi_ms === "number") {
        el.isi.appendChild(buat("p", "kecil redup",
          (d.durasi_ms / 1000).toFixed(1).replace(".", ",") + " detik" +
          (d.dari_cache ? " · SQL diambil dari pertanyaan yang sama sebelumnya; " +
            "angkanya tetap dibaca ulang dari database" : "")));
      }
      el.kotakSql.hidden = false;
      el.sql.textContent = d.sql || "";
      el.kotakPeta.hidden = true;
    }
  }

  /* ---------- kirim pertanyaan ---------- */

  function tanya(pertanyaan) {
    if (sedangTanya) return;
    pertanyaan = (pertanyaan || "").trim();
    if (!pertanyaan) { galat("Pertanyaannya masih kosong."); return; }
    bersihGalat();
    sedangTanya = true;
    el.tombol.disabled = true;
    el.menunggu.hidden = false;
    el.jawaban.hidden = true;
    ambilJson("/api/tanya", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ pertanyaan: pertanyaan, mode: el.mode.value })
    }).then(tampil).catch(function (e) {
      galat(e.message);
    }).then(function () {
      sedangTanya = false;
      el.tombol.disabled = false;
      el.menunggu.hidden = true;
    });
  }

  el.form.addEventListener("submit", function (ev) {
    ev.preventDefault();
    tanya(el.pertanyaan.value);
  });

  Array.prototype.forEach.call(document.querySelectorAll(".contoh-tanya"), function (b) {
    b.addEventListener("click", function () {
      el.pertanyaan.value = b.textContent;
      el.mode.value = "database";
      tanya(b.textContent);
    });
  });

  periksaKeadaan();
})();
