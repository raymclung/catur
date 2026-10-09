@echo off
rem Matikan server web catur (pasangan jalankan-server.cmd).
rem Batch analisis dan pembantu tidak ikut dimatikan: batch berjalan sebagai
rem proses sendiri, dan tab pembantu berhenti sendiri karena servernya hilang.
rem
rem Penjaga antrean IKUT dimatikan (hanya penjaganya, bukan batch anaknya).
rem Dulu dibiarkan: penjaga berjalan lepas, jadi setelah restart ia tetap
rem hidup dengan kode LAMA, dan jalankan-server.cmd menjawab "sudah jalan"
rem (27 Sep 2026: perbaikan batch kembar tidak aktif di penjaga selama 4 jam).
rem jalankan-server.cmd menyalakan penjaga baru sesudahnya.
setlocal
echo Mematikan server catur...
powershell -NoProfile -Command "$j = Get-CimInstance Win32_Process | Where-Object { $_.Name -like 'python*' -and $_.CommandLine -like '*jaga_antrean.py*--jaga*' }; if ($j) { $j | ForEach-Object { Stop-Process -Id $_.ProcessId -Force; \"penjaga antrean (PID $($_.ProcessId)) dimatikan; batch yang jalan dibiarkan selesai\" } } else { 'penjaga antrean memang tidak jalan' }"
powershell -NoProfile -Command "$p = Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1 -ExpandProperty OwningProcess; if ($p) { Stop-Process -Id $p -Force; \"server (PID $p) dimatikan\" } else { 'server memang tidak menyala' }"
ping -n 4 127.0.0.1 >nul
exit /b 0
