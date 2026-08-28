# Esegue un controllo Bidoo e invia eventuali alert Telegram.
# Usalo con Utilità di pianificazione Windows ogni 5 minuti.

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$python = Get-Command python -ErrorAction SilentlyContinue
if (-not $python) {
    $python = Get-Command py -ErrorAction SilentlyContinue
    if ($python) {
        & py -3 monitor.py --once
        exit $LASTEXITCODE
    }
    Write-Error "Python non trovato. Installa Python 3 e riprova."
}

& $python.Source monitor.py --once
