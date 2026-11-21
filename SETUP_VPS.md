# Panduan Setup Control Plane di VPS (`SETUP_VPS.md`)

Dokumen ini adalah panduan manual singkat untuk menyiapkan Control Plane **agy-fleet** di VPS Anda. Seluruh kredensial sensitif diatur langsung oleh Anda melalui CLI lokal `fleet-secret` di terminal VPS, sehingga **tidak pernah terbaca oleh model AI, tidak disimpan dalam file plaintext `.env`, dan tidak terekspos ke Telegram.**

---

## 1. Prasyarat Jaringan Tailscale

NATS Server **WAJIB** terikat (*bind*) hanya ke IP privat Tailscale (100.x.y.z) dan **DILARANG KERAS** dibuka ke internet publik.

1. Hubungkan VPS ke Tailscale:
   ```bash
   tailscale up
   ```
2. Cek IP privat Tailscale VPS:
   ```bash
   TAILSCALE_IP=$(tailscale ip -4)
   echo "Tailscale IP VPS Anda: $TAILSCALE_IP"
   ```
   *(Catat IP ini, misalnya `100.64.0.1`. IP ini akan digunakan oleh PC dan Laptop untuk terhubung ke NATS).*

---

## 2. Menjalankan NATS Server (JetStream & KV Store)

Gunakan skrip yang disediakan untuk menjalankan NATS Server yang otomatis mengikat ke IP Tailscale:

```bash
cd /root/agy-fleet
./scripts/run_nats_tailscale.sh
```

Atau jalankan sebagai background service / systemd:
```bash
# Pastikan nats-server terinstall di VPS:
apt update && apt install -y nats-server
```

---

## 3. Menyiapkan Virtualenv & Dependensi

Di direktori `/root/agy-fleet`:
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

---

## 4. Konfigurasi Kredensial via `fleet-secret` (Manual oleh Pemilik)

Jalankan perintah berikut di terminal VPS. CLI `fleet-secret` akan meminta input secara interaktif tanpa menampilkan karakter di layar (*hidden input*):

```bash
# 1. Simpan Token Bot Telegram Anda
fleet-secret set TELEGRAM_BOT_TOKEN

# 2. Simpan User ID Telegram Pemilik (hanya akun ini yang dilayani)
# (Dapatkan ID Anda via @userinfobot di Telegram)
fleet-secret set OWNER_USER_ID

# 3. Simpan Chat ID Grup Telegram resmi (format negatif, misal -1001234567890)
fleet-secret set ALLOWED_GROUP_ID

# 4. (Opsional) Token NATS Cluster jika menggunakan autentikasi token
fleet-secret set NATS_TOKEN

# 5. Cek daftar key yang tersimpan (nilai rahasia TIDAK AKAN ditampilkan)
fleet-secret list-keys
```

---

## 5. Menjalankan Orchestrator (Bot Telegram)

Setelah kredensial tersimpan di vault:

```bash
# Menjalankan langsung untuk pengujian:
.venv/bin/python -m orchestrator.coordinator
```

Untuk memasang sebagai daemon otomatis di VPS:
```bash
./scripts/install_service_linux.sh orchestrator
```

Periksa status service:
```bash
systemctl --user status agy-orchestrator
journalctl --user -u agy-orchestrator -f
```

---

## 6. Verifikasi Awal di Grup Telegram

1. Buka Grup Telegram resmi Anda.
2. Kirim perintah `/start` atau `/status`.
3. Orchestrator akan memverifikasi `chat_id` dan `from_user.id`. Pesan dari anggota lain atau pesan yang di-*forward* akan ditolak otomatis.
4. Gunakan `/dashboard` untuk membuat dan menyematkan (*pin*) live dashboard status online/offline ke forum topic yang Anda tentukan.
