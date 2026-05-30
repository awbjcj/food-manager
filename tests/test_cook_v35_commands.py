import pytest

from app.commands import CommandError, parse_callback


def test_parse_feedback_callbacks():
    liked = parse_callback("cookfb:7:liked")
    assert liked.verb == "cook_like" and liked.item_id == 7
    disliked = parse_callback("cookfb:7:disliked")
    assert disliked.verb == "cook_dislike" and disliked.item_id == 7


def test_parse_save_shop_callbacks():
    assert parse_callback("cooksave:7").verb == "cook_save"
    assert parse_callback("cooksave:7").item_id == 7
    assert parse_callback("cookshop:7").verb == "cook_shop"
    assert parse_callback("cookshop:7").item_id == 7


def test_parse_shopdone_and_favcook():
    assert parse_callback("shopdone:3").verb == "shop_done"
    assert parse_callback("shopdone:3").item_id == 3
    assert parse_callback("favcook:5").verb == "fav_cook"
    assert parse_callback("favcook:5").item_id == 5


@pytest.mark.parametrize("data", ["cookfb:7", "cookfb:7:meh", "cooksave:x", "favcook:y"])
def test_parse_rejects_malformed(data):
    with pytest.raises(CommandError):
        parse_callback(data)
