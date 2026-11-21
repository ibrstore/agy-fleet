"""
fleet-secret CLI utility.
Used strictly by the human operator in local terminal to manage secrets.
Never callable by AI agents; values never output to terminal stdout/stderr.
"""
import getpass
import sys
import click
from shared.vault import SecretVault


@click.group()
def cli():
    """fleet-secret: Zero-exposure local vault manager for agy-fleet."""
    pass


@cli.command("set")
@click.argument("key")
@click.argument("value", required=False)
def set_secret_cmd(key: str, value: str = None):
    """Save or update a secret in the local vault."""
    key = key.strip().upper()
    if not value:
        value = getpass.getpass(f"Masukkan secret value untuk [{key}]: ")
        if not value:
            click.echo("❌ Nilai rahasia tidak boleh kosong.", err=True)
            sys.exit(1)
        confirm = getpass.getpass(f"Konfirmasi secret value untuk [{key}]: ")
        if value != confirm:
            click.echo("❌ Nilai konfirmasi tidak cocok.", err=True)
            sys.exit(1)

    vault = SecretVault()
    if vault.set_secret(key, value):
        click.echo(f"✅ Secret [{key}] berhasil disimpan ke vault lokal.")
    else:
        click.echo(f"❌ Gagal menyimpan secret [{key}].", err=True)
        sys.exit(1)


@cli.command("list-keys")
def list_keys_cmd():
    """List all stored secret keys (values are NEVER displayed)."""
    vault = SecretVault()
    keys = vault.list_keys()
    if not keys:
        click.echo("ℹ️ Belum ada secret yang tersimpan di vault lokal.")
        return
    click.echo(f"🔑 Secret keys terdaftar ({len(keys)} key):")
    for k in keys:
        click.echo(f"  • {k}")


@cli.command("delete")
@click.argument("key")
def delete_secret_cmd(key: str):
    """Delete a secret key from the local vault."""
    key = key.strip().upper()
    vault = SecretVault()
    if vault.delete_secret(key):
        click.echo(f"✅ Secret [{key}] berhasil dihapus dari vault.")
    else:
        click.echo(f"⚠️ Secret [{key}] tidak ditemukan di vault.")


@cli.command("test")
def test_cmd():
    """Verify that vault encryption and storage are functioning properly."""
    vault = SecretVault()
    test_key = "__TEST_VAULT_KEY__"
    test_val = "ok_value_12345"
    if not vault.set_secret(test_key, test_val):
        click.echo("❌ Test failed on write.", err=True)
        sys.exit(1)
    read_val = vault.get_secret(test_key)
    if read_val != test_val:
        click.echo("❌ Test failed on read verification.", err=True)
        sys.exit(1)
    vault.delete_secret(test_key)
    click.echo("✅ Vault encryption and storage operational.")


def main():
    cli()


if __name__ == "__main__":
    main()
