@echo off
REM Avvia setup attività pianificata (bypass policy PowerShell)
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup-windows-task.ps1"
pause
