from datetime import date, date as _date
from types import SimpleNamespace
from app.correction_service import AddPayload, CorrectPayload
from app.ingest_service import IngestSummary
from app.pantry_service import Stats, UndoResult
from app.profile_service import FoodProfile
from app.renderer import (
    _fmt_date,
    build_apply_cancel_keyboard,
    build_cook_alternatives_keyboard,
    build_cook_result_keyboard,
    build_digest_keyboard,
    build_favorites_keyboard,
    build_shopping_keyboard,
    build_undo_add_keyboard,
    build_undo_keyboard,
    render_add_diff,
    render_applied_add,
    render_applied_correction,
    render_correction_diff,
    render_cook_result,
    render_digest,
    render_favorites,
    render_ingest_reply,
    render_item_line,
    render_list,
    render_profile,
    render_shopping_list,
    render_stats,
    render_terminal_state,
    render_undo_result,
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


def test_list_unknown_category_falls_back_without_crash():
    # A category outside CATEGORY_ORDER has no catalog key; render_list must not
    # raise KeyError (the old cat.capitalize() path was total).
    today = date(2026, 5, 28)
    item = _pantry_item("Mystery", date(2026, 5, 29), 1)
    item.category = "condiment"
    out = render_list([item], today=today, lang="zh")
    assert "Condiment (1)" in out


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
    text = render_ingest_reply(summary, today=date(2026, 5, 26), refined_ids=frozenset({42}))
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


# ---------------------------------------------------------------------------
# Task 15: proposals, stats, profile, buttons
# ---------------------------------------------------------------------------


# --- render_terminal_state ---

def test_terminal_state_en_unchanged():
    assert render_terminal_state("cancelled") == "Cancelled."
    assert render_terminal_state("expired") == "This proposal has expired - re-run the command."
    assert render_terminal_state("stale") == "This proposal is stale (the item changed) - re-run the command."
    assert render_terminal_state("applied") == "This proposal was already applied."
    assert render_terminal_state("nonsense") == "This proposal is no longer pending (nonsense)."


def test_terminal_state_zh():
    assert render_terminal_state("cancelled", lang="zh") == "已取消。"
    assert render_terminal_state("nonsense", lang="zh") == "此提案不再处于待处理状态（nonsense）。"


# --- build_digest_keyboard ---

def _kb_item(item_id=42, name="milk"):
    return SimpleNamespace(
        id=item_id,
        raw_name=name,
        expires_on=date(2026, 6, 10),
        storage="default",
        qty=1,
        unit=None,
    )


def test_digest_keyboard_en_open_button_label():
    rows = build_digest_keyboard([_kb_item()], has_more=False, today=date(2026, 6, 9))
    assert rows[0][0].text == "🟡 #42 milk"


def test_digest_keyboard_en_show_all_unchanged():
    rows = build_digest_keyboard([_kb_item()], has_more=True, today=date(2026, 6, 9))
    assert rows[-1][0].text == "show all"


def test_digest_keyboard_callback_data_opens_card():
    rows = build_digest_keyboard([_kb_item()], has_more=True, today=date(2026, 6, 9))
    assert rows[0][0].callback_data == "item:open:42"
    assert rows[1][0].callback_data == "show:all"


def test_digest_keyboard_zh_button_labels():
    rows = build_digest_keyboard(
        [_kb_item(name="Milk")],
        has_more=False,
        today=date(2026, 6, 9),
        lang="zh",
        names={"Milk": "牛奶"},
    )
    assert rows[0][0].text == "🟡 #42 牛奶"


def test_digest_keyboard_zh_show_all():
    rows = build_digest_keyboard(
        [_kb_item()],
        has_more=True,
        today=date(2026, 6, 9),
        lang="zh",
    )
    assert rows[-1][0].text == "显示全部"


# --- build_undo_keyboard / build_undo_add_keyboard ---

def test_undo_keyboards_en_unchanged():
    assert build_undo_keyboard(receipt_id=12)[0][0].text == "Undo"
    assert build_undo_add_keyboard(item_id=7)[0][0].text == "Undo"


def test_undo_keyboards_callback_data_unchanged():
    assert build_undo_keyboard(receipt_id=12)[0][0].callback_data == "undo:receipt:12"
    assert build_undo_add_keyboard(item_id=7)[0][0].callback_data == "undo:add:7"


def test_undo_keyboards_zh():
    assert build_undo_keyboard(receipt_id=12, lang="zh")[0][0].text == "撤销"
    assert build_undo_add_keyboard(item_id=7, lang="zh")[0][0].text == "撤销"


# --- build_apply_cancel_keyboard ---

def test_apply_cancel_keyboard_en_unchanged():
    row = build_apply_cancel_keyboard(pending_id=99)[0]
    assert row[0].text == "Apply" and row[0].callback_data == "apply:99"
    assert row[1].text == "Cancel" and row[1].callback_data == "cancel:99"


def test_apply_cancel_keyboard_zh():
    row = build_apply_cancel_keyboard(pending_id=99, lang="zh")[0]
    assert row[0].text == "应用"
    assert row[1].text == "取消"


# --- build_cook_alternatives_keyboard ---

def test_cook_alternatives_keyboard_en_unchanged():
    rows = build_cook_alternatives_keyboard(7)
    assert rows[0][0].text == "Show alternatives"
    assert rows[0][0].callback_data == "cookalt:7"


def test_cook_alternatives_keyboard_zh():
    rows = build_cook_alternatives_keyboard(7, lang="zh")
    assert rows[0][0].text == "显示替代方案"
    assert rows[0][0].callback_data == "cookalt:7"


# --- build_cook_result_keyboard ---

def test_cook_result_keyboard_en_unchanged():
    rows = build_cook_result_keyboard(5, has_alternatives=False)
    texts = [b.text for row in rows for b in row]
    assert "👍 Liked" in texts
    assert "👎 Not for me" in texts
    assert "★ Save" in texts
    assert "➕ Shopping list" in texts
    assert "Show alternatives" not in texts


def test_cook_result_keyboard_en_with_alternatives():
    rows = build_cook_result_keyboard(5, has_alternatives=True)
    texts = [b.text for row in rows for b in row]
    assert "Show alternatives" in texts


def test_cook_result_keyboard_callback_data_unchanged():
    rows = build_cook_result_keyboard(5, has_alternatives=True)
    data = {b.callback_data for row in rows for b in row}
    assert "cookfb:5:liked" in data
    assert "cookfb:5:disliked" in data
    assert "cooksave:5" in data
    assert "cookshop:5" in data
    assert "cookalt:5" in data


def test_cook_result_keyboard_zh():
    rows = build_cook_result_keyboard(5, has_alternatives=False, lang="zh")
    texts = [b.text for row in rows for b in row]
    assert "👍 喜欢" in texts
    assert "👎 不喜欢" in texts


# --- build_favorites_keyboard ---

def test_favorites_keyboard_en_unchanged():
    rows = build_favorites_keyboard([3])
    assert rows[0][0].text == "Cook this again"
    assert rows[0][0].callback_data == "favcook:3"


def test_favorites_keyboard_zh():
    rows = build_favorites_keyboard([3], lang="zh")
    assert rows[0][0].text == "再做一次"
    assert rows[0][0].callback_data == "favcook:3"


# --- build_shopping_keyboard ---

def test_shopping_keyboard_en_unchanged():
    rows = build_shopping_keyboard([10])
    assert rows[0][0].text == "Bought ✓"
    assert rows[0][0].callback_data == "shopdone:10"


def test_shopping_keyboard_zh():
    rows = build_shopping_keyboard([10], lang="zh")
    assert rows[0][0].text == "已购买 ✓"
    assert rows[0][0].callback_data == "shopdone:10"


# --- render_correction_diff ---

def _correct_payload():
    return CorrectPayload(
        diff={
            "name": {"old": "Milk", "new": "Heavy Cream"},
            "expires_on": {"old": "2026-06-02", "new": "2026-06-05"},
            "shelf_life_days": {"old": 7, "new": 10},
        },
        cache_action="move",
        rationale="user clarified",
        confidence=0.9,
        back_computed_days=True,
    )


def test_correction_diff_en_unchanged():
    text = render_correction_diff(
        pending_id=1, payload=_correct_payload(), item_id=42, item_raw_name="Milk"
    )
    assert text.startswith("Proposed correction for #42 Milk:")
    assert "  - name: Milk -> Heavy Cream" in text
    assert "  (back-computed from expires_on)" in text
    assert "  - cache: move" in text
    assert "Reason: user clarified" in text
    assert "Expires in 10 min." in text


def test_correction_diff_zh():
    text = render_correction_diff(
        pending_id=1, payload=_correct_payload(), item_id=42, item_raw_name="Milk",
        lang="zh"
    )
    assert "#42 Milk" in text
    assert "建议更正" in text
    assert "原因" in text
    assert "10 分钟后过期" in text


# --- render_add_diff ---

def _add_payload():
    return AddPayload(
        name="Oat Milk",
        category="beverage",
        qty=0.5,
        unit="gal",
        shelf_life_days=10,
        expires_on=_date(2026, 6, 6),
        shelf_life_source="user_correction",
        ingest_shelf_life_source="manual_user_hint",
        explicit_user_expiry=True,
        estimated_shelf_life_days=10,
        confidence=0.88,
    )


def test_add_diff_en_unchanged():
    text = render_add_diff(pending_id=1, payload=_add_payload())
    assert text.startswith("Proposed add - Oat Milk:")
    assert "  - category: beverage" in text
    assert "  - qty / unit: 0.5 gal" in text
    assert "  - expires_on: 2026-06-06" in text
    assert "  - shelf_life_days: 10 (source: user_correction)" in text
    assert "Confidence: 0.88" in text
    assert "Expires in 10 min." in text


def test_add_diff_zh():
    text = render_add_diff(pending_id=1, payload=_add_payload(), lang="zh")
    assert "Oat Milk" in text
    assert "建议添加" in text
    assert "置信度" in text
    assert "10 分钟后过期" in text


# --- render_undo_result ---

def test_undo_result_en_unchanged():
    assert render_undo_result(UndoResult([], [], False, expired=True)) == \
        "Undo window expired (10 min) - use /delete <id> instead."
    assert render_undo_result(UndoResult([], [], False, expired=False)) == \
        "Nothing to undo."
    full = render_undo_result(UndoResult([1, 2], [], True, expired=False))
    assert full == "Undone: removed 2 item(s)."
    partial = render_undo_result(UndoResult([1], [(3, "eaten")], False, expired=False))
    assert partial == "Undone: removed 1 item(s). skipped #3 (eaten)."


def test_undo_result_zh():
    assert "撤销" in render_undo_result(UndoResult([], [], False, expired=True), lang="zh")
    assert "没有可撤销" in render_undo_result(UndoResult([], [], False, expired=False), lang="zh")
    assert "已撤销" in render_undo_result(UndoResult([1], [], True, expired=False), lang="zh")


# --- render_applied_correction ---

def test_applied_correction_en_unchanged():
    text = render_applied_correction(item_id=42, payload=_correct_payload())
    assert text == "Applied to #42: name=Heavy Cream, expires_on=2026-06-05, shelf_life_days=10"


def test_applied_correction_no_changes_en_unchanged():
    payload = CorrectPayload(diff={}, cache_action="leave", rationale="x", confidence=1.0)
    text = render_applied_correction(item_id=5, payload=payload)
    assert text == "Applied to #5: no changes"


def test_applied_correction_zh():
    text = render_applied_correction(item_id=42, payload=_correct_payload(), lang="zh")
    assert "已应用至 #42" in text


def test_applied_correction_no_changes_zh():
    payload = CorrectPayload(diff={}, cache_action="leave", rationale="x", confidence=1.0)
    text = render_applied_correction(item_id=5, payload=payload, lang="zh")
    assert "无更改" in text


# --- render_applied_add ---

def test_applied_add_en_unchanged():
    text = render_applied_add(item_id=99, payload=_add_payload())
    assert text == "Added #99 Oat Milk (expires 2026-06-06)"


def test_applied_add_zh():
    text = render_applied_add(item_id=99, payload=_add_payload(), lang="zh")
    assert "已添加 #99 Oat Milk" in text


# --- render_stats ---

def _make_stats(**kwargs):
    defaults = dict(
        receipt_count=5,
        tracked_item_count=42,
        removed_item_count=2,
        cache_hit_percent=72.5,
        total_cost_micros_usd=92000,
        avg_cost_micros_usd=18400,
        unknown_cost_receipt_count=0,
        waste_rate_percent=18.2,
    )
    defaults.update(kwargs)
    return Stats(**defaults)  # type: ignore[arg-type]


def test_render_stats_en_unchanged():
    text = render_stats(_make_stats())
    assert text.startswith("Last 30 days")
    assert "Receipts: 5 (unknown-cost: 0)" in text
    assert "Tracked items: 42" in text
    assert "Removed (wrong import): 2" in text
    assert "Cache hit rate: 72.5%" in text
    assert "LLM spend: total $0.092  avg $0.018 / receipt" in text
    assert "Waste rate: 18.2%" in text


def test_render_stats_zh():
    text = render_stats(_make_stats(), lang="zh")
    assert "最近 30 天" in text
    assert "已跟踪项目：42" in text
    assert "浪费率" in text


# --- render_profile ---

def _make_profile(**kwargs):
    defaults = dict(
        diet="vegetarian",
        exclusions=["peanut"],
        preferred_cuisines=["chinese"],
        max_cook_minutes=30,
        household_size=2,
        note="spicy ok",
    )
    defaults.update(kwargs)
    return FoodProfile(**defaults)  # type: ignore[arg-type]


def test_render_profile_en_unchanged():
    text = render_profile(_make_profile())
    assert text.startswith("Your food profile:\n")
    assert "  Diet: vegetarian\n" in text
    assert "  Avoid: peanut\n" in text
    assert "  Cuisines: chinese\n" in text
    assert "  Max cook time: 30 min\n" in text
    assert "  Household size: 2\n" in text
    assert "  Notes: spicy ok\n" in text
    assert "Update by typing: /prefs <sentence>  (e.g. /prefs I'm vegan, no peanuts)" in text


def test_render_profile_en_no_limit_and_none_note_unchanged():
    text = render_profile(FoodProfile())
    assert "  Max cook time: no limit\n" in text
    assert "  Avoid: none\n" in text
    assert "  Cuisines: any\n" in text
    assert "  Notes: (none)\n" in text


def test_render_profile_zh():
    text = render_profile(_make_profile(), lang="zh")
    assert "您的饮食档案：" in text
    assert "饮食：vegetarian" in text
    assert "避免：peanut" in text


def test_render_profile_zh_no_limit():
    text = render_profile(FoodProfile(), lang="zh")
    assert "无限制" in text
    assert "（无）" in text
