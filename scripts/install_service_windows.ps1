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

# 3. Pasang Shortcut Redundan di Folder Startup Pengguna (Jaminan Auto-Start saat Login)
try {
    $StartupFolder = [Environment]::GetFolderPath("Startup")
    $ShortcutPath = Join-Path $StartupFolder "AntigravityFleetDaemon.lnk"
    $WshShell = New-Object -ComObject WScript.Shell
    $Shortcut = $WshShell.CreateShortcut($ShortcutPath)
    $Shortcut.TargetPath = "wscript.exe"
    $Shortcut.Arguments = "`"$VbsPath`""
    $Shortcut.WorkingDirectory = $RepoDir
    $Shortcut.WindowStyle = 7
    $Shortcut.Description = "Auto-start Antigravity Fleet Daemon"
    $Shortcut.Save()
    Write-Host "✅ Shortcut fail-safe berhasil dipasang di folder Startup Windows."
} catch {
    Write-Warning "Gagal memasang shortcut di folder startup: $_"
}

# 4. Konfigurasi Power Settings (Agar perangkat tidak Sleep saat idle, tetapi layar tetap boleh mati)
try {
    Write-Host "==> Menyetel pengaturan daya agar sistem tidak sleep saat tersambung charger/listrik..."
    powercfg /change standby-timeout-ac 0
    powercfg /change monitor-timeout-ac 10
    powercfg /setacvalueindex SCHEME_CURRENT SUB_BUTTONS LIDACTION 0
    powercfg /setactive SCHEME_CURRENT
    Write-Host "✅ Pengaturan daya: Standby AC = Never, Monitor = 10m, Lid Close = Do Nothing."
} catch {
    Write-Warning "Gagal mengonfigurasi powercfg: $_"
}

# 4. Jalankan sekarang di background secara terlepas (Detached / Hidden)
Write-Host "==> Menjalankan daemon di background (tersembunyi)..."
Start-Process -FilePath "wscript.exe" -ArgumentList "`"$VbsPath`""

Write-Host "✅ Daemon telah aktif berjalan di background!"
Write-Host "Terminal ini aman untuk ditutup sekarang tanpa menghentikan daemon."
