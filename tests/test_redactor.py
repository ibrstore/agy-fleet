"""
Unit tests for DLP Redactor & Vault.
Verifies secret masking, token pattern sanitization, and message length controls.
"""
from shared.redactor import Redactor
from shared.vault import SecretVault


def test_vault_secrets_redaction(redactor_with_vault: Redactor):
    raw_text = "Connecting with pass SuperSecretDBPass123 and token prod_secret_token_abcdef987654 to database."
    sanitized = redactor_with_vault.redact(raw_text)

    assert "SuperSecretDBPass123" not in sanitized
    assert "prod_secret_token_abcdef987654" not in sanitized
    assert "[REDACTED]" in sanitized


def test_regex_pattern_redaction(temp_vault: SecretVault):
    redactor = Redactor(vault=temp_vault)

    # 1. Telegram bot token
    tg_text = "Bot token is 123456789:ABCdefGHIjklMNOpqrsTUVwxyz1234567 in service."
    assert "123456789:ABCdefGHIjklMNOpqrsTUVwxyz1234567" not in redactor.redact(tg_text)
    assert "[REDACTED_TELEGRAM_TOKEN]" in redactor.redact(tg_text)

    # 2. GitHub Token
    gh_text = "Cloning with ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ1234567890"
    assert "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ1234567890" not in redactor.redact(gh_text)
    assert "[REDACTED_GITHUB_TOKEN]" in redactor.redact(gh_text)

    # 3. URL with user:pass
    url_text = "Connecting to https://admin:superSecretPassword99@tailscale.node:8080/feed"
    sanitized_url = redactor.redact(url_text)
    assert "superSecretPassword99" not in sanitized_url
    assert "admin:[REDACTED_PASS]@" in sanitized_url

    # 4. Private key block
    key_text = (
        "-----BEGIN RSA PRIVATE KEY-----\n"
        "MIIEowIBAAKCAQEA0Y1Z2abcxyz...\n"
        "-----END RSA PRIVATE KEY-----"
    )
    assert "MIIEowIBAAKCAQEA0Y1Z2abcxyz" not in redactor.redact(key_text)
    assert "[REDACTED_PRIVATE_KEY]" in redactor.redact(key_text)


def test_telegram_message_truncation(temp_vault: SecretVault):
    redactor = Redactor(vault=temp_vault)

    # Short message
    short_text = "Hello world from fleet!"
    display, exceeds, full = redactor.sanitize_for_telegram(short_text, max_len=100)
    assert not exceeds
    assert display == short_text
    assert full == short_text

    # Long message exceeding max_len
    long_text = "A" * 200
    display, exceeds, full = redactor.sanitize_for_telegram(long_text, max_len=100)
    assert exceeds
    assert "[Output dipotong" in display
    assert len(full) == 200


def test_vault_crud_operations(temp_vault: SecretVault):
    # Set
    assert temp_vault.set_secret("FOO_KEY", "bar_secret_value")
    # Get
    assert temp_vault.get_secret("FOO_KEY") == "bar_secret_value"
    # List keys
    keys = temp_vault.list_keys()
    assert "FOO_KEY" in keys
    # Values never exposed in list_keys
    assert "bar_secret_value" not in keys
    # Delete
    assert temp_vault.delete_secret("FOO_KEY")
    assert temp_vault.get_secret("FOO_KEY") is None
