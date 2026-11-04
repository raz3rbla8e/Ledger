@echo off
REM Double-click to run Ledger (first time: run "python -m ledger setup" instead)
cd /d "%~dp0"
python -m ledger run
pause
