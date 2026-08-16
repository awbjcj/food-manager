"""Runtime entry point.

Startup order:
1. load settings
2. open DB engine + session factory
3. pre-migration backup if DB file already exists
4. alembic upgrade head
5. construct Bot + configured LLM SDK + LLMClient
6. register per-user digest jobs
7. start AsyncIOScheduler
8. start dispatcher polling
"""

from __future__ import annotations

import asyncio
import logging
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
from aiogram import Bot
from anthropic import AsyncAnthropic
from apscheduler.schedulers.asyncio import AsyncIOScheduler

import app.bot as bot_mod
import app.client_set as client_set_mod
import app.cook.service as cook_service_mod
import app.plan_service as plan_service_mod
from app import handler_support
from app.alerts import OwnerAlerter
from app.backup import BackupError, pre_migration_backup
from app.billing import meter as meter_mod
from app.billing.payment import StarsPaymentProvider
from app.bot import build_dispatcher
from app.client_set import PerUserClients
from app.cook.llm import (
    AnthropicNutritionLLM,
    AnthropicRecipeLLM,
    AnthropicSelectionLLM,
    OpenAINutritionLLM,
    OpenAIRecipeLLM,
    OpenAISelectionLLM,
)
from app.cook.recipe_source import SpoonacularSource, TheMealDbSource
from app.db import make_engine, make_session_factory
from app.deepseek_llm import (
    DeepSeekNutritionLLM,
    DeepSeekProfileLLMClient,
    DeepSeekSearchClient,
    DeepSeekSelectionLLM,
    DeepSeekTextLLMClient,
    DeepSeekTranslationLLMClient,
)
from app.gemini_llm import (
    GeminiLLMClient,
    GeminiNutritionLLM,
    GeminiProfileLLMClient,
    GeminiRecipeLLM,
    GeminiSearchClient,
    GeminiSelectionLLM,
    GeminiTextLLMClient,
    GeminiTranslationLLMClient,
)
from app.llm import (
    AnthropicLLMClient,
    AnthropicProfileLLMClient,
    AnthropicTextLLMClient,
    LLMProviderSelector,
    NutritionLLMProviderSelector,
    OpenAILLMClient,
    OpenAIProfileLLMClient,
    OpenAITextLLMClient,
    ProfileLLMProviderSelector,
    RecipeLLMProviderSelector,
    SelectionLLMProviderSelector,
    TextLLMProviderSelector,
)
from app.nl_intent import IntentAgentSelector, build_intent_agent
from app.operator import auth as operator_auth
from app.operator.bot import build_operator_dispatcher
from app.refine_service import AnthropicSearchClient
from app.resilience import run_with_restart
from app.scheduler import (
    catch_up_missed_digests,
    register_all_user_digests,
    register_sweep_expired_cooks,
    register_sweep_expired_pendings,
    schedule_user_digest,
    send_digest_with_retry,
    unschedule_user_digest,
)
from app.settings import Settings
from app.shelf_life_search import SearchProviderSelector
from app.translation_llm import (
    AnthropicTranslationLLMClient,
    OpenAITranslationLLMClient,
    TranslationLLMProviderSelector,
)
from app.week_composer import WeekComposerSelector, build_week_composer


def _configure_logging(env: str, level: str) -> None:
    fmt = (
        "%(asctime)s %(levelname)s %(name)s %(message)s"
        if env != "prod"
        else '{"ts":"%(asctime)s","lvl":"%(levelname)s","logger":"%(name)s","msg":"%(message)s"}'
    )
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format=fmt,
        stream=sys.stdout,
    )


@dataclass
class LLMBundle:
    image: LLMProviderSelector
    text: TextLLMProviderSelector
    search: SearchProviderSelector | None
    selection: SelectionLLMProviderSelector
    recipe: RecipeLLMProviderSelector
    nutrition: NutritionLLMProviderSelector
    profile: ProfileLLMProviderSelector
    translation: TranslationLLMProviderSelector | None


