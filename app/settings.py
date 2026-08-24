from typing import Any, ClassVar, Self, TypeVar, cast
from urllib.parse import urlsplit

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.providers import (
    ALL_CREDENTIAL_MODES,
    ALL_PROVIDERS,
    PROVIDER_CAPABILITIES,
    CredentialMode,
    Provider,
    ProviderCredentials,
    supports,
)

SettingsT = TypeVar("SettingsT", bound="Settings")
# Re-exported for callers that imported the provider type from settings; the
# canonical definition now lives in app.providers.
LLMProvider = Provider


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        hide_input_in_errors=True,
    )

    telegram_bot_token: str = Field(alias="TELEGRAM_BOT_TOKEN")
    allowed_telegram_user_id: int = Field(alias="ALLOWED_TELEGRAM_USER_ID")
    llm_provider: LLMProvider = Field(default="anthropic", alias="LLM_PROVIDER")
    anthropic_api_key: str | None = Field(default=None, alias="ANTHROPIC_API_KEY")
    anthropic_model: str = Field(default="claude-sonnet-5", alias="ANTHROPIC_MODEL")
    anthropic_text_model: str = Field(
        default="claude-haiku-4-5-20251001",
        alias="ANTHROPIC_TEXT_MODEL",
    )
    openai_api_key: str | None = Field(default=None, alias="OPENAI_API_KEY")
    openai_model: str = Field(default="gpt-5.6-terra", alias="OPENAI_MODEL")
    openai_text_model: str = Field(default="gpt-5.6-luna", alias="OPENAI_TEXT_MODEL")
    anthropic_search_model: str = Field(default="claude-sonnet-5", alias="ANTHROPIC_SEARCH_MODEL")
    gemini_api_key: str | None = Field(default=None, alias="GEMINI_API_KEY")
    gemini_model: str = Field(default="gemini-3.1-pro-preview", alias="GEMINI_MODEL")
    gemini_text_model: str = Field(default="gemini-3.5-flash", alias="GEMINI_TEXT_MODEL")
    # DeepSeek is OpenAI-Responses-API-compatible; it has no image capability,
    # but does have a native web_search tool. One model field suffices since
    # a single model serves both the text and search capabilities.
    deepseek_api_key: str | None = Field(default=None, alias="DEEPSEEK_API_KEY")
    # deepseek-chat is a Chat Completions-only alias; the Responses API this
    # module now uses (app.deepseek_llm) only documents deepseek-v4-flash and
    # deepseek-v4-pro as supported models.
    deepseek_model: str = Field(default="deepseek-v4-flash", alias="DEEPSEEK_MODEL")
    deepseek_base_url: str = Field(
        default="https://api.deepseek.com", alias="DEEPSEEK_BASE_URL"
    )
    # sub2api: one gateway root shared by every provider, with a per-provider
    # token that tells the gateway which upstream subscription to spend. A
    # provider defaults to the gateway whenever its token is set (see
    # ``default_credential_mode``); operators override that per provider at
    # runtime through ``app.provider_mode_service``.
    sub2api_base_url: str | None = Field(default=None, alias="SUB2API_BASE_URL")
    sub2api_anthropic_token: str | None = Field(
        default=None, alias="SUB2API_ANTHROPIC_TOKEN"
    )
    sub2api_openai_token: str | None = Field(default=None, alias="SUB2API_OPENAI_TOKEN")
    sub2api_gemini_token: str | None = Field(default=None, alias="SUB2API_GEMINI_TOKEN")
    sub2api_deepseek_token: str | None = Field(
        default=None, alias="SUB2API_DEEPSEEK_TOKEN"
    )
    spoonacular_api_key: str | None = Field(default=None, alias="SPOONACULAR_API_KEY")
    billing_enabled: bool = Field(default=False, alias="BILLING_ENABLED")
    ingest_provider: str = Field(default="", alias="INGEST_PROVIDER")
    operator_telegram_ids: str = Field(default="", alias="OPERATOR_TELEGRAM_IDS")
    operator_bot_token: str | None = Field(default=None, alias="OPERATOR_BOT_TOKEN")
    open_registration: bool = Field(default=False, alias="OPEN_REGISTRATION")
    web_app_url: str | None = Field(default=None, alias="WEB_APP_URL")
    port: int = Field(default=8000, alias="PORT")
    cook_cost_ceiling_micros: int = Field(
        default=100_000, alias="COOK_COST_CEILING_MICROS"
    )
    plan_cost_ceiling_micros: int = Field(
        default=150_000, alias="PLAN_COST_CEILING_MICROS"
    )
    database_path: str = Field(default="./food.db", alias="DATABASE_PATH")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    env: str = Field(default="dev", alias="ENV")

    @model_validator(mode="after")
    def validate_web_app(self) -> "Settings":
        if not 1 <= self.port <= 65535:
            raise ValueError("PORT must be between 1 and 65535")
        if self.web_app_url and not self.web_app_url.startswith("https://"):
            raise ValueError("WEB_APP_URL must use HTTPS")
        return self

    @model_validator(mode="after")
    def validate_sub2api(self) -> "Settings":
        if self.sub2api_base_url:
            parsed = urlsplit(self.sub2api_base_url)
            if parsed.scheme != "https" or not parsed.hostname:
                raise ValueError("SUB2API_BASE_URL must be an HTTPS URL")
            if parsed.username or parsed.password:
                raise ValueError("SUB2API_BASE_URL must not contain credentials")
        orphans = [p for p in ALL_PROVIDERS if self._sub2api_token_for(p)]
        if orphans and not self.sub2api_base_url:
            # A token with nowhere to send it would silently do nothing, and the
            # provider would quietly stay on its metered API. Fail at boot.
            raise ValueError(
                "SUB2API_BASE_URL is required when any SUB2API_*_TOKEN is set "
                f"(tokens set for: {', '.join(orphans)})"
            )
        return self

    def _api_key_for(self, provider: str) -> str | None:
        return {
            "anthropic": self.anthropic_api_key,
            "openai": self.openai_api_key,
            "gemini": self.gemini_api_key,
            "deepseek": self.deepseek_api_key,
        }.get(provider)

    def _sub2api_token_for(self, provider: str) -> str | None:
        return {
            "anthropic": self.sub2api_anthropic_token,
            "openai": self.sub2api_openai_token,
            "gemini": self.sub2api_gemini_token,
            "deepseek": self.sub2api_deepseek_token,
        }.get(provider)

    def credentials_for(
        self, provider: str, mode: CredentialMode
    ) -> ProviderCredentials | None:
        """Resolve ``provider``'s key + endpoint for ``mode``, else ``None``.

        ``None`` means "not configured for that mode" and is a normal answer,
        not an error: it is how a provider you hold no subscription for is
        excluded from subscription mode without any allow-list.
        """
        if mode == "subscription":
            token = self._sub2api_token_for(provider)
            if not (self.sub2api_base_url and token):
                return None
            return ProviderCredentials(
                provider=cast(Provider, provider),
                mode="subscription",
                api_key=token,
                base_url=self.sub2api_base_url,
            )
        key = self._api_key_for(provider)
        if not key:
            return None
        return ProviderCredentials(
            provider=cast(Provider, provider),
            mode="api",
            api_key=key,
            # DeepSeek is the one provider whose own API already needed a base
            # URL; the rest use their SDK's built-in endpoint.
            base_url=self.deepseek_base_url if provider == "deepseek" else None,
        )

    def default_credential_mode(self, provider: str) -> CredentialMode:
        """The gateway wins whenever a sub2api token is configured.

        Deriving the default from config rather than a hard-coded list is what
        lets a provider you later buy a subscription for flip over by adding one
        env var, with no code change.
        """
        if self.credentials_for(provider, "subscription"):
            return "subscription"
        return "api"

    def has_credentials(self, provider: str) -> bool:
        """True if ``provider`` is usable in *either* credential mode."""
        return any(
            self.credentials_for(provider, mode) is not None
            for mode in ALL_CREDENTIAL_MODES
        )

    @model_validator(mode="after")
    def validate_provider_key(self) -> "Settings":
        if not self.has_credentials(self.llm_provider):
            raise ValueError(
                f"{self.llm_provider.upper()}_API_KEY is required when "
                f"LLM_PROVIDER={self.llm_provider} (or set SUB2API_BASE_URL "
                f"plus SUB2API_{self.llm_provider.upper()}_TOKEN)"
            )
        # An image-incapable default provider (e.g. deepseek) cannot read
        # receipt photos; that falls back to a capable provider, so at least
        # one image-capable key must be configured or the bot can't ingest.
        if not supports(self.llm_provider, "image"):
            capable = [
                provider
                for provider, caps in PROVIDER_CAPABILITIES.items()
                if "image" in caps and self.has_credentials(provider)
            ]
            if not capable:
                raise ValueError(
                    f"LLM_PROVIDER={self.llm_provider} cannot process images; "
                    "configure an API key for an image-capable provider "
                    "(anthropic, openai, or gemini)"
                )
        return self

    _INGEST_PREFERENCE: ClassVar[tuple[str, ...]] = ("gemini", "openai", "anthropic")

    @model_validator(mode="after")
    def resolve_ingest_provider(self) -> "Settings":
        if self.ingest_provider:
            if not supports(self.ingest_provider, "image"):
                raise ValueError(
                    f"INGEST_PROVIDER={self.ingest_provider} cannot process images"
                )
            if not self.has_credentials(self.ingest_provider):
                raise ValueError(
                    f"INGEST_PROVIDER={self.ingest_provider} has no credentials"
                )
            return self
        for provider in self._INGEST_PREFERENCE:
            if self.has_credentials(provider):
                object.__setattr__(self, "ingest_provider", provider)
                break
        return self

    @property
    def operator_ids(self) -> frozenset[int]:
        raw = self.operator_telegram_ids.strip()
        if not raw:
            return frozenset({self.allowed_telegram_user_id})
        try:
            return frozenset(
                int(part.strip()) for part in raw.split(",") if part.strip()
            )
        except ValueError as exc:
            raise ValueError(
                "OPERATOR_TELEGRAM_IDS must contain comma-separated integers"
            ) from exc

    @classmethod
    def load(cls) -> Self:
        return cast(Any, cls)()
