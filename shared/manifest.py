"""
Execution Manifest hashing, signing, and verification.
Anti Perintah Diam-Diam: Tamper-evident execution manifests.
"""
import hashlib
import json
from datetime import datetime, timezone
from typing import Dict, Any
from shared.models import ExecutionManifest


def canonical_json(data: Any) -> str:
    """
    Produce deterministic canonical JSON string for hashing.
    Keys are sorted, whitespace eliminated, utf-8 encoded.
    """
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def get_hashable_payload(manifest: ExecutionManifest) -> Dict[str, Any]:
    """
    Extract dict payload for hashing, strictly excluding approval/hash fields.
    """
    raw_dict = manifest.model_dump()
    # Fields that do not factor into the content hash:
    raw_dict.pop("hash", None)
    raw_dict.pop("approved_by", None)
    raw_dict.pop("approved_at", None)
    return raw_dict


def calculate_manifest_hash(manifest: ExecutionManifest) -> str:
    """
    Compute SHA-256 hash of the canonical JSON representation of the manifest.
    """
    payload = get_hashable_payload(manifest)
    serialized = canonical_json(payload)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def sign_manifest(manifest: ExecutionManifest, approver_id: int) -> ExecutionManifest:
    """
    Seal the manifest with calculated hash and user approval record.
    """
    computed_hash = calculate_manifest_hash(manifest)
    manifest.hash = computed_hash
    manifest.approved_by = approver_id
    manifest.approved_at = datetime.now(timezone.utc).isoformat()
    return manifest


def verify_manifest_hash(manifest: ExecutionManifest, expected_hash: str) -> bool:
    """
    Verify both that:
    1. The manifest's internal hash matches expected_hash.
    2. Recalculating the canonical content hash matches expected_hash.
    """
    if not expected_hash or not manifest.hash:
        return False
    if manifest.hash.lower() != expected_hash.lower():
        return False
    recomputed = calculate_manifest_hash(manifest)
    return recomputed.lower() == expected_hash.lower()


def manifest_to_summary_markdown(manifest: ExecutionManifest) -> str:
    """
    Format manifest content into clean Markdown for Telegram approval message.
    """
    short_hash = (manifest.hash or calculate_manifest_hash(manifest))[:12]
    lines = [
        f"📋 *PROPOSAL RENCANA EKSEKUSI* (Hash: `{short_hash}...`)",
        f"*Proyek:* `{manifest.project_id}` | *Judul:* {manifest.title}",
    ]
    if manifest.description:
        lines.append(f"*Keterangan:* {manifest.description}")
    lines.append("")

    for node_id, node_manifest in manifest.nodes.items():
        lines.append(f"🖥️ *Node: `{node_id}`* ({len(node_manifest.commands)} langkah)")
        for idx, cmd in enumerate(node_manifest.commands, 1):
            sec_note = f" [Secrets: {', '.join(cmd.env_secrets.keys())}]" if cmd.env_secrets else ""
            desc = f" ({cmd.description})" if cmd.description else ""
            lines.append(f"  {idx}. `{cmd.command}`{desc}{sec_note}")

        if node_manifest.rollback_commands:
            lines.append(f"  *Rollback:* {len(node_manifest.rollback_commands)} langkah")
            for r_cmd in node_manifest.rollback_commands:
                lines.append(f"    ↺ `{r_cmd.command}`")

        if node_manifest.verification_commands:
            lines.append(f"  *Verifikasi:* {len(node_manifest.verification_commands)} langkah")
            for v_cmd in node_manifest.verification_commands:
                lines.append(f"    ✓ `{v_cmd.command}`")
        lines.append("")

    return "\n".join(lines).strip()
