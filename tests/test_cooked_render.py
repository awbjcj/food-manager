from datetime import UTC, date, datetime

from app.cook.cooked_service import ConsumeCandidate, CookedSheet
from app.models import CookedMeal
from app.renderer import (
    build_cooked_sheet_keyboard,
    render_cooked_history,
    render_cooked_sheet,
)


def _sheet(selected=(1,)):
    return CookedSheet(
        cooked_id=7,
        recipe_title="Chicken Tikka",
        candidates=[
            ConsumeCandidate(item_id=1, raw_name="Chicken Thighs"),
            ConsumeCandidate(item_id=2, raw_name="Greek Yogurt"),
        ],
        selected_ids=set(selected),
    )


def test_english_sheet_text_is_exact():
    assert render_cooked_sheet(_sheet()) == (
        "🍳 Cooked: Chicken Tikka\n\nWhich did you use up?"
    )


def test_empty_sheet_says_nothing_matched():
    empty = CookedSheet(cooked_id=7, recipe_title="Chicken Tikka", candidates=[], selected_ids=set())
    assert render_cooked_sheet(empty) == (
        "🍳 Cooked: Chicken Tikka\n\nNothing in your pantry matched this recipe."
    )


def test_keyboard_marks_checked_and_unchecked_items():
    rows = build_cooked_sheet_keyboard(_sheet())
    assert rows[0][0].text == "✅ Chicken Thighs"
    assert rows[0][0].callback_data == "cooked:tog:7:1"
    assert rows[1][0].text == "⬜ Greek Yogurt"
    assert rows[1][0].callback_data == "cooked:tog:7:2"
    assert [b.callback_data for b in rows[-1]] == ["cooked:ok:7", "cooked:none:7"]


def test_keyboard_uses_translated_names():
    rows = build_cooked_sheet_keyboard(_sheet(), lang="fr", names={"Chicken Thighs": "Cuisses de poulet"})
    assert rows[0][0].text == "✅ Cuisses de poulet"


def test_empty_sheet_keyboard_has_only_the_action_row():
    empty = CookedSheet(cooked_id=7, recipe_title="Chicken Tikka", candidates=[], selected_ids=set())
    rows = build_cooked_sheet_keyboard(empty)
    assert len(rows) == 1
    assert [b.callback_data for b in rows[0]] == ["cooked:ok:7", "cooked:none:7"]


def test_render_cooked_history_lists_source_and_date():
    rows = [
        CookedMeal(
            household_id=1,
            source="cook",
            recipe_key="tikka",
            recipe_title="Chicken Tikka",
            cooked_on=date(2026, 8, 14),
            confirmed_at=datetime(2026, 8, 14, 20, tzinfo=UTC),
        )
    ]
    assert render_cooked_history(rows, today=date(2026, 8, 15)) == (
        "🍽 Cooked history\n\nAug 14 · Chicken Tikka · /cook"
    )


def test_render_cooked_history_empty():
    assert render_cooked_history([], today=date(2026, 8, 15)) == (
        "No cooked meals recorded yet."
    )
