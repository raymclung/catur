/* Akun online milik sendiri (Lichess, Chess.com), dipakai halaman Beranda,
   Latihan, Kekalahan, Kelemahan, Repertoire, dan Ringkasan.

   Sumber kebenarannya kolom `pemain.akun_pribadi` di database lewat
   `/api/pemain?pribadi=true` (Permintaan API #7, dipasang backend 11 Sep).
   Kalau endpoint lama yang dipakai (server belum diperbarui), fungsi ini
   kembali menebak: tanpa gelar, tanpa FIDE ID, ≥ 100 partai, dan bukan akun
   yang dikecualikan Ray. */

window.AkunPribadi = (function () {
  "use strict";

  var cache = null;

  /* Keputusan Ray 11 Sep (SESI.md bagian 6): akun Chess.com yang dihitung
     hanya rayylung dan Stanley-SquarePants; RyuMcLungG bukan akun Ray. */
  var DIKECUALIKAN = ["RyuMcLungG"];

  function ambil(url) {
    return fetch(url).then(function (r) {
      if (!r.ok) throw new Error("Server menjawab " + r.status + " untuk " + url);
      return r.json();
    });
  }

  function labelSumber(perSumber) {
    var nama = { "lichess-user": "Lichess", "chesscom": "Chess.com", "twic": "TWIC", "lichess-broadcast": "broadcast" };
    return (perSumber || []).map(function (s) { return nama[s[0]] || s[0]; }).join(" + ");
  }

  /* Daftar {id, nama, jumlah_partai, sumber} diurutkan menurut jumlah partai. */
  function muat() {
    if (cache) return Promise.resolve(cache);
    return ambil("/openapi.json").then(function (d) {
      var param = (((d.paths || {})["/api/pemain"] || {}).get || {}).parameters || [];
      return param.some(function (p) { return p.name === "pribadi"; });
    }).catch(function () { return false; }).then(function (adaPenanda) {
      var url = adaPenanda ? "/api/pemain?pribadi=true&limit=50" : "/api/pemain?limit=200";
      return ambil(url).then(function (d) {
        var akun = adaPenanda ? d.pemain
          : d.pemain.filter(function (p) { return !p.gelar && !p.fide_id && p.jumlah_partai >= 100; });
        akun = akun.filter(function (p) { return DIKECUALIKAN.indexOf(p.nama) < 0; });
        return Promise.all(akun.map(function (p) {
          return ambil("/api/pemain/" + p.id).then(function (rinci) {
            return { id: p.id, nama: p.nama, jumlah_partai: p.jumlah_partai, sumber: labelSumber(rinci.per_sumber) };
          }).catch(function () {
            return { id: p.id, nama: p.nama, jumlah_partai: p.jumlah_partai, sumber: "" };
          });
        }));
      });
    }).then(function (daftar) {
      daftar.sort(function (a, b) { return b.jumlah_partai - a.jumlah_partai; });
      cache = daftar;
      return daftar;
    });
  }

  return { muat: muat };
})();
