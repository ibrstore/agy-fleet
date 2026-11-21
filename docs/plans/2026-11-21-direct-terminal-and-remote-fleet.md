# Direct Interactive Streaming Terminal & Remote Device Control Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans or superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Transition `agy-fleet` from asynchronous Telegram bot polling to instant, real-time Interactive Streaming Terminals (0-second token streaming) and Full GUI Remote Device Control over the existing encrypted Tailscale Mesh Network, while cleanly decommissioning the Telegram orchestrator service.

**Architecture:** 
- Network Layer: Private Tailscale WireGuard Mesh (`100.x.x.x`) connects all 6 machines directly without public ports.
- Terminal Layer: Tailscale SSH / Windows OpenSSH provides sub-second interactive CLI sessions with native streaming token output for Antigravity (`agy`).
- GUI Remote Layer: Direct RDP and RustDesk over Tailscale IP for full remote desktop control (mouse, keyboard, clipboard, file transfer) with zero relay server lag.
- Decommission Layer: Safe retirement of `agy-orchestrator` and bot services to free system resources.

**Tech Stack:** Tailscale SSH, Windows OpenSSH Server, Windows Terminal Profiles, RustDesk / RDP, Bash/PowerShell tooling.

---

## Global Constraints

- Never expose ports to the public internet; all terminal and remote traffic must bind strictly to Tailscale IP addresses (`100.x.x.x`).
- Do not delete git repository history; archive Telegram orchestrator code cleanly.
- Windows commands must be headless-friendly and executable via PowerShell 5.1/7.
- Antigravity CLI (`agy`) runs interactively on each target node under its respective user account and environment.

---

## Tasks

### Task 1: Decommission Telegram Bot & Orchestrator Services

**Files:**
- Modify: `/root/.config/systemd/user/agy-orchestrator.service`
- Modify: `/root/.config/systemd/user/agy-daemon.service`

**Steps:**
- [ ] Stop and disable `agy-orchestrator.service` on VPS (`systemctl --user stop agy-orchestrator && systemctl --user disable agy-orchestrator`).
- [ ] Stop and disable `agy-daemon.service` on VPS (`systemctl --user stop agy-daemon && systemctl --user disable agy-daemon`).
- [ ] Stop and remove docker container `agy-fleet-nats` (`docker stop agy-fleet-nats && docker rm agy-fleet-nats`).
- [ ] Verify VPS processes are clean (`ps aux | grep -E "orchestrator|daemon\.agent"`).
- [ ] Commit decommission state to git.

---

### Task 2: Enable Tailscale SSH & Direct Terminal Streaming

**Files:**
- Create: `scripts/enable_tailscale_ssh.sh` (Linux VPS)
- Create: `scripts/setup_openssh_windows.ps1` (Windows Nodes)
- Create: `configs/ssh_config_template` (Client SSH Config)

**Steps:**
- [ ] Enable Tailscale SSH on VPS (`tailscale up --ssh --accept-routes --reset`).
- [ ] Create `scripts/setup_openssh_windows.ps1` that:
  - Installs Windows OpenSSH Server capability if not installed.
  - Sets default shell to PowerShell / bash.
  - Ensures OpenSSH listens on Tailscale interface.
  - Starts and sets `sshd` service to automatic startup.
- [ ] Create `configs/ssh_config_template` mapping node IDs to Tailscale IPs:
  - `vps`: `100.66.49.13`
  - `pc-naufal`: `100.86.37.72`
  - `pc-diba`: `100.66.59.93`
  - `lap-asus`: `100.96.176.36`
  - `lap-user` & `pc-nci`
- [ ] Test SSH connection from VPS to local and active Tailscale peers.

---

### Task 3: Setup Full GUI Remote Device Control (RDP & RustDesk)

**Files:**
- Create: `scripts/enable_remote_desktop_windows.ps1`
- Create: `scripts/setup_rustdesk_windows.ps1`

**Steps:**
- [ ] Create `scripts/enable_remote_desktop_windows.ps1`:
  - Enables Windows Remote Desktop (`fDenyTSConnections = 0`).
  - Enables Network Level Authentication (NLA) or tailnet-only firewall rules.
  - Grants remote access for current user.
- [ ] Create `scripts/setup_rustdesk_windows.ps1`:
  - Downloads lightweight portable RustDesk or installs RustDesk service.
  - Configures Direct IP Access enabled (allowing 100.x.x.x connections without external relay).
  - Sets permanent security password for unattended access.
- [ ] Test and document direct RDP connection syntax: `mstsc /v:100.86.37.72`.

---

### Task 4: Interactive Fleet Launcher & Windows Terminal Profiles

**Files:**
- Create: `scripts/fleet_connect.bat` (Windows 1-click launcher)
- Create: `scripts/fleet_connect.sh` (Linux/Mac launcher)
- Create: `configs/windows_terminal_profiles.json` (Multi-tab layout)

**Steps:**
- [ ] Create `scripts/fleet_connect.bat`:
  - Simple numbered menu to immediately open an interactive terminal to any device:
    - 1: PC Naufal (Runs `ssh -t pc-naufal "agy"`)
    - 2: PC Diba (Runs `ssh -t pc-diba "agy"`)
    - 3: Lap Asus (Runs `ssh -t lap-asus "agy"`)
    - 4: VPS (Runs `ssh -t vps "agy"`)
    - 5: Remote Desktop GUI (Launches RDP to selected node)
- [ ] Create `configs/windows_terminal_profiles.json` with multi-pane profiles for launching all AI nodes in split screen.

---

### Task 5: Documentation & Git Verification

**Files:**
- Modify: `README.md`
- Create: `docs/REMOTE_DEVICE_GUIDE.md`

**Steps:**
- [ ] Write `docs/REMOTE_DEVICE_GUIDE.md` explaining:
  - How to connect to any node's live interactive Antigravity CLI terminal in 1 second.
  - How to connect to any node's full GUI desktop via RDP or RustDesk.
  - How to use the multi-tab terminal launcher.
- [ ] Update `README.md` to reflect the direct streaming terminal and remote device architecture.
- [ ] Run verification tests.
- [ ] Commit and push to GitHub repository via Coolify GitHub App installation token.
