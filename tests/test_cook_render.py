import pytest

from app.cook.models import (
    NutritionScore,
    RecipeCandidate,
    RecipeIngredient,
    ScoredCandidate,
)
from app.commands import CommandError, parse_callback
from app.renderer import (
    build_cook_result_keyboard,
    build_cook_round_keyboard,
    render_cook_result,
)


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


def test_cook_result_keyboard_offers_more_and_adjust_before_alternatives():
    rows = build_cook_result_keyboard(5, has_alternatives=True, lang="en")
    callback_rows = [[button.callback_data for button in row] for row in rows]

    assert ["cookmore2:5", "cookadj:5"] in callback_rows
    assert callback_rows.index(["cookmore2:5", "cookadj:5"]) < callback_rows.index(
        ["cookalt:5"]
    )


def test_purpose_round_exposes_every_recipe_purpose_in_stable_order():
    from app.renderer import PURPOSE_OPTIONS

    assert [code for code, _key in PURPOSE_OPTIONS] == [
        "use_it_up",
        "quick",
        "healthy",
        "comfort",
        "surprise",
    ]


def test_parse_more_adjust_and_extended_round_callbacks():
    more = parse_callback("cookmore2:5")
    assert more.verb == "cook_more" and more.item_id == 5

    adjust = parse_callback("cookadj:5")
    assert adjust.verb == "cook_adjust" and adjust.item_id == 5

    expand = parse_callback("cookmore:5:cuisine_full")
    assert (
        expand.verb == "cook_more_opts"
        and expand.item_id == 5
        and expand.round_name == "cuisine_full"
    )

    purpose = parse_callback("cookpick:5:purpose:2")
    assert (
        purpose.verb == "cook_pick"
        and purpose.item_id == 5
        and purpose.option_index == 2
        and purpose.round_name == "purpose"
    )

    full_cuisine = parse_callback("cookpick:5:cuisine_full:7")
    assert (
        full_cuisine.verb == "cook_pick"
        and full_cuisine.item_id == 5
        and full_cuisine.option_index == 7
        and full_cuisine.round_name == "cuisine_full"
    )


def test_full_cuisine_keyboard_index_matches_the_displayed_option():
    from app.bot import SPOONACULAR_CUISINES

    options = [*SPOONACULAR_CUISINES, "Surprise me"]
    rows = build_cook_round_keyboard(5, options, round_name="cuisine_full")

    assert rows[7][0].text == options[7]
    assert rows[7][0].callback_data == "cookpick:5:cuisine_full:7"


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
