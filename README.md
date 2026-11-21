# 🛰️ agy-fleet: Distributed Multi-Node Antigravity Orchestrator

Monorepo siap pakai untuk menyatukan dan mengendalikan 5 perangkat (**2 PC, 2 Laptop, 1 VPS**) yang masing-masing menjalankan Antigravity CLI (`agy`) secara lokal dengan akun Google terpisah, dikendalikan dari 1 Grup Telegram resmi (mendukung Forum Topics) menggunakan 1 Bot Telegram melalui jaringan privat **Tailscale** dan **NATS JetStream**.

---

## 🏗️ Arsitektur Sistem

```
                                  +------------------------------+
                                  |   Pemilik Resmi (Telegram)   |
                                  |   Strict Owner Auth Check    |
                                  +--------------+---------------+
                                                 |
                                                 v
                                  +------------------------------+
                                  |   Control Plane (VPS)        |
                                  |   orchestrator/ (aiogram v3) |
                                  |   • Presence Monitor (KV)    |
                                  |   • Turn-Lock State Machine  |
                                  |   • Per-Node Task Queue      |
                                  |   • Signed Manifest Manager  |
                                  +--------------+---------------+
                                                 |
                   [ NATS JetStream & KV via IP Privat Tailscale (100.x.y.z) ]
                                                 |
         +-------------------+-------------------+-------------------+-------------------+
         |                   |                   |                   |                   |
         v                   v                   v                   v                   v
+-----------------+ +-----------------+ +-----------------+ +-----------------+ +-----------------+
|   Node: VPS     | |   Node: PC 1    | |   Node: PC 2    | | Node: Laptop 1  | | Node: Laptop 2  |
| daemon/ (Linux) | | daemon/ (Win/Lx)| | daemon/ (Win/Lx)| | daemon/ (macOS) | | daemon/ (Win/Lx)|
| • agy Adapter   | | • agy Adapter   | | • agy Adapter   | | • agy Adapter   | | • agy Adapter   |
| • 5s Heartbeat  | | • 5s Heartbeat  | | • 5s Heartbeat  | | • 5s Heartbeat  | | • 5s Heartbeat  |
| • Wake-Lock     | | • Wake-Lock     | | • Wake-Lock     | | • Wake-Lock     | | • Wake-Lock     |
| • Consult Guard | | • Consult Guard | | • Consult Guard | | • Consult Guard | | • Consult Guard |
| • Manifest Exec | | • Manifest Exec | | • Manifest Exec | | • Manifest Exec | | • Manifest Exec |
| • Secret Vault  | | • Secret Vault  | | • Secret Vault  | | • Secret Vault  | | • Secret Vault  |
+-----------------+ +-----------------+ +-----------------+ +-----------------+ +-----------------+
```

---

## 🔒 Fitur Keamanan Utama (Anti Perintah Diam-Diam)

### 1. Default = CONSULT (Read-Only Fact Gathering)
- Semua percakapan Telegram default ke mode `CONSULT`.
- Pada mode ini, `daemon` memberlakukan **Allowlist Perintah Diagnostik Baca-Saja** (`uname`, `df`, `free`, `ps`, `ip`, `ss`, `docker ps`, `cat`, `grep`, `ffprobe`, `git status`).
- Segala upaya penulisan file (`>`, `>>`), eskalasi root (`sudo`), eksekusi installer (`apt`, `pip`, `npm`), modifikasi layanan (`systemctl restart`), atau penghapusan file (`rm`) **otomatis ditolak di level kode** (`ConsultModeViolationError`).

### 2. Signed Execution Manifest (Integritas Terkunci SHA-256)
- Jika permintaan memerlukan perubahan sistem, agen menyusun `ExecutionManifest` berisi perintah eksak per node, langkah *rollback*, dan langkah *verifikasi objektif*.
- Orchestrator menampilkan ringkasan manifest ke Telegram dengan Inline Keyboard:
  `[✅ Setujui & Jalankan]` `[✏️ Minta Revisi]` `[❌ Batal]`
- Saat disetujui pemilik, Orchestrator menghitung SHA-256 canonical hash manifest dan mengirimkannya ke node terkait.
- Daemon **HANYA** mengeksekusi perintah jika hash manifest cocok 100%. Modifikasi apapun di tengah jalan akan memicu pembatalan eksekusi dan menjalankan *rollback*.

### 3. Zero-Exposure Secrets Vault & Output DLP
- Kredensial sensitif **TIDAK PERNAH** disimpan di file `.env`, tidak dapat dibaca oleh `agy`, dan tidak pernah dikirim ke Telegram.
- Menggunakan CLI lokal `fleet-secret` yang dijalankan manusia di terminal lokal untuk menyimpan rahasia ke OS Keyring / Encrypted Store (AES-256-GCM).
- Agen hanya melihat nama key (`CCTV_PASS`), bukan nilainya. Saat eksekusi manifest disetujui, daemon menyuntikkan nilai asli ke *temporary environment variable* saat runtime.
- **DLP Redactor Middleware:** Menyaring stdout, stderr, dan file log dari token Telegram, GitHub PAT, kunci privat, password URL, dan secret vault sebelum dikirim keluar.

