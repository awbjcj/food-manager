"""Typed callback route registry."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from app.callback_dispatch import CallbackResult
from app.callbacks.context import CallbackContext
from app.commands import CallbackRequest, CallbackRoute

CallbackHandler = Callable[
    [CallbackRequest, CallbackContext], Awaitable[CallbackResult]
]


class CallbackRegistry:
    def __init__(self) -> None:
        self._handlers: dict[CallbackRoute, CallbackHandler] = {}

    @property
    def routes(self) -> frozenset[CallbackRoute]:
        return frozenset(self._handlers)

    def register(
        self, *routes: CallbackRoute
    ) -> Callable[[CallbackHandler], CallbackHandler]:
        def decorate(handler: CallbackHandler) -> CallbackHandler:
            for route in routes:
                if route in self._handlers:
                    raise ValueError(f"duplicate callback route: {route}")
                self._handlers[route] = handler
            return handler

        return decorate

    async def dispatch(
        self, request: CallbackRequest, context: CallbackContext
    ) -> CallbackResult:
        handler = self._handlers.get(request.route)
        if handler is None:
            return CallbackResult(ack="unrecognized action")
        return await handler(request, context)
