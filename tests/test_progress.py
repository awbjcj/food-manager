from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.progress import clear_progress, finish_progress, start_progress


def _msg():
    return SimpleNamespace(
        answer=AsyncMock(return_value="ACK"),
        chat=SimpleNamespace(id=5),
        bot=None,
    )


@pytest.mark.asyncio
async def test_start_progress_returns_ack_message():
    msg = _msg()
    ack = await start_progress(msg, "working…")
    assert ack == "ACK"
    msg.answer.assert_awaited_once_with("working…")


@pytest.mark.asyncio
async def test_start_progress_swallows_send_failure():
    msg = _msg()
    msg.answer.side_effect = RuntimeError("boom")
    assert await start_progress(msg, "working…") is None


@pytest.mark.asyncio
async def test_start_progress_sends_typing_action():
    msg = _msg()
    msg.bot = SimpleNamespace(send_chat_action=AsyncMock())
    await start_progress(msg, "working…")
    msg.bot.send_chat_action.assert_awaited_once_with(chat_id=5, action="typing")


@pytest.mark.asyncio
async def test_start_progress_swallows_chat_action_failure():
    msg = _msg()
    msg.bot = SimpleNamespace(
        send_chat_action=AsyncMock(side_effect=RuntimeError("nope"))
    )
    assert await start_progress(msg, "working…") == "ACK"


@pytest.mark.asyncio
async def test_finish_progress_edits_in_place():
    msg = _msg()
    progress = SimpleNamespace(edit_text=AsyncMock())
    final = await finish_progress(progress, msg, "done", keyboard="kb")
    assert final is progress
    progress.edit_text.assert_awaited_once_with("done", reply_markup="kb")
    msg.answer.assert_not_awaited()


@pytest.mark.asyncio
async def test_finish_progress_not_modified_is_success():
    msg = _msg()
    progress = SimpleNamespace(
        edit_text=AsyncMock(
            side_effect=Exception("Bad Request: message is not modified")
        )
    )
    final = await finish_progress(progress, msg, "done")
    assert final is progress
    msg.answer.assert_not_awaited()


@pytest.mark.asyncio
async def test_finish_progress_falls_back_to_fresh_message():
    msg = _msg()
    progress = SimpleNamespace(edit_text=AsyncMock(side_effect=RuntimeError("old")))
    final = await finish_progress(progress, msg, "done")
    assert final == "ACK"
    msg.answer.assert_awaited_once_with("done", reply_markup=None)


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
