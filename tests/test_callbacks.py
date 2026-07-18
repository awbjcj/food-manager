from datetime import datetime

import pytest

from app.callback_dispatch import CallbackResult
from app.callbacks import CallbackContext, CallbackRegistry
from app.client_set import PerUserClients
from app.commands import parse_callback_request


class _CallbackUser:
    @property
    def id(self) -> int:
        return 1


class _Callback:
    data: str | None = None
    message: object | None = None

    @property
    def from_user(self) -> _CallbackUser:
        return _CallbackUser()

    async def answer(self, text: str = "", *, show_alert: bool = False) -> object:
        return None


def _context():
    return CallbackContext(
        callback=_Callback(),
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
