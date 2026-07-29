from app.billing.meter import METERED_OPS, UNMETERED
from app.bot import _MESSAGE_COMMANDS
from app.callbacks import EXPECTED_CALLBACK_ROUTES


def test_every_dispatcher_entry_point_has_one_quota_classification():
    entry_points = (
        {name for name, _handler, _deps in _MESSAGE_COMMANDS}
        | set(EXPECTED_CALLBACK_ROUTES)
        | {"photo", "correct_reply", "nl_text"}
    )
    assert entry_points - set(METERED_OPS) - UNMETERED == set()
    assert set(METERED_OPS) & UNMETERED == set()
    assert set(METERED_OPS) | UNMETERED == entry_points
