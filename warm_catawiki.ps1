# Warm-up Catawiki: apre Chrome, aspetta fino a 10 min per Akamai, salva cookie.
# NON serve login Catawiki. Poi: python monitor_catawiki.py

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"

python warm_catawiki.py
