"""
Strict Consult-Mode Read-Only Command Validator.
Enforces the read-only fact-gathering boundary at the code level.
Rejects any destructive, mutating, writing, or installer commands.
"""
import re
import shlex
from typing import List, Set

ALLOWED_READ_ONLY_BINARIES: Set[str] = {
    # System info & metrics
    "uname", "hostname", "uptime", "whoami", "id", "date",
    "free", "df", "top", "vmstat", "iostat", "lsblk", "lscpu", "lsmem",
    # Process inspection
    "ps", "pgrep",
    # Network diagnostics
    "ip", "ifconfig", "netstat", "ss", "lsof", "ping", "traceroute",
    "curl", "dig", "nslookup", "route", "arp",
    # Read-only file inspection
    "cat", "ls", "head", "tail", "grep", "egrep", "fgrep", "wc", "find",
    "diff", "file", "stat",
    # Service inspection
    "systemctl", "journalctl",
    # Containers
    "docker", "podman",
    # Hardware & Media inspection
    "ffprobe", "nvidia-smi", "lspci", "lsusb",
    # Git inspection
    "git",
    # Shell echo / printf for tests
    "echo", "printf",
}

# Subcommands that are mutating/forbidden even for allowed binaries
FORBIDDEN_SUBCOMMANDS = {
    "systemctl": {"restart", "stop", "start", "reload", "enable", "disable", "mask", "unmask", "edit", "reboot", "poweroff"},
    "docker": {"run", "exec", "rm", "rmi", "stop", "start", "restart", "kill", "build", "compose", "volume", "network"},
    "podman": {"run", "exec", "rm", "rmi", "stop", "start", "restart", "kill", "build"},
    "git": {"commit", "push", "checkout", "switch", "merge", "rebase", "reset", "pull", "clone", "init", "add", "rm", "mv", "apply", "clean"},
}

FORBIDDEN_SHELL_PATTERNS = [
    # Write redirection (e.g. > file, >> file), but allow safe 2>/dev/null or 2>&1
    re.compile(r"(?<!2)>[ \t]*(?!/dev/null\b)"),
    # Sudo or root elevation
    re.compile(r"\b(sudo|su|doas)\b"),
    # Destructive utilities
    re.compile(r"\b(rm|mv|cp|touch|chmod|chown|truncate|dd|mkfs|fdisk|parted)\b"),
    # Package managers & pip write
    re.compile(r"\b(apt|apt-get|yum|dnf|pacman|apk|zypper)\b"),
    re.compile(r"\b(pip|uv|npm|yarn|cargo)\s+(install|uninstall|update|add|remove)\b"),
    # Inline file modification
    re.compile(r"\bsed\s+-[a-zA-Z]*i\b"),
    # Process termination
    re.compile(r"\b(kill|pkill|killall)\b"),
]


class ConsultModeViolationError(Exception):
    """Raised when a command violates the read-only consult mode boundaries."""
    pass


def validate_consult_command(command: str) -> None:
    """
    Validate that a command string conforms to read-only consult mode.
    Raises ConsultModeViolationError if any write or forbidden pattern is detected.
    """
    cmd = command.strip()
    if not cmd:
        raise ConsultModeViolationError("Perintah kosong tidak diizinkan.")

    # 1. Check for forbidden shell patterns (redirections, sudo, destructive tools)
    for pattern in FORBIDDEN_SHELL_PATTERNS:
        match = pattern.search(cmd)
        if match:
            raise ConsultModeViolationError(
                f"Perintah ditolak dalam mode CONSULT: terdeteksi operasi mutasi/tulis '{match.group(0)}'."
            )

    # 2. Split pipeline commands separated by '|' or '&&' or ';'
    sub_commands = re.split(r"\||&&|;", cmd)
    for sub in sub_commands:
        sub = sub.strip()
        if not sub:
            continue
        try:
            tokens = shlex.split(sub)
        except ValueError as e:
            raise ConsultModeViolationError(f"Gagal mem-parse sintaks perintah: {e}")

        if not tokens:
            continue

        binary = tokens[0].split("/")[-1].lower()

        # Check binary allowlist
        if binary not in ALLOWED_READ_ONLY_BINARIES:
            raise ConsultModeViolationError(
                f"Binary '{binary}' tidak ada dalam allowlist read-only mode CONSULT."
            )

        # Check curl flags: curl in consult mode must be read-only (head or output to stdout)
        if binary == "curl":
            forbidden_curl_flags = {"-o", "--output", "-O", "--remote-name", "-X", "--request", "-d", "--data", "--data-raw", "--data-binary", "-F", "--form"}
            for t in tokens[1:]:
                if t in forbidden_curl_flags or t.startswith("-o") or t.startswith("-O"):
                    raise ConsultModeViolationError(
                        f"Opsi curl '{t}' tidak diizinkan dalam mode CONSULT (hanya read-only seperti -I / --head)."
                    )

        # Check binary-specific forbidden subcommands
        if binary in FORBIDDEN_SUBCOMMANDS:
            forbidden_set = FORBIDDEN_SUBCOMMANDS[binary]
            for token in tokens[1:]:
                if not token.startswith("-") and token.lower() in forbidden_set:
                    raise ConsultModeViolationError(
                        f"Subperintah '{binary} {token}' ditolak: mengubah status mesin dalam mode CONSULT."
                    )
