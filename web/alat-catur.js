/* Alat bersama untuk halaman yang menganalisis partai sendiri (Latihan,
   Kekalahan, Kelemahan, Beranda, Kemajuan). Aturan Ray ada di satu tempat
   saja supaya tidak menyimpang antar halaman — lihat SESI.md bagian 6:

     - lawan yang dihitung: ber-rating di atas 2200, atau bergelar
     - format waktu: bullet < 3 menit, blitz < 8, rapid < 25, selebihnya
       klasik (perkiraan durasi = waktu awal + 40 × inkremen, batas Lichess)

   Sejak backend memasang kolom `format` (Permintaan API #8), format partai
   sudah ikut di `/api/partai`; `formatDari()` hanya cadangan untuk partai
   lama yang kolomnya kosong. */

window.AlatCatur = (function () {
  "use strict";

  var AMBANG_ELO_LAWAN = 2200;

  /* mode: "ketentuan" (>2200 atau bergelar), "2200", "gelar", "semua" */
  function lolosLawan(g, warna, mode) {
    if (mode === "semua") return true;
    var gelar = warna === "putih" ? g.hitam_gelar : g.putih_gelar;
    var eloLawan = warna === "putih" ? g.elo_hitam : g.elo_putih;
    var bergelar = !!gelar;
    var kuat = !!(eloLawan && eloLawan > AMBANG_ELO_LAWAN);
    if (mode === "gelar") return bergelar;
    if (mode === "2200") return kuat;
    return bergelar || kuat;
  }

  function formatDari(kontrol) {
    if (!kontrol) return "tanpa";
    var b = String(kontrol).split("+");
    var awal = parseInt(b[0], 10), inc = parseInt(b[1] || "0", 10);
    if (isNaN(awal)) return "tanpa";
    var durasi = awal + 40 * (isNaN(inc) ? 0 : inc);
    if (durasi < 180) return "bullet";
    if (durasi < 480) return "blitz";
    if (durasi < 1500) return "rapid";
    return "klasik";
  }

  /* format partai: kolom database dulu, hitung sendiri kalau kosong */
  function formatPartai(g) { return (g && g.format) || formatDari(g && g.kontrol_waktu); }

  /* Sumber OTB (sama dengan SUMBER_GM di app/posisi.py): di sini angka dasar
     yang kecil berarti MENIT, bukan detik. */
  var SUMBER_OTB = ["twic", "lichess-broadcast", "pgnmentor"];

  function angkaTc(s) {
    var n = parseFloat(String(s === undefined || s === null ? "0" : s).replace(",", "."));
    return isNaN(n) ? 0 : n;
  }

  /* Kontrol waktu dalam bentuk yang biasa dibaca pemain: "180+0" -> "3+0".
     TimeControl dari sumber daring memakai DETIK (Lichess/Chess.com), jadi
     dasarnya dibagi 60; dari sumber OTB angka di bawah 180 sudah menit —
     aturan yang sama dengan `format_dari_kontrol_waktu` di
     app/format_partai.py, jangan dibiarkan menyimpang.
     Dasar di bawah satu menit ditulis dengan satuan detik ("15+0" -> "15s+0"):
     ultrabullet tidak punya bentuk menit yang lazim, dan tanpa satuan "10+2"
     dari Chess.com akan terbaca 10 menit padahal 10 detik. Harian
     ("1/259200") dibiarkan apa adanya. "" kalau tidak ada kontrol waktu.
     Inkremen selalu detik, di bentuk mana pun. */
  function kontrolNormal(kontrol, sumber) {
    var tc = String(kontrol === undefined || kontrol === null ? "" : kontrol).trim();
    if (!tc || tc === "-" || tc === "?") return "";
    if (tc.indexOf("/") >= 0) return tc;
    var m = /^(\d+(?:[.,]\d+)?)\s*min(?:ute)?s?\s*(?:\+\s*(\d+(?:[.,]\d+)?)\s*s(?:ec)?(?:ond)?s?)?$/i.exec(tc);
    if (m) return angkaTc(m[1]) + "+" + angkaTc(m[2]);      /* "3 min + 2 s" sudah menit */
    m = /^(\d+(?:[.,]\d+)?)(?:\s*\+\s*(\d+(?:[.,]\d+)?))?$/.exec(tc);
    if (!m) return tc;
    var dasar = angkaTc(m[1]), inc = angkaTc(m[2]);
    if (SUMBER_OTB.indexOf(sumber) >= 0 && dasar < 180) return dasar + "+" + inc;
    if (dasar < 60) return dasar + "s+" + inc;
    return (Math.round((dasar / 60) * 100) / 100) + "+" + inc;
  }

  /* Kontrol waktu partai untuk ditampilkan: bentuk dari backend dulu (field
     `kontrol_normal`, app/repositori.py), hitung sendiri hanya kalau
     responsnya lama/tanpa field itu — sama pola dengan formatPartai(). */
  function kontrolPartai(g) {
    if (g && typeof g.kontrol_normal === "string") return g.kontrol_normal;
    return kontrolNormal(g && g.kontrol_waktu, g && g.sumber);
  }

  /* ---------- PGN: tag dan jam ---------- */

  function tagPgn(pgn) {
    var t = {};
    String(pgn || "").replace(/\[(\w+) "([^"]*)"\]/g, function (_, k, v) { t[k] = v; });
    return t;
  }

  /* Sisa jam (detik) setelah tiap ply, dari komentar %clk. [] kalau tidak ada. */
  function jamPerPly(pgn) {
    var badan = String(pgn || "").replace(/\[(\w+) "([^"]*)"\]\s*/g, "");
    var re = /\{[^}]*%clk\s+(\d+):(\d\d):(\d\d(?:\.\d+)?)[^}]*\}/g, m, jam = [];
    while ((m = re.exec(badan)) !== null) {
      jam.push(parseInt(m[1], 10) * 3600 + parseInt(m[2], 10) * 60 + parseFloat(m[3]));
    }
    return jam;
  }

  /* Detik yang dipakai untuk langkah pada ply tertentu. null kalau tak terhitung. */
  function detikDipakai(jam, ply, kontrol) {
    if (!jam.length || jam[ply] === undefined) return null;
    var inc = parseInt(String(kontrol || "").split("+")[1] || "0", 10) || 0;
    var sebelum = ply >= 2 ? jam[ply - 2] : parseInt(String(kontrol || "").split("+")[0], 10);
    if (sebelum === undefined || isNaN(sebelum)) return null;
    return Math.max(0, Math.round((sebelum - jam[ply] + inc) * 10) / 10);
  }

  /* Sisa jam pihak `warna` pada ply tertentu (null = jam terakhir miliknya). */
  function jamSendiri(jam, warna, ply) {
    var k = warna === "putih" ? 0 : 1;
    var i = (ply === null || ply === undefined) ? jam.length - 1 : ply;
    while (i >= 0 && (i % 2) !== k) i--;
    return i >= 0 && jam[i] !== undefined ? jam[i] : null;
  }

  function kalahWaktu(tags) {
    var t = (tags.Termination || "").toLowerCase();
    return t.indexOf("time forfeit") >= 0 || t.indexOf("on time") >= 0 || t.indexOf("waktu") >= 0;
  }

  return {
    AMBANG_ELO_LAWAN: AMBANG_ELO_LAWAN,
    lolosLawan: lolosLawan,
    formatDari: formatDari,
    formatPartai: formatPartai,
    kontrolNormal: kontrolNormal,
    kontrolPartai: kontrolPartai,
    tagPgn: tagPgn,
    jamPerPly: jamPerPly,
    detikDipakai: detikDipakai,
    jamSendiri: jamSendiri,
    kalahWaktu: kalahWaktu
  };
})();
