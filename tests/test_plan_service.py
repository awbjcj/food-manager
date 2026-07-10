def test_plan_cost_ceiling_setting(monkeypatch):
    from app.settings import Settings

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("ALLOWED_TELEGRAM_USER_ID", "1")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    assert Settings().plan_cost_ceiling_micros == 150_000  # type: ignore[call-arg]
    monkeypatch.setenv("PLAN_COST_CEILING_MICROS", "999")
    assert Settings().plan_cost_ceiling_micros == 999  # type: ignore[call-arg]
