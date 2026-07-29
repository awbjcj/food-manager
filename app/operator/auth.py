from __future__ import annotations

import logging

log = logging.getLogger(__name__)
OPERATOR_IDS: frozenset[int] = frozenset()


def is_operator(telegram_id: int) -> bool:
    return telegram_id in OPERATOR_IDS


async def require_operator(msg) -> bool:
    sender = getattr(getattr(msg, "from_user", None), "id", None)
    if sender is not None and is_operator(sender):
        return True
    log.warning("operator_access_denied", extra={"telegram_id": sender})
    return False
