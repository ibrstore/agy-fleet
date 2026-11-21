"""
Zero-Exposure Secret Vault.
Uses OS Keyring with an encrypted file fallback (AES-256-GCM) for headless servers.
Allows listing key names ONLY to agents. Values are strictly guarded and only injected at runtime.
"""
import json
import os
import stat
from pathlib import Path
from typing import Dict, List, Optional
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

SERVICE_NAME = "agy-fleet"


class SecretVault:
    def __init__(self, vault_dir: Optional[Path] = None, use_keyring: bool = True):
        self.vault_dir = vault_dir or Path.home() / ".agy-fleet"
        self.vault_dir.mkdir(parents=True, exist_ok=True)
        self.key_file = self.vault_dir / ".vault_key"
        self.enc_file = self.vault_dir / "vault.enc"
        self.use_keyring = use_keyring
        self._keyring_available = False

        if self.use_keyring:
            try:
                import keyring
                # Test whether backend is functional (not fail backend)
                backend = keyring.get_keyring()
                if backend and "fail" not in backend.__class__.__name__.lower():
                    self._keyring_available = True
            except Exception:
                self._keyring_available = False

    def _get_master_key(self) -> bytes:
        """
        Derive or load a 256-bit machine-local AES key with chmod 600 permissions.
        """
        if not self.key_file.exists():
            key = AESGCM.generate_key(bit_length=256)
            with open(self.key_file, "wb") as f:
                f.write(key)
            try:
                os.chmod(self.key_file, stat.S_IRUSR | stat.S_IWUSR)
            except Exception:
                pass
            return key
        with open(self.key_file, "rb") as f:
            return f.read()

    def _read_encrypted_store(self) -> Dict[str, str]:
        if not self.enc_file.exists():
            return {}
        try:
            with open(self.enc_file, "rb") as f:
                data = f.read()
            if len(data) < 12:
                return {}
            nonce = data[:12]
            ciphertext = data[12:]
            key = self._get_master_key()
            aesgcm = AESGCM(key)
            decrypted = aesgcm.decrypt(nonce, ciphertext, None)
            return json.loads(decrypted.decode("utf-8"))
        except Exception:
            return {}

    def _write_encrypted_store(self, secrets: Dict[str, str]) -> None:
        key = self._get_master_key()
        aesgcm = AESGCM(key)
        nonce = os.urandom(12)
        payload = json.dumps(secrets).encode("utf-8")
        ciphertext = aesgcm.encrypt(nonce, payload, None)
        with open(self.enc_file, "wb") as f:
            f.write(nonce + ciphertext)
        try:
            os.chmod(self.enc_file, stat.S_IRUSR | stat.S_IWUSR)
        except Exception:
            pass

    def set_secret(self, key: str, value: str) -> bool:
        """Store a secret in keyring or encrypted fallback."""
        key = key.strip().upper()
        if not key or not value:
            return False

        if self._keyring_available:
            try:
                import keyring
                keyring.set_password(SERVICE_NAME, key, value)
                # Also track key name in local index
                self._add_key_index(key)
                return True
            except Exception:
                pass

        # Fallback to AES-GCM file store
        data = self._read_encrypted_store()
        data[key] = value
        self._write_encrypted_store(data)
        self._add_key_index(key)
        return True

    def get_secret(self, key: str) -> Optional[str]:
        """
        Retrieve a secret value. Strictly for internal execution runtime only.
        NEVER expose to AI prompt or Telegram.
        """
        key = key.strip().upper()
        if self._keyring_available:
            try:
                import keyring
                val = keyring.get_password(SERVICE_NAME, key)
                if val is not None:
                    return val
            except Exception:
                pass

        data = self._read_encrypted_store()
        return data.get(key)

    def delete_secret(self, key: str) -> bool:
        key = key.strip().upper()
        deleted = False
        if self._keyring_available:
            try:
                import keyring
                keyring.delete_password(SERVICE_NAME, key)
                deleted = True
            except Exception:
                pass

        data = self._read_encrypted_store()
        if key in data:
            del data[key]
            self._write_encrypted_store(data)
            deleted = True

        self._remove_key_index(key)
        return deleted

    def _add_key_index(self, key: str) -> None:
        idx_file = self.vault_dir / "keys.index"
        keys = set(self.list_keys())
        keys.add(key)
        idx_file.write_text("\n".join(sorted(keys)))

    def _remove_key_index(self, key: str) -> None:
        idx_file = self.vault_dir / "keys.index"
        keys = set(self.list_keys())
        keys.discard(key)
        idx_file.write_text("\n".join(sorted(keys)))

    def list_keys(self) -> List[str]:
        """
        Returns list of secret KEY NAMES only. Safe to share with agents.
        """
        idx_file = self.vault_dir / "keys.index"
        if idx_file.exists():
            lines = [l.strip() for l in idx_file.read_text().splitlines() if l.strip()]
            return sorted(list(set(lines)))
        data = self._read_encrypted_store()
        return sorted(list(data.keys()))

    def get_all_secrets_for_redaction(self) -> List[str]:
        """
        Internal DLP helper: returns all active secret string values
        so the redactor can replace them with [REDACTED].
        """
        values: List[str] = []
        for k in self.list_keys():
            val = self.get_secret(k)
            if val and len(val.strip()) >= 3:
                values.append(val.strip())
        return values
