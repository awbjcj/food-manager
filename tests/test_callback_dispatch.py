"""The callback dispatch seam owns answer-first + edit-or-resend.

These tests pin the two rules that fix the "button does nothing / menu won't
open" bug: an identical-content edit counts as success, and a genuinely failed
edit falls back to a fresh message instead of being swallowed.
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.callback_dispatch import CallbackResult, View, answer, apply, edit_or_resend


def _cb():
    message = SimpleNamespace(edit_text=AsyncMock(), answer=AsyncMock())
    return SimpleNamespace(message=message, answer=AsyncMock())


@pytest.mark.asyncio
async def test_edit_in_place_when_editable():
    cb = _cb()
    await edit_or_resend(cb, "new text", keyboard="kb")
    cb.message.edit_text.assert_awaited_once_with("new text", reply_markup="kb")
    cb.message.answer.assert_not_awaited()


@pytest.mark.asyncio
async def test_not_modified_is_success_no_resend():
    cb = _cb()
    cb.message.edit_text.side_effect = Exception("Bad Request: message is not modified")
    await edit_or_resend(cb, "same text")
    # Treated as a no-op success: we do NOT spam a duplicate message.
    cb.message.answer.assert_not_awaited()


@pytest.mark.asyncio
async def test_uneditable_message_resends_fresh():
    cb = _cb()
    cb.message.edit_text.side_effect = Exception("Bad Request: message can't be edited")
    await edit_or_resend(cb, "card text", keyboard="kb")
    # Flow survives: the view is delivered as a new message.
    cb.message.answer.assert_awaited_once_with("card text", reply_markup="kb")


@pytest.mark.asyncio
async def test_resend_failure_is_swallowed():
    cb = _cb()
    cb.message.edit_text.side_effect = Exception("too old")
    cb.message.answer.side_effect = Exception("blocked chat")
    # Must not raise even when both edit and resend fail.
    await edit_or_resend(cb, "x")


@pytest.mark.asyncio
async def test_no_message_is_noop():
    cb = SimpleNamespace(message=None, answer=AsyncMock())
    await edit_or_resend(cb, "x")  # must not raise


@pytest.mark.asyncio
async def test_answer_swallows_failure():
    cb = SimpleNamespace(answer=AsyncMock(side_effect=Exception("expired")))
    await answer(cb, "toast")  # must not raise
    cb.answer.assert_awaited_once()


@pytest.mark.asyncio
async def test_apply_answers_before_running_deferred_work_and_rendering():
    cb = _cb()
    events = []
    cb.answer.side_effect = lambda *args, **kwargs: events.append("answer")
    cb.message.edit_text.side_effect = lambda *args, **kwargs: events.append("edit")

    async def deferred():
        events.append("deferred")
        return View("done", keyboard="kb")

    await apply(cb, CallbackResult(ack="working", deferred=deferred))

    assert events == ["answer", "deferred", "edit"]


@pytest.mark.asyncio
async def test_apply_supports_ack_only_and_direct_deferred_effects():
    cb = _cb()
    called = False

    async def deferred():
        nonlocal called
        called = True
        return None

    await apply(cb, CallbackResult(ack="saved", deferred=deferred))

    assert called is True
    cb.answer.assert_awaited_once_with("saved", show_alert=False)
    cb.message.edit_text.assert_not_awaited()
