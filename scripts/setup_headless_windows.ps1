# setup_headless_windows.ps1
# Configures Windows for Headless Auto-Logon using official Microsoft Sysinternals Autologon
param (
    [string]$Password = ""
)

$ErrorActionPreference = "Stop"

Write-Host "=========================================================="
Write-Host "  ANTIGRAVITY FLEET - WINDOWS HEADLESS AUTO-LOGON SETUP"
Write-Host "=========================================================="

# 1. Nonaktifkan batasan Windows Hello Passwordless agar Auto-Logon diizinkan
try {
    $regPath = "HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\PasswordLess\Device"
    if (Test-Path $regPath) {
        Set-ItemProperty -Path $regPath -Name "DevicePasswordLessBuildVersion" -Value 0 -Force
        Write-Host "✅ Kebijakan Windows Hello disesuaikan (Auto-logon diizinkan)."
    }
} catch {
    Write-Warning "Catatan: Butuh PowerShell 'Run as Administrator' untuk mengubah registry PasswordLess."
}

# 2. Unduh Autologon resmi dari Microsoft Sysinternals
$TempExe = Join-Path $env:TEMP "Autologon64.exe"
$Url = "https://live.sysinternals.com/Autologon64.exe"

Write-Host "==> Mengunduh Microsoft Sysinternals Autologon..."
try {
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    Invoke-WebRequest -Uri $Url -OutFile $TempExe -UseBasicParsing
    Write-Host "✅ Tool resmi Microsoft berhasil diunduh ke: $TempExe"
} catch {
    Write-Error "Gagal mengunduh Autologon dari live.sysinternals.com: $_"
    exit 1
}

# 3. Jalankan konfigurasi
if ($Password -ne "") {
    Write-Host "==> Mengonfigurasi Auto-Logon secara otomatis untuk user: $env:USERNAME..."
    Start-Process -FilePath $TempExe -ArgumentList "`"$env:USERNAME`" `"$env:USERDOMAIN`" `"$Password`"" -Wait -NoNewWindow
    Write-Host "✅ Auto-Logon berhasil dikonfigurasi dan password telah dienkripsi via LSA Secrets."
} else {
    Write-Host "==> Membuka GUI Microsoft Autologon..."
    Write-Host "👉 Username ($env:USERNAME) dan Komputer ($env:USERDOMAIN) sudah terisi otomatis."
    Write-Host "👉 Cukup ketik Password Windows Anda pada kotak yang tersedia lalu klik 'Enable'."
    Start-Process -FilePath $TempExe -Wait
    Write-Host "✅ Konfigurasi GUI selesai."
}

# Bersihkan file executable sementara
if (Test-Path $TempExe) {
    Remove-Item -Path $TempExe -Force -ErrorAction SilentlyContinue
}

Write-Host ""
Write-Host "🎉 PC/Laptop ini sekarang siap mode HEADLESS!"
Write-Host "Setiap kali PC dinyalakan atau restart (misal mati lampu), Windows akan:"
Write-Host "  1. Otomatis masuk ke akun pengguna ($env:USERNAME)"
Write-Host "  2. Otomatis menghubungkan Tailscale ke VPS"
Write-Host "  3. Otomatis menjalankan background daemon armada agy"
Write-Host "=========================================================="
