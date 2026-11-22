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

# 1. Simpan konfigurasi node_id ke ~/.agy-fleet/daemon.json
$ConfigDir = Join-Path $env:USERPROFILE ".agy-fleet"
if (-not (Test-Path $ConfigDir)) { New-Item -ItemType Directory -Path $ConfigDir -Force | Out-Null }
$ConfigFile = Join-Path $ConfigDir "daemon.json"

# Auto-detect node_id based on fleet hardware inventory if not explicitly provided
$cName = $env:COMPUTERNAME.ToLower()
$uName = $env:USERNAME.ToLower()

if (Test-Path $ConfigFile) {
    try {
        $Existing = Get-Content $ConfigFile -Raw | ConvertFrom-Json
        if ($Existing.node_id -and ($NodeId -eq $env:COMPUTERNAME.ToLower())) {
            $NodeId = $Existing.node_id
        }
    } catch {}
}

if ($NodeId -eq $env:COMPUTERNAME.ToLower()) {
    if ($cName -like "*mat4kru*" -or $uName -like "*diba*") {
        $NodeId = "pc-diba"
    } elseif ($cName -like "*qgv4egb*" -or $uName -like "*naufa*") {
        $NodeId = "pc-naufal"
    } elseif ($cName -like "*k3jacf2f*" -or $cName -like "*asus*") {
        $NodeId = "lap-asus"
    } elseif ($cName -like "*kq902s5*" -or $cName -like "*febrina*") {
        $NodeId = "lap-user"
    }
}

$DevType = if ($NodeId -like "*lap*" -or $NodeId -like "*laptop*") { "laptop" } else { "pc" }
$ConfigData = [ordered]@{
    node_id = $NodeId
    device_name = $env:COMPUTERNAME
    device_type = $DevType
    os_name = "windows"
    nats_url = "nats://100.66.49.13:4222"
    heartbeat_interval = 5.0
    agy_binary_path = "agy"
}
$ConfigData | ConvertTo-Json -Depth 5 | Set-Content $ConfigFile -Encoding utf8
Write-Host "✅ Konfigurasi tersimpan di $ConfigFile (Node ID: $($ConfigData.node_id), NATS: $($ConfigData.nats_url))"

# 2. Hentikan proses daemon lama yang mungkin menggantung / stale
Write-Host "==> Membersihkan proses daemon lama..."
try {
    Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object { $_.CommandLine -like "*daemon.agent*" } | ForEach-Object {
        Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
    }
    $PidFile = Join-Path $ConfigDir "daemon_$NodeId.pid"
    if (Test-Path $PidFile) { Remove-Item $PidFile -Force -ErrorAction SilentlyContinue }
} catch {}

# 3. Pastikan script watchdog hidden tersedia
$VbsPath = Join-Path $RepoDir "scripts\run_watchdog_hidden.vbs"

# 4. Daftarkan ke Task Scheduler jika Administrator, atau lewati jika non-admin
$TaskRegistered = $false
try {
    $isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
    if ($isAdmin) {
        $Action = New-ScheduledTaskAction -Execute "wscript.exe" -Argument "`"$VbsPath`"" -WorkingDirectory $RepoDir
        $Trigger = New-ScheduledTaskTrigger -AtLogOn
        $Settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit 0 -RestartCount 99 -RestartInterval (New-TimeSpan -Minutes 1)
        
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
        Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger -Settings $Settings -Description "Antigravity Fleet Self-Healing Watchdog for $NodeId"
        Write-Host "✅ Task Scheduler '$TaskName' berhasil didaftarkan dengan Watchdog Self-Healing."
        $TaskRegistered = $true
    } else {
        Write-Warning "PowerShell tidak berjalan sebagai Administrator. Task Scheduler dilewati (aman, menggunakan Startup Shortcut & Direct Watchdog)."
    }
} catch {
    Write-Warning "Task Scheduler tidak dapat didaftarkan: $_"
}

# 5. Pasang Shortcut Redundan di Folder Startup Pengguna (Jaminan Auto-Start saat Login)
try {
    $StartupFolder = [Environment]::GetFolderPath("Startup")
    $ShortcutPath = Join-Path $StartupFolder "AntigravityFleetDaemon.lnk"
    $WshShell = New-Object -ComObject WScript.Shell
    $Shortcut = $WshShell.CreateShortcut($ShortcutPath)
    $Shortcut.TargetPath = "wscript.exe"
    $Shortcut.Arguments = "`"$VbsPath`""
    $Shortcut.WorkingDirectory = $RepoDir
    $Shortcut.WindowStyle = 7
    $Shortcut.Description = "Auto-start Antigravity Fleet Daemon Watchdog"
    $Shortcut.Save()
    Write-Host "✅ Shortcut fail-safe Watchdog berhasil dipasang di folder Startup Windows."
} catch {
    Write-Warning "Gagal memasang shortcut di folder startup: $_"
}

# 6. Konfigurasi Power Settings (Agar perangkat tidak Sleep saat idle, tetapi layar tetap boleh mati)
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

# 7. Jalankan daemon sekarang di background secara terlepas (Detached / Hidden)
Write-Host "==> Menjalankan daemon di background via Watchdog..."
if ($TaskRegistered) {
    try {
        Start-ScheduledTask -TaskName $TaskName -ErrorAction Stop
        Write-Host "✅ Daemon telah aktif berjalan via Task Scheduler!"
    } catch {
        Start-Process "wscript.exe" -ArgumentList "`"$VbsPath`"" -WorkingDirectory $RepoDir
        Write-Host "✅ Daemon telah aktif berjalan di background via Watchdog!"
    }
} else {
    Start-Process "wscript.exe" -ArgumentList "`"$VbsPath`"" -WorkingDirectory $RepoDir
    Write-Host "✅ Daemon telah aktif berjalan di background via Watchdog!"
}

Write-Host ""
Write-Host "🎉 [SUKSES] Node '$NodeId' telah aktif dan terlindungi Self-Healing Watchdog 24/7!"
Write-Host "Terminal ini aman untuk ditutup sekarang."
