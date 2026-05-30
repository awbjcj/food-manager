from app.bot import HELP_TEXT


def test_help_mentions_shopping_and_favorites():
    assert "/shopping" in HELP_TEXT
    assert "/favorites" in HELP_TEXT
