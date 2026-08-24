from app import bot as bot_mod
from app import handler_support
from app.billing import meter
from app.client_set import PerUserClients
from app.handlers.billing import COMMANDS as BILLING_COMMANDS
from app.operator.bot import OPERATOR_COMMANDS


def test_registration_and_billing_default_closed():
    assert handler_support.OPEN_REGISTRATION is False
    assert bot_mod.OPEN_REGISTRATION is False
    assert meter.BILLING_ENABLED is False


def test_customer_commands_include_all_billing_surfaces():
    assert {name for name, _handler, _deps in BILLING_COMMANDS} == {
        "quota",
        "buy",
        "billing",
    }


def test_operator_commands_are_complete():
    assert {name for name, _handler, _deps, _usage in OPERATOR_COMMANDS} == {
        "whois",
        "grant",
        "refund",
        "ban",
        "unban",
        "revenue",
        "reconcile",
        "providers",
        "provider",
    }


def test_ingest_has_a_pinned_provider_accessor():
    assert hasattr(PerUserClients, "image_for_ingest")
