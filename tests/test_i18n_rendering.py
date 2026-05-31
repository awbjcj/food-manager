from datetime import date
from app.ingest_service import IngestSummary
from app.renderer import (
    render_ingest_reply,
    render_item_line,
    _fmt_date,
    render_digest,
    render_list,
    render_cook_result,
    render_shopping_list,
    render_favorites,
)
from tests.test_renderer_commands import _pantry_item


def test_item_line_en_unchanged():
    item = _pantry_item("Chicken", date(2026, 5, 29), 7)
    item.qty, item.unit = 2.0, "lb"
    assert render_item_line(item, today=date(2026, 5, 28)) == "🟡 #7 2 lb Chicken - May 29 (1d)"


def test_item_line_en_expired_and_today_unchanged():
    expired = render_item_line(_pantry_item("Old", date(2026, 5, 26), 3), today=date(2026, 5, 28))
    assert expired == "🔴 #3 Old - expired 2d"
    due = render_item_line(_pantry_item("Due", date(2026, 5, 28), 4), today=date(2026, 5, 28))
    assert due == "🔴 #4 Due - today"


def test_item_line_zh_translates_name_and_tail():
    item = _pantry_item("Chicken", date(2026, 5, 26), 7)  # expired 2d
    line = render_item_line(item, today=date(2026, 5, 28), lang="zh", names={"Chicken": "鸡肉"})
    assert "鸡肉" in line
    assert "已过期 2天" in line


def test_fmt_date_still_importable_en():
    assert _fmt_date(date(2026, 6, 2), today=date(2026, 5, 28)) == "Jun 2"


def test_digest_and_list_en_unchanged():
    today = date(2026, 5, 28)
    item = _pantry_item("Milk", date(2026, 5, 29), 1)
    item.category = "dairy"
    assert "Dairy (1)" in render_list([item], today=today)
    assert "Pantry digest -" in render_digest([item], today=today).text


def test_digest_zh_headers_and_names():
    today = date(2026, 5, 28)
    item = _pantry_item("Milk", date(2026, 5, 28), 1)  # due today
    item.category = "dairy"
    out = render_digest([item], today=today, lang="zh", names={"Milk": "牛奶"})
    assert "今天" in out.text       # section "Today" -> zh
    assert "牛奶" in out.text       # translated name


def test_list_zh_category_header_and_name():
    today = date(2026, 5, 28)
    item = _pantry_item("Milk", date(2026, 5, 29), 1)
    item.category = "dairy"
    out = render_list([item], today=today, lang="zh", names={"Milk": "牛奶"})
    assert "乳制品" in out           # category "Dairy" -> zh
    assert "牛奶" in out


# ---------------------------------------------------------------------------
# render_ingest_reply i18n tests
# ---------------------------------------------------------------------------

def _ingest_summary(**kwargs) -> IngestSummary:
    """Build a minimal IngestSummary; override fields via kwargs."""
    values: dict = dict(
        receipt_id=1,
        inserted_food_count=0,
        inserted_item_ids=[],
        inserted_item_names=[],
        inserted_item_expires_on=[],
        inserted_item_shelf_life_days=[],
        skipped_non_food_count=0,
        skipped_low_confidence_count=0,
        skipped_low_confidence_names=[],
        low_confidence_inserted_ids=[],
        skipped_excluded_count=0,
        skipped_excluded_names=[],
        purchase_date=date(2026, 5, 26),
        purchase_date_assumed=False,
        cost_micros_usd=None,
    )
    values.update(kwargs)
    return IngestSummary(**values)  # type: ignore[arg-type]


def test_ingest_reply_en_empty_receipt_unchanged():
    """en path: empty receipt produces the exact legacy strings."""
    summary = _ingest_summary(cost_micros_usd=None)
    text = render_ingest_reply(summary, today=date(2026, 5, 26))
    assert "No food items found in this receipt." in text
    assert "Cost: unavailable" in text


def test_ingest_reply_en_empty_receipt_with_cost_unchanged():
    """en path: cost line is byte-identical to legacy."""
    summary = _ingest_summary(cost_micros_usd=18000)
    text = render_ingest_reply(summary, today=date(2026, 5, 26))
    assert "Cost: $0.018" in text


def test_ingest_reply_zh_empty_receipt():
    """zh path: empty receipt renders Chinese strings."""
    summary = _ingest_summary(cost_micros_usd=None)
    text = render_ingest_reply(summary, today=date(2026, 5, 26), lang="zh")
    assert "此收据中未找到食品。" in text
    assert "费用：不可用" in text


def test_ingest_reply_en_logged_items_unchanged():
    """en path: logged items block is byte-identical to legacy."""
    summary = _ingest_summary(
        inserted_food_count=1,
        inserted_item_ids=[42],
        inserted_item_names=["Whole Milk"],
        inserted_item_expires_on=[date(2026, 6, 2)],
        inserted_item_shelf_life_days=[7],
        cost_micros_usd=18000,
    )
    text = render_ingest_reply(summary, today=date(2026, 5, 26))
    assert "Logged 1 items from this receipt:" in text
    assert "  - #42 Whole Milk - exp Jun 2 (7d)" in text
    assert "Cost: $0.018" in text


def test_ingest_reply_en_refined_mark_unchanged():
    """en path: refined mark is the exact legacy ' ✓refined' string."""
    summary = _ingest_summary(
        inserted_food_count=1,
        inserted_item_ids=[42],
        inserted_item_names=["Whole Milk"],
        inserted_item_expires_on=[date(2026, 6, 2)],
        inserted_item_shelf_life_days=[7],
        cost_micros_usd=None,
    )
    text = render_ingest_reply(summary, today=date(2026, 5, 26), refined_ids={42})
    assert "  - #42 Whole Milk - exp Jun 2 (7d) ✓refined" in text


def test_ingest_reply_zh_logged_items_with_names():
    """zh path: translated item name appears, zh date suffix used."""
    summary = _ingest_summary(
        inserted_food_count=1,
        inserted_item_ids=[42],
        inserted_item_names=["Whole Milk"],
        inserted_item_expires_on=[date(2026, 6, 2)],
        inserted_item_shelf_life_days=[7],
        cost_micros_usd=None,
    )
    text = render_ingest_reply(
        summary,
        today=date(2026, 5, 26),
        lang="zh",
        names={"Whole Milk": "全脂牛奶"},
    )
    assert "已从此收据记录 1 项：" in text
    assert "全脂牛奶" in text
    assert "到期" in text        # zh date prefix in item line
    assert "费用：不可用" in text


# ---------------------------------------------------------------------------
# cook / shopping / favorites i18n tests (Task 14)
# ---------------------------------------------------------------------------


def test_cook_none_en_unchanged():
    assert render_cook_result([], show_alternatives=False) == \
        "Couldn't find a recipe that fits your pantry and restrictions."


def test_cook_none_zh():
    out = render_cook_result([], show_alternatives=False, lang="zh")
    assert out == "找不到符合您储藏和限制的食谱。"


def test_shopping_empty_en_unchanged():
    assert render_shopping_list([]) == \
        "Your shopping list is empty. Tap ➕ Shopping list on a /cook result."


def test_favorites_empty_en_unchanged():
    assert render_favorites([]) == \
        "No saved recipes yet. Tap ★ Save on a /cook result."


def test_shopping_empty_zh():
    out = render_shopping_list([], lang="zh")
    assert "购物清单" in out


def test_favorites_empty_zh():
    out = render_favorites([], lang="zh")
    assert "保存的食谱" in out