### 4. Turn-Lock State Machine & Task Queue
- Turn-Lock: 1 giliran aktif per topik/proyek.
- Hard Limit: Maksimal 4 putaran diskusi antar-agen untuk mencegah *infinite loop* dan pemborosan token.
- Cumulative Facts: Menghimpun temuan fakta diagnostik lintas mesin.
- Per-Node Task Queue: Antrean FIFO per perangkat agar tidak terjadi tabrakan tugas.

---

## 📂 Struktur Monorepo

```
agy-fleet/
├── pyproject.toml               # Dependensi & script entrypoints
├── README.md                    # Dokumentasi utama proyek
├── SETUP_VPS.md                 # Panduan langkah setup VPS Control Plane
├── CLIENT_PROMPT.md             # Prompt copy-paste onboarding PC & Laptop
├── shared/
│   ├── config.py                # Konstanta fleet, heartbeat, limit
│   ├── models.py                # Schema Pydantic protokol & manifest
│   ├── manifest.py              # Canonical JSON, SHA-256 hashing, signing
│   ├── redactor.py              # DLP Redactor & regex sanitizer
│   └── vault.py                 # OS Keyring + Encrypted store (AES-GCM)
├── cli/
│   └── secret.py                # CLI 'fleet-secret' untuk operator manusia
├── daemon/
│   ├── config.py                # Konfigurasi node lokal
│   ├── agy_adapter.py           # Interface adapter headless Antigravity CLI
│   ├── allowlist.py             # Strict Consult-mode read-only validator
│   ├── wakelock.py              # Cross-platform wake-lock (Linux/Win/Mac)
│   ├── executor.py              # Manifest executor & verification runner
│   └── agent.py                 # Service daemon NATS worker & 5s heartbeat
├── orchestrator/
│   ├── config.py                # Konfigurasi bot Telegram & credentials
│   ├── auth_middleware.py       # Strict Owner Auth (chat & user ID lock)
│   ├── presence.py              # Heartbeat watcher & live dashboard generator
│   ├── queue.py                 # Per-node sequential task queue
│   ├── turn_lock.py             # Turn-lock state machine (max 4 turns)
│   ├── manifest_manager.py      # Manifest proposal & approval lifecycle
│   ├── dlp_filter.py            # Outbound Telegram DLP & .txt log attacher
│   └── coordinator.py           # Main Telegram bot coordinator loop
├── scripts/
│   ├── run_nats_tailscale.sh    # Runner NATS JetStream bound to Tailscale
│   ├── install_service_linux.sh # Systemd installer (daemon & orchestrator)
│   ├── install_service_windows.ps1 # Scheduled Task installer untuk Windows
│   └── install_service_macos.sh # LaunchAgent installer untuk macOS
└── tests/
    ├── test_auth.py             # Test Strict Owner Auth Telegram
    ├── test_redactor.py         # Test DLP Redactor & Secret Vault
    ├── test_manifest.py         # Test SHA-256 Hashing & Anti-Tampering
    ├── test_turn_lock.py        # Test Turn-Lock & Anti-Looping limit
    ├── test_consult_allowlist.py# Test Consult Mode Read-Only allowlist
    └── test_agy_adapter.py      # Test agy CLI adapter parsing
```

---

## 🚀 Perintah Operasional di Telegram

| Perintah | Deskripsi |
|---|---|
| `/status` | Cek status kehadiran live seluruh node (CPU, RAM, task aktif). |
| `/dashboard` | Kirim dan sematkan (*pin*) live dashboard status di Forum Topic. |
| `/mode ringkas` | Atur diskusi antar-agen hanya mengirimkan kesimpulan akhir. |
| `/mode pantau` | Atur diskusi antar-agen menampilkan progres ringkas per giliran. |
| `/stop` atau `/abort` | Siarkan sinyal darurat (`SIGTERM`/`SIGKILL`) ke semua proses aktif di semua node. |
| `/update @online` | Perintahkan seluruh node aktif untuk `git pull` dan me-restart daemon secara aman. |

### Contoh Format Tagging Chat:
- `pc1: periksa sisa disk`
- `pc1 vps: bandingkan port yang terbuka`
- `@online: periksa utilisasi memori`
- `@all: status layanan nginx`

---

## 🧪 Menjalankan Automated Test Suite

Untuk memverifikasi seluruh komponen keamanan:

```bash
cd /root/agy-fleet
.venv/bin/pytest -v
```

Hasil uji: **66 tests passed (100% coverage untuk fitur inti)**.
