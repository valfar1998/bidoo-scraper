# Esegue lo scraping catalogo ogni 2 ore dal TUO PC (rete di casa).
#
# Uso (scegli uno):
#   1. Doppio click su setup-windows-task.bat
#   2. PowerShell come amministratore:
#      powershell -ExecutionPolicy Bypass -File .\setup-windows-task.ps1
#
# Rimuovi: Unregister-ScheduledTask -TaskName "BidooMonitor" -Confirm:$false

$ErrorActionPreference = "Stop"
$taskName = "ResaleMonitor"
$scriptPath = Join-Path $PSScriptRoot "run-check.ps1"

if (-not (Test-Path $scriptPath)) {
    Write-Error "File non trovato: $scriptPath"
}

$action = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$scriptPath`""

$start = (Get-Date).AddMinutes(1)
# MaxValue non è accettato dal Pianificatore Windows; 10 anni bastano.
$trigger = New-ScheduledTaskTrigger `
    -Once `
    -At $start `
    -RepetitionInterval (New-TimeSpan -Hours 2) `
    -RepetitionDuration (New-TimeSpan -Days 3650)

$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 120)

$existing = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($existing) {
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
}

Register-ScheduledTask `
    -TaskName $taskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Description "Scraping catalogo multi-sito (discovery) ogni 2 ore + alert se MONITOR_MODE=full/sniper" | Out-Null

$legacy = Get-ScheduledTask -TaskName "BidooMonitor" -ErrorAction SilentlyContinue
if ($legacy) {
    Unregister-ScheduledTask -TaskName "BidooMonitor" -Confirm:$false
    Write-Host "Rimossa attivita legacy 'BidooMonitor'."
}

$envPath = Join-Path $PSScriptRoot ".env"
if (-not (Test-Path $envPath)) {
    $example = Join-Path $PSScriptRoot ".env.example"
    if (Test-Path $example) {
        Copy-Item $example $envPath
        Write-Host ""
        Write-Host "Creato .env da .env.example."
        Write-Host "Apri .env e imposta TELEGRAM_BOT_TOKEN e TELEGRAM_CHAT_ID."
    } else {
        Write-Warning ".env mancante: crea il file con TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, ENABLED_SOURCES, INCLUDE_BIDOO=false"
    }
} else {
    $lines = Get-Content $envPath | Where-Object { $_ -match '^\s*[^#]' }
    $vars = @{}
    foreach ($line in $lines) {
        if ($line -match '^\s*([^=]+)=(.*)$') {
            $vars[$Matches[1].Trim()] = $Matches[2].Trim()
        }
    }
    $required = @("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "ENABLED_SOURCES", "INCLUDE_BIDOO")
    $missing = @($required | Where-Object { -not $vars.ContainsKey($_) -or -not $vars[$_] })
    if ($missing.Count -gt 0) {
        Write-Warning ".env incompleto. Mancano o sono vuoti: $($missing -join ', ')"
    } else {
        Write-Host ""
        Write-Host ".env OK: Telegram configurato, ENABLED_SOURCES impostato, INCLUDE_BIDOO=$($vars['INCLUDE_BIDOO'])."
    }
}

Write-Host ""
Write-Host "Attivita '$taskName' creata. Scraping ogni 2 ore da:"
Write-Host $scriptPath
Write-Host ""
Write-Host "MONITOR_MODE nel .env: discovery (solo catalogo) | full | sniper"
Write-Host "Test manuale: powershell -ExecutionPolicy Bypass -File .\run-check.ps1"
