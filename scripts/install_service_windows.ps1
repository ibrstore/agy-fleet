# install_service_windows.ps1
# Sets up Windows Scheduled Task for agy-daemon to run automatically on logon in the background
param (
    [string]$NodeId = $env:COMPUTERNAME.ToLower(),
    [string]$TaskName = "AntigravityFleetDaemon"
)

$ErrorActionPreference = "Stop"

Write-Host "==> Menyiapkan Task Scheduler untuk Windows Node: $NodeId..."

$RepoDir = Split-Path -Parent $PSScriptRoot
$PythonExe = Join-Path $RepoDir ".venv\Scripts\python.exe"

if (-not (Test-Path $PythonExe)) {
    Write-Error "Virtualenv Python tidak ditemukan di: $PythonExe. Pastikan virtualenv sudah dibuat."
    exit 1
}

$Action = New-ScheduledTaskAction -Execute $PythonExe -Argument "-m daemon.agent" -WorkingDirectory $RepoDir
$Trigger = New-ScheduledTaskTrigger -AtLogOn
$Settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit 0 -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)

# Unregister existing task if present
Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue

# Register task under current user
Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger -Settings $Settings -Description "Antigravity Fleet Node Agent for $NodeId"

Write-Host "==> Menjalankan task sekarang..."
Start-ScheduledTask -TaskName $TaskName

Write-Host "✅ Task Scheduler '$TaskName' berhasil didaftarkan dan dijalankan!"
Write-Host "Untuk memeriksa status: Get-ScheduledTask -TaskName $TaskName"
