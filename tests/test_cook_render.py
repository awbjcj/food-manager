import pytest

from app.cook_models import (
    NutritionScore,
    RecipeCandidate,
    RecipeIngredient,
    ScoredCandidate,
)
from app.commands import CommandError, parse_callback
from app.renderer import render_cook_result


def _scored(title, n_alts=0):
    rec = RecipeCandidate(
        title=title,
        cuisine="italian",
        source_url="https://x",
        ingredients=[RecipeIngredient(name="pasta")],
        method_gist="boil",
        deliciousness=0.7,
    )
    nut = NutritionScore(health_score=80, effort="easy", est_minutes=20, rationale="ok")
    return ScoredCandidate(
        recipe=rec,
        nutrition=nut,
        expiry_use=0.5,
        final_score=0.7,
        shopping_list=["pasta"],
    )


def test_render_cook_result_shows_top_pick_and_shopping():
    text = render_cook_result([_scored("Top"), _scored("Alt")], show_alternatives=False)
    assert "Top" in text
    assert "80" in text  # health score
    assert "20 min" in text
    assert "pasta" in text  # shopping list
    assert "Alt" not in text  # hidden until expanded


def test_render_cook_result_expanded_shows_alternatives():
    text = render_cook_result([_scored("Top"), _scored("Alt")], show_alternatives=True)
    assert "Alt" in text


def test_parse_cook_callbacks():
    pick = parse_callback("cookpick:7:2")
    assert pick.verb == "cook_pick" and pick.item_id == 7 and pick.option_index == 2
    alt = parse_callback("cookalt:7")
    assert alt.verb == "cook_alt" and alt.item_id == 7
    assert alt.option_index is None


@pytest.mark.parametrize("data", ["cookpick:7", "cookpick:7:x", "cookpick:7:-1"])
def test_parse_cook_callbacks_reject_malformed_data(data):
    with pytest.raises(CommandError):
        parse_callback(data)
