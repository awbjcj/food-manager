from datetime import date, datetime, timedelta, timezone

import pytest

from app.commands import (
    CallbackAction,
    CommandError,
    parse_callback,
    parse_correct_args,
    parse_digest_at,
    parse_item_id_arg,
    parse_list_filter,
    parse_snooze_args,
    parse_tz,
)
from app.ingest_service import IngestSummary
from app.models import PantryItem
from app.pantry_service import ListFilter, Stats
from app.renderer import (
    build_digest_keyboard,
    render_digest,
    render_ingest_reply,
    render_list,
    render_stats,
)


def test_command_parsers():
    assert parse_tz("America/Detroit") == "America/Detroit"
    with pytest.raises(CommandError):
        parse_tz("EST")
    assert parse_digest_at("0") == 0
    assert parse_digest_at("23") == 23
    for value in ("-1", "24", "8.5", "x"):
        with pytest.raises(CommandError):
            parse_digest_at(value)
    assert parse_item_id_arg("#42") == 42
    assert parse_snooze_args(["42"]) == (42, 2)
    assert parse_snooze_args(["#42", "5"]) == (42, 5)
    assert parse_correct_args(["#42", "5"]) == (42, 5)
    with pytest.raises(CommandError):
        parse_correct_args(["42", "731"])
    assert parse_list_filter([]) == ListFilter.default()
    assert parse_list_filter(["dairy"]) == ListFilter(category="dairy")
    assert parse_list_filter(["week"]) == ListFilter(window="week")
    with pytest.raises(CommandError):
        parse_list_filter(["unknownthing"])


def test_callback_parser():
    assert parse_callback("act:ate:42") == CallbackAction(verb="ate", item_id=42)
    assert parse_callback("act:toss:5") == CallbackAction(verb="toss", item_id=5)
    assert parse_callback("act:snooze2:9") == CallbackAction(verb="snooze2", item_id=9)
    assert parse_callback("show:all") == CallbackAction(verb="show_all", item_id=None)
    with pytest.raises(CommandError):
        parse_callback("act:nope:1")


def _summary(**kwargs):
    values = dict(
        receipt_id=1,
        inserted_food_count=2,
        inserted_item_ids=[42, 43],
        inserted_item_names=["Whole Milk 1 gal", "Bananas"],
        inserted_item_expires_on=[date(2026, 5, 31), date(2026, 5, 29)],
        inserted_item_shelf_life_days=[7, 3],
        skipped_non_food_count=0,
        skipped_low_confidence_count=0,
        skipped_low_confidence_names=[],
        low_confidence_inserted_ids=[],
        purchase_date=date(2026, 5, 26),
        purchase_date_assumed=False,
        cost_micros_usd=18000,
    )
    values.update(kwargs)
    return IngestSummary(**values)


def test_render_ingest_reply_variants():
    text = render_ingest_reply(_summary(), today=date(2026, 5, 26))
    assert "Logged 2 items" in text
    assert "#42" in text and "Whole Milk 1 gal" in text
    assert "exp May 31" in text and "(7d)" in text
    assert "$0.018" in text
    assert "Purchase date assumed" in render_ingest_reply(
        _summary(purchase_date_assumed=True), today=date(2026, 5, 26)
    )
    assert "Low confidence" in render_ingest_reply(
        _summary(low_confidence_inserted_ids=[42]), today=date(2026, 5, 26)
    )
    skipped = render_ingest_reply(
        _summary(skipped_low_confidence_count=2, skipped_low_confidence_names=["A", "B"]),
        today=date(2026, 5, 26),
    )
    assert "skipped 2 unclear" in skipped.lower()
    assert "Cost: unavailable" in render_ingest_reply(
        _summary(cost_micros_usd=None), today=date(2026, 5, 26)
    )
    zero = render_ingest_reply(
        IngestSummary(
            receipt_id=None,
            inserted_food_count=0,
            purchase_date=date(2026, 5, 26),
            purchase_date_assumed=False,
            cost_micros_usd=2000,
        ),
        today=date(2026, 5, 26),
    )
    assert "no food" in zero.lower()


def _pantry_item(name, expires_on, item_id):
    return PantryItem(
        id=item_id,
        user_id=1,
        raw_name=name,
        normalized_name=name.lower(),
        category="other",
        qty=1.0,
        unit=None,
        purchased_on=date(2026, 5, 20),
        shelf_life_days=(expires_on - date(2026, 5, 20)).days,
        shelf_life_source="llm",
        ingest_shelf_life_source="llm",
        expires_on=expires_on,
        status="active",
        created_via="manual",
        created_at=datetime.now(timezone.utc),
    )


def test_render_digest_buckets_and_keyboard():
    today = date(2026, 5, 27)
    items = [
        _pantry_item("Spinach", today - timedelta(days=1), 41),
        _pantry_item("Whole Milk 1 gal", today, 42),
        _pantry_item("Bananas", today, 43),
        _pantry_item("Sliced Bread", today + timedelta(days=1), 44),
        _pantry_item("Greek Yogurt", today + timedelta(days=5), 45),
    ]
    rendered = render_digest(items, today=today)
    assert "Expired (1)" in rendered.text
    assert "#41 Spinach" in rendered.text
    assert "Today (2)" in rendered.text
    assert "Tomorrow (1)" in rendered.text
    assert "This week (1)" in rendered.text
    keyboard = build_digest_keyboard([42], has_more=False)
    assert {button.callback_data for button in keyboard[0]} == {
        "act:ate:42",
        "act:toss:42",
        "act:snooze2:42",
    }


def test_render_digest_truncates_at_20():
    today = date(2026, 5, 27)
    rendered = render_digest(
        [_pantry_item(f"Item {i}", today, 100 + i) for i in range(25)],
        today=today,
    )
    assert rendered.rendered_count == 20
    assert "5 more" in rendered.text
    assert len(build_digest_keyboard(rendered.rendered_item_ids, has_more=True)) == 21


def test_render_list_and_stats():
    text = render_list([_pantry_item("Milk", date(2026, 5, 30), 1)], today=date(2026, 5, 26))
    assert "#1 Milk" in text and "May 30" in text
    assert "no items" in render_list([], today=date(2026, 5, 26)).lower()
    stats = Stats(
        receipt_count=5,
        tracked_item_count=42,
        removed_item_count=2,
        cache_hit_percent=72.5,
        total_cost_micros_usd=92000,
        avg_cost_micros_usd=18400,
        unknown_cost_receipt_count=0,
        waste_rate_percent=18.2,
    )
    rendered_stats = render_stats(stats)
    assert "Receipts: 5" in rendered_stats
    assert "Tracked items: 42" in rendered_stats
    assert "72.5%" in rendered_stats
    assert "$0.092" in rendered_stats
    empty_stats = render_stats(
        Stats(
            receipt_count=0,
            tracked_item_count=0,
            removed_item_count=0,
            cache_hit_percent=None,
            total_cost_micros_usd=0,
            avg_cost_micros_usd=None,
            unknown_cost_receipt_count=0,
            waste_rate_percent=None,
        )
    )
    assert "-" in empty_stats
