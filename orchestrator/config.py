"""
Orchestrator configuration.
Securely retrieves credentials from OS Vault/Keyring or environment.
Never expects raw credentials to be hardcoded or stored in plaintext .env.
"""
import os
from dataclasses import dataclass
from typing import Optional
from shared.vault import SecretVault


@dataclass
class OrchestratorConfig:
    telegram_bot_token: str
    owner_user_id: int
    allowed_group_id: int
    nats_url: str = "nats://127.0.0.1:4222"
    nats_token: str = ""
    max_turns: int = 4
    mode: str = "ringkas"  # "ringkas" (summary only) or "pantau" (per-turn update)
    dashboard_topic_id: Optional[int] = None
    dashboard_message_id: Optional[int] = None

    @classmethod
    def load(cls) -> "OrchestratorConfig":
        vault = SecretVault()

        # Token
        token = vault.get_secret("TELEGRAM_BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN", "")

        # Owner ID
        owner_raw = vault.get_secret("OWNER_USER_ID") or os.getenv("OWNER_USER_ID", "0")
        try:
            owner_id = int(owner_raw)
        except ValueError:
            owner_id = 0

        # Allowed Group ID (defaults to owner_id if not set, enabling direct 1-on-1 private chat)
        group_raw = vault.get_secret("ALLOWED_GROUP_ID") or os.getenv("ALLOWED_GROUP_ID", "0")
        try:
            group_id = int(group_raw)
        except ValueError:
            group_id = 0
        if group_id == 0 and owner_id != 0:
            group_id = owner_id

        # NATS
        nats_url = vault.get_secret("NATS_URL") or os.getenv("FLEET_NATS_URL", "nats://127.0.0.1:4222")
        nats_token = vault.get_secret("NATS_TOKEN") or os.getenv("FLEET_NATS_TOKEN", "")

        # Mode
        mode = os.getenv("FLEET_MODE", "ringkas")

        # Dashboard Topic & Message
        topic_raw = os.getenv("FLEET_DASHBOARD_TOPIC_ID")
        msg_raw = os.getenv("FLEET_DASHBOARD_MESSAGE_ID")

        return cls(
            telegram_bot_token=token,
            owner_user_id=owner_id,
            allowed_group_id=group_id,
            nats_url=nats_url,
            nats_token=nats_token,
            max_turns=4,
            mode=mode,
            dashboard_topic_id=int(topic_raw) if topic_raw else None,
            dashboard_message_id=int(msg_raw) if msg_raw else None,
        )
