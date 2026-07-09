import logging
from unittest.mock import AsyncMock

import pytest

from app.llm_transport import with_transport_retry


@pytest.mark.asyncio
async def test_success_logs_timing(caplog):
    ticks = iter([10.0, 10.25])

    async def call():
        return "ok"

    with caplog.at_level(logging.INFO, logger="app.llm_transport"):
        result = await with_transport_retry(
            call, log_event="test_call", clock=lambda: next(ticks)
        )

    assert result == "ok"
    record = next(r for r in caplog.records if r.message == "test_call_timing")
    assert record.duration_ms == 250
    assert record.attempts == 1


@pytest.mark.asyncio
async def test_timing_spans_retries(caplog):
    ticks = iter([0.0, 5.0])
    calls = {"n": 0}

    async def flaky():
        calls["n"] += 1
        if calls["n"] == 1:
            raise TimeoutError("transient")
        return "ok"

    with caplog.at_level(logging.INFO, logger="app.llm_transport"):
        result = await with_transport_retry(
            flaky, log_event="test_call", sleep=AsyncMock(), clock=lambda: next(ticks)
        )

    assert result == "ok"
    record = next(r for r in caplog.records if r.message == "test_call_timing")
    assert record.duration_ms == 5000
    assert record.attempts == 2
