# install_service_windows.ps1
# Sets up Windows Background Runner & Task Scheduler for agy-daemon
param (
    [string]$NodeId = $env:COMPUTERNAME.ToLower(),
    [string]$TaskName = "AntigravityFleetDaemon"
)

$ErrorActionPreference = "Stop"

Write-Host "==> Menyiapkan Background Service untuk Windows Node: $NodeId..."

$RepoDir = Split-Path -Parent $PSScriptRoot
$PythonExe = Join-Path $RepoDir ".venv\Scripts\python.exe"
$PythonWExe = Join-Path $RepoDir ".venv\Scripts\pythonw.exe"

if (-not (Test-Path $PythonExe)) {
    Write-Error "Virtualenv Python tidak ditemukan di: $PythonExe. Pastikan virtualenv sudah dibuat."
    exit 1
}

# Gunakan pythonw.exe jika ada (menjalankan tanpa jendela konsol sama sekali)
$TargetPython = if (Test-Path $PythonWExe) { $PythonWExe } else { $PythonExe }

# 1. Buat Runner VBScript untuk eksekusi 100% tersembunyi (Hidden Window)
$VbsPath = Join-Path $RepoDir "scripts\run_daemon_hidden.vbs"
$VbsContent = @"
Set WshShell = CreateObject("WScript.Shell")
WshShell.CurrentDirectory = "$RepoDir"
WshShell.Run """" & "$PythonExe" & """ -m daemon.agent", 0, False
Set WshShell = Nothing
"@
[System.IO.File]::WriteAllText($VbsPath, $VbsContent)

# 2. Daftarkan ke Task Scheduler agar otomatis jalan saat login / startup
try {
    $Action = New-ScheduledTaskAction -Execute "wscript.exe" -Argument "`"$VbsPath`"" -WorkingDirectory $RepoDir
    $Trigger = New-ScheduledTaskTrigger -AtLogOn
    $Settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit 0 -RestartCount 99 -RestartInterval (New-TimeSpan -Minutes 1)
    
    # Hapus task lama jika ada
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue

    # Daftarkan task baru
    Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger -Settings $Settings -Description "Antigravity Fleet Daemon for $NodeId"
    Write-Host "✅ Task Scheduler '$TaskName' berhasil didaftarkan."
} catch {
    Write-Warning "Gagal mendaftarkan Task Scheduler (butuh Run as Administrator): $_"
}

# 3. Jalankan sekarang di background secara terlepas (Detached / Hidden)
Write-Host "==> Menjalankan daemon di background (tersembunyi)..."
Start-Process -FilePath "wscript.exe" -ArgumentList "`"$VbsPath`""

Write-Host "✅ Daemon telah aktif berjalan di background!"
Write-Host "Terminal ini aman untuk ditutup sekarang tanpa menghentikan daemon."
