from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

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
