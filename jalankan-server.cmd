@echo off
rem Nyalakan server web catur lalu buka di browser (Ray, 18 Sep 2026:
rem "ini kalau mau run server gimana caranya? bisa kamu buatkan shortcutnya").
rem
rem Sengaja TIDAK menyala otomatis saat Windows hidup: Ray minta analisis hanya
rem berjalan kalau dia sendiri yang menjalankannya. Jendela ini boleh ditutup;
rem servernya proses terpisah dan tetap hidup.
setlocal
cd /d "%~dp0"
set TUNGGU=0

call :cek
if "%ADA%"=="1" (
    echo Server sudah menyala.
    goto buka
)

echo Menyalakan server catur...
powershell -NoProfile -WindowStyle Hidden -Command "Start-Process -FilePath python -ArgumentList '-X','utf8','-m','uvicorn','app.main:aplikasi','--host','127.0.0.1','--port','8000' -WorkingDirectory '%CD%' -WindowStyle Hidden -RedirectStandardOutput 'data\laporan\log-server.txt' -RedirectStandardError 'data\laporan\log-server-galat.txt'"

:tunggu
ping -n 3 127.0.0.1 >nul
call :cek
if "%ADA%"=="1" goto buka
set /a TUNGGU=%TUNGGU%+1
if %TUNGGU% lss 15 goto tunggu
echo.
echo Server tidak menjawab. Lihat data\laporan\log-server-galat.txt
pause
exit /b 1

:buka
echo Alamat: http://127.0.0.1:8000/
call :analisa
start "" "http://127.0.0.1:8000/"
ping -n 3 127.0.0.1 >nul
exit /b 0

rem Ray, 25 Sep 2026: "set setiap klik mulai maka otomatis analisa juga".
rem Menekan pintasan ini = Ray sendiri yang meminta analisis, jadi tidak
rem melanggar aturan "jangan menyala otomatis saat Windows hidup".
rem
rem Yang dinyalakan penjaga antrean, BUKAN batch langsung: penjaga menghormati
rem batas batch, mengisi slot kosong dengan --bagian berbeda supaya dua batch
rem tidak mengerjakan partai yang sama, dan berhenti sendiri kalau semua
rem partai sudah dianalisis.
rem
rem JEDA DIHORMATI (Ray, 29 Sep 2026, pilihan "Hormati Jeda"): dulu pintasan ini
rem selalu mencabut Jeda, jadi Jeda yang ditekan di halaman Mesin hilang tiap
rem kali server dinyalakan. Kini: kalau sedang dijeda, tidak ada yang
rem dinyalakan; jendela ini hanya memberi tahu sejak kapan dan cara lanjutnya.
:analisa
echo Menyalakan analisis (penjaga antrean)...
powershell -NoProfile -Command "$b='http://127.0.0.1:8000/api/analisa'; try { $s = Invoke-RestMethod ($b+'/status') -TimeoutSec 30; if ($s.jeda.jeda) { '  Mesin sedang DIJEDA sejak ' + $s.jeda.sejak + ' - analisis tidak dinyalakan.'; '  Mau lanjut? Tekan Lanjutkan di halaman Mesin (http://127.0.0.1:8000/static/mesin.html).' } else { $a = Invoke-RestMethod ($b+'/antrean/jaga?jalan=true') -Method Post -TimeoutSec 30; if ($a.penjaga_pid) { '  penjaga ' + $a.status + ' (PID ' + $a.penjaga_pid + ') - pekerjaan berikutnya dimulai sendiri' } else { '  penjaga tidak menyala (' + $a.status + '); nyalakan dari halaman Mesin' }; $n = @($s.tugas | Where-Object { $_.status -eq 'berjalan' }).Count; '  batch berjalan: ' + $n + ' dari ' + $s.maks_batch + '; belum dianalisis: ' + ($s.belum_saya + $s.belum_gm) } } catch { '  analisis tidak bisa dinyalakan: ' + $_.Exception.Message } "
exit /b 0

:cek
set ADA=0
for /f %%p in ('powershell -NoProfile -Command "if (Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue) { 1 } else { 0 }"') do set ADA=%%p
exit /b 0