def _capable_default(clients: dict, preferred: str) -> str:
    """Seed default for a fallback selector: a provider actually in ``clients``.

    Image/search/recipe selectors fall back when the chosen provider lacks the
    capability, so their seed default must itself be capable — the user's
    preferred provider if present, else the first configured one. Settings
    validation guarantees ``clients`` is non-empty for the image seam.
    """
    return preferred if preferred in clients else min(clients)


def _build_intent_agents(settings: Settings) -> IntentAgentSelector | None:
    agents: dict = {}
    if settings.anthropic_api_key:
        agents["anthropic"] = build_intent_agent(
            "anthropic",
            model_id=settings.anthropic_text_model,
            api_key=settings.anthropic_api_key,
        )
    if settings.openai_api_key:
        agents["openai"] = build_intent_agent(
            "openai",
            model_id=settings.openai_text_model,
            api_key=settings.openai_api_key,
        )
    if settings.gemini_api_key:
        agents["gemini"] = build_intent_agent(
            "gemini",
            model_id=settings.gemini_text_model,
            api_key=settings.gemini_api_key,
        )
    if settings.deepseek_api_key:
        agents["deepseek"] = build_intent_agent(
            "deepseek",
            model_id=settings.deepseek_model,
            api_key=settings.deepseek_api_key,
            base_url=settings.deepseek_base_url,
        )
    if not agents:
        return None
    return IntentAgentSelector(agents, settings.llm_provider)


def _build_week_composers(settings: Settings) -> WeekComposerSelector | None:
    composers: dict = {}
    if settings.anthropic_api_key:
        composers["anthropic"] = build_week_composer(
            "anthropic",
            model_id=settings.anthropic_text_model,
            api_key=settings.anthropic_api_key,
        )
    if settings.openai_api_key:
        composers["openai"] = build_week_composer(
            "openai",
            model_id=settings.openai_text_model,
            api_key=settings.openai_api_key,
        )
    if settings.gemini_api_key:
        composers["gemini"] = build_week_composer(
            "gemini",
            model_id=settings.gemini_text_model,
            api_key=settings.gemini_api_key,
        )
    if settings.deepseek_api_key:
        composers["deepseek"] = build_week_composer(
            "deepseek",
            model_id=settings.deepseek_model,
            api_key=settings.deepseek_api_key,
            base_url=settings.deepseek_base_url,
        )
    if not composers:
        return None
    return WeekComposerSelector(composers, settings.llm_provider)


