# Client Onboarding Prompt (`CLIENT_PROMPT.md`)

Gunakan prompt di bawah ini untuk diberikan langsung ke sesi lokal `agy` di masing-masing perangkat klien:
- **`pc-naufal`** (PC Naufal)
- **`pc-diba`** (PC Diba)
- **`lap-asus`** (Laptop Asus)
- **`lap-user`** (Laptop User)
- **`pc-nci`** (PC Nci)

Salin teks prompt di bawah ini ke prompt `agy`, lalu cukup ubah parameter `<NAMA_NODE>` (misal: `pc-naufal`, `pc-diba`, `lap-asus`, `lap-user`, atau `pc-nci`) dan `<IP_TAILSCALE_VPS>` (misal: IP Tailscale VPS Anda, contoh `100.64.0.1`):

---

```markdown
Kamu bertindak sebagai Node Installer untuk agy-fleet.
Tugasmu adalah menyiapkan agy-fleet daemon di mesin ini agar terhubung ke Control Plane VPS secara otomatis dan aman sebagai background service.

Parameter Node ini:
- NODE_ID: "<NAMA_NODE>" (pilihan: pc-naufal | pc-diba | lap-asus | lap-user | pc-nci)
- NATS_URL: "nats://100.66.49.13:4222"
- REPO_URL: "https://github.com/ibrstore/agy-fleet.git"

Instruksi Eksekusi:
1. Pastikan koneksi Tailscale ke VPS aktif (tes ping ke 100.66.49.13).
2. Clone repository agy-fleet ke folder lokal:
   - Linux/macOS: git clone https://github.com/ibrstore/agy-fleet.git ~/agy-fleet
   - Windows: git clone https://github.com/ibrstore/agy-fleet.git $HOME\agy-fleet
3. Masuk ke direktori agy-fleet dan siapkan environment:
   - Buat virtualenv Python 3.11+: python -m venv .venv
   - Aktifkan virtualenv dan install dependensi: pip install -e .
4. Konfigurasikan daemon lokal di ~/.agy-fleet/daemon.json dengan format:
   {
     "node_id": "<NAMA_NODE>",
     "device_type": "<pc atau laptop>",
     "nats_url": "nats://100.66.49.13:4222"
   }
5. Daftarkan dan jalankan daemon sebagai background service serta optimalkan power setting (never sleep on AC):
   - Di Windows (PowerShell): .\scripts\install_service_windows.ps1 -NodeId "<NAMA_NODE>"
   - Di Linux: ./scripts/install_service_linux.sh daemon <NAMA_NODE>
   - Di macOS: ./scripts/install_service_macos.sh <NAMA_NODE>
6. Verifikasi bahwa service aktif dan mengirim heartbeat 5 detik ke NATS.

CATATAN KEAMANAN:
- JANGAN meminta token Telegram, kata sandi, atau GitHub PAT. Node ini hanya memerlukan IP Tailscale privat untuk terhubung ke NATS.
```

---

## Catatan Tambahan untuk Operator:

Jika Anda memiliki secret lokal di PC/Laptop tertentu (misalnya password CCTV lokal atau token API kamera), simpan secara manual di terminal lokal perangkat tersebut menggunakan CLI `fleet-secret`:

```bash
fleet-secret set CCTV_PASS
```

Agen AI di Telegram hanya akan melihat nama key (`CCTV_PASS`), dan daemon lokal akan menyuntikkannya otomatis via environment variable saat eksekusi manifest yang disetujui.
