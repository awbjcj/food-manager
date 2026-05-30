from typing import Any, Literal, TypeVar, cast

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

SettingsT = TypeVar("SettingsT", bound="Settings")
LLMProvider = Literal["anthropic", "openai"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    telegram_bot_token: str = Field(alias="TELEGRAM_BOT_TOKEN")
    allowed_telegram_user_id: int = Field(alias="ALLOWED_TELEGRAM_USER_ID")
    llm_provider: LLMProvider = Field(default="anthropic", alias="LLM_PROVIDER")
    anthropic_api_key: str | None = Field(default=None, alias="ANTHROPIC_API_KEY")
    anthropic_model: str = Field(default="claude-sonnet-4-6", alias="ANTHROPIC_MODEL")
    anthropic_text_model: str = Field(
        default="claude-haiku-4-5-20251001",
        alias="ANTHROPIC_TEXT_MODEL",
    )
    openai_api_key: str | None = Field(default=None, alias="OPENAI_API_KEY")
    openai_model: str = Field(default="gpt-5.4", alias="OPENAI_MODEL")
    openai_text_model: str = Field(default="gpt-5.4-mini", alias="OPENAI_TEXT_MODEL")
    anthropic_search_model: str = Field(default="claude-sonnet-4-6", alias="ANTHROPIC_SEARCH_MODEL")
    cook_cost_ceiling_micros: int = Field(
        default=100_000, alias="COOK_COST_CEILING_MICROS"
    )
    database_path: str = Field(default="./food.db", alias="DATABASE_PATH")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    env: str = Field(default="dev", alias="ENV")

    @model_validator(mode="after")
    def validate_provider_key(self) -> "Settings":
        if self.llm_provider == "anthropic" and not self.anthropic_api_key:
            raise ValueError("ANTHROPIC_API_KEY is required when LLM_PROVIDER=anthropic")
        if self.llm_provider == "openai" and not self.openai_api_key:
            raise ValueError("OPENAI_API_KEY is required when LLM_PROVIDER=openai")
        return self

    @classmethod
    def load(cls: type[SettingsT]) -> SettingsT:
        return cast(Any, cls)()
