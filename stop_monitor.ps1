# Ferma monitor Python e libera il profilo Playwright (.playwright-profile)
Get-Process python*, py -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
Write-Host "Processi Python fermati. Chiudi anche TUTTE le finestre Chrome, poi rilancia warm_catawiki.py"
