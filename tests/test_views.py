from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlmodel import Session, SQLModel, create_engine

from app.models import PantryItem
from app.renderer import DIGEST_CAP, render_digest, render_item_card, render_list
from app.views import digest, item_card, pantry_list
from tests.fakes import FakeTranslationLLM

TODAY = date(2026, 7, 17)


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as value:
        yield value


def _item(index: int, name: str) -> PantryItem:
    return PantryItem(
        id=index,
        household_id=1,
        raw_name=name,
        normalized_name=name.lower(),
        category="produce",
        purchased_on=TODAY,
        shelf_life_days=2,
        shelf_life_source="llm",
        ingest_shelf_life_source="llm",
        expires_on=TODAY + timedelta(days=2),
        created_via="manual",
        created_at=datetime.now(UTC),
    )


@pytest.mark.asyncio
async def test_digest_preserves_renderer_default_cap_and_complete_metadata(session):
    items = [_item(index, f"Item {index}") for index in range(1, 13)]
    user = SimpleNamespace(lang="en", household_id=1)

    view = await digest(
        session,
        items,
        user=user,
        today=TODAY,
        translation_llm=None,
    )
    expected = render_digest(items, today=TODAY)

    assert DIGEST_CAP == 10
    assert view.text == expected.text
    assert view.rendered_count == expected.rendered_count
    assert view.total_count == expected.total_count
    assert view.rendered_item_ids == expected.rendered_item_ids
    assert view.has_more is True
    assert view.names == {}


@pytest.mark.asyncio
async def test_list_and_item_views_match_existing_english_composition(session):
    items = [_item(1, "Milk"), _item(2, "Bread")]
    user = SimpleNamespace(lang="en")

    list_view = await pantry_list(
        session, items, user=user, today=TODAY, translation_llm=None
    )
    item_view = await item_card(
        session, items[0], user=user, today=TODAY, translation_llm=None
    )

    assert list_view.text == render_list(items, today=TODAY)
    assert item_view.text == render_item_card(items[0], today=TODAY)


@pytest.mark.asyncio
async def test_non_english_view_translates_dynamic_names_before_rendering(session):
    item = _item(1, "Milk")
    user = SimpleNamespace(lang="zh")
    translator = FakeTranslationLLM(table={"Milk": "牛奶"})

    view = await item_card(
        session,
        item,
        user=user,
        today=TODAY,
        translation_llm=translator,
    )

    assert "牛奶" in view.text
    assert view.names == {"Milk": "牛奶"}
