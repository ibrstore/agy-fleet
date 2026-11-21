"""
Unit tests for Strict Owner Authentication Middleware.
Verifies owner authorization, group restriction, forwarded message rejection, and bot blocking.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock
from aiogram.types import Message, CallbackQuery, Chat, User
from orchestrator.auth_middleware import StrictOwnerAuthMiddleware

ALLOWED_GROUP_ID = -1001234567890
OWNER_USER_ID = 987654321


@pytest.fixture
def auth_middleware():
    return StrictOwnerAuthMiddleware(
        allowed_group_id=ALLOWED_GROUP_ID,
        owner_user_id=OWNER_USER_ID
    )


@pytest.mark.asyncio
async def test_owner_in_allowed_group_accepted(auth_middleware):
    handler = AsyncMock(return_value="OK")
    message = MagicMock(spec=Message)
    message.chat = MagicMock(spec=Chat, id=ALLOWED_GROUP_ID)
    message.from_user = MagicMock(spec=User, id=OWNER_USER_ID, is_bot=False)
    message.forward_date = None
    message.forward_origin = None

    result = await auth_middleware(handler, message, {})
    assert result == "OK"
    handler.assert_called_once()


@pytest.mark.asyncio
async def test_owner_in_direct_private_chat_accepted(auth_middleware):
    handler = AsyncMock(return_value="OK_PRIVATE")
    message = MagicMock(spec=Message)
    message.chat = MagicMock(spec=Chat, id=OWNER_USER_ID, type="private")
    message.from_user = MagicMock(spec=User, id=OWNER_USER_ID, is_bot=False)
    message.forward_date = None
    message.forward_origin = None

    result = await auth_middleware(handler, message, {})
    assert result == "OK_PRIVATE"
    handler.assert_called_once()


@pytest.mark.asyncio
async def test_non_owner_rejected(auth_middleware):
    handler = AsyncMock(return_value="OK")
    message = MagicMock(spec=Message)
    message.chat = MagicMock(spec=Chat, id=ALLOWED_GROUP_ID)
    message.from_user = MagicMock(spec=User, id=111222333, is_bot=False)  # wrong user
    message.forward_date = None
    message.forward_origin = None

    result = await auth_middleware(handler, message, {})
    assert result is None
    handler.assert_not_called()


@pytest.mark.asyncio
async def test_wrong_group_rejected(auth_middleware):
    handler = AsyncMock(return_value="OK")
    message = MagicMock(spec=Message)
    message.chat = MagicMock(spec=Chat, id=-1009999999999)  # wrong group
    message.from_user = MagicMock(spec=User, id=OWNER_USER_ID, is_bot=False)
    message.forward_date = None
    message.forward_origin = None

    result = await auth_middleware(handler, message, {})
    assert result is None
    handler.assert_not_called()


@pytest.mark.asyncio
async def test_forwarded_message_rejected(auth_middleware):
    handler = AsyncMock(return_value="OK")
    message = MagicMock(spec=Message)
    message.chat = MagicMock(spec=Chat, id=ALLOWED_GROUP_ID)
    message.from_user = MagicMock(spec=User, id=OWNER_USER_ID, is_bot=False)
    message.forward_date = 1600000000  # Forwarded message!
    message.forward_origin = MagicMock()

    result = await auth_middleware(handler, message, {})
    assert result is None
    handler.assert_not_called()


@pytest.mark.asyncio
async def test_bot_message_rejected(auth_middleware):
    handler = AsyncMock(return_value="OK")
    message = MagicMock(spec=Message)
    message.chat = MagicMock(spec=Chat, id=ALLOWED_GROUP_ID)
    message.from_user = MagicMock(spec=User, id=OWNER_USER_ID, is_bot=True)  # Bot sender!
    message.forward_date = None
    message.forward_origin = None

    result = await auth_middleware(handler, message, {})
    assert result is None
    handler.assert_not_called()


@pytest.mark.asyncio
async def test_callback_query_owner_accepted(auth_middleware):
    handler = AsyncMock(return_value="CALLBACK_OK")
    cb = MagicMock(spec=CallbackQuery)
    cb.message = MagicMock(spec=Message)
    cb.message.chat = MagicMock(spec=Chat, id=ALLOWED_GROUP_ID)
    cb.from_user = MagicMock(spec=User, id=OWNER_USER_ID)

    result = await auth_middleware(handler, cb, {})
    assert result == "CALLBACK_OK"
    handler.assert_called_once()


@pytest.mark.asyncio
async def test_callback_query_non_owner_rejected(auth_middleware):
    handler = AsyncMock(return_value="CALLBACK_OK")
    cb = MagicMock(spec=CallbackQuery)
    cb.message = MagicMock(spec=Message)
    cb.message.chat = MagicMock(spec=Chat, id=ALLOWED_GROUP_ID)
    cb.from_user = MagicMock(spec=User, id=444555666)
    cb.answer = AsyncMock()

    result = await auth_middleware(handler, cb, {})
    assert result is None
    handler.assert_not_called()
    cb.answer.assert_called_once()
