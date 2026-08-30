from __future__ import annotations

import ast
from pathlib import Path

from app.bot import _MESSAGE_COMMANDS
from app.callbacks import EXPECTED_CALLBACK_ROUTES

ROOT = Path(__file__).parents[1]


def test_handler_modules_do_not_import_dispatcher_module():
    modules = [
        *ROOT.joinpath("app", "handlers").glob("*.py"),
        *ROOT.joinpath("app", "callbacks").glob("*.py"),
    ]
    for module in modules:
        tree = ast.parse(module.read_text(encoding="utf-8"))
        imports = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        imports.update(
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module is not None
        )
        assert "app.bot" not in imports, module


def test_dispatcher_rosters_cover_every_supported_entry_point():
    assert {name for name, _handler, _deps in _MESSAGE_COMMANDS} == {
        "start",
        "invite",
        "join",
        "bind",
        "household",
        "leave",
        "remove",
        "tz",
        "lang",
        "digest_at",
        "list",
        "pantry",
        "add",
        "ate",
        "toss",
        "delete",
        "snooze",
        "correct",
        "stats",
        "cook",
        "history",
        "plan",
        "calendar",
        "shopping",
        "favorites",
        "llm",
        "prefs",
        "help",
        "quota",
        "buy",
        "billing",
    }
    assert len(EXPECTED_CALLBACK_ROUTES) == 38
