"""
Strict Owner Authentication Middleware for aiogram v3.
Ensures ONLY the authorized human owner in the designated group chat
can interact with the bot. Drops forwarded messages, bots, and unauthorized users.
"""
import logging
from typing import Callable, Dict, Any, Awaitable
from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, Message, CallbackQuery

logger = logging.getLogger("fleet-auth")


class StrictOwnerAuthMiddleware(BaseMiddleware):
    def __init__(self, allowed_group_id: int, owner_user_id: int):
        super().__init__()
        self.allowed_group_id = allowed_group_id
        self.owner_user_id = owner_user_id

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any]
    ) -> Any:
        # 1. Handle incoming Message
        if isinstance(event, Message):
            # Check forwarded message (anti-spoofing)
            if getattr(event, "forward_date", None) is not None or getattr(event, "forward_origin", None) is not None:
                logger.warning(f"Auth rejected: Forwarded message detected from user {event.from_user.id if event.from_user else 'unknown'}")
                return None

            # Check bot sender
            if event.from_user and event.from_user.is_bot:
                logger.warning("Auth rejected: Message from a bot account.")
                return None

            # Validate chat and user IDs
            if event.chat.id != self.allowed_group_id:
                logger.warning(f"Auth rejected: Message from unallowed chat_id {event.chat.id} (expected {self.allowed_group_id})")
                return None

            if not event.from_user or event.from_user.id != self.owner_user_id:
                logger.warning(f"Auth rejected: Message from unallowed user_id {event.from_user.id if event.from_user else 'None'} (expected {self.owner_user_id})")
                return None

        # 2. Handle CallbackQuery (inline button clicks)
        elif isinstance(event, CallbackQuery):
            chat_id = event.message.chat.id if event.message else None
            user_id = event.from_user.id if event.from_user else None

            if chat_id != self.allowed_group_id or user_id != self.owner_user_id:
                logger.warning(f"Auth rejected: CallbackQuery from chat {chat_id}, user {user_id}")
                await event.answer("⛔ Akses ditolak: Hanya pemilik resmi yang diizinkan.", show_alert=True)
                return None

        return await handler(event, data)
