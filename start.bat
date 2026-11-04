@echo off
REM Ledger — one double-click: checks Python, stops any old copy, runs setup (keeps what already works), starts Ledger.
REM First time, or to change a password/token: run  python -m ledger setup  instead (it asks questions).
cd /d "%~dp0"
title Ledger
set "PY="
py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3,11) else 1)" >nul 2>nul && set "PY=py -3"
if not defined PY python -c "import sys; sys.exit(0 if sys.version_info >= (3,11) else 1)" >nul 2>nul && set "PY=python"
if not defined PY (
  echo Python 3.11 or newer wasn't found. Install it from python.org ^(tick "Add python.exe to PATH"^),
  echo then double-click this file again.
  pause
  exit /b 1
)
echo Stopping any older Ledger that's still running...
powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"Name like 'python%%'\" | Where-Object { $_.CommandLine -match '-m ledger' -or $_.CommandLine -match 'run_module' } | ForEach-Object { Invoke-CimMethod -InputObject $_ -MethodName Terminate | Out-Null }"
schtasks /End /TN Ledger >nul 2>nul
timeout /t 2 /nobreak >nul
echo.
%PY% -m ledger setup --auto
echo.
echo Checking that it started...
timeout /t 5 /nobreak >nul
%PY% -m ledger status
if errorlevel 1 goto notrunning
start "" http://127.0.0.1:8765
echo.
echo Ledger is running in the background and starts by itself from now on. You can close this window.
pause
exit /b 0
:notrunning
echo.
echo Ledger did NOT start. Take a screenshot of this window and send it to Claude.
if exist "%USERPROFILE%\Ledger\logs\crash.log" type "%USERPROFILE%\Ledger\logs\crash.log"
pause
exit /b 1
