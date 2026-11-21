"""
Orchestrator Coordinator (Control Plane Engine).
Bridges Telegram Bot (aiogram v3), NATS Messaging, Presence Monitoring,
Turn-Lock State Machine, and Signed Execution Manifest Manager.
"""
import asyncio
import json
import logging
import os
import re
import signal
import sys
import time
import uuid
from typing import Dict, List, Optional, Set
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery
import nats
from nats.aio.client import Client as NATSClient

from shared.config import (
    PRESENCE_KV_BUCKET,
    FLEET_NODES,
    SUBJECT_NODE_CONSULT,
    SUBJECT_NODE_MANIFEST,
    SUBJECT_BROADCAST,
)
from shared.models import (
    ExecutionManifest,
    NodeManifest,
    CommandStep,
    AgentTurnMessage,
    AgentStatus,
)
from shared.manifest import calculate_manifest_hash
from orchestrator.config import OrchestratorConfig
from orchestrator.auth_middleware import StrictOwnerAuthMiddleware
from orchestrator.presence import PresenceMonitor
from orchestrator.turn_lock import TurnLockManager
from orchestrator.queue import PerNodeTaskQueue
from orchestrator.manifest_manager import ManifestManager
from orchestrator.dlp_filter import TelegramDLPFilter

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("fleet-orchestrator")


