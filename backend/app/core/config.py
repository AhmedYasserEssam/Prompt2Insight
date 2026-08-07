from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_env: str = "development"
    app_name: str = "Prompt2Insight"
    api_prefix: str = "/api/v1"
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:3000"])
    mock_mode: bool = True

    app_database_url: str = (
        "postgresql+asyncpg://prompt2insight:prompt2insight@localhost:5432/prompt2insight"
    )
    litellm_base_url: str = "http://localhost:4000/v1"
    litellm_master_key: str = "replace-this-in-production"

    query_timeout_ms: int = 8000
    lock_timeout_ms: int = 2000
    max_output_rows: int = 1000
    max_joins: int = 6
    max_estimated_rows: int = 100_000
    max_query_cost: float = 100_000.0

    @field_validator("cors_origins", mode="before")
    @classmethod
    def parse_cors_origins(cls, value: object) -> object:
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
