from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest
from sqlmodel import Session, SQLModel, create_engine

import app.bot as bot_mod
from app.cook.models import NutritionScore, RecipeCandidate, RecipeIngredient, SourcedRecipe
from app.models import Household, MealPlan, PantryItem, User
from app.week_composer import DaySpec


class FakeComposerSelector:
    def __init__(self, specs=None, error=None):
        self.specs, self.error = specs, error

    def for_provider(self, provider):
        return self

    async def compose(self, *, pantry, profile, days, today):
        if self.error:
            raise self.error
        return self.specs


class FakeRecipeSource:
    def __init__(self, pages):
        self.pages = list(pages)
        self.calls = []

    def available(self):
        return True

    async def search(self, criteria, *, remaining_cost_micros=None):
        self.calls.append(criteria)
        return (self.pages.pop(0) if self.pages else []), 100


def _sourced(title, *, ingredients, external_id, health=50, deliciousness=0.5):
    return SourcedRecipe(
        recipe=RecipeCandidate(
            title=title, cuisine="italian", source_url=f"https://recipes.test/{external_id}",
            ingredients=[RecipeIngredient(name=n) for n in ingredients],
            method_gist="Cook it.", deliciousness=deliciousness,
        ),
        nutrition=NutritionScore(health_score=health, effort="easy", est_minutes=20, rationale="x"),
        external_id=external_id,
    )


@pytest.fixture
def session_factory():
    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine)

    def make():
        return Session(engine)

    with make() as db:
        household = Household(created_at=datetime.now(timezone.utc))
        db.add(household)
        db.commit()
        db.refresh(household)
        db.add(
            User(
                telegram_id=1,
                chat_id=1,
                household_id=household.id,
                created_at=datetime.now(timezone.utc),
            )
        )
        db.commit()
    return make


def _seed_pantry(session_factory, today):
    with session_factory() as db:
        for name, days in [
            ("yogurt", 1), ("chicken", 2), ("rice", 300), ("beans", 200), ("pasta", 250),
        ]:
            db.add(PantryItem(
                household_id=1, raw_name=name, normalized_name=name,
                category="produce", qty=1.0, purchased_on=today,
                shelf_life_days=days, shelf_life_source="llm", ingest_shelf_life_source="llm",
                expires_on=today + timedelta(days=days), status="active",
                created_via="receipt", created_at=datetime.now(timezone.utc),
            ))
        db.commit()


def _msg(text: str):
    from types import SimpleNamespace

    return SimpleNamespace(
        text=text,
        from_user=SimpleNamespace(id=1),
        chat=SimpleNamespace(id=1, type="private"),
        answer=AsyncMock(
            return_value=SimpleNamespace(
                message_id=9, edit_text=AsyncMock(), delete=AsyncMock()
            )
        ),
        photo=None,
        reply_to_message=None,
        bot=None,
    )


def _NOW(tz):
    return datetime(2026, 7, 9, tzinfo=timezone.utc)


@pytest.mark.asyncio
async def test_plan_happy_path_renders_and_persists(session_factory, monkeypatch):
    monkeypatch.setattr(bot_mod, "ALLOWED_TELEGRAM_USER_ID", 1)
    today = datetime(2026, 7, 9).date()
    _seed_pantry(session_factory, today)
    composer = FakeComposerSelector(specs=[
        DaySpec(day_index=0, cuisine="italian", feature_items=["yogurt"]),
        DaySpec(day_index=1, cuisine="asian", feature_items=["chicken"]),
        DaySpec(day_index=2, cuisine="mexican", feature_items=["rice"]),
    ])
    source = FakeRecipeSource([
        [_sourced("Yogurt Bowl", ingredients=["yogurt"], external_id="A")],
        [_sourced("Chicken Stir Fry", ingredients=["chicken"], external_id="B")],
        [_sourced("Rice Pilaf", ingredients=["rice"], external_id="C")],
    ])
    msg = _msg("/plan 3")
    await bot_mod.handle_plan(
        msg,
        session_factory=session_factory,
        now_provider=_NOW,
        composer=composer,
        recipe_sources=[source],
    )
    ack = msg.answer.return_value
    assert ack.edit_text.await_args is not None
    text = ack.edit_text.await_args.args[0]
    assert "Yogurt Bowl" in text and "Chicken Stir Fry" in text
    with session_factory() as db:
        from sqlmodel import select

        plan = db.exec(select(MealPlan)).one()
        assert plan.status == "active"
        assert plan.message_id == 9


@pytest.mark.asyncio
async def test_plan_bad_days_arg_shows_usage(session_factory, monkeypatch):
    monkeypatch.setattr(bot_mod, "ALLOWED_TELEGRAM_USER_ID", 1)
    msg = _msg("/plan 9")
    await bot_mod.handle_plan(
        msg,
        session_factory=session_factory,
        now_provider=_NOW,
        composer=FakeComposerSelector(specs=[]),
        recipe_sources=[],
    )
    assert "usage: /plan" in msg.answer.await_args.args[0]


@pytest.mark.asyncio
async def test_plan_tiny_pantry_replies_not_enough(session_factory, monkeypatch):
    monkeypatch.setattr(bot_mod, "ALLOWED_TELEGRAM_USER_ID", 1)
    msg = _msg("/plan")
    await bot_mod.handle_plan(
        msg,
        session_factory=session_factory,
        now_provider=_NOW,
        composer=FakeComposerSelector(specs=[]),
        recipe_sources=[],
    )
    ack = msg.answer.return_value
    text = ack.edit_text.await_args.args[0] if ack.edit_text.await_args else msg.answer.await_args.args[0]
    from app.i18n import t

    assert text == t("plan.not_enough", "en")


@pytest.mark.asyncio
async def test_second_plan_supersedes_first(session_factory, monkeypatch):
    monkeypatch.setattr(bot_mod, "ALLOWED_TELEGRAM_USER_ID", 1)
    today = datetime(2026, 7, 9).date()
    _seed_pantry(session_factory, today)
    composer = FakeComposerSelector(specs=[
        DaySpec(day_index=0, feature_items=["yogurt"]),
        DaySpec(day_index=1, feature_items=["chicken"]),
        DaySpec(day_index=2, feature_items=["rice"]),
    ])

    source1 = FakeRecipeSource([
        [_sourced("First", ingredients=["yogurt"], external_id="A")],
        [_sourced("First2", ingredients=["chicken"], external_id="B")],
        [_sourced("First3", ingredients=["rice"], external_id="C")],
    ])
    msg1 = _msg("/plan 3")
    await bot_mod.handle_plan(
        msg1, session_factory=session_factory, now_provider=_NOW,
        composer=composer, recipe_sources=[source1],
    )

    source2 = FakeRecipeSource([
        [_sourced("Second", ingredients=["yogurt"], external_id="D")],
        [_sourced("Second2", ingredients=["chicken"], external_id="E")],
        [_sourced("Second3", ingredients=["rice"], external_id="F")],
    ])
    msg2 = _msg("/plan 3")
    await bot_mod.handle_plan(
        msg2, session_factory=session_factory, now_provider=_NOW,
        composer=composer, recipe_sources=[source2],
    )
    ack2 = msg2.answer.return_value
    text2 = ack2.edit_text.await_args.args[0]
    assert "Replaced your previous plan" in text2
    with session_factory() as db:
        from sqlmodel import select

        plans = list(db.exec(select(MealPlan)).all())
        assert len(plans) == 2
        statuses = sorted(p.status for p in plans)
        assert statuses == ["active", "cancelled"]
