"""
Conversation Manager for agy-fleet.
Maintains persistent 1-conversation session per device, tracking turn count
and total tokens, and automatically rotating when context gets heavy or full.
"""
import json
import logging
import os
import time
from pathlib import Path
from typing import Dict, Any, Optional

logger = logging.getLogger("fleet-conversations")

DEFAULT_MAX_TURNS = 30
DEFAULT_MAX_TOKENS = 120_000


class ConversationManager:
    def __init__(
        self,
        storage_path: Optional[str] = None,
        max_turns: int = DEFAULT_MAX_TURNS,
        max_tokens: int = DEFAULT_MAX_TOKENS,
    ):
        self.max_turns = max_turns
        self.max_tokens = max_tokens
        self.storage_path = Path(storage_path) if storage_path else Path.home() / ".agy-fleet" / "conversations.json"
        self.conversations: Dict[str, Dict[str, Any]] = {}
        self._load()

    def _load(self) -> None:
        """Load persisted conversations from disk."""
        if self.storage_path.exists():
            try:
                data = json.loads(self.storage_path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    self.conversations = data
                    logger.info(f"Loaded {len(self.conversations)} active device conversations from {self.storage_path}")
            except Exception as e:
                logger.warning(f"Could not load conversations from {self.storage_path}: {e}")
                self.conversations = {}

    def _save(self) -> None:
        """Persist conversations to disk atomically."""
        try:
            self.storage_path.parent.mkdir(parents=True, exist_ok=True)
            temp_path = self.storage_path.with_suffix(".tmp")
            temp_path.write_text(json.dumps(self.conversations, indent=2), encoding="utf-8")
            temp_path.replace(self.storage_path)
        except Exception as e:
            logger.error(f"Failed to persist conversations to {self.storage_path}: {e}")

    def get_conversation_id(self, node_id: str) -> Optional[str]:
        """Get the active conversation ID for a node, or None if new/rotated."""
        entry = self.conversations.get(node_id)
        if entry and entry.get("conversation_id"):
            return entry["conversation_id"]
        return None

    def get_info(self, node_id: str) -> Optional[Dict[str, Any]]:
        """Return conversation info for display."""
        return self.conversations.get(node_id)

    def record_turn(
        self,
        node_id: str,
        conversation_id: str,
        turns: int = 1,
        total_tokens: int = 0
    ) -> bool:
        """
        Record turn completion.
        Returns True if conversation was rotated (exceeded limits), False otherwise.
        """
        if not conversation_id:
            return False

        entry = self.conversations.get(node_id, {})
        current_id = entry.get("conversation_id")

        if current_id == conversation_id:
            # Continue existing conversation
            new_turns = max(entry.get("turn_count", 0) + 1, turns)
            new_tokens = max(entry.get("total_tokens", 0), total_tokens)
        else:
            # First time seeing this conversation or new conversation started
            new_turns = turns
            new_tokens = total_tokens

        # Check rotation thresholds ("kalau sudah berat dan banyak baru pindah")
        if new_turns >= self.max_turns or new_tokens >= self.max_tokens:
            logger.info(
                f"Node '{node_id}' conversation {conversation_id[:8]} reached rotation limit "
                f"(turns: {new_turns}/{self.max_turns}, tokens: {new_tokens}/{self.max_tokens}). Rotating to new."
            )
            self.reset(node_id)
            return True

        self.conversations[node_id] = {
            "conversation_id": conversation_id,
            "turn_count": new_turns,
            "total_tokens": new_tokens,
            "updated_at": time.time(),
        }
        self._save()
        return False

    def reset(self, node_id: str) -> None:
        """Clear active conversation for a node so next interaction starts fresh."""
        if node_id in self.conversations:
            del self.conversations[node_id]
            self._save()
            logger.info(f"Reset active conversation for node '{node_id}'")

    def reset_all(self) -> None:
        """Clear all active conversations across all devices."""
        self.conversations.clear()
        self._save()
        logger.info("Reset all device conversations.")
