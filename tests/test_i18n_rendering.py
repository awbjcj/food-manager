from datetime import date
from app.renderer import render_item_line, _fmt_date
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
