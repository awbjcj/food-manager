from unittest.mock import AsyncMock

import pytest

from app.resilience import run_with_restart


@pytest.mark.asyncio
async def test_clean_return_stops_loop():
    start = AsyncMock(return_value=None)
    await run_with_restart(start, sleep=AsyncMock())
    assert start.await_count == 1


@pytest.mark.asyncio
async def test_crash_restarts_with_growing_backoff():
    start = AsyncMock(side_effect=[RuntimeError("a"), RuntimeError("b"), None])
    sleep = AsyncMock()
    await run_with_restart(start, sleep=sleep, clock=lambda: 0.0)
    assert start.await_count == 3
    assert [c.args[0] for c in sleep.await_args_list] == [1.0, 2.0]


@pytest.mark.asyncio
async def test_stable_run_resets_backoff():
    # clock() is read twice per crashed run: at start and in the except block.
    # Run 1 crashes instantly (backoff 1->2), run 2 crashes after 700s of
    # stability (backoff resets to 1), run 3 returns cleanly.
    ticks = iter([0.0, 0.0, 0.0, 700.0, 700.0])
    start = AsyncMock(side_effect=[RuntimeError("a"), RuntimeError("b"), None])
    sleep = AsyncMock()
    await run_with_restart(start, sleep=sleep, clock=lambda: next(ticks))
    assert [c.args[0] for c in sleep.await_args_list] == [1.0, 1.0]


@pytest.mark.asyncio
async def test_on_crash_hook_called_and_best_effort():
    start = AsyncMock(side_effect=[RuntimeError("x"), None])
    hook = AsyncMock(side_effect=RuntimeError("hook broken"))
    await run_with_restart(start, on_crash=hook, sleep=AsyncMock(), clock=lambda: 0.0)
    hook.assert_awaited_once()
    assert start.await_count == 2


@pytest.mark.asyncio
async def test_max_restarts_reraises():
    start = AsyncMock(side_effect=RuntimeError("always"))
    with pytest.raises(RuntimeError):
        await run_with_restart(
            start, sleep=AsyncMock(), clock=lambda: 0.0, max_restarts=2
        )
    assert start.await_count == 3
