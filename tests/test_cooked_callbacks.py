from datetime import UTC, date, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from app.callbacks import EXPECTED_CALLBACK_ROUTES
from app.callbacks.cooked import handle_cooked_callback
from app.callbacks.routes import build_callback_registry
from app.commands import CommandError, parse_callback
from app.cook.models import (
    NutritionScore,
    RecipeCandidate,
    RecipeIngredient,
    ScoredCandidate,
)
from app.i18n import t
from app.models import CookedMeal, Household, MealPlan, MealPlanEntry, PantryItem, User


def test_plan_cooked_parses_plan_and_day():
    action = parse_callback("plan:cooked:12:3")
    assert action.verb == "plan_cooked"
    assert action.item_id == 12
    assert action.option_index == 3


def test_toggle_parses_cooked_and_item():
    action = parse_callback("cooked:tog:7:42")
    assert action.verb == "cooked_toggle"
    assert action.item_id == 7
    assert action.option_index == 42


def test_confirm_and_none_parse():
    assert parse_callback("cooked:ok:7").verb == "cooked_confirm"
    assert parse_callback("cooked:none:7").verb == "cooked_none"


def test_malformed_cooked_data_is_rejected():
    with pytest.raises(CommandError):
        parse_callback("cooked:tog:7")
    with pytest.raises(CommandError):
        parse_callback("cooked:ok:abc")
    with pytest.raises(CommandError):
        parse_callback("cooked:bogus:7")


def test_every_new_route_has_exactly_one_handler():
    registry = build_callback_registry()
    for route in ("plan_cooked", "cooked_toggle", "cooked_confirm", "cooked_none"):
        assert route in EXPECTED_CALLBACK_ROUTES
        assert registry.routes == EXPECTED_CALLBACK_ROUTES


TODAY = date(2026, 8, 14)


def _NOW(tz):
    return datetime(2026, 8, 14, tzinfo=UTC)


@pytest.fixture
def session_factory():
    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine)
    return lambda: Session(engine)


def _build_plan_entry(session_factory, *, lang: str) -> tuple[int, int]:
    """Returns (plan_id, day_index) for a household with a matching pantry item."""
    with session_factory() as db:
        household = Household(created_at=datetime.now(UTC))
        db.add(household)
        db.commit()
        db.refresh(household)
        assert household.id is not None
        db.add(
            User(
                telegram_id=1,
                chat_id=1,
                household_id=household.id,
                lang=lang,
                created_at=datetime.now(UTC),
            )
        )
        plan = MealPlan(
            household_id=household.id,
            start_date=TODAY,
            days=1,
            status="active",
            chat_id=1,
            created_at=datetime.now(UTC),
        )
        db.add(plan)
        db.commit()
        db.refresh(plan)
        assert plan.id is not None
        candidate = ScoredCandidate(
            recipe=RecipeCandidate(
                title="Chicken Tikka",
                cuisine="indian",
                source_url="https://recipes.test/10",
                ingredients=[RecipeIngredient(name="chicken")],
                method_gist="Cook it.",
                deliciousness=0.5,
            ),
            nutrition=NutritionScore(
                health_score=50, effort="easy", est_minutes=20, rationale="x"
            ),
            expiry_use=0.0,
            external_id="spoon:10",
            final_score=0.5,
        )
        entry = MealPlanEntry(
            plan_id=plan.id,
            day_index=0,
            date=TODAY,
            recipe_json=candidate.model_dump_json(),
            spec_json="{}",
        )
        db.add(entry)
        db.add(
            PantryItem(
                household_id=household.id,
                raw_name="Chicken Thighs",
                normalized_name="chicken",
                category="other",
                qty=1.0,
                purchased_on=TODAY,
                expires_on=date(2026, 8, 20),
                shelf_life_days=6,
                shelf_life_source="llm",
                ingest_shelf_life_source="llm",
                created_via="manual",
                created_at=datetime.now(UTC),
            )
        )
        db.commit()
        return plan.id, 0


class _CbMessage:
    def __init__(self) -> None:
        self.chat = SimpleNamespace(id=1)
        self.message_id = 9
        self.edit_text = AsyncMock()
        self.answer = AsyncMock()


class _Cb:
    def __init__(self, data: str, user_id: int = 1):
        self.data = data
        self.from_user = SimpleNamespace(id=user_id)
        self.message = _CbMessage()
        self.answer = AsyncMock()


@pytest.mark.asyncio
async def test_confirm_message_shows_canonical_english_names_even_for_non_english_user(
    session_factory,
):
    """Known v5.5 limitation: the post-confirm line is never translated, only the
    sheet view is, so a non-English user still sees the English pantry name here."""
    plan_id, day_index = _build_plan_entry(session_factory, lang="es")

    open_cb = _Cb(f"plan:cooked:{plan_id}:{day_index}")
    await handle_cooked_callback(
        open_cb, session_factory=session_factory, now_provider=_NOW, translation_llm=None
    )
    open_cb.answer.assert_awaited()

    with session_factory() as db:
        cooked_id = db.exec(select(CookedMeal)).one().id

    confirm_cb = _Cb(f"cooked:ok:{cooked_id}")
    await handle_cooked_callback(
        confirm_cb,
        session_factory=session_factory,
        now_provider=_NOW,
        translation_llm=None,
    )
    confirm_cb.answer.assert_awaited()
    assert confirm_cb.message.edit_text.await_args is not None
    text = confirm_cb.message.edit_text.await_args.args[0]
    assert text == t("cooked.done", "es", names="Chicken Thighs")
    assert "Chicken Thighs" in text