def _build_llm_clients(settings: Settings) -> LLMBundle:
    image_clients = {}
    text_clients = {}
    profile_clients = {}
    selection_clients = {}
    recipe_clients = {}
    nutrition_clients = {}
    translation_clients: dict = {}
    search_clients: dict = {}

    if settings.anthropic_api_key:
        anthropic_sdk = AsyncAnthropic(api_key=settings.anthropic_api_key)
        image_clients["anthropic"] = AnthropicLLMClient(
            sdk=anthropic_sdk,
            model=settings.anthropic_model,
        )
        text_clients["anthropic"] = AnthropicTextLLMClient(
            sdk=anthropic_sdk,
            model=settings.anthropic_text_model,
        )
        profile_clients["anthropic"] = AnthropicProfileLLMClient(
            sdk=anthropic_sdk,
            model=settings.anthropic_text_model,
        )
        selection_clients["anthropic"] = AnthropicSelectionLLM(
            anthropic_sdk, settings.anthropic_model
        )
        recipe_clients["anthropic"] = AnthropicRecipeLLM(
            anthropic_sdk, settings.anthropic_search_model
        )
        nutrition_clients["anthropic"] = AnthropicNutritionLLM(
            anthropic_sdk, settings.anthropic_model
        )
        translation_clients["anthropic"] = AnthropicTranslationLLMClient(
            anthropic_sdk, settings.anthropic_text_model
        )
        search_clients["anthropic"] = AnthropicSearchClient(
            sdk=anthropic_sdk,
            model=settings.anthropic_search_model,
        )

    if settings.openai_api_key:
        from openai import AsyncOpenAI

        openai_sdk = AsyncOpenAI(api_key=settings.openai_api_key)
        image_clients["openai"] = OpenAILLMClient(
            sdk=openai_sdk,
            model=settings.openai_model,
        )
        text_clients["openai"] = OpenAITextLLMClient(
            sdk=openai_sdk,
            model=settings.openai_text_model,
        )
        profile_clients["openai"] = OpenAIProfileLLMClient(
            sdk=openai_sdk,
            model=settings.openai_text_model,
        )
        selection_clients["openai"] = OpenAISelectionLLM(
            openai_sdk, settings.openai_model
        )
        recipe_clients["openai"] = OpenAIRecipeLLM(
            openai_sdk, settings.openai_model
        )
        nutrition_clients["openai"] = OpenAINutritionLLM(
            openai_sdk, settings.openai_model
        )
        translation_clients["openai"] = OpenAITranslationLLMClient(
            openai_sdk, settings.openai_text_model
        )
        # OpenAI's models can web-search, but no ShelfLifeSearchClient is wired
        # for it yet; an OpenAI user's lookups fall back to a search provider.

    if settings.gemini_api_key:
        from google import genai

        gemini_sdk = genai.Client(api_key=settings.gemini_api_key)
        image_clients["gemini"] = GeminiLLMClient(gemini_sdk, settings.gemini_model)
        text_clients["gemini"] = GeminiTextLLMClient(
            gemini_sdk, settings.gemini_text_model
        )
        profile_clients["gemini"] = GeminiProfileLLMClient(
            gemini_sdk, settings.gemini_text_model
        )
        selection_clients["gemini"] = GeminiSelectionLLM(
            gemini_sdk, settings.gemini_model
        )
        recipe_clients["gemini"] = GeminiRecipeLLM(gemini_sdk, settings.gemini_model)
        nutrition_clients["gemini"] = GeminiNutritionLLM(
            gemini_sdk, settings.gemini_model
        )
        translation_clients["gemini"] = GeminiTranslationLLMClient(
            gemini_sdk, settings.gemini_text_model
        )
        search_clients["gemini"] = GeminiSearchClient(
            gemini_sdk, settings.gemini_model
        )

    if settings.deepseek_api_key:
        from openai import AsyncOpenAI

        deepseek_sdk = AsyncOpenAI(
            api_key=settings.deepseek_api_key,
            base_url=settings.deepseek_base_url,
        )
        # Still image-incapable: no image extraction / search-backed recipe
        # client here; those fall back to a capable provider via the selectors.
        # Search itself is native (DeepSeekSearchClient, via the Responses API
        # web_search tool) and no longer falls back.
        text_clients["deepseek"] = DeepSeekTextLLMClient(
            deepseek_sdk, settings.deepseek_model
        )
        profile_clients["deepseek"] = DeepSeekProfileLLMClient(
            deepseek_sdk, settings.deepseek_model
        )
        selection_clients["deepseek"] = DeepSeekSelectionLLM(
            deepseek_sdk, settings.deepseek_model
        )
        nutrition_clients["deepseek"] = DeepSeekNutritionLLM(
            deepseek_sdk, settings.deepseek_model
        )
        translation_clients["deepseek"] = DeepSeekTranslationLLMClient(
            deepseek_sdk, settings.deepseek_model
        )
        search_clients["deepseek"] = DeepSeekSearchClient(
            deepseek_sdk, settings.deepseek_model
        )

    default = settings.llm_provider
    translation_llm = (
        TranslationLLMProviderSelector(translation_clients, default)
        if translation_clients
        else None
    )
    search = (
        SearchProviderSelector(
            search_clients, _capable_default(search_clients, default), fallback=True
        )
        if search_clients
        else None
    )

    return LLMBundle(
        image=LLMProviderSelector(
            image_clients, _capable_default(image_clients, default), fallback=True
        ),
        text=TextLLMProviderSelector(text_clients, default),
        search=search,
        selection=SelectionLLMProviderSelector(selection_clients, default),
        recipe=RecipeLLMProviderSelector(
            recipe_clients, _capable_default(recipe_clients, default), fallback=True
        ),
        nutrition=NutritionLLMProviderSelector(nutrition_clients, default),
        profile=ProfileLLMProviderSelector(profile_clients, default),
        translation=translation_llm,
    )


