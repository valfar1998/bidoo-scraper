@echo off
REM Controllo singolo Bidoo (bypass policy PowerShell)
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0run-check.ps1"
pause
