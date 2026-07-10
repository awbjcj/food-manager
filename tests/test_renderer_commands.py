from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.commands import (
    CallbackAction,
    CommandError,
    ItemAction,
    parse_callback,
    parse_correct_reply_marker,
    parse_digest_at,
    parse_item_callback,
    parse_item_id_arg,
    parse_llm_provider,
    parse_list_filter,
    parse_pantry_arg,
    parse_snooze_args,
    parse_tz,
)
from app.cook.models import NutritionScore, RecipeCandidate, RecipeIngredient, ScoredCandidate
from app.ingest_service import IngestSummary
from app.models import PantryItem
from app.pantry_service import ListFilter, Stats
from app.renderer import (
    build_correct_menu_keyboard,
    build_digest_keyboard,
    build_item_card_keyboard,
    build_remove_confirm_keyboard,
    render_correct_menu,
    render_digest,
    render_ingest_reply,
    render_item_card,
    render_list,
    render_remove_confirm,
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
    assert parse_list_filter([]) == ListFilter.default()
    assert parse_list_filter(["dairy"]) == ListFilter(category="dairy")
    assert parse_list_filter(["week"]) == ListFilter(window="week")
    assert parse_llm_provider([]) is None
    assert parse_llm_provider(["OpenAI"]) == "openai"
    with pytest.raises(CommandError):
        parse_list_filter(["unknownthing"])
    with pytest.raises(CommandError):
        parse_llm_provider(["local"])


def test_callback_parser():
    assert parse_callback("act:ate:42") == CallbackAction(verb="ate", item_id=42)
    assert parse_callback("act:toss:5") == CallbackAction(verb="toss", item_id=5)
    assert parse_callback("act:snooze2:9") == CallbackAction(verb="snooze2", item_id=9)
    assert parse_callback("act:ate:42:all") == CallbackAction(
        verb="ate",
        item_id=42,
        back_to="all",
    )
    assert parse_callback("show:all") == CallbackAction(verb="show_all", item_id=None)
    for bad in ("act:nope:1", "act:ate:42:unknown"):
        with pytest.raises(CommandError):
            parse_callback(bad)


def test_parse_item_callback_kinds():
    assert parse_item_callback("item:open:3") == ItemAction(kind="open", item_id=3)
    assert parse_item_callback("item:corr:3") == ItemAction(kind="corr", item_id=3)
    assert parse_item_callback("item:ctext:3") == ItemAction(kind="ctext", item_id=3)
    assert parse_item_callback("item:rm:3") == ItemAction(kind="rm", item_id=3)
    assert parse_item_callback("item:rmok:3") == ItemAction(kind="rmok", item_id=3)
    assert parse_item_callback("item:list") == ItemAction(kind="list", item_id=None)
    assert parse_item_callback("item:nudge:3:p7") == ItemAction(
        kind="nudge",
        item_id=3,
        nudge_code="p7",
    )


def test_parse_item_callback_back_to_origin():
    assert parse_item_callback("item:list").back_to == "digest"
    assert parse_item_callback("item:list:all") == ItemAction(kind="list", back_to="all")
    assert parse_item_callback("item:open:5").back_to == "digest"
    assert parse_item_callback("item:open:5:all") == ItemAction(
        kind="open",
        item_id=5,
        back_to="all",
    )


def test_parse_item_callback_rejects_bad_back_to_origin():
    for bad in ("item:list:unknown", "item:open:5:unknown", "item:corr:5:all"):
        with pytest.raises(CommandError):
            parse_item_callback(bad)


def test_parse_pantry_arg():
    assert parse_pantry_arg([]) == "all"
    assert parse_pantry_arg(["digest"]) == "digest"
    assert parse_pantry_arg(["5"]) == 5
    assert parse_pantry_arg(["#42"]) == 42
    for bad in (["unknown"], ["digest", "extra"]):
        with pytest.raises(CommandError):
            parse_pantry_arg(bad)


def test_parse_item_callback_rejects_bad():
    for bad in (
        "item:open:x",
        "item:nudge:3:zz",
        "item:nudge:3",
        "item:bogus:3",
        "act:ate:3",
        "item:",
    ):
        with pytest.raises(CommandError):
            parse_item_callback(bad)


def test_parse_correct_reply_marker():
    assert parse_correct_reply_marker("fix #3 spinach [correct:#3]") == 3
    assert parse_correct_reply_marker("no marker here") is None
    assert parse_correct_reply_marker(None) is None


def _summary(**kwargs) -> IngestSummary:
    values: dict = dict(
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
    return IngestSummary(**values)  # type: ignore[arg-type]


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
        household_id=1,
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


def _digest_item(item_id, name, expires_on, storage="default"):
    return SimpleNamespace(
        id=item_id,
        raw_name=name,
        expires_on=expires_on,
        storage=storage,
        qty=1,
        unit=None,
    )


def test_digest_keyboard_emits_labeled_open_buttons():
    today = date(2026, 6, 9)
    items = [
        _digest_item(3, "spinach", date(2026, 6, 7)),
        _digest_item(7, "milk", date(2026, 6, 8)),
    ]
    rows = build_digest_keyboard(items, has_more=False, today=today, lang="en")
    assert rows[0][0].callback_data == "item:open:3"
    assert rows[0][0].text == "🔴 #3 spinach"
    assert rows[0][1].callback_data == "item:open:7"


def test_digest_keyboard_can_return_to_full_pantry():
    today = date(2026, 6, 9)
    rows = build_digest_keyboard(
        [_digest_item(5, "milk", date(2026, 6, 10))],
        has_more=False,
        today=today,
        back_to="all",
    )
    assert rows[0][0].callback_data == "item:open:5:all"


def test_digest_keyboard_show_all_when_more():
    today = date(2026, 6, 9)
    rows = build_digest_keyboard(
        [_digest_item(1, "a", date(2026, 6, 10))],
        has_more=True,
        today=today,
        lang="en",
    )
    assert rows[-1][0].callback_data == "show:all"


def test_render_digest_cap_none_shows_all():
    today = date(2026, 6, 9)
    items = [_digest_item(i, f"x{i}", date(2026, 6, 10)) for i in range(15)]
    capped = render_digest(items, today=today)
    assert capped.has_more is True and len(capped.rendered_items) == 10
    full = render_digest(items, today=today, cap=None)
    assert full.has_more is False and len(full.rendered_items) == 15


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
    assert "🔴 #41 Spinach" in rendered.text
    assert "Today (2)" in rendered.text
    assert "Tomorrow (1)" in rendered.text
    assert "This week (1)" in rendered.text
    assert "🔴 #42 Whole Milk 1 gal - today" in rendered.text
    assert "🟡 #44 Sliced Bread - May 28 (1d)" in rendered.text
    keyboard = build_digest_keyboard([items[1]], has_more=False, today=today)
    assert keyboard[0][0].callback_data == "item:open:42"
    assert keyboard[0][0].text == "🔴 #42 Whole Milk 1 gal"


def test_render_digest_truncates_at_10():
    today = date(2026, 5, 27)
    rendered = render_digest(
        [_pantry_item(f"Item {i}", today, 100 + i) for i in range(25)],
        today=today,
    )
    assert rendered.rendered_count == 10
    assert "15 more" in rendered.text
    assert len(build_digest_keyboard(rendered.rendered_items, has_more=True, today=today)) == 6


def _card_item(item_id=3, name="spinach", storage="default", days=7):
    return SimpleNamespace(
        id=item_id,
        raw_name=name,
        storage=storage,
        qty=1,
        unit=None,
        shelf_life_days=days,
        expires_on=date(2026, 6, 9),
    )


def test_card_keyboard_has_all_actions_when_not_frozen():
    rows = build_item_card_keyboard(_card_item(), lang="en")
    datas = [button.callback_data for row in rows for button in row]
    assert "act:ate:3" in datas
    assert "act:toss:3" in datas
    assert "act:snooze2:3" in datas
    assert "act:freeze:3" in datas
    assert "item:corr:3" in datas
    assert "item:rm:3" in datas
    assert "item:list" in datas


def test_card_keyboard_can_return_to_full_pantry():
    rows = build_item_card_keyboard(_card_item(item_id=5), lang="en", back_to="all")
    datas = [button.callback_data for row in rows for button in row]
    assert "act:ate:5:all" in datas
    assert "act:toss:5:all" in datas
    assert "act:snooze2:5:all" in datas
    assert "act:freeze:5:all" in datas
    assert rows[-1][0].callback_data == "item:list:all"


def test_card_keyboard_hides_freeze_when_frozen():
    rows = build_item_card_keyboard(_card_item(storage="frozen"), lang="en")
    datas = [button.callback_data for row in rows for button in row]
    assert "act:freeze:3" not in datas
    assert "act:ate:3" in datas


def test_render_item_card_reuses_item_line_shape():
    text = render_item_card(_card_item(), today=date(2026, 6, 9), lang="en")
    assert text == "🔴 #3 spinach - today"


def test_correct_menu_keyboard_and_header():
    rows = build_correct_menu_keyboard(3, lang="en")
    datas = [button.callback_data for row in rows for button in row]
    assert datas[:4] == [
        "item:nudge:3:p7",
        "item:nudge:3:p3",
        "item:nudge:3:m3",
        "item:nudge:3:today",
    ]
    assert "item:ctext:3" in datas
    assert "item:open:3" in datas
    header = render_correct_menu(_card_item(days=7), today=date(2026, 6, 9), lang="en")
    assert header.startswith("✏️ Correct #3 spinach")
    assert "shelf life 7d" in header


def test_remove_confirm():
    rows = build_remove_confirm_keyboard(3, lang="en")
    datas = [button.callback_data for row in rows for button in row]
    assert "item:rmok:3" in datas
    assert "item:open:3" in datas
    assert render_remove_confirm(_card_item(), lang="en") == (
        "Remove #3 spinach?\nThis can't be undone here."
    )


def test_render_list_and_stats():
    text = render_list([_pantry_item("Milk", date(2026, 5, 30), 1)], today=date(2026, 5, 26))
    assert "#1 Milk" in text and "May 30" in text
    assert "Other (1)" in text
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


def _plan_candidate(title, *, cuisine="italian", minutes=20):
    rec = RecipeCandidate(
        title=title, cuisine=cuisine, source_url="https://x",
        ingredients=[RecipeIngredient(name="pasta")], method_gist="boil",
        deliciousness=0.7,
    )
    nut = NutritionScore(health_score=80, effort="easy", est_minutes=minutes, rationale="ok")
    return ScoredCandidate(recipe=rec, nutrition=nut, expiry_use=0.5, final_score=0.7)


def test_render_plan_shows_header_days_and_fire_flag():
    from app.renderer import render_plan

    rows = [
        (date(2026, 7, 9), _plan_candidate("Yogurt Bowl", minutes=20), True),
        (date(2026, 7, 10), _plan_candidate("Pasta", minutes=30), False),
    ]
    text = render_plan(rows)
    assert text == (
        "🗓 Dinner plan — 2 days\n"
        "Thu: Yogurt Bowl (italian, 20m)🔥\n"
        "Fri: Pasta (italian, 30m)"
    )


def test_parse_plan_arg():
    from app.commands import parse_plan_arg

    assert parse_plan_arg([]) == 5
    assert parse_plan_arg(["3"]) == 3
    assert parse_plan_arg(["7"]) == 7
    for bad in (["2"], ["8"], ["x"], ["3", "4"]):
        with pytest.raises(CommandError):
            parse_plan_arg(bad)


def test_parse_plan_callbacks():
    swap = parse_callback("plan:swap:5:2")
    assert swap.verb == "plan_swap" and swap.item_id == 5 and swap.option_index == 2
    shop = parse_callback("plan:shop:5")
    assert shop.verb == "plan_shop" and shop.item_id == 5
    cancel = parse_callback("plan:cancel:5")
    assert cancel.verb == "plan_cancel" and cancel.item_id == 5
    for bad in ("plan:swap:5", "plan:swap:5:x", "plan:bogus:5", "plan:shop:x"):
        with pytest.raises(CommandError):
            parse_callback(bad)


def test_build_plan_keyboard_emits_swap_shop_cancel():
    from app.renderer import build_plan_keyboard

    day_rows = [(0, date(2026, 7, 9)), (1, date(2026, 7, 10))]
    rows = build_plan_keyboard(5, day_rows)
    datas = [b.callback_data for row in rows for b in row]
    assert datas == ["plan:swap:5:0", "plan:swap:5:1", "plan:shop:5", "plan:cancel:5"]


def test_to_aiogram_keyboard_maps_url_button():
    from app.bot import to_aiogram_keyboard
    from app.renderer import CallbackButton

    rows = [[CallbackButton(text="x", url="https://e.com")]]
    keyboard = to_aiogram_keyboard(rows)
    button = keyboard.inline_keyboard[0][0]
    assert button.url == "https://e.com"
    assert button.callback_data is None


def test_nl_picker_keyboard_opens_item_cards():
    from app.renderer import build_nl_picker_keyboard

    items = [
        SimpleNamespace(id=3, raw_name="Whole Milk"),
        SimpleNamespace(id=9, raw_name="Oat Milk"),
    ]
    rows = build_nl_picker_keyboard(items)
    assert [b.callback_data for row in rows for b in row] == [
        "item:open:3",
        "item:open:9",
    ]
    assert rows[0][0].text == "#3 Whole Milk"
