from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    autodoc_secret: str = ""
    openai_api_key: str = ""
    openai_model_vision: str = "gpt-4o"
    idempotency_ttl_seconds: int = 86_400
    idempotency_max_entries: int = 2_000


@lru_cache
def get_settings() -> Settings:
    return Settings()
