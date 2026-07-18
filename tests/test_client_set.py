from types import SimpleNamespace

from app.client_set import PerUserClients, resolve


class _Selector:
    def __init__(self, mapping):
        self._mapping = mapping

    @property
    def available_providers(self):
        return tuple(sorted(self._mapping))

    def for_provider(self, provider):
        return self._mapping[provider]


class _BareFake:
    pass


def _user(provider="anthropic"):
    return SimpleNamespace(llm_provider=provider)


def test_resolve_routes_selectors_and_passes_bare_clients_through():
    anthropic = _BareFake()
    deepseek = _BareFake()
    selector = _Selector({"anthropic": anthropic, "deepseek": deepseek})

    assert resolve(selector, "deepseek") is deepseek
    assert resolve(anthropic, "deepseek") is anthropic
    assert resolve(None, "deepseek") is None


def test_capability_methods_resolve_with_the_users_provider():
    anthropic = _BareFake()
    deepseek = _BareFake()
    selector = _Selector({"anthropic": anthropic, "deepseek": deepseek})
    clients = PerUserClients.for_tests(text=selector, image=anthropic)

    assert clients.text(_user("deepseek")) is deepseek
    assert clients.text(_user("anthropic")) is anthropic
    assert clients.image(_user("deepseek")) is anthropic


def test_optional_capabilities_and_translation_preserve_existing_semantics():
    translation = _BareFake()
    clients = PerUserClients.for_tests(translation=translation)

    assert clients.search(_user()) is None
    assert clients.translation is translation


def test_available_text_providers_comes_from_selector_or_bare_fake_default():
    selector = _Selector({"deepseek": _BareFake(), "anthropic": _BareFake()})

    assert PerUserClients.for_tests(text=selector).available_text_providers == (
        "anthropic",
        "deepseek",
    )
    assert PerUserClients.for_tests(text=_BareFake()).available_text_providers == (
        "anthropic",
    )
