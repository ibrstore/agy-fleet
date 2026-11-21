"""
Pytest configuration and shared fixtures for agy-fleet.
"""
import pytest
from pathlib import Path
from shared.vault import SecretVault
from shared.redactor import Redactor


@pytest.fixture
def temp_vault(tmp_path: Path) -> SecretVault:
    vault_dir = tmp_path / "vault"
    # Force file-based encrypted store for deterministic tests without relying on host keyring daemon
    return SecretVault(vault_dir=vault_dir, use_keyring=False)


@pytest.fixture
def redactor_with_vault(temp_vault: SecretVault) -> Redactor:
    temp_vault.set_secret("DATABASE_PASSWORD", "SuperSecretDBPass123")
    temp_vault.set_secret("API_KEY", "prod_secret_token_abcdef987654")
    return Redactor(vault=temp_vault)
