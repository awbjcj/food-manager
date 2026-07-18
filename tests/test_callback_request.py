import pytest

from app.commands import (
    ActionCallbackRequest,
    CommandError,
    HelpCallbackRequest,
    ItemCallbackRequest,
    parse_callback_request,
)


@pytest.mark.parametrize(
    ("data", "route"),
    [
        ("act:ate:1", "ate"),
        ("show:all", "show_all"),
        ("apply:1", "apply"),
        ("undo:receipt:1", "undo_receipt"),
        ("cookpick:1:meal:0", "cook_pick"),
        ("cookalt:1", "cook_alt"),
        ("cookfb:1:liked", "cook_like"),
        ("cooksave:1", "cook_save"),
        ("cookmore2:1", "cook_more"),
        ("cookadj:1", "cook_adjust"),
        ("plan:swap:1:0", "plan_swap"),
        ("plan:shop:1", "plan_shop"),
    ],
)
def test_action_callbacks_keep_the_existing_parser_contract(data, route):
    request = parse_callback_request(data)

    assert isinstance(request, ActionCallbackRequest)
    assert request.route == route


def test_item_and_help_callbacks_are_part_of_the_top_level_contract():
    item = parse_callback_request("item:open:7:all")
    help_request = parse_callback_request("help:pantry")

    assert isinstance(item, ItemCallbackRequest)
    assert item.route == "item_open"
    assert item.action.item_id == 7
    assert isinstance(help_request, HelpCallbackRequest)
    assert help_request.route == "help"
    assert help_request.topic == "pantry"


@pytest.mark.parametrize("data", ["", "wat", "item:nope:1", "plan:nope:1"])
def test_top_level_callback_parser_rejects_unknown_data(data):
    with pytest.raises(CommandError):
        parse_callback_request(data)
