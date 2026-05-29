import pytest
from app.commands import CallbackAction, CommandError, parse_callback


def test_parse_undo_receipt_and_add():
    assert parse_callback("undo:receipt:12") == CallbackAction(verb="undo_receipt", item_id=12)
    assert parse_callback("undo:add:7") == CallbackAction(verb="undo_add", item_id=7)


def test_parse_undo_bad_id():
    with pytest.raises(CommandError):
        parse_callback("undo:receipt:x")


def test_parse_undo_bad_kind():
    with pytest.raises(CommandError):
        parse_callback("undo:bogus:1")
