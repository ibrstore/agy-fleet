"""
Daemon configuration management.
Reads settings from local config file (~/.agy-fleet/daemon.json) or environment.
"""
import json
import os
import platform
import socket
from pathlib import Path
from pydantic import BaseModel, Field
from shared.vault import SecretVault


class DaemonConfig(BaseModel):
    node_id: str = Field(default_factory=lambda: os.getenv("FLEET_NODE_ID", socket.gethostname().lower()))
    device_name: str = Field(default_factory=lambda: socket.gethostname())
    device_type: str = "pc"  # pc, laptop, vps
    os_name: str = Field(default_factory=lambda: platform.system().lower())
    nats_url: str = Field(default_factory=lambda: os.getenv("FLEET_NATS_URL", "nats://127.0.0.1:4222"))
    nats_token: str = Field(default_factory=lambda: os.getenv("FLEET_NATS_TOKEN", ""))
    heartbeat_interval: float = 5.0
    agy_binary_path: str = "agy"
    config_dir: str = Field(default_factory=lambda: str(Path.home() / ".agy-fleet"))

    @classmethod
    def load(cls, config_path: str = None) -> "DaemonConfig":
        path = Path(config_path) if config_path else Path.home() / ".agy-fleet" / "daemon.json"
        if path.exists():
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    return cls(**data)
            except Exception:
                pass

        # Try to read token from vault if present
        vault = SecretVault()
        token = vault.get_secret("NATS_NODE_TOKEN") or ""

        return cls(nats_token=token)

    def save(self, config_path: str = None) -> None:
        path = Path(config_path) if config_path else Path.home() / ".agy-fleet" / "daemon.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        # Avoid saving raw token in plain json if possible
        data = self.model_dump()
        data.pop("nats_token", None)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
