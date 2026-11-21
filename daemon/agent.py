"""
Node Agent Service (Data Plane).
Runs continuously on all 5 nodes (VPS, 2 PC, 2 Laptop).
Connects to NATS via Tailscale private IP.
Maintains 5-second heartbeat, listens for consult/manifest tasks, and enforces boundaries.
"""
import asyncio
import json
import logging
import os
import signal
import socket
import sys
import time
from typing import Optional, Set
import nats
from nats.aio.client import Client as NATSClient
from nats.js.kv import KeyValue
import psutil

from shared.config import (
    PRESENCE_KV_BUCKET,
    HEARTBEAT_INTERVAL_SEC,
    SUBJECT_NODE_CONSULT,
    SUBJECT_NODE_MANIFEST,
    SUBJECT_BROADCAST,
)
from shared.models import (
    NodeHeartbeat,
    NodeStatus,
    ExecutionManifest,
)
from shared.redactor import Redactor
from shared.vault import SecretVault
from daemon.config import DaemonConfig
from daemon.agy_adapter import AgyAdapter
from daemon.allowlist import validate_consult_command, ConsultModeViolationError
from daemon.executor import ManifestExecutor

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("fleet-daemon")


class FleetDaemon:
    def __init__(self, config: DaemonConfig):
        self.config = config
        self.vault = SecretVault()
        self.redactor = Redactor(vault=self.vault)
        self.agy = AgyAdapter(binary_path=config.agy_binary_path, redactor=self.redactor)
        self.executor = ManifestExecutor(node_id=config.node_id, vault=self.vault, redactor=self.redactor)
        self.nc: Optional[NATSClient] = None
        self.kv: Optional[KeyValue] = None
        self.is_running = False
        self.active_processes: Set[asyncio.subprocess.Process] = set()
        self.active_task_id: Optional[str] = None

    def _get_local_ip(self) -> str:
        """Find Tailscale IP (100.x) or private LAN IP."""
        try:
            for iface, addrs in psutil.net_if_addrs().items():
                for addr in addrs:
                    if addr.family == socket.AF_INET:
                        ip = addr.address
                        # Prefer Tailscale IP range
                        if ip.startswith("100."):
                            return ip
            # Fallback to general socket IP
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("1.1.1.1", 80))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except Exception:
            return "127.0.0.1"

    async def start(self) -> None:
        """Connect to NATS and start workers."""
        logger.info(f"Starting agy-fleet daemon for node '{self.config.node_id}'...")
        self.is_running = True

        # Connect to NATS
        connect_opts = {
            "servers": [self.config.nats_url],
            "name": f"fleet-node-{self.config.node_id}",
            "reconnect_time_wait": 2,
            "max_reconnect_attempts": -1,
        }
        if self.config.nats_token:
            connect_opts["token"] = self.config.nats_token

        self.nc = await nats.connect(**connect_opts)
        logger.info(f"Connected to NATS at {self.config.nats_url}")

        # Connect to JetStream KV for Presence
        js = self.nc.jetstream()
        try:
            self.kv = await js.key_value(bucket=PRESENCE_KV_BUCKET)
        except Exception:
            # Create KV bucket if not exists (with 15s TTL)
            try:
                self.kv = await js.create_key_value(
                    bucket=PRESENCE_KV_BUCKET,
                    ttl=15,  # 15s TTL for auto-expire on sleep/crash
                    history=1
                )
            except Exception as e:
                logger.warning(f"Could not bind KV bucket: {e}")

        # Subscribe to subjects
        consult_subj = SUBJECT_NODE_CONSULT.format(node_id=self.config.node_id)
        manifest_subj = SUBJECT_NODE_MANIFEST.format(node_id=self.config.node_id)

        await self.nc.subscribe(consult_subj, cb=self._handle_consult)
        await self.nc.subscribe(manifest_subj, cb=self._handle_manifest)
        await self.nc.subscribe(SUBJECT_BROADCAST, cb=self._handle_broadcast)

        logger.info(f"Listening on subjects: {consult_subj}, {manifest_subj}, {SUBJECT_BROADCAST}")

        # Start background heartbeat loop
        asyncio.create_task(self._heartbeat_loop())

    async def _heartbeat_loop(self) -> None:
        """Publishes heartbeat every 5 seconds to presence KV store."""
        agy_ver = await self.agy.get_version()
        while self.is_running and self.nc and not self.nc.is_closed:
            try:
                cpu = psutil.cpu_percent()
                mem = psutil.virtual_memory().percent
                ip = self._get_local_ip()

                heartbeat = NodeHeartbeat(
                    node_id=self.config.node_id,
                    name=self.config.device_name,
                    device_type=self.config.device_type,
                    os_name=self.config.os_name,
                    ip=ip,
                    status=NodeStatus.BUSY if self.active_task_id else NodeStatus.ONLINE,
                    cpu_pct=cpu,
                    mem_pct=mem,
                    agy_version=agy_ver,
                    active_task_id=self.active_task_id,
                    timestamp=time.time()
                )

                if self.kv:
                    await self.kv.put(self.config.node_id, heartbeat.model_dump_json().encode("utf-8"))

            except Exception as e:
                logger.warning(f"Heartbeat update error: {e}")

            await asyncio.sleep(self.config.heartbeat_interval)

    async def _handle_consult(self, msg) -> None:
        """Handle read-only diagnostic command or consult prompt."""
        try:
            data = json.loads(msg.data.decode("utf-8"))
            cmd = data.get("command")
            prompt = data.get("prompt")
            task_id = data.get("task_id", f"consult_{int(time.time())}")
            self.active_task_id = task_id

            response_data = {"status": "SUCCESS", "task_id": task_id, "node_id": self.config.node_id}

            if cmd:
                # 1. Strictly validate read-only consult boundaries
                try:
                    validate_consult_command(cmd)
                except ConsultModeViolationError as cve:
                    response_data["status"] = "ERROR"
                    response_data["error"] = str(cve)
                    if msg.reply:
                        await self.nc.publish(msg.reply, json.dumps(response_data).encode("utf-8"))
                    return

                # 2. Execute safe diagnostic shell command
                proc = await asyncio.create_subprocess_shell(
                    cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE
                )
                self.active_processes.add(proc)
                try:
                    stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30.0)
                    response_data["stdout"] = self.redactor.redact(stdout.decode("utf-8", errors="replace"))
                    response_data["stderr"] = self.redactor.redact(stderr.decode("utf-8", errors="replace"))
                    response_data["exit_code"] = proc.returncode
                finally:
                    self.active_processes.discard(proc)

            elif prompt:
                # Execute via agy adapter
                agy_res = await self.agy.run_prompt(
                    prompt=prompt,
                    conversation_id=data.get("conversation_id"),
                    timeout_sec=data.get("timeout_sec", 60)
                )
                response_data["agy"] = agy_res

            if msg.reply:
                await self.nc.publish(msg.reply, json.dumps(response_data).encode("utf-8"))

        except Exception as e:
            logger.error(f"Error handling consult: {e}")
            if msg.reply:
                err_payload = {"status": "ERROR", "error": str(e), "node_id": self.config.node_id}
                await self.nc.publish(msg.reply, json.dumps(err_payload).encode("utf-8"))
        finally:
            self.active_task_id = None

    async def _handle_manifest(self, msg) -> None:
        """Handle execution manifest."""
        try:
            data = json.loads(msg.data.decode("utf-8"))
            raw_manifest = data.get("manifest")
            expected_hash = data.get("expected_hash")

            manifest = ExecutionManifest(**raw_manifest)
            self.active_task_id = manifest.manifest_id

            logger.info(f"Executing manifest {manifest.manifest_id} with hash {expected_hash[:12]}...")
            result = await self.executor.execute_manifest(manifest, expected_hash)

            if msg.reply:
                await self.nc.publish(msg.reply, result.model_dump_json().encode("utf-8"))

        except Exception as e:
            logger.error(f"Error handling manifest: {e}")
            if msg.reply:
                err_payload = {
                    "manifest_id": data.get("manifest", {}).get("manifest_id", "unknown"),
                    "node_id": self.config.node_id,
                    "success": False,
                    "error_message": f"Manifest execution error: {str(e)}"
                }
                await self.nc.publish(msg.reply, json.dumps(err_payload).encode("utf-8"))
        finally:
            self.active_task_id = None

    async def _handle_broadcast(self, msg) -> None:
        """Handle broadcast events like /stop, /abort, or /update."""
        try:
            data = json.loads(msg.data.decode("utf-8"))
            action = data.get("action")
            target = data.get("target", "@all")

            if target != "@all" and target != "@online" and target != self.config.node_id:
                return

            if action in ["stop", "abort"]:
                logger.warning(f"Received emergency STOP signal. Terminating {len(self.active_processes)} active processes...")
                for p in list(self.active_processes):
                    try:
                        p.terminate()
                    except Exception:
                        pass
                await asyncio.sleep(0.5)
                for p in list(self.active_processes):
                    try:
                        p.kill()
                    except Exception:
                        pass
                self.active_processes.clear()
                self.active_task_id = None

            elif action == "update":
                logger.info("Received /update command: pulling latest changes from git...")
                proc = await asyncio.create_subprocess_shell(
                    "git pull",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE
                )
                stdout, stderr = await proc.communicate()
                logger.info(f"Git pull output: {stdout.decode()}")
                # Exit cleanly so systemd / service supervisor restarts us with updated code
                sys.exit(0)

        except Exception as e:
            logger.error(f"Error handling broadcast: {e}")

    async def stop(self) -> None:
        self.is_running = False
        if self.nc:
            await self.nc.drain()
            await self.nc.close()
        logger.info("Daemon stopped.")


def main():
    cfg = DaemonConfig.load()
    daemon = FleetDaemon(cfg)

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, lambda: asyncio.create_task(daemon.stop()))
        except (NotImplementedError, RuntimeError):
            pass

    try:
        loop.run_until_complete(daemon.start())
        loop.run_forever()
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        loop.run_until_complete(daemon.stop())
        loop.close()


if __name__ == "__main__":
    main()
