# Esegue tutti i monitor abilitati in ENABLED_SOURCES (+ Bidoo se INCLUDE_BIDOO=true).
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
