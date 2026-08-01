from datetime import UTC, datetime

import pytest
from aiogram import Bot
from aiogram.types import Chat, Message, Update
from aiogram.types import User as TgUser

from app.operator import auth
from app.operator.bot import build_operator_dispatcher

NOW = datetime(2026, 7, 28, 12, tzinfo=UTC)


@pytest.fixture
def operator_bot(monkeypatch):
    monkeypatch.setattr(auth, "OPERATOR_IDS", frozenset({7}))
    return Bot(token="123456:TEST-TOKEN-AAAAAAAAAAAAAAAAAAAAAAAAAAAA")


def _update(text: str) -> Update:
    chat = Chat(id=7, type="private")
    sender = TgUser(id=7, is_bot=False, first_name="Op")
    msg = Message(message_id=1, date=NOW, chat=chat, from_user=sender, text=text)
    return Update(update_id=1, message=msg)


@pytest.mark.asyncio
async def test_unmatched_message_gets_a_help_reply_instead_of_silence(
    operator_bot, monkeypatch
):
    dispatcher = build_operator_dispatcher(
        session_factory=lambda: None,
        now_provider=lambda _tz: NOW,
        payments=None,
    )
    sent: list[str] = []

    async def fake_answer(self, text, **kwargs):
        sent.append(text)

    monkeypatch.setattr(Message, "answer", fake_answer)

    result = await dispatcher.feed_update(operator_bot, _update("/start"))

    assert result is None, "aiogram reports the update as unhandled"
    assert sent, "operator got no reply for an unrecognized message"
