"""
Telegram Outbound DLP Filter and Log Handler.
Sanitizes outbound messages, truncates long texts, and sends full logs as .txt attachments.
"""
from typing import Tuple, Optional
from aiogram import Bot
from aiogram.types import BufferedInputFile
from shared.config import TELEGRAM_MAX_TEXT_LEN
from shared.redactor import Redactor


class TelegramDLPFilter:
    def __init__(self, redactor: Optional[Redactor] = None):
        self.redactor = redactor or Redactor()

    def sanitize_text(self, text: str) -> Tuple[str, bool, str]:
        """
        Redacts secrets and checks length limit (3500 chars).
        Returns (display_text, exceeds_limit, full_redacted_text).
        """
        return self.redactor.sanitize_for_telegram(text, max_len=TELEGRAM_MAX_TEXT_LEN)

    async def send_safe_message(
        self,
        bot: Bot,
        chat_id: int,
        text: str,
        topic_id: Optional[int] = None,
        reply_to_message_id: Optional[int] = None,
        parse_mode: Optional[str] = "Markdown",
        filename_prefix: str = "fleet_doc"
    ):
        """
        Sends message to Telegram. If text exceeds 3,500 chars, sends the preview
        and attaches full redacted log as a .md document file.
        """
        display_text, exceeds, full_redacted = self.sanitize_text(text)

        kwargs = {
            "chat_id": chat_id,
            "text": display_text,
            "message_thread_id": topic_id,
            "reply_to_message_id": reply_to_message_id,
            "parse_mode": parse_mode,
        }
        # Filter out None values
        send_kwargs = {k: v for k, v in kwargs.items() if v is not None}

        sent_msg = await bot.send_message(**send_kwargs)

        if exceeds:
            # Attach full text as a .md document
            file_bytes = full_redacted.encode("utf-8")
            doc = BufferedInputFile(file_bytes, filename=f"{filename_prefix}.md")
            doc_kwargs = {
                "chat_id": chat_id,
                "document": doc,
                "caption": "📄 Dokumen lengkap (DLP-sanitized)",
                "message_thread_id": topic_id,
                "reply_to_message_id": sent_msg.message_id
            }
            await bot.send_document(**{k: v for k, v in doc_kwargs.items() if v is not None})

        return sent_msg
