from typing import Any, TypeVar, cast

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.providers import PROVIDER_CAPABILITIES, Provider, supports

SettingsT = TypeVar("SettingsT", bound="Settings")
# Re-exported for callers that imported the provider type from settings; the
# canonical definition now lives in app.providers.
LLMProvider = Provider


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
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
    # DeepSeek is OpenAI-compatible and text-only (no image/search). One model
    # field suffices since it serves only the text capabilities.
    deepseek_api_key: str | None = Field(default=None, alias="DEEPSEEK_API_KEY")
    deepseek_model: str = Field(default="deepseek-chat", alias="DEEPSEEK_MODEL")
    deepseek_base_url: str = Field(
        default="https://api.deepseek.com", alias="DEEPSEEK_BASE_URL"
    )
    spoonacular_api_key: str | None = Field(default=None, alias="SPOONACULAR_API_KEY")
    cook_cost_ceiling_micros: int = Field(
        default=100_000, alias="COOK_COST_CEILING_MICROS"
    )
    plan_cost_ceiling_micros: int = Field(
        default=150_000, alias="PLAN_COST_CEILING_MICROS"
    )
    database_path: str = Field(default="./food.db", alias="DATABASE_PATH")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    env: str = Field(default="dev", alias="ENV")

    def _api_key_for(self, provider: str) -> str | None:
        return {
            "anthropic": self.anthropic_api_key,
            "openai": self.openai_api_key,
            "gemini": self.gemini_api_key,
            "deepseek": self.deepseek_api_key,
        }.get(provider)

    @model_validator(mode="after")
    def validate_provider_key(self) -> "Settings":
        if not self._api_key_for(self.llm_provider):
            raise ValueError(
                f"{self.llm_provider.upper()}_API_KEY is required when "
                f"LLM_PROVIDER={self.llm_provider}"
            )
        # A text-only default provider (e.g. deepseek) cannot read receipt photos
        # or run web searches; those fall back to a capable provider, so at least
        # one image/search-capable key must be configured or the bot can't ingest.
        if not supports(self.llm_provider, "image"):
            capable = [
                provider
                for provider, caps in PROVIDER_CAPABILITIES.items()
                if "image" in caps and self._api_key_for(provider)
            ]
            if not capable:
                raise ValueError(
                    f"LLM_PROVIDER={self.llm_provider} cannot process images; "
                    "configure an API key for an image-capable provider "
                    "(anthropic, openai, or gemini)"
                )
        return self

    @classmethod
    def load(cls: type[SettingsT]) -> SettingsT:
        return cast(Any, cls)()
