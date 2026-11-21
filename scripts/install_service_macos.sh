#!/usr/bin/env bash
# install_service_macos.sh
# Installs launchd LaunchAgent for agy-daemon on macOS
set -euo pipefail

NODE_ID="${1:-$(scutil --get ComputerName 2>/dev/null || hostname -s | tr '[:upper:]' '[:lower:]')}"
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_PYTHON="$REPO_DIR/.venv/bin/python"

if [ ! -f "$VENV_PYTHON" ]; then
    echo "❌ Virtualenv python tidak ditemukan di $VENV_PYTHON."
    exit 1
fi

PLIST_DIR="$HOME/Library/LaunchAgents"
mkdir -p "$PLIST_DIR"
PLIST_FILE="$PLIST_DIR/com.antigravity.fleet.daemon.plist"

echo "==> Memasang LaunchAgent untuk node '$NODE_ID' di $PLIST_FILE..."

cat <<EOF > "$PLIST_FILE"
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.antigravity.fleet.daemon</string>
    <key>ProgramArguments</key>
    <array>
        <string>$VENV_PYTHON</string>
        <string>-m</string>
        <string>daemon.agent</string>
    </array>
    <key>WorkingDirectory</key>
    <string>$REPO_DIR</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>FLEET_NODE_ID</key>
        <string>$NODE_ID</string>
        <key>PATH</key>
        <string>/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:$HOME/.local/bin</string>
    </dict>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>$HOME/.agy-fleet/daemon.stdout.log</string>
    <key>StandardErrorPath</key>
    <string>$HOME/.agy-fleet/daemon.stderr.log</string>
</dict>
</plist>
EOF

mkdir -p "$HOME/.agy-fleet"
launchctl unload "$PLIST_FILE" 2>/dev/null || true
launchctl load -w "$PLIST_FILE"

echo "✅ LaunchAgent berhasil dipasang dan diaktifkan!"
echo "Cek status: launchctl list | grep antigravity"
