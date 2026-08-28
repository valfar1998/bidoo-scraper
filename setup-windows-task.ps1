# Esegue il monitor ogni 5 minuti dal TUO PC (rete di casa).
# Richiede PowerShell avviato come amministratore.
#
# Uso:  .\setup-windows-task.ps1
# Rimuovi: Unregister-ScheduledTask -TaskName "BidooMonitor" -Confirm:$false

$ErrorActionPreference = "Stop"
$taskName = "BidooMonitor"
$scriptPath = Join-Path $PSScriptRoot "run-check.ps1"

if (-not (Test-Path $scriptPath)) {
    Write-Error "File non trovato: $scriptPath"
}

$action = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$scriptPath`""

$start = (Get-Date).AddMinutes(1)
$trigger = New-ScheduledTaskTrigger `
    -Once `
    -At $start `
    -RepetitionInterval (New-TimeSpan -Minutes 5) `
    -RepetitionDuration ([TimeSpan]::MaxValue)

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
    -Description "Monitor Bidoo con alert Telegram ogni 5 minuti" | Out-Null

Write-Host "Attività '$taskName' creata. Controllo ogni 5 minuti da:"
Write-Host $scriptPath
Write-Host ""
Write-Host "Assicurati di avere il file .env con TELEGRAM_BOT_TOKEN e TELEGRAM_CHAT_ID."
