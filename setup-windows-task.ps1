# Esegue il monitor ogni 5 minuti dal TUO PC (rete di casa).
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
    -RepetitionInterval (New-TimeSpan -Minutes 5) `
    -RepetitionDuration (New-TimeSpan -Days 3650)

$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 10)

$existing = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($existing) {
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
}

Register-ScheduledTask `
    -TaskName $taskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Description "Monitor multi-sito (Remundo/PrezziShock/...) con alert Telegram ogni 5 minuti" | Out-Null

Write-Host "Attività '$taskName' creata. Controllo ogni 5 minuti da:"
Write-Host $scriptPath
Write-Host ""
Write-Host "Assicurati di avere il file .env con TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID,"
Write-Host "ENABLED_SOURCES e INCLUDE_BIDOO=false (default consigliato)."
Write-Host "Se avevi 'BidooMonitor', puoi rimuoverla:"
Write-Host "  Unregister-ScheduledTask -TaskName BidooMonitor -Confirm:`$false"