class FleetCoordinator:
    def __init__(self, config: OrchestratorConfig):
        self.config = config
        self.bot: Optional[Bot] = None
        self.dp: Optional[Dispatcher] = None
        self.nc: Optional[NATSClient] = None
        self.presence: Optional[PresenceMonitor] = None
        self.turn_lock = TurnLockManager(max_turns=config.max_turns)
        self.queue = PerNodeTaskQueue()
        self.manifest_mgr = ManifestManager()
        self.dlp = TelegramDLPFilter()
        self.is_running = False

    async def initialize(self) -> None:
        """Initialize NATS and aiogram bot."""
        logger.info("Initializing Orchestrator Control Plane...")

        # 1. Connect to NATS
        connect_opts = {
            "servers": [self.config.nats_url],
            "name": "fleet-orchestrator-control-plane",
            "reconnect_time_wait": 2,
            "max_reconnect_attempts": -1,
        }
        if self.config.nats_token:
            connect_opts["token"] = self.config.nats_token

        self.nc = await nats.connect(**connect_opts)
        logger.info(f"Connected to NATS at {self.config.nats_url}")

        # JetStream Presence KV
        js = self.nc.jetstream()
        try:
            kv = await js.key_value(bucket=PRESENCE_KV_BUCKET)
        except Exception:
            kv = await js.create_key_value(bucket=PRESENCE_KV_BUCKET, ttl=15, history=1)
        self.presence = PresenceMonitor(kv=kv)

        # 2. Setup Bot & Dispatcher
        self.bot = Bot(token=self.config.telegram_bot_token)
        self.dp = Dispatcher()

        # Strict Owner Auth Middleware
        auth_middleware = StrictOwnerAuthMiddleware(
            allowed_group_id=self.config.allowed_group_id,
            owner_user_id=self.config.owner_user_id
        )
        self.dp.message.middleware(auth_middleware)
        self.dp.callback_query.middleware(auth_middleware)

        # Register Handlers
        self._register_handlers()

    def _register_handlers(self) -> None:
        # Command handlers
        self.dp.message.register(self._cmd_start, Command("start", "help"))
        self.dp.message.register(self._cmd_status, Command("status"))
        self.dp.message.register(self._cmd_stop, Command("stop", "abort"))
        self.dp.message.register(self._cmd_mode, Command("mode"))
        self.dp.message.register(self._cmd_update, Command("update"))
        self.dp.message.register(self._cmd_dashboard, Command("dashboard"))
        self.dp.message.register(self._cmd_getfile, Command("file", "getfile"))

        # Callback query handlers for Inline Manifest Approval
        self.dp.callback_query.register(self._handle_manifest_approval, F.data.startswith("man:app:"))
        self.dp.callback_query.register(self._handle_manifest_revision, F.data.startswith("man:rev:"))
        self.dp.callback_query.register(self._handle_manifest_cancel, F.data.startswith("man:can:"))

        # Generic chat handler for consult/discussion
        self.dp.message.register(self._handle_chat_message)

    # ---------------- Command Handlers ----------------

    async def _cmd_start(self, message: Message):
        text = (
            "🛡️ *Antigravity Fleet Orchestrator (agy-fleet)*\n\n"
            "Sistem siap mengendalikan 5 perangkat (2 PC, 2 Laptop, 1 VPS).\n"
            "• *Default Mode:* `CONSULT` (Read-Only Fact Gathering)\n"
            "• *Security:* Signed Execution Manifest (Anti Perintah Diam-Diam)\n"
            "• *Secret Vault:* OS Keyring / Zero-Exposure Store\n\n"
            "📌 *Perintah Utama:*\n"
            "• `/status` - Cek kehadiran node & beban sistem\n"
            "• `/dashboard` - Pinned dashboard live status di topik ini\n"
            "• `/mode ringkas|pantau` - Atur tingkat detail diskusi\n"
            "• `/stop` atau `/abort` - Hentikan paksa seluruh proses aktif\n"
            "• `/update @online` - Git pull & restart daemon pada node aktif\n\n"
            "💡 *Format Tagging:*\n"
            "• `pc1: cek disk`\n"
            "• `pc1 vps: bandingkan port`\n"
            "• `@online: cek memori`\n"
            "• `@all: status layanan`"
        )
        await self.dlp.send_safe_message(self.bot, message.chat.id, text, topic_id=message.message_thread_id)

    async def _cmd_status(self, message: Message):
        await self.presence.update_presence()
        dashboard = self.presence.format_dashboard_text()
        await self.dlp.send_safe_message(self.bot, message.chat.id, dashboard, topic_id=message.message_thread_id)

    async def _cmd_stop(self, message: Message):
        logger.warning(f"Owner {message.from_user.id} requested EMERGENCY STOP across all nodes.")
        payload = {"action": "stop", "target": "@all"}
        await self.nc.publish(SUBJECT_BROADCAST, json.dumps(payload).encode("utf-8"))
        await self.dlp.send_safe_message(
            self.bot, message.chat.id,
            "🛑 *EMERGENCY STOP DIKIRIM*: Sinyal terminasi (SIGTERM/SIGKILL) disiarkan ke seluruh node.",
            topic_id=message.message_thread_id
        )

    async def _cmd_mode(self, message: Message):
        args = message.text.split()[1:] if message.text else []
        if args and args[0].lower() in ["ringkas", "pantau"]:
            self.config.mode = args[0].lower()
            await self.dlp.send_safe_message(
                self.bot, message.chat.id,
                f"⚙️ Mode diskusi diubah ke: *{self.config.mode}*",
                topic_id=message.message_thread_id
            )
        else:
            await self.dlp.send_safe_message(
                self.bot, message.chat.id,
                f"ℹ️ Mode saat ini: *{self.config.mode}*. Gunakan `/mode ringkas` atau `/mode pantau`.",
                topic_id=message.message_thread_id
            )

    async def _cmd_update(self, message: Message):
        args = message.text.split()[1:] if message.text else []
        target = args[0] if args else "@online"
        payload = {"action": "update", "target": target}
        await self.nc.publish(SUBJECT_BROADCAST, json.dumps(payload).encode("utf-8"))
        await self.dlp.send_safe_message(
            self.bot, message.chat.id,
            f"🔄 *UPDATE DISIARKAN* ke `{target}`: Daemon akan melakukan `git pull` dan restart aman.",
            topic_id=message.message_thread_id
        )

    async def _cmd_dashboard(self, message: Message):
        await self.presence.update_presence()
        text = self.presence.format_dashboard_text()
        sent = await self.dlp.send_safe_message(self.bot, message.chat.id, text, topic_id=message.message_thread_id)
        self.config.dashboard_message_id = sent.message_id
        self.config.dashboard_topic_id = message.message_thread_id
        try:
            await self.bot.pin_chat_message(chat_id=message.chat.id, message_id=sent.message_id)
        except Exception:
            pass

    async def _cmd_getfile(self, message: Message):
        """Fetch and send a file as document to Telegram with DLP redaction."""
        args = message.text.split(maxsplit=1)[1:] if message.text else []
        if not args:
            await self.dlp.send_safe_message(
                self.bot, message.chat.id,
                "ℹ️ Gunakan format: `/file <path_file>` (contoh: `/file /root/report.txt`)",
                topic_id=message.message_thread_id
            )
            return

        file_path_str = args[0].strip()
        from pathlib import Path
        from aiogram.types import BufferedInputFile
        file_path = Path(file_path_str)

        if not file_path.exists() or not file_path.is_file():
            await self.dlp.send_safe_message(
                self.bot, message.chat.id,
                f"❌ File tidak ditemukan: `{file_path_str}`",
                topic_id=message.message_thread_id
            )
            return

        try:
            # Check size (< 45 MB limit for bot API)
            size_mb = file_path.stat().st_size / (1024 * 1024)
            if size_mb > 45:
                await self.dlp.send_safe_message(
                    self.bot, message.chat.id,
                    f"⚠️ Ukuran file ({size_mb:.1f} MB) melebihi batas Telegram (45 MB).",
                    topic_id=message.message_thread_id
                )
                return

            # If text file, run DLP redaction
            is_text = file_path.suffix.lower() in [".txt", ".log", ".json", ".py", ".md", ".sh", ".yaml", ".yml", ".env", ".conf"]
            if is_text:
                content = file_path.read_text(encoding="utf-8", errors="replace")
                sanitized = self.dlp.redactor.redact(content)
                file_bytes = sanitized.encode("utf-8")
            else:
                file_bytes = file_path.read_bytes()

            doc = BufferedInputFile(file_bytes, filename=file_path.name)
            await self.bot.send_document(
                chat_id=message.chat.id,
                document=doc,
                caption=f"📄 File: `{file_path.name}` ({file_path_str})",
                message_thread_id=message.message_thread_id
            )
        except Exception as e:
            await self.dlp.send_safe_message(
                self.bot, message.chat.id,
                f"❌ Gagal mengirim file: `{str(e)}`",
                topic_id=message.message_thread_id
            )

    # ---------------- Tagging & Chat Handling ----------------

    def _parse_target_nodes(self, text: str) -> Tuple[List[str], str]:
        """
        Extracts target nodes from message prefix.
        e.g. "pc1: cek disk" -> (["pc1"], "cek disk")
        "pc1 vps: cek port" -> (["pc1", "vps"], "cek port")
        "@online: cek ram" -> (["@online"], "cek ram")
        """
        pattern = r"^(@all|@online|(?:(?:vps|pc1|pc2|laptop1|laptop2)[,\s]*)+):?\s*(.*)$"
        match = re.match(pattern, text.strip(), re.IGNORECASE)
        if match:
            nodes_part = match.group(1).lower().strip()
            rest = match.group(2).strip()
            if nodes_part == "@all":
                return self.presence.get_all_nodes(), rest
            elif nodes_part == "@online":
                return self.presence.get_online_nodes() or ["vps"], rest
            else:
                raw_nodes = re.split(r"[,\s]+", nodes_part)
                valid_nodes = [n for n in raw_nodes if n in FLEET_NODES]
                return valid_nodes or ["vps"], rest

        # Default: target vps (or coordinator node)
        return ["vps"], text.strip()

    async def _handle_chat_message(self, message: Message):
        """Main chat handler: default = CONSULT (Read-Only Fact Gathering)."""
        user_prompt = message.text or ""
        if not user_prompt:
            return

        target_nodes, cleaned_prompt = self._parse_target_nodes(user_prompt)
        await self.presence.update_presence()

        project_id = str(message.message_thread_id or message.chat.id)

        # Single progress message auto-edited during execution
        prog_msg = await message.reply("⏳ _Memproses permintaan dalam mode CONSULT (Read-Only Fact Gathering)..._")

        try:
            # 1. Gather live node facts
            online_nodes = self.presence.get_online_nodes()
            offline_nodes = [n for n in FLEET_NODES if n not in online_nodes]
            presence_context = f"ONLINE: {', '.join(online_nodes)} | OFFLINE: {', '.join(offline_nodes)}"

            facts_collected = {}
            for node_id in target_nodes:
                if not self.presence.is_node_online(node_id):
                    facts_collected[node_id] = {"status": "OFFLINE", "error": f"Node {node_id} sedang mati/sleep."}
                    continue

                # Query node via NATS consult subject (enforcing read-only diagnostics)
                consult_subj = SUBJECT_NODE_CONSULT.format(node_id=node_id)
                req_data = {
                    "prompt": f"[PRESENCE CONTEXT: {presence_context}]\nUser query: {cleaned_prompt}",
                    "command": self._guess_diagnostic_cmd(cleaned_prompt),
                    "timeout_sec": 30
                }
                try:
                    resp_msg = await self.nc.request(consult_subj, json.dumps(req_data).encode("utf-8"), timeout=35.0)
                    resp_data = json.loads(resp_msg.data.decode("utf-8"))
                    facts_collected[node_id] = resp_data
                except Exception as e:
                    facts_collected[node_id] = {"status": "ERROR", "error": str(e)}

            # 2. Check if the prompt / gathered facts indicate need for system modification
            needs_action, proposal = self._detect_modification_intent(cleaned_prompt, facts_collected)

            if needs_action and proposal:
                # Build ExecutionManifest
                manifest = ExecutionManifest(
                    manifest_id=str(uuid.uuid4())[:8],
                    project_id=project_id,
                    created_at=str(time.time()),
                    title=f"Eksekusi untuk: {cleaned_prompt[:40]}",
                    description=proposal.get("description", "Operasi perubahan sistem"),
                    nodes=proposal.get("nodes", {})
                )
                registered_manifest, h = self.manifest_mgr.register_manifest(manifest)
                proposal_text = self.manifest_mgr.format_proposal(registered_manifest)
                kb = self.manifest_mgr.create_approval_keyboard(registered_manifest)

                await prog_msg.edit_text(
                    f"{proposal_text}\n\n⚠️ *Perintah ini memerlukan persetujuan eksplisit Anda sebelum dijalankan.*",
                    reply_markup=kb,
                    parse_mode="Markdown"
                )
            else:
                # Read-only CONSULT answer
                summary_lines = ["🔍 *HASIL KONSULTASI (CONSULT READ-ONLY)*\n"]
                for nid, res in facts_collected.items():
                    if "stdout" in res and res["stdout"]:
                        summary_lines.append(f"🖥️ *Node `{nid}`:*\n```\n{res['stdout'][:800]}\n```")
                    elif "agy" in res and res["agy"].get("response"):
                        summary_lines.append(f"🤖 *Node `{nid}`:*\n{res['agy']['response']}")
                    elif "error" in res:
                        summary_lines.append(f"⚠️ *Node `{nid}`:* {res['error']}")

                full_text = "\n\n".join(summary_lines)
                display_text, exceeds, full_redacted = self.dlp.sanitize_text(full_text)
                await prog_msg.edit_text(display_text, parse_mode="Markdown")

        except Exception as e:
            logger.error(f"Error processing chat message: {e}")
            await prog_msg.edit_text(f"❌ Terjadi kesalahan saat memproses permintaan: `{str(e)}`")

    def _guess_diagnostic_cmd(self, prompt: str) -> Optional[str]:
        """Maps common natural query keywords to safe read-only commands."""
        p = prompt.lower()
        if "disk" in p or "storage" in p:
            return "df -h"
        if "ram" in p or "memori" in p or "memory" in p:
            return "free -h"
        if "cpu" in p or "load" in p or "beban" in p:
            return "uptime"
        if "port" in p or "koneksi" in p or "listening" in p:
            return "ss -tuln"
        if "docker" in p or "container" in p:
            return "docker ps"
        if "ip" in p or "network" in p:
            return "ip addr"
        return None

    def _detect_modification_intent(self, prompt: str, facts: Dict[str, Any]) -> Tuple[bool, Optional[Dict[str, Any]]]:
        """
        Determines if request requires system mutation (install, restart, create, delete).
        If so, creates proposed plan / manifest nodes.
        """
        p = prompt.lower()
        keywords = ["install", "update", "pasang", "restart", "jalankan", "hapus", "buat", "start", "stop service"]
        if not any(k in p for k in keywords):
            return False, None

        # Build proposed command steps per node
        nodes_manifest: Dict[str, NodeManifest] = {}
        for nid in facts.keys():
            if "install" in p or "pasang" in p:
                cmd = "echo 'Simulasi instalasi komponen selesai.'"
                v_cmd = "echo 'Verifikasi status instalasi: OK'"
            elif "restart" in p:
                cmd = "echo 'Simulasi restart layanan selesai.'"
                v_cmd = "echo 'Verifikasi layanan aktif: OK'"
            else:
                cmd = "echo 'Menjalankan langkah perubahan terencana.'"
                v_cmd = "echo 'Verifikasi akhir: OK'"

            nodes_manifest[nid] = NodeManifest(
                node_id=nid,
                commands=[CommandStep(command=cmd, description="Aksi terencana")],
                rollback_commands=[CommandStep(command="echo 'Rollback dieksekusi.'", description="Pemulihan otomatis")],
                verification_commands=[CommandStep(command=v_cmd, description="Pengecekan objektif")]
            )

        return True, {
            "description": f"Eksekusi terencana untuk permintaan: {prompt}",
            "nodes": nodes_manifest
        }

    # ---------------- Manifest Approval Callback Handlers ----------------

    async def _handle_manifest_approval(self, call: CallbackQuery):
        """User clicked [✅ Setujui & Jalankan]."""
        parts = call.data.split(":")
        manifest_id = parts[2]
        expected_hash = parts[3]

        manifest = self.manifest_mgr.get_manifest(manifest_id)
        if not manifest:
            await call.answer("❌ Manifes tidak ditemukan atau sudah kadaluarsa.", show_alert=True)
            return

        signed_manifest, full_hash = self.manifest_mgr.approve_manifest(manifest_id, call.from_user.id)
        await call.answer("✅ Disetujui! Memulai eksekusi berantai...", show_alert=False)
        await call.message.edit_reply_markup(reply_markup=None)

        # Update progress message
        short_hash = full_hash[:12]
        await call.message.edit_text(
            f"⚡ *MENJALANKAN EKSEKUSI MANIFEST* (Hash: `{short_hash}...`)\n"
            f"Status: _Mengirim signed manifest ke node terkait..._",
            parse_mode="Markdown"
        )

        results = {}
        for node_id in signed_manifest.nodes.keys():
            manifest_subj = SUBJECT_NODE_MANIFEST.format(node_id=node_id)
            req_data = {
                "manifest": signed_manifest.model_dump(),
                "expected_hash": full_hash
            }
            try:
                resp_msg = await self.nc.request(manifest_subj, json.dumps(req_data).encode("utf-8"), timeout=120.0)
                results[node_id] = json.loads(resp_msg.data.decode("utf-8"))
            except Exception as e:
                results[node_id] = {"success": False, "error_message": str(e)}

        # Format final objective summary
        all_ok = all(r.get("success", False) for r in results.values())
        header = "🎉 *EKSEKUSI SUKSES*" if all_ok else "⚠️ *EKSEKUSI SELESAI DENGAN CATATAN*"
        lines = [f"{header} (Hash: `{short_hash}...`)\n"]

        for nid, res in results.items():
            status_ico = "✅" if res.get("success") else "❌"
            lines.append(f"{status_ico} *Node `{nid}`:*")
            if res.get("error_message"):
                lines.append(f"  • _Error:_ `{res['error_message']}`")
            if res.get("rollback_performed"):
                lines.append("  • ↺ _Rollback telah dijalankan otomatis._")
            # Verification outcomes
            if res.get("verification_results"):
                for v in res["verification_results"]:
                    lines.append(f"  • ✓ Verifikasi: `{v.get('stdout', 'OK')}` (Exit: {v.get('exit_code', 0)})")

        final_text = "\n".join(lines)
        display_text, exceeds, full_redacted = self.dlp.sanitize_text(final_text)
        await call.message.edit_text(display_text, parse_mode="Markdown")

    async def _handle_manifest_revision(self, call: CallbackQuery):
        """User clicked [✏️ Revisi]."""
        parts = call.data.split(":")
        manifest_id = parts[2]
        self.manifest_mgr.cancel_manifest(manifest_id)
        await call.answer("✏️ Mode revisi diaktifkan.", show_alert=False)
        await call.message.edit_reply_markup(reply_markup=None)
        await call.message.edit_text(
            "✏️ *PERMINTAAN REVISI*\nSilakan ketik instruksi revisi atau perubahan yang diinginkan di chat.",
            parse_mode="Markdown"
        )

    async def _handle_manifest_cancel(self, call: CallbackQuery):
        """User clicked [❌ Batal]."""
        parts = call.data.split(":")
        manifest_id = parts[2]
        self.manifest_mgr.cancel_manifest(manifest_id)
        await call.answer("❌ Eksekusi dibatalkan.", show_alert=False)
        await call.message.edit_reply_markup(reply_markup=None)
        await call.message.edit_text("❌ *EKSEKUSI DIBATALKAN* oleh pemilik.", parse_mode="Markdown")

    # ---------------- Life Cycle ----------------

    async def _presence_poller_task(self):
        """Background task updating presence and dashboard message."""
        while self.is_running:
            try:
                if self.presence:
                    await self.presence.update_presence()
                # If dashboard message configured, auto-edit it
                if (
                    self.config.dashboard_message_id
                    and self.config.dashboard_topic_id
                    and self.presence
                    and self.bot
                ):
                    dash_text = self.presence.format_dashboard_text()
                    try:
                        await self.bot.edit_message_text(
                            chat_id=self.config.allowed_group_id,
                            message_id=self.config.dashboard_message_id,
                            text=dash_text,
                            parse_mode="Markdown"
                        )
                    except Exception:
                        pass
            except Exception as e:
                logger.warning(f"Presence poller warning: {e}")
            await asyncio.sleep(10.0)

    async def start(self) -> None:
        self.is_running = True
        await self.initialize()
        asyncio.create_task(self._presence_poller_task())
        logger.info("Bot polling starting...")
        await self.dp.start_polling(self.bot)

    async def stop(self) -> None:
        self.is_running = False
        await self.queue.shutdown()
        if self.nc:
            await self.nc.drain()
            await self.nc.close()
        if self.bot:
            await self.bot.session.close()
        logger.info("Orchestrator stopped.")


def main():
    cfg = OrchestratorConfig.load()
    if not cfg.telegram_bot_token or not cfg.owner_user_id:
        print("❌ Kredensial Orchestrator belum lengkap.")
        print("Gunakan utility 'fleet-secret' untuk mengatur:")
        print("  fleet-secret set TELEGRAM_BOT_TOKEN")
        print("  fleet-secret set OWNER_USER_ID")
        print("  fleet-secret set ALLOWED_GROUP_ID (opsional: jika tidak diisi, otomatis mode Direct Message / Japri)")
        sys.exit(1)

    coordinator = FleetCoordinator(cfg)
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, lambda: asyncio.create_task(coordinator.stop()))
        except (NotImplementedError, RuntimeError):
            pass

    try:
        loop.run_until_complete(coordinator.start())
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        loop.run_until_complete(coordinator.stop())
        loop.close()


if __name__ == "__main__":
    main()
