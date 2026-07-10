from unittest.mock import AsyncMock

import pytest

from app.alerts import OwnerAlerter


@pytest.mark.asyncio
async def test_alert_sends_dm():
    bot = AsyncMock()
    alerter = OwnerAlerter(bot, 42, clock=lambda: 0.0)
    assert await alerter.alert("digest_failed", "boom") is True
    bot.send_message.assert_awaited_once_with(
        chat_id=42, text="⚠️ digest_failed: boom"
    )


@pytest.mark.asyncio
async def test_alert_rate_limits_per_event():
    bot = AsyncMock()
    ticks = iter([0.0, 10.0, 400.0])
    alerter = OwnerAlerter(
        bot, 42, min_interval_seconds=300.0, clock=lambda: next(ticks)
    )
    assert await alerter.alert("digest_failed", "a") is True
    assert await alerter.alert("digest_failed", "b") is False  # 10s later
    assert await alerter.alert("digest_failed", "c") is True  # 400s later
    assert bot.send_message.await_count == 2


@pytest.mark.asyncio
async def test_alert_different_events_not_suppressed():
    bot = AsyncMock()
    ticks = iter([0.0, 1.0])
    alerter = OwnerAlerter(bot, 42, clock=lambda: next(ticks))
    assert await alerter.alert("digest_failed", "a") is True
    assert await alerter.alert("handler_error", "b") is True


@pytest.mark.asyncio
async def test_alert_send_failure_swallowed():
    bot = AsyncMock()
    bot.send_message.side_effect = RuntimeError("blocked the bot")
    alerter = OwnerAlerter(bot, 42, clock=lambda: 0.0)
    assert await alerter.alert("digest_failed", "boom") is False


@pytest.mark.asyncio
async def test_digest_final_failure_invokes_hook(monkeypatch):
    from app import scheduler as scheduler_mod

    async def failing_send_once(**kwargs):
        raise RuntimeError("network down")

    monkeypatch.setattr(scheduler_mod, "send_digest_once", failing_send_once)

    calls: list[tuple[int, str]] = []

    async def hook(user_id: int, exc: Exception) -> None:
        calls.append((user_id, type(exc).__name__))

    await scheduler_mod.send_digest_with_retry(
        user_id=7,
        bot=AsyncMock(),
        session_factory=lambda: None,  # type: ignore[arg-type,return-value]
        today_provider=lambda tz: None,  # type: ignore[arg-type,return-value]
        retry_sleep_seconds=0,
        on_final_failure=hook,
    )
    assert calls == [(7, "RuntimeError")]
