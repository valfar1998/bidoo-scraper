# Monitor locale in modalità snipe (timer <= 60s, poll ogni 15s).
# Tieni il PC acceso. Interrompi con Ctrl+C.

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$env:MONITOR_MODE = "snipe"

$python = Get-Command python -ErrorAction SilentlyContinue
if (-not $python) {
    $python = Get-Command py -ErrorAction SilentlyContinue
    if ($python) {
        & py -3 monitor.py --mode snipe
        exit $LASTEXITCODE
    }
    Write-Error "Python non trovato."
}

& $python.Source monitor.py --mode snipe
