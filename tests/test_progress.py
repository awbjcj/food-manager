from dataclasses import dataclass, field
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.application.context import CommandContext
from app.progress import clear_progress, finish_progress, start_progress


@dataclass
class FakeMessenger:
    send_text: AsyncMock = field(default_factory=lambda: AsyncMock(return_value="ACK"))
    update_text: AsyncMock = field(default_factory=AsyncMock)
    remove_message: AsyncMock = field(default_factory=AsyncMock)
    typing: AsyncMock = field(default_factory=AsyncMock)
    send_document: AsyncMock = field(default_factory=AsyncMock)


def _transport(context: CommandContext) -> FakeMessenger:
    assert isinstance(context.transport, FakeMessenger)
    return context.transport


def _msg():
    return CommandContext(
        actor_id=1, conversation_key=5, message_key=1, transport=FakeMessenger()
    )


@pytest.mark.asyncio
async def test_start_progress_returns_ack_message():
    msg = _msg()
    ack = await start_progress(msg, "working…")
    assert ack == "ACK"
    _transport(msg).send_text.assert_awaited_once_with(
        conversation_key=5, text="working…", actions=None
    )


@pytest.mark.asyncio
async def test_start_progress_swallows_send_failure():
    msg = _msg()
    _transport(msg).send_text.side_effect = RuntimeError("boom")
    assert await start_progress(msg, "working…") is None


@pytest.mark.asyncio
async def test_start_progress_sends_typing_action():
    msg = _msg()
    _transport(msg).typing = AsyncMock()
    await start_progress(msg, "working…")
    _transport(msg).typing.assert_awaited_once_with(conversation_key=5)


@pytest.mark.asyncio
async def test_start_progress_swallows_chat_action_failure():
    msg = _msg()
    _transport(msg).typing.side_effect = RuntimeError("nope")
    assert await start_progress(msg, "working…") == "ACK"


@pytest.mark.asyncio
async def test_finish_progress_edits_in_place():
    msg = _msg()
    progress = CommandContext(
        actor_id=1, conversation_key=5, message_key=2, transport=FakeMessenger()
    )
    final = await finish_progress(progress, msg, "done", keyboard="kb")
    assert final is progress
    _transport(progress).update_text.assert_awaited_once_with(
        conversation_key=5, message_key=2, text="done", actions="kb"
    )
    _transport(msg).send_text.assert_not_awaited()


@pytest.mark.asyncio
async def test_finish_progress_not_modified_is_success():
    msg = _msg()
    progress = CommandContext(
        actor_id=1,
        conversation_key=5,
        message_key=2,
        transport=FakeMessenger(
            update_text=AsyncMock(
                side_effect=Exception("Bad Request: message is not modified")
            )
        ),
    )
    final = await finish_progress(progress, msg, "done")
    assert final is progress
    _transport(msg).send_text.assert_not_awaited()


@pytest.mark.asyncio
async def test_finish_progress_falls_back_to_fresh_message():
    msg = _msg()
    progress = CommandContext(
        actor_id=1,
        conversation_key=5,
        message_key=2,
        transport=FakeMessenger(update_text=AsyncMock(side_effect=RuntimeError("old"))),
    )
    final = await finish_progress(progress, msg, "done")
    assert final == "ACK"
    _transport(msg).send_text.assert_awaited_once_with(
        conversation_key=5, text="done", actions=None
    )


@pytest.mark.asyncio
async def test_finish_progress_without_ack_sends_fresh_message():
    msg = _msg()
    final = await finish_progress(None, msg, "done")
    assert final == "ACK"


@pytest.mark.asyncio
async def test_clear_progress_is_best_effort():
    progress = SimpleNamespace(delete=AsyncMock(side_effect=RuntimeError("gone")))
    await clear_progress(progress)  # must not raise
    await clear_progress(None)  # must not raise
