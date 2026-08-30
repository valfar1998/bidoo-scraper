# Un controllo multi-sito (ENABLED_SOURCES) + Bidoo solo se INCLUDE_BIDOO=true nel .env.
# Usalo con Utilità di pianificazione Windows ogni 5 minuti (setup-windows-task.ps1).

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$python = Get-Command python -ErrorAction SilentlyContinue
if (-not $python) {
    $python = Get-Command py -ErrorAction SilentlyContinue
    if ($python) {
        & py -3 monitor_all.py
        exit $LASTEXITCODE
    }
    Write-Error "Python non trovato. Installa Python 3 e riprova."
}

& $python.Source monitor_all.py
