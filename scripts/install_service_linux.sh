#!/usr/bin/env bash
# install_service_linux.sh
# Installs systemd service for agy-daemon (on node) or agy-orchestrator (on VPS)
set -euo pipefail

SERVICE_TYPE="${1:-daemon}" # "daemon" or "orchestrator"
NODE_ID="${2:-$(hostname -s | tr '[:upper:]' '[:lower:]')}"
USER_NAME="$(whoami)"
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_PYTHON="$REPO_DIR/.venv/bin/python"

if [ ! -f "$VENV_PYTHON" ]; then
    echo "❌ Virtualenv python tidak ditemukan di $VENV_PYTHON."
    echo "Silakan jalankan 'uv venv && uv sync' terlebih dahulu."
    exit 1
fi

SYSTEMD_DIR="$HOME/.config/systemd/user"
mkdir -p "$SYSTEMD_DIR"

if [ "$SERVICE_TYPE" = "daemon" ]; then
    SERVICE_NAME="agy-daemon"
    EXEC_CMD="$VENV_PYTHON -m daemon.agent"
    echo "==> Memasang service systemd: $SERVICE_NAME untuk node '$NODE_ID'..."

    cat <<EOF > "$SYSTEMD_DIR/${SERVICE_NAME}.service"
[Unit]
Description=Antigravity Fleet Node Daemon ($NODE_ID)
After=network.target

[Service]
Type=simple
WorkingDirectory=$REPO_DIR
Environment="FLEET_NODE_ID=$NODE_ID"
ExecStart=$EXEC_CMD
Restart=always
RestartSec=5

[Install]
WantedBy=default.target
EOF

elif [ "$SERVICE_TYPE" = "orchestrator" ]; then
    SERVICE_NAME="agy-orchestrator"
    EXEC_CMD="$VENV_PYTHON -m orchestrator.coordinator"
    echo "==> Memasang service systemd: $SERVICE_NAME (Control Plane)..."

    cat <<EOF > "$SYSTEMD_DIR/${SERVICE_NAME}.service"
[Unit]
Description=Antigravity Fleet Telegram Orchestrator
After=network.target

[Service]
Type=simple
WorkingDirectory=$REPO_DIR
ExecStart=$EXEC_CMD
Restart=always
RestartSec=5

[Install]
WantedBy=default.target
EOF
else
    echo "❌ Tipe service '$SERVICE_TYPE' tidak valid. Gunakan 'daemon' atau 'orchestrator'."
    exit 1
fi

echo "==> Mengaktifkan service via systemctl --user..."
systemctl --user daemon-reload
systemctl --user enable "$SERVICE_NAME"
systemctl --user restart "$SERVICE_NAME"

echo "✅ Service $SERVICE_NAME berhasil diaktifkan!"
echo "Status: systemctl --user status $SERVICE_NAME"
echo "Log:    journalctl --user -u $SERVICE_NAME -f"
