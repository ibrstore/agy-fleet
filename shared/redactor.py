"""
Output DLP (Data Loss Prevention) and Redaction Middleware.
Sanitizes stdout, stderr, logs, and messages before passing to AI or Telegram.
"""
import re
from typing import List, Optional, Tuple
from shared.config import TELEGRAM_MAX_TEXT_LEN
from shared.vault import SecretVault

PATTERNS = [
    # Telegram Bot Tokens: e.g. 123456789:ABCdefGHIjklMNOpqrsTUVwxyz1234567
    (re.compile(r"\b\d{8,11}:[a-zA-Z0-9_-]{30,45}\b"), "[REDACTED_TELEGRAM_TOKEN]"),

    # Private Keys (RSA, EC, OPENSSH, etc.)
    (re.compile(r"-----BEGIN [A-Z ]+ PRIVATE KEY-----[\s\S]*?-----END [A-Z ]+ PRIVATE KEY-----"), "[REDACTED_PRIVATE_KEY]"),

    # GitHub Tokens
    (re.compile(r"\b(ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9_]{36,}\b"), "[REDACTED_GITHUB_TOKEN]"),

    # URLs with embedded user:password
    (re.compile(r"(https?://|ssh://|ftp://)([^:\s/@]+):([^@\s/]+)@"), r"\1\2:[REDACTED_PASS]@"),

    # Authorization / Bearer headers
    (re.compile(r"(?i)\b(Authorization|Bearer)\s*[:=]\s*([a-zA-Z0-9_\-\.]{15,})"), r"\1: [REDACTED_TOKEN]"),

    # Key-value secret assignments: e.g. API_KEY=xyz, password="xyz"
    (re.compile(r"(?i)\b(api[_-]?key|secret[_-]?key|access[_-]?token|password|passwd|auth[_-]?token)\s*[:=]\s*['\"]?([a-zA-Z0-9_\-]{8,})['\"]?"), r'\1=[REDACTED]'),
]


class Redactor:
    def __init__(self, vault: Optional[SecretVault] = None, extra_secrets: Optional[List[str]] = None):
        self.vault = vault or SecretVault()
        self.extra_secrets = extra_secrets or []

    def redact(self, text: str) -> str:
        """
        Redact sensitive information from text.
        1. Explicit active secrets from vault.
        2. Regular expression patterns for credentials, tokens, and keys.
        """
        if not text:
            return ""

        redacted = text

        # 1. Redact all known active secret values from the vault
        known_secrets = self.vault.get_all_secrets_for_redaction()
        all_secrets = set(known_secrets + self.extra_secrets)

        # Sort by length descending to prevent partial replacements
        for sec in sorted(all_secrets, key=len, reverse=True):
            if sec and len(sec) >= 3:
                redacted = redacted.replace(sec, "[REDACTED]")

        # 2. Apply regex pattern sanitization
        for pattern, replacement in PATTERNS:
            redacted = pattern.sub(replacement, redacted)

        return redacted

    def sanitize_for_telegram(self, text: str, max_len: int = TELEGRAM_MAX_TEXT_LEN) -> Tuple[str, bool, str]:
        """
        Redacts text and checks if it exceeds Telegram's safe message length.
        Returns:
            (display_text, is_truncated, full_redacted_text)
        """
        full_redacted = self.redact(text)
        if len(full_redacted) <= max_len:
            return full_redacted, False, full_redacted

        truncated_preview = full_redacted[:max_len - 200]
        truncated_display = (
            f"{truncated_preview}\n\n"
            f"⚠️ *[Output dipotong karena melebihi {max_len} karakter. Log lengkap dilampirkan sebagai file .txt]*"
        )
        return truncated_display, True, full_redacted
