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
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, BufferedInputFile, InlineKeyboardMarkup, InlineKeyboardButton
import nats
from nats.aio.client import Client as NATSClient

from shared.config import (
    PRESENCE_KV_BUCKET,
    FLEET_NODES,
    NODE_ALIASES,
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
from orchestrator.conversations import ConversationManager

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("fleet-orchestrator")

SETTINGS_DIR = Path.home() / ".agy-fleet"
SETTINGS_FILE = SETTINGS_DIR / "settings.json"

FACTS_AND_DATA_INSTRUCTION = (
    "[ATURAN MUTLAK JAWABAN - FAKTA & DATA RIIL]:\n"
    "1. Jawab HANYA berdasarkan fakta, metrik, konfigurasi, dan kondisi riil di node Anda saat ini.\n"
    "2. DILARANG berspekulasi atau berasumsi jika belum memeriksa kondisi sistem riil.\n"
    "3. Selalu sertakan data konkret (seperti angka metrik CPU/RAM, kapasitas disk, status port, IP address, status service/docker, atau file konfigurasi riil) dalam penjelasan Anda.\n"
    "4. Gunakan tools sistem Anda (baca file, jalankan perintah diagnostik read-only) untuk mengumpulkan fakta objektif sebelum merumuskan jawaban."
)

REASONING_MODES: Dict[str, Dict[str, Any]] = {
    "auto": {
        "name": "auto",
        "label": "🤖 Auto (Smart: Diskusi=Fast, Pengerjaan=High)",
        "model": "auto",
        "effort": "auto",
        "timeout_sec": 600,
        "desc": "Deteksi cerdas otomatis: diskusi/rangkum/tanya jawab -> Fast (Low), pengerjaan/eksekusi/setup sistem -> Deep (High).",
    },
    "deep": {
        "name": "deep",
        "label": "🧠 Deep Reasoning (High)",
        "model": "gemini-3.8-flash-high",
        "effort": "high",
        "timeout_sec": 600,
        "desc": "Penalaran mendalam untuk arsitektur kompleks & analisis komprehensif (Timeout: 10m).",
    },
    "balanced": {
        "name": "balanced",
        "label": "⚖️ Balanced (Medium)",
        "model": "gemini-3.8-flash-medium",
        "effort": "medium",
        "timeout_sec": 300,
        "desc": "Keseimbangan kecepatan dan analisis cerdas (Timeout: 5m).",
    },
    "fast": {
        "name": "fast",
        "label": "⚡ Fast (Low)",
        "model": "gemini-3.8-flash-low",
        "effort": "low",
        "timeout_sec": 180,
        "desc": "Respons super cepat, minimal reasoning delay (Timeout: 3m).",
    },
}


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
        self.conv_mgr = ConversationManager()
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
        self.dp.message.register(self._cmd_mode, Command("mode", "model", "speed"))
        self.dp.message.register(self._cmd_update, Command("update"))
        self.dp.message.register(self._cmd_dashboard, Command("dashboard"))
        self.dp.message.register(self._cmd_getfile, Command("file", "getfile"))
        self.dp.message.register(self._cmd_new_conversation, Command("new", "reset"))

        # Callback query handlers for Inline Manifest Approval & Reasoning Mode
        self.dp.callback_query.register(self._handle_manifest_approval, F.data.startswith("man:app:"))
        self.dp.callback_query.register(self._handle_manifest_revision, F.data.startswith("man:rev:"))
        self.dp.callback_query.register(self._handle_manifest_cancel, F.data.startswith("man:can:"))
        self.dp.callback_query.register(self._handle_mode_selection, F.data.startswith("fleet_mode:"))

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
            "• `/mode` - Menu tombol pilihan model & reasoning mode (Fast / Balanced / Deep)\n"
            "• `/new` atau `/reset` - Mulai sesi percakapan baru (bersihkan memory context)\n"
            "• `/stop` atau `/abort` - Hentikan paksa seluruh proses aktif\n"
            "• `/update @online` - Git pull & restart daemon pada node aktif\n\n"
            "💡 *Format Tagging:*\n"
            "• `pc-naufal: cek disk`\n"
            "• `pc-naufal vps: bandingkan port`\n"
            "• `lap-asus: cek memori`\n"
            "• `@online: status semua mesin online`\n"
            "• `@all: status layanan`\n\n"
            "*(Daftar node: vps, pc-naufal, pc-diba, lap-asus, lap-user, pc-nci)*"
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

    # ---------------- Fleet Reasoning Mode & Settings ----------------

    @staticmethod
    def _load_settings() -> Dict[str, Any]:
        try:
            if SETTINGS_FILE.exists():
                with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                    return json.load(f)
        except Exception as e:
            logger.warning(f"Failed to load settings from {SETTINGS_FILE}: {e}")
        return {}

    @classmethod
    def _save_settings(cls, data: Dict[str, Any]) -> None:
        try:
            SETTINGS_DIR.mkdir(parents=True, exist_ok=True)
            current = cls._load_settings()
            current.update(data)
            with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
                json.dump(current, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save settings to {SETTINGS_FILE}: {e}")

    @classmethod
    def _get_active_reasoning_mode(cls) -> str:
        settings = cls._load_settings()
        mode = settings.get("reasoning_mode", "auto").lower()
        if mode not in REASONING_MODES:
            mode = "auto"
        return mode

    @classmethod
    def _set_reasoning_mode(cls, mode: str) -> bool:
        mode = mode.lower()
        if mode in REASONING_MODES:
            cls._save_settings({"reasoning_mode": mode})
            return True
        return False

    @classmethod
    def _classify_prompt_complexity(cls, prompt: str, is_chain: bool = False) -> str:
        """
        Classifies prompt into 'fast', 'balanced', or 'deep' based on operational intent:
        - Diskusi / Rangkuman / Status / Tanya Jawab -> 'fast' (Low effort, 180s)
        - Diskusi Multi-Node / Analisis Komparatif -> 'balanced' (Medium effort, 300s)
        - Pengerjaan / Eksekusi / Setup / Migrasi Sistem -> 'deep' (High effort, 600s)
        """
        p = prompt.strip().lower()
        words = p.split()
        word_count = len(words)
        char_count = len(p)

        # 1. Pengerjaan / Eksekusi / Mutasi Sistem (Deep Reasoning / High / 600s)
        work_keywords = [
            "eksekusi", "kerjakan", "jalankan", "setup", "pasang", "install",
            "deploy", "konfigurasi", "migrasi", "buatkan", "bikin", "implementasi",
            "terapkan", "tulis kode", "tulis skrip", "tulis script", "perbaiki", "fix",
            "update sistem", "restart service", "manifest", "/deploy", "/exec", "/manifest"
        ]
        if any(k in p for k in work_keywords):
            return "deep"

        # 2. Multi-node sequential chain discussion (Balanced / Medium / 300s)
        if is_chain:
            return "balanced"

        # 3. Diskusi / Rangkuman / Ringkas / Status Cek (Fast / Low / 180s)
        summary_and_light_keywords = [
            # Rangkuman & Ringkasan
            "rangkum", "ringkas", "kesimpulan", "rekap", "poin penting", "inti sari",
            # Status & Metrik Cepat
            "cek", "status", "spek", "spesifikasi", "ram", "disk", "memori", "cpu",
            "ip", "online", "uptime", "versi", "version", "port", "load", "ping",
            # Sapaan & Obrolan Ringan
            "halo", "hai", "pagi", "siang", "sore", "malam", "tes", "test",
            "siapa kamu", "bisa apa", "help", "bantuan", "info"
        ]
        if any(k in p for k in summary_and_light_keywords):
            if word_count <= 25 or any(k in p for k in ["rangkum", "ringkas", "kesimpulan", "cek", "status"]):
                return "fast"

        # 4. In-depth analytical discussions without execution (Balanced / Medium / 300s)
        deep_discussion_keywords = [
            "arsitektur", "rancang", "desain", "topologi", "skema",
            "analisis", "analisa", "evaluasi", "investigasi", "mengapa", "kenapa",
            "bagaimana cara", "solusi", "rekomendasi", "optimasi", "optimisasi",
            "bandingkan", "perbandingan", "troubleshoot", "debug"
        ]
        if any(k in p for k in deep_discussion_keywords) or word_count >= 25 or char_count >= 160:
            return "balanced"

        # Default fallback: fast for general chat
        return "fast"

    @classmethod
    def _resolve_effective_mode(cls, active_mode: str, prompt: str, is_chain: bool = False) -> Tuple[str, Dict[str, Any]]:
        """
        Resolves the final concrete mode ('fast', 'balanced', or 'deep').
        If active_mode is 'auto', dynamically determines the mode from prompt complexity.
        """
        resolved_name = active_mode
        if active_mode == "auto":
            resolved_name = cls._classify_prompt_complexity(prompt, is_chain=is_chain)

        cfg = REASONING_MODES.get(resolved_name, REASONING_MODES["deep"])
        return resolved_name, cfg

    @staticmethod
    def _build_mode_keyboard(current_mode: str) -> InlineKeyboardMarkup:
        kb = [
            [
                InlineKeyboardButton(
                    text=f"{'🔘' if current_mode == 'auto' else '⚪'} 🤖 Auto (Smart: Diskusi=Fast, Pengerjaan=High)",
                    callback_data="fleet_mode:auto"
                )
            ],
            [
                InlineKeyboardButton(
                    text=f"{'🔘' if current_mode == 'fast' else '⚪'} ⚡ Fast (Low)",
                    callback_data="fleet_mode:fast"
                ),
                InlineKeyboardButton(
                    text=f"{'🔘' if current_mode == 'balanced' else '⚪'} ⚖️ Balanced (Medium)",
                    callback_data="fleet_mode:balanced"
                ),
            ],
            [
                InlineKeyboardButton(
                    text=f"{'🔘' if current_mode == 'deep' else '⚪'} 🧠 Deep Reasoning (High)",
                    callback_data="fleet_mode:deep"
                )
            ]
        ]
        return InlineKeyboardMarkup(inline_keyboard=kb)

    @classmethod
    def _format_mode_text(cls, current_mode: str) -> str:
        cur_cfg = REASONING_MODES.get(current_mode, REASONING_MODES["auto"])
        lines = [
            "⚙️ *PENGATURAN MODE PENALARAN & MODEL*",
            "Sesuaikan tingkat kedalaman berpikir (_reasoning effort_) dan model AI untuk seluruh node fleet:\n",
            "• 🤖 *Auto (Smart)*: Diskusi / rangkuman / status -> Fast (Low), pengerjaan / eksekusi -> Deep (High)",
            "• 🧠 *Deep Reasoning (High)*: `gemini-3.8-flash-high` | effort: high | timeout: 600s (10m)",
            "• ⚖️ *Balanced (Medium)*: `gemini-3.8-flash-medium` | effort: medium | timeout: 300s (5m)",
            "• ⚡ *Fast (Low)*: `gemini-3.8-flash-low` | effort: low | timeout: 180s (3m)\n",
            f"Mode aktif saat ini: *{cur_cfg['label']}*",
            f"_{cur_cfg['desc']}_\n",
            "💡 _Tip: Anda juga dapat menimpa mode secara instan per pesan dengan menyertakan flag `--auto`, `--fast`, `--deep`, atau `--balanced`._"
        ]
        return "\n".join(lines)

    @classmethod
    def _extract_mode_override(cls, text: str) -> Tuple[Optional[str], str]:
        """
        Extracts inline flags like --auto, --fast, --deep, --balanced, --low, --high, --medium
        and returns (override_mode, cleaned_text).
        """
        patterns = [
            (r"(?:^|\s)--(auto|pintar|smart)(?:\s|$)", "auto"),
            (r"(?:^|\s)--(fast|cepat|low)(?:\s|$)", "fast"),
            (r"(?:^|\s)--(deep|dalam|high)(?:\s|$)", "deep"),
            (r"(?:^|\s)--(balanced|sedang|medium)(?:\s|$)", "balanced"),
        ]
        override = None
        cleaned = text
        for pat, mode_val in patterns:
            if re.search(pat, cleaned, flags=re.IGNORECASE):
                override = mode_val
                cleaned = re.sub(pat, " ", cleaned, flags=re.IGNORECASE).strip()
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        return override, cleaned

    async def _cmd_mode(self, message: Message):
        args = message.text.split()[1:] if message.text else []
        if args:
            sub = args[0].lower().replace("--", "")
            if sub in ["ringkas", "pantau"]:
                self.config.mode = sub
                await self.dlp.send_safe_message(
                    self.bot, message.chat.id,
                    f"⚙️ Mode diskusi diubah ke: *{self.config.mode}*",
                    topic_id=message.message_thread_id
                )
                return

            alias_map = {
                "auto": "auto", "smart": "auto", "pintar": "auto",
                "fast": "fast", "low": "fast", "cepat": "fast",
                "balanced": "balanced", "medium": "balanced", "sedang": "balanced",
                "deep": "deep", "high": "deep", "dalam": "deep"
            }
            if sub in alias_map:
                chosen = alias_map[sub]
                self._set_reasoning_mode(chosen)
                cfg = REASONING_MODES[chosen]
                desc_extra = f"\n\n_{cfg['desc']}_"
                model_info = f"\n• Model: `{cfg['model']}`\n• Effort: `{cfg['effort']}`\n• Timeout: `{cfg['timeout_sec']}s`" if chosen != "auto" else ""
                await self.dlp.send_safe_message(
                    self.bot, message.chat.id,
                    f"✅ Mode penalaran fleet diubah ke: *{cfg['label']}*{model_info}{desc_extra}",
                    topic_id=message.message_thread_id
                )
                return
                await self.dlp.send_safe_message(
                    self.bot, message.chat.id,
                    f"✅ Mode penalaran fleet diubah ke: *{cfg['label']}*\n\n"
                    f"• Model: `{cfg['model']}`\n"
                    f"• Effort: `{cfg['effort']}`\n"
                    f"• Timeout: `{cfg['timeout_sec']}s`",
                    topic_id=message.message_thread_id
                )
                return

        # Show interactive inline keyboard
        cur_mode = self._get_active_reasoning_mode()
        text = self._format_mode_text(cur_mode)
        kb = self._build_mode_keyboard(cur_mode)
        await self.bot.send_message(
            chat_id=message.chat.id,
            text=text,
            parse_mode="Markdown",
            reply_markup=kb,
            message_thread_id=message.message_thread_id
        )

    async def _handle_mode_selection(self, callback: CallbackQuery):
        data = callback.data or ""
        parts = data.split(":")
        if len(parts) == 2:
            chosen = parts[1].lower()
            if chosen in REASONING_MODES:
                self._set_reasoning_mode(chosen)
                cfg = REASONING_MODES[chosen]
                await callback.answer(f"Mode diubah ke: {cfg['label']}")
                new_text = self._format_mode_text(chosen)
                new_kb = self._build_mode_keyboard(chosen)
                try:
                    await callback.message.edit_text(
                        text=new_text,
                        parse_mode="Markdown",
                        reply_markup=new_kb
                    )
                except Exception as e:
                    logger.debug(f"Could not edit mode message: {e}")
                return
        await callback.answer("Pilihan tidak valid.", show_alert=True)

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

    async def _cmd_new_conversation(self, message: Message):
        """Reset conversation session for specified node or all nodes."""
        args = message.text.split()[1:] if message.text else []
        target = args[0].strip().lower().replace("@", "") if args else "@online"

        if target in ["all", "@all", "semua"]:
            self.conv_mgr.reset_all()
            await message.reply("🔄 *Semua sesi percakapan perangkat telah di-reset ke sesi baru.*", parse_mode="Markdown")
        elif target in ["online", "@online"]:
            online = self.presence.get_online_nodes()
            for nid in online:
                self.conv_mgr.reset(nid)
            nodes_str = ", ".join(online) if online else "tidak ada"
            await message.reply(f"🔄 *Sesi percakapan pada node online ({nodes_str}) telah di-reset ke sesi baru.*", parse_mode="Markdown")
        else:
            canonical = NODE_ALIASES.get(target, target)
            self.conv_mgr.reset(canonical)
            await message.reply(f"🔄 *Sesi percakapan pada `{canonical}` telah di-reset ke sesi baru.*", parse_mode="Markdown")

    @staticmethod
    def _strip_markdown(text: str) -> str:
        """Removes raw markdown formatting tokens for clean plain text display."""
        t = re.sub(r"^#{1,6}\s+", "", text, flags=re.MULTILINE)
        t = re.sub(r"\*\*([^*]+)\*\*", r"\1", t)
        t = re.sub(r"\*([^*]+)\*", r"\1", t)
        t = re.sub(r"_([^_]+)_", r"\1", t)
        t = re.sub(r"`([^`]+)`", r"\1", t)
        t = re.sub(r"```[a-zA-Z]*\n?([\s\S]*?)```", r"\1", t)
        t = t.replace("**", "").replace("```", "").replace("`", "")
        # Remove orphaned asterisks or underscores that could look like noise
        t = re.sub(r"(?<!\w)[*_]+(?!\w)", "", t)
        return t

    async def _safe_edit_message(self, message: Message, text: str, reply_markup=None) -> None:
        """Safely edit a message, falling back to clean plain text if Telegram fails parsing entities."""
        try:
            await message.edit_text(text, parse_mode="Markdown", reply_markup=reply_markup)
        except Exception as e:
            err_msg = str(e).lower()
            if "message is not modified" in err_msg:
                # Text didn't change, normal during progressive stream finish
                return
            logger.warning(f"Markdown edit failed ({e}). Retrying with sanitized plain text.")
            try:
                clean_text = self._strip_markdown(text)
                await message.edit_text(clean_text, parse_mode=None, reply_markup=reply_markup)
            except Exception as e2:
                err_msg2 = str(e2).lower()
                if "message is not modified" in err_msg2:
                    return
                logger.error(f"Failed to edit message with plain text: {e2}")

    # ---------------- Tagging & Chat Handling ----------------

    def _parse_target_nodes(self, text: str) -> Tuple[List[str], str]:
        """
        Extracts target node IDs from user text:
        "@pc-naufal: cek ram" -> (["pc-naufal"], "cek ram")
        "@naufal halo apa kabar?" -> (["pc-naufal"], "halo apa kabar?")
        "@pc-naufal, @pc-diba: halo" -> (["pc-naufal", "pc-diba"], "halo")
        "@pc-diba lalu @vps: halo" -> (["pc-diba", "vps"], "halo")
        "@online: cek ram" -> (online_nodes, "cek ram")
        "@all: status" -> (all_nodes, "status")
        "vps: cek disk" -> (["vps"], "cek disk")
        "halo apa kabar?" -> (["vps"], "halo apa kabar?")
        """
        clean_text = text.strip()
        if not clean_text:
            return ["vps"], ""

        # Check for @all or @semua
        m_all = re.match(r"^@(all|semua)\b[:\s]*(.*)$", clean_text, re.IGNORECASE)
        if m_all:
            return list(FLEET_NODES), m_all.group(2).strip()

        # Check for @online
        m_online = re.match(r"^@online\b[:\s]*(.*)$", clean_text, re.IGNORECASE)
        if m_online:
            online = self.presence.get_online_nodes()
            return (online if online else ["vps"]), m_online.group(1).strip()

        CONNECTORS = {"lalu", "kemudian", "dan", "terus", "ke", "->", "=>", "&", "setelah", "itu", "next"}

        # Check for leading node tags (e.g. @pc-naufal, @naufal, pc-naufal:, @pc-diba, @pc-diba lalu @vps)
        words = clean_text.split()
        found_nodes = []
        idx = 0
        while idx < len(words):
            raw_w = words[idx]
            w = raw_w.strip(",;:").lower()
            if w in CONNECTORS and found_nodes:
                # check if there is a fleet node ahead
                peek = idx + 1
                while peek < len(words) and words[peek].strip(",;:").lower() in CONNECTORS:
                    peek += 1
                if peek < len(words):
                    next_cand = words[peek].strip(",;:").lower()
                    next_cand = next_cand[1:] if next_cand.startswith("@") else next_cand
                    if NODE_ALIASES.get(next_cand, next_cand) in FLEET_NODES:
                        idx = peek
                        continue
                break

            candidate = w[1:] if w.startswith("@") else w
            canonical = NODE_ALIASES.get(candidate, candidate)
            if canonical in FLEET_NODES:
                if canonical not in found_nodes:
                    found_nodes.append(canonical)
                idx += 1
            else:
                break

        if found_nodes:
            rest = " ".join(words[idx:]).strip()
            if rest.startswith(":"):
                rest = rest[1:].strip()
            return found_nodes, rest

        # Check colon format (e.g. "pc-naufal: halo")
        m_colon = re.match(r"^([a-zA-Z0-9_\-]+)\s*:\s*(.*)$", clean_text)
        if m_colon:
            candidate = m_colon.group(1).lower()
            canonical = NODE_ALIASES.get(candidate, candidate)
            if canonical in FLEET_NODES:
                return [canonical], m_colon.group(2).strip()

        # Default fallback: target first available online node (or vps)
        online = self.presence.get_online_nodes()
        default_target = ["vps"] if "vps" in online else (online[:1] or ["vps"])
        return default_target, clean_text

    async def _handle_chat_message(self, message: Message):
        """Main chat handler: default = CONSULT (Read-Only Fact Gathering)."""
        user_prompt = message.text or ""
        if not user_prompt:
            return

        target_nodes, cleaned_prompt = self._parse_target_nodes(user_prompt)
        mode_override, cleaned_prompt = self._extract_mode_override(cleaned_prompt)

        is_chain = self._is_chain_request(cleaned_prompt, target_nodes, raw_text=user_prompt)
        raw_mode = mode_override or self._get_active_reasoning_mode()
        effective_mode_name, mode_cfg = self._resolve_effective_mode(raw_mode, cleaned_prompt, is_chain=is_chain)

        active_model = mode_cfg["model"]
        active_effort = mode_cfg["effort"]
        active_timeout = int(mode_cfg["timeout_sec"])
        mode_indicator = f"Auto: {mode_cfg['label']}" if raw_mode == "auto" else mode_cfg["label"]

        await self.presence.update_presence()

        # If user only typed tags like "@online", "@all", or node name with no prompt, show Dashboard
        if not cleaned_prompt:
            dashboard_text = self.presence.format_dashboard_text()
            await message.reply(dashboard_text, parse_mode="Markdown")
            return

        project_id = str(message.message_thread_id or message.chat.id)

        # Single progress message auto-edited during execution
        prog_msg = await message.reply(f"⏳ _Memproses permintaan via Antigravity Fleet ({mode_indicator})..._")

        try:
            # 1. Gather live node facts in parallel or chain
            online_nodes = self.presence.get_online_nodes()
            offline_nodes = [n for n in FLEET_NODES if n not in online_nodes]
            presence_context = f"ONLINE: {', '.join(online_nodes)} | OFFLINE: {', '.join(offline_nodes)}"

            edit_lock = asyncio.Lock()

            async def query_node(node_id: str, prompt_override: Optional[str] = None) -> Tuple[str, Dict[str, Any]]:
                consult_subj = SUBJECT_NODE_CONSULT.format(node_id=node_id)
                active_conv_id = self.conv_mgr.get_conversation_id(node_id)
                effective_prompt = prompt_override or (
                    f"[PRESENCE CONTEXT: {presence_context}]\n"
                    f"{FACTS_AND_DATA_INSTRUCTION}\n\n"
                    f"User query: {cleaned_prompt}"
                )

                req_data = {
                    "prompt": effective_prompt,
                    "command": None,
                    "conversation_id": active_conv_id,
                    "timeout_sec": active_timeout,
                    "model": active_model,
                    "effort": active_effort
                }

                # Resilient presence check: if marked offline in KV store, ping via NATS first before failing
                if not self.presence.is_node_online(node_id):
                    try:
                        ping_req = {"command": "echo pong"}
                        await self.nc.request(consult_subj, json.dumps(ping_req).encode("utf-8"), timeout=5.0)
                        logger.info(f"Node '{node_id}' responded to live ping despite KV expiry; proceeding.")
                        if node_id in self.presence.nodes_state:
                            self.presence.nodes_state[node_id].status = NodeStatus.ONLINE
                    except Exception:
                        return node_id, {"status": "OFFLINE", "error": f"Node {node_id} sedang mati/sleep."}
                try:
                    resp_msg = await self.nc.request(consult_subj, json.dumps(req_data).encode("utf-8"), timeout=float(active_timeout + 15.0))
                    resp_data = json.loads(resp_msg.data.decode("utf-8"))

                    # Record conversation turn and check if rotation needed
                    agy_info = resp_data.get("agy", {})
                    conv_id = agy_info.get("conversation_id")
                    if conv_id:
                        turns = agy_info.get("num_turns", 1)
                        usage = agy_info.get("usage", {})
                        tokens = usage.get("total_tokens", 0)
                        rotated = self.conv_mgr.record_turn(node_id, conv_id, turns, tokens)
                        resp_data["_rotated"] = rotated
                        resp_data["_conv_info"] = self.conv_mgr.get_info(node_id)

                    return node_id, resp_data
                except Exception as e:
                    return node_id, {"status": "ERROR", "error": str(e)}

            is_chain = self._is_chain_request(cleaned_prompt, target_nodes, raw_text=user_prompt)

            async def _keepalive_typing():
                while True:
                    try:
                        await asyncio.sleep(5.0)
                        await self.bot.send_chat_action(chat_id=message.chat.id, action="typing")
                    except asyncio.CancelledError:
                        break
                    except Exception:
                        pass

            typing_task = asyncio.create_task(_keepalive_typing())
            try:
                if is_chain:
                    # ----------------- REKOMENDASI 2: SEQUENTIAL CHAIN-OF-THOUGHT -----------------
                    completed_results: Dict[str, Dict[str, Any]] = {}
                    prev_response = ""
                    prev_node = ""

                    for idx, nid in enumerate(target_nodes):
                        step_num = idx + 1
                        targets_str = f"`{nid}`"

                        # Clean step progress indicator without prematurely dumping huge responses
                        lines = [f"🔗 *DISKUSI BERANTAI (CHAIN-OF-THOUGHT)*\n"]
                        for prev_nid in target_nodes:
                            if prev_nid in completed_results:
                                lines.append(f"✅ *Node `{prev_nid}`:* Analisis selesai.")
                        lines.append(f"\n⏳ *Node `{nid}`:* _Sedang menganalisis & merumuskan tanggapan (Langkah {step_num}/{len(target_nodes)})..._")

                        full_text = "\n\n".join(lines)
                        display_text, _, _ = self.dlp.sanitize_text(full_text)
                        await self._safe_edit_message(prog_msg, display_text)

                        # Prepare prompt with previous node's answer as context
                        node_prompt = (
                            f"[PRESENCE CONTEXT: {presence_context}]\n"
                            f"{FACTS_AND_DATA_INSTRUCTION}\n\n"
                            f"User query: {cleaned_prompt}"
                        )
                        if prev_response:
                            node_prompt = (
                                f"[PRESENCE CONTEXT: {presence_context}]\n"
                                f"{FACTS_AND_DATA_INSTRUCTION}\n\n"
                                f"[KONTEKS RESPONS SEBELUMNYA DARI `{prev_node}`]:\n{prev_response}\n\n"
                                f"[INSTRUKSI / PERTANYAAN LANJUTAN UNTUK KAMU `{nid}`]:\n{cleaned_prompt}"
                            )

                        _, res = await query_node(nid, prompt_override=node_prompt)
                        completed_results[nid] = res
                        prev_node = nid
                        agy_resp = res.get("agy", {}).get("response", "") or res.get("stdout", "")
                        prev_response = agy_resp[:3000]

                    facts_collected = completed_results

                    # Render final chain result
                    header = f"🔗 *HASIL DISKUSI BERANTAI (CHAIN-OF-THOUGHT)* ({len(target_nodes)} Langkah Selesai)"
                    display_text, exceeds, full_redacted = self._build_multi_node_preview(
                        header=header,
                        target_nodes=target_nodes,
                        results=completed_results,
                        mode_label=mode_indicator
                    )
                    await self._safe_edit_message(prog_msg, display_text)
                    if exceeds:
                        try:
                            doc = BufferedInputFile(full_redacted.encode("utf-8"), filename=f"diskusi_berantai_{int(time.time())}.md")
                            await self.bot.send_document(
                                chat_id=message.chat.id,
                                document=doc,
                                caption="📄 *Dokumen Lengkap Diskusi Berantai (.md)*",
                                parse_mode="Markdown",
                                message_thread_id=message.message_thread_id,
                                reply_to_message_id=prog_msg.message_id
                            )
                        except Exception as doc_err:
                            logger.error(f"Failed to send chain markdown document: {doc_err}")
                            try:
                                doc = BufferedInputFile(full_redacted.encode("utf-8"), filename=f"diskusi_berantai_{int(time.time())}.md")
                                await self.bot.send_document(
                                    chat_id=message.chat.id,
                                    document=doc,
                                    caption="📄 Dokumen Lengkap Diskusi Berantai (.md)",
                                    message_thread_id=message.message_thread_id
                                )
                            except Exception as doc_err2:
                                logger.error(f"Retry sending chain doc failed: {doc_err2}")

                else:
                    # ----------------- REKOMENDASI 1: PROGRESSIVE REALTIME UPDATE -----------------
                    completed_results: Dict[str, Dict[str, Any]] = {}
                    pending_nodes = list(target_nodes)

                    async def _render_progressive_status(is_final: bool = False):
                        async with edit_lock:
                            header = "🔍 *HASIL KONSULTASI (CONSULT READ-ONLY)*"
                            if is_final:
                                display_text, exceeds, full_redacted = self._build_multi_node_preview(
                                    header=header,
                                    target_nodes=target_nodes,
                                    results=completed_results,
                                    mode_label=mode_indicator
                                )
                            else:
                                lines = [header + "\n"]
                                for nid in target_nodes:
                                    if nid in completed_results:
                                        lines.append(f"✅ *Node `{nid}`:* Analisis selesai.")
                                if pending_nodes:
                                    waiting_str = ", ".join([f"`{n}`" for n in pending_nodes])
                                    lines.append(f"\n⏳ _Menunggu respons dari {waiting_str}..._")
                                full_text = "\n\n".join(lines)
                                display_text, exceeds, full_redacted = self.dlp.sanitize_text(full_text)

                            await self._safe_edit_message(prog_msg, display_text)
                            if is_final and exceeds:
                                try:
                                    doc = BufferedInputFile(full_redacted.encode("utf-8"), filename=f"hasil_konsultasi_{int(time.time())}.md")
                                    await self.bot.send_document(
                                        chat_id=message.chat.id,
                                        document=doc,
                                        caption="📄 *Dokumen Lengkap Hasil Konsultasi (.md)*",
                                        parse_mode="Markdown",
                                        message_thread_id=message.message_thread_id,
                                        reply_to_message_id=prog_msg.message_id
                                    )
                                except Exception as doc_err:
                                    logger.error(f"Failed to send markdown document: {doc_err}")
                                    try:
                                        doc = BufferedInputFile(full_redacted.encode("utf-8"), filename=f"hasil_konsultasi_{int(time.time())}.md")
                                        await self.bot.send_document(
                                            chat_id=message.chat.id,
                                            document=doc,
                                            caption="📄 Dokumen Lengkap Hasil Konsultasi (.md)",
                                            message_thread_id=message.message_thread_id
                                        )
                                    except Exception as doc_err2:
                                        logger.error(f"Retry sending consult doc failed: {doc_err2}")

                    async def run_single_node(nid: str):
                        res = await query_node(nid)
                        completed_results[nid] = res[1]
                        if nid in pending_nodes:
                            pending_nodes.remove(nid)
                        await _render_progressive_status(is_final=False)
                        return res

                    node_results = await asyncio.gather(*[run_single_node(nid) for nid in target_nodes])
                    facts_collected = dict(node_results)

                    # Final render without pending footer
                    await _render_progressive_status(is_final=True)

            finally:
                typing_task.cancel()

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

                await self._safe_edit_message(
                    prog_msg,
                    f"{proposal_text}\n\n⚠️ *Perintah ini memerlukan persetujuan eksplisit Anda sebelum dijalankan.*",
                    reply_markup=kb
                )

        except Exception as e:
            logger.error(f"Error processing chat message: {e}")
            await self._safe_edit_message(prog_msg, f"❌ Terjadi kesalahan saat memproses permintaan: `{str(e)}`")

    def _format_node_result(self, nid: str, res: Dict[str, Any], mode_label: Optional[str] = None) -> str:
        """Format individual node result for Telegram rendering."""
        agy_info = res.get("agy", {})
        if "stdout" in res and res["stdout"]:
            return f"🖥️ *Node `{nid}`:*\n```\n{res['stdout'][:800]}\n```"
        elif res.get("stderr"):
            return f"⚠️ *Node `{nid}`:*\n```\n{res['stderr'][:800]}\n```"
        elif agy_info.get("response"):
            ans = agy_info['response'].strip()
            conv_info = res.get("_conv_info")
            footer = ""
            if conv_info:
                turn_c = conv_info.get("turn_count", 1)
                tok = conv_info.get("total_tokens", 0)
                mode_str = f" | {mode_label}" if mode_label else ""
                footer = f"\n\n_💬 Sesi `{nid}`: Turn {turn_c}/{self.conv_mgr.max_turns} | ~{tok//1000}k tokens{mode_str}_"
            rot_text = f"\n🔄 _Konteks percakapan di `{nid}` telah penuh dan di-refresh otomatis._" if res.get("_rotated") else ""
            return f"🤖 *Node `{nid}`:*\n{ans}{footer}{rot_text}"
        elif agy_info.get("error"):
            return f"⚠️ *Node `{nid}` error:* `{agy_info['error']}`"
        elif "error" in res:
            return f"⚠️ *Node `{nid}`:* {res['error']}"
        else:
            return f"ℹ️ *Node `{nid}`:* Berhasil diproses (tanpa output teks)."

    def _build_multi_node_preview(
        self,
        header: str,
        target_nodes: List[str],
        results: Dict[str, Dict[str, Any]],
        max_len: int = 3000,
        mode_label: Optional[str] = None
    ) -> Tuple[str, bool, str]:
        """
        Builds the message content based on length:
        - If total content <= 3000 chars: returns (full_redacted, False, full_redacted) -> text sent in chat, NO file sent.
        - If total content > 3000 chars: returns (notice_text, True, full_redacted) -> chat only shows clean redirect notice,
          full unclipped content sent as .md attachment without cluttering chat with broken text.
        """
        # Full text without any clipping
        full_lines = [header + "\n"]
        for nid in target_nodes:
            if nid in results:
                full_lines.append(self._format_node_result(nid, results[nid], mode_label=mode_label))
        full_unclipped = "\n\n".join(full_lines)
        full_redacted = self.dlp.redactor.redact(full_unclipped)

        # Condition 1: Under 3000 characters -> Send text directly, NO file attachment
        if len(full_redacted) <= max_len:
            return full_redacted, False, full_redacted

        # Condition 2: Over 3000 characters -> Direct user to file, NO messy text in chat
        nodes_str = ", ".join([f"`{n}`" for n in target_nodes if n in results])
        char_count = f"{len(full_redacted):,}"
        mode_str = f" | Mode: {mode_label}" if mode_label else ""
        notice_lines = [
            f"{header}\n",
            f"✅ *Analisis Selesai* ({nodes_str}){mode_str}\n",
            f"📄 *Hasil pembahasan lengkap ({char_count} karakter)* telah dikemas ke dalam dokumen `.md` terlampir di bawah karena melebihi batas tampilan chat.\n",
            "👉 _Silakan unduh dan buka file `.md` terlampir untuk membaca detail penjelasan, fakta, data sistem, dan konfigurasi lengkapnya._"
        ]
        notice_text = self.dlp.redactor.redact("\n".join(notice_lines))
        return notice_text, True, full_redacted

    def _is_chain_request(self, prompt: str, target_nodes: List[str], raw_text: str = "") -> bool:
        """Determines if query requests a sequential chain between multiple nodes."""
        if len(target_nodes) < 2:
            return False
        combined = f"{raw_text} {prompt}".lower()
        if "/chain" in combined or "->" in combined or "=>" in combined:
            return True
        chain_keywords = ["lalu", "kemudian", "setelah itu", "lanjut ke", "berikutnya", "tanggapi"]
        return any(k in combined for k in chain_keywords)

    def _guess_diagnostic_cmd(self, prompt: str, node_id: str = "vps") -> Optional[str]:
        """Disabled: all conversational queries pass directly to Antigravity AI for intelligent responses."""
        return None

    def _detect_modification_intent(self, prompt: str, facts: Dict[str, Any]) -> Tuple[bool, Optional[Dict[str, Any]]]:
        """
        Determines if request requires system mutation.
        Only triggers for explicit command prefixes like /deploy or /exec, never hijacking general discussions.
        """
        p = prompt.strip().lower()
        if not (p.startswith("/deploy") or p.startswith("/exec") or p.startswith("/manifest")):
            return False, None

        keywords = ["install", "update", "pasang", "restart", "jalankan", "hapus", "buat", "start", "stop service"]

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
        await self._safe_edit_message(call.message, display_text)

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
