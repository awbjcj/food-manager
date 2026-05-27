from typing import Any, TypeVar, cast

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

SettingsT = TypeVar("SettingsT", bound="Settings")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    telegram_bot_token: str = Field(alias="TELEGRAM_BOT_TOKEN")
    allowed_telegram_user_id: int = Field(alias="ALLOWED_TELEGRAM_USER_ID")
    anthropic_api_key: str = Field(alias="ANTHROPIC_API_KEY")
    anthropic_model: str = Field(default="claude-sonnet-4-6", alias="ANTHROPIC_MODEL")
    anthropic_text_model: str = Field(
        default="claude-haiku-4-5-20251001",
        alias="ANTHROPIC_TEXT_MODEL",
    )
    database_path: str = Field(default="./food.db", alias="DATABASE_PATH")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    env: str = Field(default="dev", alias="ENV")

    @classmethod
    def load(cls: type[SettingsT]) -> SettingsT:
        return cast(Any, cls)()
