from datetime import date
from app.renderer import _fmt_date, _urgency_icon, _qty_prefix, render_item_line
from tests.test_renderer_commands import _pantry_item  # reuse builder


def test_fmt_date_same_year_omits_year():
    assert _fmt_date(date(2026, 6, 2), today=date(2026, 5, 28)) == "Jun 2"


def test_fmt_date_different_year_shows_year():
    assert _fmt_date(date(2027, 6, 2), today=date(2026, 5, 28)) == "Jun 2 2027"


def test_fmt_date_dec_jan_boundary_shows_year_even_when_close():
    # 8 days out but next calendar year -> show year
    assert _fmt_date(date(2027, 1, 5), today=date(2026, 12, 28)) == "Jan 5 2027"


def test_urgency_icon_thresholds():
    today = date(2026, 5, 28)
    assert _urgency_icon(date(2026, 5, 27), today=today) == "🔴"   # expired
    assert _urgency_icon(date(2026, 5, 28), today=today) == "🔴"   # today
    assert _urgency_icon(date(2026, 5, 30), today=today) == "🟡"   # within 3d
    assert _urgency_icon(date(2026, 6, 10), today=today) == "🟢"   # later


def test_qty_prefix_renders_qty_and_unit():
    assert _qty_prefix(2.0, "lb") == "2 lb "
    assert _qty_prefix(1.0, None) == ""          # qty 1 + no unit -> nothing
    assert _qty_prefix(3.0, None) == "3 "        # qty>1, no unit
    assert _qty_prefix(1.0, "gal") == "1 gal "


def test_render_item_line_combines_icon_qty_date():
    item = _pantry_item("Chicken", date(2026, 5, 29), 7)
    item.qty, item.unit = 2.0, "lb"
    line = render_item_line(item, today=date(2026, 5, 28))
    assert line == "🟡 #7 2 lb Chicken - May 29 (1d)"

    expired = render_item_line(_pantry_item("Old", date(2026, 5, 26), 3), today=date(2026, 5, 28))
    assert expired == "🔴 #3 Old - expired 2d"
    due = render_item_line(_pantry_item("Due", date(2026, 5, 28), 4), today=date(2026, 5, 28))
    assert due == "🔴 #4 Due - today"
