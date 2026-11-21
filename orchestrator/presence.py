"""
Fleet Presence and Heartbeat Monitor.
Watches NATS Key-Value store (15s TTL) and updates live fleet dashboard.
"""
import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional
from nats.js.kv import KeyValue
from shared.config import FLEET_NODES, HEARTBEAT_TTL_SEC
from shared.models import NodeHeartbeat, NodeStatus

logger = logging.getLogger("fleet-presence")


class PresenceMonitor:
    def __init__(self, kv: Optional[KeyValue] = None):
        self.kv = kv
        self.nodes_state: Dict[str, NodeHeartbeat] = {}
        for nid in FLEET_NODES:
            self.nodes_state[nid] = NodeHeartbeat(
                node_id=nid,
                name=nid.upper(),
                device_type="vps" if nid == "vps" else ("laptop" if "laptop" in nid else "pc"),
                os_name="unknown",
                ip="offline",
                status=NodeStatus.OFFLINE,
                timestamp=0.0
            )

    async def update_presence(self) -> None:
        """Poll KV store and refresh node statuses."""
        if not self.kv:
            return

        now = time.time()
        for nid in FLEET_NODES:
            try:
                entry = await self.kv.get(nid)
                if entry and entry.value:
                    data = json.loads(entry.value.decode("utf-8"))
                    hb = NodeHeartbeat(**data)
                    # Check TTL expiry
                    if (now - hb.timestamp) <= HEARTBEAT_TTL_SEC:
                        self.nodes_state[nid] = hb
                    else:
                        self.nodes_state[nid].status = NodeStatus.OFFLINE
                else:
                    self.nodes_state[nid].status = NodeStatus.OFFLINE
            except Exception:
                self.nodes_state[nid].status = NodeStatus.OFFLINE

    def get_online_nodes(self) -> List[str]:
        return [nid for nid, hb in self.nodes_state.items() if hb.status in [NodeStatus.ONLINE, NodeStatus.BUSY]]

    def get_all_nodes(self) -> List[str]:
        return list(FLEET_NODES)

    def is_node_online(self, node_id: str) -> bool:
        hb = self.nodes_state.get(node_id)
        return hb is not None and hb.status in [NodeStatus.ONLINE, NodeStatus.BUSY]

    def format_dashboard_text(self) -> str:
        """Format live dashboard text for Telegram."""
        now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        online_count = len(self.get_online_nodes())
        lines = [
            "🛰️ *FLEET DASHBOARD (LIVE PRESENCE)*",
            f"🕒 _Update: {now_str}_\n",
        ]

        for nid in FLEET_NODES:
            hb = self.nodes_state.get(nid)
            if hb and hb.status in [NodeStatus.ONLINE, NodeStatus.BUSY]:
                icon = "🟡" if hb.status == NodeStatus.BUSY else "🟢"
                status_str = f"BUSY: `{hb.active_task_id}`" if hb.status == NodeStatus.BUSY else "IDLE"
                lines.append(
                    f"{icon} *{nid}* ({hb.os_name.capitalize()} | `{hb.ip}`) "
                    f"• CPU: `{hb.cpu_pct:.0f}%` RAM: `{hb.mem_pct:.0f}%` [{status_str}]"
                )
            else:
                lines.append(f"🔴 *{nid}* • _OFFLINE_")

        lines.append(f"\n⚡ *Status:* `{online_count}/5` node online | Tag: `@online`, `@all`")
        return "\n".join(lines)
