# Monitor continuo per rivendita (loop ogni POLL_INTERVAL, default 5 min).
# Tieni il PC acceso. Interrompi con Ctrl+C.

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$python = Get-Command python -ErrorAction SilentlyContinue
if (-not $python) {
    $python = Get-Command py -ErrorAction SilentlyContinue
    if ($python) {
        & py -3 monitor.py
        exit $LASTEXITCODE
    }
    Write-Error "Python non trovato."
}

& $python.Source monitor.py
