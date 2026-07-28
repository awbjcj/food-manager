"""Callback parsing and dispatch package."""

from typing import get_args

from app.callbacks.context import CallbackContext
from app.callbacks.registry import CallbackRegistry
from app.commands import ItemRoute, Verb

EXPECTED_CALLBACK_ROUTES = frozenset([*get_args(Verb), *get_args(ItemRoute), "help"])

__all__ = [
    "EXPECTED_CALLBACK_ROUTES",
    "CallbackContext",
    "CallbackRegistry",
]
