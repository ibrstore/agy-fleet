"""
Fleet configuration constants and settings.
"""
from dataclasses import dataclass
from typing import List

# Heartbeat & Presence
HEARTBEAT_INTERVAL_SEC: float = 5.0
HEARTBEAT_TTL_SEC: float = 15.0
PRESENCE_KV_BUCKET: str = "fleet_presence"

# Multi-Agent Turn Limits
DEFAULT_MAX_TURNS: int = 4

# Telegram UX limits
TELEGRAM_MAX_TEXT_LEN: int = 3500

# NATS Subject Patterns
SUBJECT_NODE_CONSULT = "fleet.node.{node_id}.consult"
SUBJECT_NODE_MANIFEST = "fleet.node.{node_id}.manifest"
SUBJECT_NODE_COMMAND = "fleet.node.{node_id}.command"
SUBJECT_NODE_CANCEL = "fleet.node.{node_id}.cancel"
SUBJECT_BROADCAST = "fleet.broadcast"
SUBJECT_ORCHESTRATOR_EVENTS = "fleet.orchestrator.events"

# Known fleet node identifiers (VPS, 3 PC, 2 Laptop)
FLEET_NODES: List[str] = [
    "vps",
    "pc-naufal",
    "pc-diba",
    "lap-asus",
    "lap-user",
    "pc-nci",
]

# Legacy / shorthand aliases mapping to canonical node IDs
NODE_ALIASES: dict[str, str] = {
    "pc1": "pc-naufal",
    "pc2": "pc-diba",
    "laptop1": "lap-asus",
    "laptop2": "lap-user",
    "naufal": "pc-naufal",
    "diba": "pc-diba",
    "asus": "lap-asus",
    "user": "lap-user",
    "nci": "pc-nci",
}


@dataclass
class FleetSettings:
    nats_url: str = "nats://127.0.0.1:4222"
    nats_user: str = "fleet_user"
    nats_password: str = ""
    nats_token: str = ""
    turn_limit: int = DEFAULT_MAX_TURNS
    heartbeat_interval: float = HEARTBEAT_INTERVAL_SEC
    heartbeat_ttl: float = HEARTBEAT_TTL_SEC
