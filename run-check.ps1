# Scraping catalogo (ENABLED_SOURCES). Pianificato ogni 2 ore con setup-windows-task.ps1.
# MONITOR_MODE nel .env: discovery (default consigliato a 2h) | full | sniper

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (-not $env:MONITOR_MODE) {
    $envPath = Join-Path $PSScriptRoot ".env"
    if (Test-Path $envPath) {
        Get-Content $envPath | ForEach-Object {
            if ($_ -match '^\s*MONITOR_MODE\s*=\s*(.+)\s*$') {
                $env:MONITOR_MODE = $Matches[1].Trim()
            }
        }
    }
    if (-not $env:MONITOR_MODE) {
        $env:MONITOR_MODE = "discovery"
    }
}
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
