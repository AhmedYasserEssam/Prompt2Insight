from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"),
        env_file_encoding="utf-8",
        extra="ignore",
        enable_decoding=False,
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
    litellm_timeout_seconds: float = 60
    planner_primary_model: str = "sql-planner-primary"
    vllm_base_url: str = "http://localhost:8000/v1"
    vllm_api_key: str = "EMPTY"
    vllm_model: str = "Qwen/Qwen3.5-9B"
    vllm_timeout_seconds: float = 60
    vllm_enabled: bool = False
    planner_fallback_model: str = "sql-planner-fallback"
    answer_primary_model: str = "answer-primary"
    answer_fallback_model: str = "answer-fallback"

    # The planner's selected model determines this input window.  The conservative
    # estimator in conversation_context is used when no model tokenizer is available.
    conversation_context_token_budget: int = Field(default=8_000, ge=512)
    conversation_output_token_reserve: int = Field(default=1_500, ge=128)
    conversation_recent_message_limit: int = Field(default=12, ge=1, le=100)
    conversation_result_sample_rows: int = Field(default=5, ge=0, le=20)
    conversation_summary_threshold_tokens: int = Field(default=6_000, ge=512)
    conversation_summary_keep_messages: int = Field(default=6, ge=1, le=50)
    conversation_summary_max_chars: int = Field(default=4_000, ge=256, le=20_000)

    query_timeout_ms: int = 8000
    lock_timeout_ms: int = 2000
    max_output_rows: int = 1000
    max_joins: int = 6
    max_estimated_rows: int = 100_000
    max_query_cost: float = 100_000.0
    max_chart_bar_categories: int = Field(default=20, ge=2, le=100)
    max_chart_donut_categories: int = Field(default=6, ge=2, le=12)
    max_chart_series: int = Field(default=8, ge=2, le=20)

    @field_validator("cors_origins", mode="before")
    @classmethod
    def parse_cors_origins(cls, value: object) -> object:
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
