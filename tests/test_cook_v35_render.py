from datetime import UTC, datetime

from app.cook.models import RecipeCandidate, RecipeIngredient
from app.models import SavedRecipe, ShoppingList
from app.renderer import (
    build_cook_result_keyboard,
    build_favorites_keyboard,
    build_shopping_keyboard,
    render_favorites,
    render_recook,
    render_shopping_list,
)


def test_result_keyboard_has_feedback_save_shop_and_alternatives():
    rows = build_cook_result_keyboard(7, has_alternatives=True)
    data = [b.callback_data for row in rows for b in row]
    assert "cookfb:7:liked" in data
    assert "cookfb:7:disliked" in data
    assert "cooksave:7" in data
    assert "cookshop:7" in data
    assert "cookalt:7" in data


def test_result_keyboard_omits_alternatives_when_single():
    rows = build_cook_result_keyboard(7, has_alternatives=False)
    data = [b.callback_data for row in rows for b in row]
    assert "cookalt:7" not in data
    assert "cooksave:7" in data


def test_render_shopping_list_lists_items_or_empty():
    now = datetime(2026, 5, 30, 12, 0, tzinfo=UTC)
    assert "empty" in render_shopping_list([]).lower()
    items = [ShoppingList(id=3, household_id=1, name_raw="Tomatoes",
                          name_normalized="tomatoes", added_at=now)]
    text = render_shopping_list(items)
    assert "Tomatoes" in text


def test_build_shopping_keyboard_one_button_per_item():
    rows = build_shopping_keyboard([3, 4])
    assert [b.callback_data for row in rows for b in row] == ["shopdone:3", "shopdone:4"]


def test_render_favorites_lists_or_empty():
    now = datetime(2026, 5, 30, 12, 0, tzinfo=UTC)
    assert "no saved" in render_favorites([]).lower()
    saved = [SavedRecipe(id=5, household_id=1, title="Pasta", cuisine="italian",
                         ingredients_json="[]", method_gist="boil", saved_at=now)]
    text = render_favorites(saved)
    assert "#5" in text and "Pasta" in text


def test_build_favorites_keyboard_one_button_per_recipe():
    rows = build_favorites_keyboard([5, 6])
    assert [b.callback_data for row in rows for b in row] == ["favcook:5", "favcook:6"]


def test_render_recook_shows_recipe_and_shopping():
    recipe = RecipeCandidate(
        title="Pasta", cuisine="italian", source_url="https://x",
        ingredients=[RecipeIngredient(name="pasta")], method_gist="boil")
    text = render_recook(recipe, shopping=["pasta"])
    assert "Pasta" in text
    assert "pasta" in text
    text_full = render_recook(recipe, shopping=[])
    assert "have it all" in text_full or "everything" in text_full
