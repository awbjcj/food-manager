from datetime import UTC, date, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlmodel import Session, SQLModel, create_engine

from app.models import Household, ShelfLifeCache, User
from app.nl_intent import AgnoIntentAgent, NLIntent, match_items


def _item(item_id: int, normalized: str, raw: str | None = None):
    return SimpleNamespace(
        id=item_id, normalized_name=normalized, raw_name=raw or normalized
    )


def test_nlintent_defaults():
    intent = NLIntent(kind="unknown")
    assert intent.mark_action is None
    assert intent.item_name is None
    assert intent.food is None
    assert intent.shopping_action is None
    assert intent.shopping_items == []


def test_match_items_exact_beats_substring():
    items = [_item(1, "milk"), _item(2, "oat milk")]
    assert [i.id for i in match_items("milk", items)] == [1]


def test_match_items_substring_both_directions():
    items = [_item(1, "greek yogurt")]
    assert match_items("yogurt", items)[0].id == 1
    assert match_items("plain greek yogurt", items)[0].id == 1


def test_match_items_multiple_and_none():
    items = [_item(1, "whole milk"), _item(2, "oat milk")]
    assert [i.id for i in match_items("milk", items)] == [1, 2]
    assert match_items("salmon", items) == []


@pytest.mark.asyncio
async def test_agent_parse_returns_typed_intent():
    intent = NLIntent(kind="mark", mark_action="ate", item_name="yogurt")
    inner = SimpleNamespace(
        arun=AsyncMock(return_value=SimpleNamespace(content=intent))
    )
    agent = AgnoIntentAgent(inner)
    got = await agent.parse(
        "ate the yogurt", today=date(2026, 7, 9), pantry_names=["yogurt", "milk"]
    )
    assert got is intent
    prompt = inner.arun.await_args.args[0]
    assert "2026-07-09" in prompt
    assert "yogurt" in prompt
    assert "ate the yogurt" in prompt


@pytest.mark.asyncio
async def test_agent_parse_rejects_unstructured_content():
    inner = SimpleNamespace(
        arun=AsyncMock(return_value=SimpleNamespace(content="free text"))
    )
    with pytest.raises(ValueError):
        await AgnoIntentAgent(inner).parse(
            "hello", today=date(2026, 7, 9), pantry_names=[]
        )


@pytest.fixture
def session_factory():
    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine)

    def make():
        return Session(engine)

    with make() as db:
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
                created_at=datetime.now(UTC),
            )
        )
        db.commit()
    return make


@pytest.mark.asyncio
async def test_shelf_life_cache_hit_short_circuits(session_factory):
    from app.bot import _answer_shelf_life

    with session_factory() as session:
        session.add(
            ShelfLifeCache(
                household_id=1,
                normalized_name="salmon",
                days=3,
                confidence=0.9,
                learned_at=datetime.now(UTC),
            )
        )
        session.commit()
        answer = await _answer_shelf_life(
            session, household_id=1, food="salmon", lang="en"
        )
    assert "3" in answer and "salmon" in answer


@pytest.mark.asyncio
async def test_shelf_life_falls_back_to_defaults_table(session_factory):
    from app.bot import _answer_shelf_life

    with session_factory() as session:
        answer = await _answer_shelf_life(
            session, household_id=1, food="whole milk", lang="en"
        )
    assert "7" in answer  # shelf_life_defaults: "whole milk" -> 7d


@pytest.mark.asyncio
async def test_shelf_life_unknown_is_honest(session_factory):
    from app.bot import _answer_shelf_life

    with session_factory() as session:
        answer = await _answer_shelf_life(
            session, household_id=1, food="dragonfruit tart", lang="en"
        )
    assert "not sure" in answer.lower()


def test_intent_schema_covers_the_v55_kinds():
    from app.nl_intent import NLIntent

    for kind in ("cook", "plan", "cooked", "correct", "shopping"):
        assert NLIntent(kind=kind).kind == kind


def test_shopping_intent_carries_action_and_items():
    intent = NLIntent(
        kind="shopping", shopping_action="add", shopping_items=["milk", "eggs"]
    )
    assert intent.shopping_action == "add"
    assert intent.shopping_items == ["milk", "eggs"]


def test_plan_intent_carries_an_optional_day_count():
    from app.nl_intent import NLIntent

    assert NLIntent(kind="plan").days is None
    assert NLIntent(kind="plan", days=5).days == 5


def test_instructions_draw_the_mark_versus_cooked_boundary():
    from app.nl_intent import _INSTRUCTIONS

    assert "cooked" in _INSTRUCTIONS
    assert "planned meal" in _INSTRUCTIONS
    assert '"correct"' in _INSTRUCTIONS
    assert '"shopping"' in _INSTRUCTIONS
