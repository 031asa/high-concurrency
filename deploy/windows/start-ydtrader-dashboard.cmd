@echo off
setlocal
title YDTrader Dashboard
echo Starting WSL services...

wsl.exe -d Ubuntu-24.04 -u hello -- bash -lc "cd /home/hello/projects/high-concurrency && bash scripts/ydtrader_stack.sh install-start"
if errorlevel 1 goto failed

echo Keeping WSL active while the stack is running...
powershell.exe -NoProfile -Command "$ErrorActionPreference='Stop'; $hold=Start-Process -FilePath wsl.exe -ArgumentList '-d Ubuntu-24.04 -u hello -- bash /home/hello/projects/high-concurrency/scripts/ydtrader_stack.sh hold' -WindowStyle Hidden -PassThru; if ($hold.WaitForExit(500) -and $hold.ExitCode -ne 0) { exit 1 }"
if errorlevel 1 goto failed

echo Waiting for Dashboard HTTP readiness...
powershell.exe -NoProfile -Command "$ErrorActionPreference='Stop'; for ($attempt=0; $attempt -lt 30; $attempt++) { try { $response=Invoke-WebRequest -UseBasicParsing -Uri 'http://127.0.0.1:8080/api/status' -TimeoutSec 2; if ($response.StatusCode -eq 200) { exit 0 } } catch {}; Start-Sleep -Seconds 1 }; exit 1"
if errorlevel 1 goto failed

echo Dashboard ready: http://127.0.0.1:8080/
if /i "%~1"=="--check" exit /b 0
start "" "http://127.0.0.1:8080/"
if errorlevel 1 goto failed
exit /b 0

:failed
echo.
echo Startup failed. Keep this window open and report the error above.
echo Manual URL: http://127.0.0.1:8080/
if /i "%~1"=="--check" exit /b 1
pause
exit /b 1
