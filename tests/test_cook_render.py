import pytest

from app.cook.models import (
    NutritionScore,
    RecipeCandidate,
    RecipeIngredient,
    ScoredCandidate,
)
from app.commands import CommandError, parse_callback
from app.renderer import build_cook_round_keyboard, render_cook_result


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


def test_render_cook_result_shows_ingredient_list():
    rec = RecipeCandidate(
        title="Stew",
        cuisine="french",
        source_url="https://x",
        ingredients=[RecipeIngredient(name="carrot"), RecipeIngredient(name="onion")],
        method_gist="simmer",
        deliciousness=0.7,
    )
    nut = NutritionScore(health_score=70, effort="easy", est_minutes=40, rationale="ok")
    card = ScoredCandidate(
        recipe=rec, nutrition=nut, expiry_use=0.5, final_score=0.7, shopping_list=[]
    )
    text = render_cook_result([card], show_alternatives=False)
    assert "Ingredients: carrot, onion" in text


def test_parse_cook_callbacks():
    pick = parse_callback("cookpick:7:2")
    assert (
        pick.verb == "cook_pick"
        and pick.item_id == 7
        and pick.option_index == 2
        and pick.round_name is None
    )
    meal = parse_callback("cookpick:7:meal:2")
    assert (
        meal.verb == "cook_pick"
        and meal.item_id == 7
        and meal.option_index == 2
        and meal.round_name == "meal"
    )
    cuisine = parse_callback("cookpick:7:cuisine:1")
    assert (
        cuisine.verb == "cook_pick"
        and cuisine.item_id == 7
        and cuisine.option_index == 1
        and cuisine.round_name == "cuisine"
    )
    alt = parse_callback("cookalt:7")
    assert alt.verb == "cook_alt" and alt.item_id == 7
    assert alt.option_index is None


@pytest.mark.parametrize(
    "data",
    [
        "cookpick:7",
        "cookpick:7:x",
        "cookpick:7:-1",
        "cookpick:7:snack:0",
        "cookpick:7:meal:-1",
    ],
)
def test_parse_cook_callbacks_reject_malformed_data(data):
    with pytest.raises(CommandError):
        parse_callback(data)


def test_build_cook_round_keyboard_can_include_round_token():
    rows = build_cook_round_keyboard(7, ["Dinner", "Lunch"], round_name="meal")
    assert [row[0].callback_data for row in rows] == [
        "cookpick:7:meal:0",
        "cookpick:7:meal:1",
    ]


def test_render_stats_includes_cook_line():
    from app.pantry_service import Stats
    from app.renderer import render_stats

    stats = Stats(
        receipt_count=0, tracked_item_count=0, removed_item_count=0,
        cache_hit_percent=None, total_cost_micros_usd=0, avg_cost_micros_usd=None,
        unknown_cost_receipt_count=0, waste_rate_percent=None,
        cook_cost_micros_usd=1500, cook_count=2,
    )
    text = render_stats(stats)
    assert "Cook sessions: 2" in text
    assert "0.001" in text or "0.002" in text
