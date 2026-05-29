from app.llm import ParsedItem


def test_parsed_item_defaults_track_worthy_true():
    item = ParsedItem(
        is_food=True, name="Whole Milk", est_shelf_life_days=7, confidence=0.9
    )
    assert item.track_worthy is True
    assert item.exclusion_reason is None


def test_parsed_item_can_be_excluded():
    item = ParsedItem(
        is_food=True, name="Ketchup", est_shelf_life_days=365, confidence=0.9,
        track_worthy=False, exclusion_reason="shelf_stable",
    )
    assert item.track_worthy is False
    assert item.exclusion_reason == "shelf_stable"