async def _amain(settings: Settings) -> None:
    log = logging.getLogger("food-manager")
    log.info("startup_begin")

    engine = make_engine(settings.database_path)
    session_factory = make_session_factory(engine)

    if Path(settings.database_path).exists():
        try:
            backup_path = pre_migration_backup(settings.database_path, keep=5)
            log.info("pre_migration_backup_ok", extra={"path": backup_path})
        except BackupError as exc:
            log.error("pre_migration_backup_failed", extra={"error": str(exc)})
            raise SystemExit(2) from exc

    result = await asyncio.to_thread(
        subprocess.run,
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        env={
            "DATABASE_PATH": settings.database_path,
            **{key: value for key, value in __import__("os").environ.items()},
        },
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        log.error("migration_failed", extra={"stderr": result.stderr})
        raise SystemExit(3)
    log.info("migration_ok")

    bot = Bot(token=settings.telegram_bot_token)
    payments = StarsPaymentProvider(bot) if settings.billing_enabled else None
    alerter = OwnerAlerter(bot, settings.allowed_telegram_user_id)
    recipe_http = httpx.AsyncClient()
    recipe_sources = [
        SpoonacularSource(http=recipe_http, api_key=settings.spoonacular_api_key),
        TheMealDbSource(http=recipe_http),
    ]
    bundle = _build_llm_clients(settings)
    clients = PerUserClients.create(
        image=bundle.image,
        text=bundle.text,
        profile=bundle.profile,
        selection=bundle.selection,
        recipe=bundle.recipe,
        nutrition=bundle.nutrition,
        search=bundle.search,
        translation=bundle.translation,
    )
    intent_agent = _build_intent_agents(settings)
    composer = _build_week_composers(settings)
    # Per-provider model names so the log reflects the configured provider
    # (deepseek is text-only, hence "n/a" for image).
    _image_models = {
        "anthropic": settings.anthropic_model,
        "openai": settings.openai_model,
        "gemini": settings.gemini_model,
        "deepseek": "n/a",
    }
    _text_models = {
        "anthropic": settings.anthropic_text_model,
        "openai": settings.openai_text_model,
        "gemini": settings.gemini_text_model,
        "deepseek": settings.deepseek_model,
    }
    log.info(
        "llm_provider_configured",
        extra={
            "provider": settings.llm_provider,
            "image_model": _image_models.get(settings.llm_provider, "n/a"),
            "text_model": _text_models.get(settings.llm_provider, "n/a"),
        },
    )
    handler_support.DEFAULT_LLM_PROVIDER = settings.llm_provider
    handler_support.ALLOWED_TELEGRAM_USER_ID = settings.allowed_telegram_user_id
    handler_support.OPEN_REGISTRATION = settings.open_registration
    # Keep the historical module attributes coherent for integrations that
    # inspect app.bot, while handler_support remains the runtime source of truth.
    bot_mod.DEFAULT_LLM_PROVIDER = settings.llm_provider
    bot_mod.ALLOWED_TELEGRAM_USER_ID = settings.allowed_telegram_user_id
    bot_mod.OPEN_REGISTRATION = settings.open_registration
    meter_mod.BILLING_ENABLED = settings.billing_enabled
    client_set_mod.INGEST_PROVIDER = settings.ingest_provider
    cook_service_mod.COOK_COST_CEILING_MICROS = settings.cook_cost_ceiling_micros
    plan_service_mod.PLAN_COST_CEILING_MICROS = settings.plan_cost_ceiling_micros

    scheduler = AsyncIOScheduler()

    translation_llm = bundle.translation

    async def _alert_digest_failure(user_id: int, exc: Exception) -> None:
        await alerter.alert(
            "digest_failed", f"user {user_id}: {type(exc).__name__}: {exc}"
        )

    async def send(user_id: int) -> None:
        await send_digest_with_retry(
            user_id=user_id,
            bot=bot,
            session_factory=session_factory,
            today_provider=lambda tz: datetime.now(ZoneInfo(tz)).date(),
            translation_llm=translation_llm,
            on_final_failure=_alert_digest_failure,
        )

    register_all_user_digests(scheduler, session_factory=session_factory, send=send)
    register_sweep_expired_pendings(scheduler, session_factory=session_factory)
    register_sweep_expired_cooks(scheduler, session_factory=session_factory)

    def reschedule(user) -> None:
        schedule_user_digest(scheduler, user, send=send)

    def unschedule(telegram_id: int) -> None:
        unschedule_user_digest(scheduler, telegram_id)

    def on_user_created(user) -> None:
        reschedule(user)
        if settings.open_registration:
            log.info(
                "household_registered",
                extra={"telegram_id": user.telegram_id, "household_id": user.household_id},
            )
            asyncio.create_task(
                alerter.alert(
                    "new_household",
                    f"telegram_id={user.telegram_id} household={user.household_id}",
                )
            )

    dispatcher = build_dispatcher(
        bot=bot,
        session_factory=session_factory,
        clients=clients,
        now_provider=lambda tz: datetime.now(ZoneInfo(tz)),
        on_user_created=on_user_created,
        reschedule=reschedule,
        unschedule=unschedule,
        translation_llm=translation_llm,
        alerter=alerter,
        recipe_sources=recipe_sources,
        intent_agent=intent_agent,
        composer=composer,
        payments=payments,
    )

    scheduler.start()
    log.info("scheduler_started")

    caught_up = await catch_up_missed_digests(
        session_factory=session_factory,
        send=send,
        now_provider=lambda tz: datetime.now(ZoneInfo(tz)),
    )
    if caught_up:
        log.info("digest_catch_up_done", extra={"count": caught_up})

    log.info("polling_start")

    async def _alert_polling_crash(exc: Exception) -> None:
        await alerter.alert("polling_crashed", f"{type(exc).__name__}: {exc}")

    operator_bot = None
    operator_dispatcher = None
    if settings.operator_bot_token:
        operator_auth.OPERATOR_IDS = settings.operator_ids
        operator_bot = Bot(token=settings.operator_bot_token)
        operator_dispatcher = build_operator_dispatcher(
            session_factory=session_factory,
            now_provider=lambda tz: datetime.now(ZoneInfo(tz)),
            payments=payments,
        )

    async def _alert_operator_crash(exc: Exception) -> None:
        await alerter.alert("operator_polling_crashed", f"{type(exc).__name__}: {exc}")

    loops = [
        run_with_restart(
            lambda: dispatcher.start_polling(bot), on_crash=_alert_polling_crash
        )
    ]
    if operator_bot is not None and operator_dispatcher is not None:
        loops.append(
            run_with_restart(
                lambda: operator_dispatcher.start_polling(operator_bot),
                on_crash=_alert_operator_crash,
            )
        )

    try:
        await asyncio.gather(*loops)
    finally:
        scheduler.shutdown(wait=False)
        await recipe_http.aclose()
        await bot.session.close()
        if operator_bot is not None:
            await operator_bot.session.close()


def main() -> None:
    settings = Settings()  # type: ignore[call-arg]
    _configure_logging(settings.env, settings.log_level)
    asyncio.run(_amain(settings))


if __name__ == "__main__":
    main()
