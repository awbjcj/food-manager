from datetime import datetime

import pytest

from app.callback_dispatch import CallbackResult
from app.callbacks import CallbackContext, CallbackRegistry
from app.client_set import PerUserClients
from app.commands import parse_callback_request


def _context():
    return CallbackContext(
        callback=object(),
        session_factory=lambda: None,  # type: ignore[arg-type,return-value]
        now_provider=lambda _tz: datetime(2026, 7, 17),
        clients=PerUserClients.for_tests(),
    )


@pytest.mark.asyncio
async def test_registry_dispatches_by_typed_route():
    registry = CallbackRegistry()

    @registry.register("ate")
    async def handle_ate(request, context):
        assert request.route == "ate"
        assert context is not None
        return CallbackResult(ack="eaten")

    result = await registry.dispatch(parse_callback_request("act:ate:7"), _context())

    assert result.ack == "eaten"


def test_registry_rejects_duplicate_routes():
    registry = CallbackRegistry()

    @registry.register("help")
    async def first(request, context):
        return CallbackResult()

    with pytest.raises(ValueError, match="duplicate callback route: help"):

        @registry.register("help")
        async def second(request, context):
            return CallbackResult()


@pytest.mark.asyncio
async def test_missing_route_soft_acks_as_unrecognized():
    result = await CallbackRegistry().dispatch(
        parse_callback_request("act:ate:7"), _context()
    )

    assert result == CallbackResult(ack="unrecognized action")
