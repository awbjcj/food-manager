from datetime import date
from app.renderer import _fmt_date


def test_fmt_date_same_year_omits_year():
    assert _fmt_date(date(2026, 6, 2), today=date(2026, 5, 28)) == "Jun 2"


def test_fmt_date_different_year_shows_year():
    assert _fmt_date(date(2027, 6, 2), today=date(2026, 5, 28)) == "Jun 2 2027"


def test_fmt_date_dec_jan_boundary_shows_year_even_when_close():
    # 8 days out but next calendar year -> show year
    assert _fmt_date(date(2027, 1, 5), today=date(2026, 12, 28)) == "Jan 5 2027"
