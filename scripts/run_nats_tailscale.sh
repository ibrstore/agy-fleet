#!/usr/bin/env bash
# run_nats_tailscale.sh
# Runs NATS Server strictly bound to Tailscale private IP (never public 0.0.0.0)
set -euo pipefail

echo "==> Mendeteksi IP Tailscale..."
TAILSCALE_IP=$(tailscale ip -4 2>/dev/null || ip -4 addr show tailscale0 2>/dev/null | grep -oP '(?<=inet\s)\d+(\.\d+){3}' || true)

if [ -z "$TAILSCALE_IP" ]; then
    echo "❌ IP Tailscale tidak ditemukan! Pastikan Tailscale sudah berjalan ('tailscale up')."
    echo "Untuk testing lokal darurat tanpa Tailscale, bind ke 127.0.0.1:"
    TAILSCALE_IP="127.0.0.1"
fi

echo "✅ Mengikat NATS Server ke IP privat: ${TAILSCALE_IP}:4222"

NATS_CONF_DIR="$HOME/.agy-fleet/nats"
mkdir -p "$NATS_CONF_DIR/data"

cat <<EOF > "$NATS_CONF_DIR/nats-server.conf"
# NATS Configuration for agy-fleet
# Bind strictly to Tailscale IP - PUBLIC INTERNET EXPOSURE IS FORBIDDEN
host: "${TAILSCALE_IP}"
port: 4222

jetstream {
    store_dir: "${NATS_CONF_DIR}/data"
    max_mem: 512MB
    max_file: 2GB
}

# Optional auth token
# authorization {
#     token: "YOUR_NATS_CLUSTER_TOKEN"
# }
EOF

echo "==> Memulai NATS Server..."
if command -v nats-server &>/dev/null; then
    exec nats-server -c "$NATS_CONF_DIR/nats-server.conf"
elif command -v docker &>/dev/null; then
    echo "==> Menjalankan NATS via Docker..."
    exec docker run --rm -it \
        --name agy-fleet-nats \
        --network host \
        -v "$NATS_CONF_DIR/nats-server.conf:/nats-server.conf:ro" \
        -v "$NATS_CONF_DIR/data:/data" \
        nats:latest -c /nats-server.conf
else
    echo "❌ nats-server binary atau docker tidak ditemukan."
    echo "Silakan install nats-server via package manager: apt install nats-server / brew install nats-server"
    exit 1
fi
