from datetime import date
from app.renderer import render_item_line, _fmt_date, render_digest, render_list
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
