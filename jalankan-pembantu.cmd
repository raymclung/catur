@echo off
rem Server KHUSUS PEMBANTU di port 8001, untuk dibuka lewat tunnel Cloudflare.
rem
rem Kenapa server terpisah, bukan server biasa yang ditunnel (audit 21 Sep 2026):
rem penjagaan lama fail-open — permintaan dianggap tepercaya kecuali membawa
rem header proxy, jadi tunnel yang tidak mengirim header itu akan membuka
rem SELURUH API (impor, hentikan batch, ganti kode) ke internet. Server yang
rem dijalankan berkas ini memakai CATUR_MODE=pembantu: jalur selain halaman
rem pembantu ditolak dari siapa pun, termasuk 127.0.0.1.
rem
rem Server biasa (jalankan-server.cmd, port 8000) tetap dipakai Ray sendiri dan
rem TIDAK boleh ditunnel.
setlocal
cd /d "%~dp0"
set CATUR_MODE=pembantu

echo Menyalakan server pembantu (port 8001, mode pembantu-saja)...
powershell -NoProfile -WindowStyle Hidden -Command "$env:CATUR_MODE='pembantu'; Start-Process -FilePath python -ArgumentList '-X','utf8','-m','uvicorn','app.main:aplikasi','--host','127.0.0.1','--port','8001' -WorkingDirectory '%CD%' -WindowStyle Hidden -RedirectStandardOutput 'data\laporan\log-server-pembantu.txt' -RedirectStandardError 'data\laporan\log-server-pembantu-galat.txt'"

ping -n 6 127.0.0.1 >nul
echo Sekarang nyalakan tunnelnya ke port 8001:
echo   engine\cloudflared\cloudflared.exe tunnel --no-autoupdate --url http://localhost:8001
echo.
echo Alamat publik yang muncul di log tunnel + /pembantu = tautan untuk komputer lain.
ping -n 4 127.0.0.1 >nul
exit /b 0
