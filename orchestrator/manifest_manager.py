"""
Plan-and-Approve Manifest Manager.
Manages manifest lifecycle: proposal, canonical hashing, Telegram inline approval, and execution dispatch.
"""
from typing import Dict, Optional, Tuple
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from shared.manifest import (
    calculate_manifest_hash,
    sign_manifest,
    manifest_to_summary_markdown,
)
from shared.models import ExecutionManifest


class ManifestManager:
    def __init__(self):
        self.pending_manifests: Dict[str, ExecutionManifest] = {}
        self.completed_manifests: Dict[str, ExecutionManifest] = {}

    def register_manifest(self, manifest: ExecutionManifest) -> Tuple[ExecutionManifest, str]:
        """
        Calculates canonical hash, registers manifest in pending pool,
        and returns the manifest and calculated hash.
        """
        content_hash = calculate_manifest_hash(manifest)
        manifest.hash = content_hash
        self.pending_manifests[manifest.manifest_id] = manifest
        return manifest, content_hash

    def get_manifest(self, manifest_id: str) -> Optional[ExecutionManifest]:
        return self.pending_manifests.get(manifest_id) or self.completed_manifests.get(manifest_id)

    def create_approval_keyboard(self, manifest: ExecutionManifest) -> InlineKeyboardMarkup:
        """
        Creates Telegram Inline Keyboard:
        [✅ Setujui & Jalankan] [✏️ Revisi] [❌ Batal]
        """
        short_hash = (manifest.hash or calculate_manifest_hash(manifest))[:8]
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="✅ Setujui & Jalankan",
                        callback_data=f"man:app:{manifest.manifest_id}:{short_hash}"
                    ),
                ],
                [
                    InlineKeyboardButton(
                        text="✏️ Minta Revisi",
                        callback_data=f"man:rev:{manifest.manifest_id}"
                    ),
                    InlineKeyboardButton(
                        text="❌ Batal",
                        callback_data=f"man:can:{manifest.manifest_id}"
                    ),
                ]
            ]
        )
        return kb

    def approve_manifest(self, manifest_id: str, approver_id: int) -> Optional[Tuple[ExecutionManifest, str]]:
        manifest = self.pending_manifests.pop(manifest_id, None)
        if not manifest:
            return None
        signed = sign_manifest(manifest, approver_id)
        self.completed_manifests[manifest_id] = signed
        return signed, signed.hash

    def cancel_manifest(self, manifest_id: str) -> bool:
        if manifest_id in self.pending_manifests:
            del self.pending_manifests[manifest_id]
            return True
        return False

    def format_proposal(self, manifest: ExecutionManifest) -> str:
        return manifest_to_summary_markdown(manifest)
