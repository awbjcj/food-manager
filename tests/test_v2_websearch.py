from app.refine_service import ShelfLifeSearchResult, resolve_search_days, SEARCH_MIN_CONFIDENCE


def test_resolve_accepts_confident_in_range():
    r = ShelfLifeSearchResult(days=14, confidence=0.9, cost_micros_usd=500)
    assert resolve_search_days(r) == 14


def test_resolve_rejects_low_confidence():
    r = ShelfLifeSearchResult(days=14, confidence=SEARCH_MIN_CONFIDENCE - 0.01, cost_micros_usd=500)
    assert resolve_search_days(r) is None


def test_resolve_rejects_out_of_range_or_missing():
    assert resolve_search_days(ShelfLifeSearchResult(days=None, confidence=0.9, cost_micros_usd=0)) is None
    assert resolve_search_days(ShelfLifeSearchResult(days=0, confidence=0.9, cost_micros_usd=0)) is None
    assert resolve_search_days(ShelfLifeSearchResult(days=999, confidence=0.9, cost_micros_usd=0)) is None
