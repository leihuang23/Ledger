from functools import lru_cache
from typing import Annotated, Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "ledger-api"
    app_env: str = "local"
    app_version: str = "0.1.0"
    database_url: str = Field(
        default="postgresql+psycopg://ledger:ledger@localhost:5432/ledger"
    )
    redis_url: str = "redis://localhost:6379/0"
    backend_cors_origins: Annotated[list[str], NoDecode] = ["http://localhost:3000"]
    allow_unsafe_bootstrap_seed: bool = False
    demo_operator_token: str | None = None
    embedding_provider: Literal["local", "openai", "zhipu"] = "local"
    embedding_model: Literal["local-hashing-v1"] = "local-hashing-v1"
    openai_embedding_model: str = "text-embedding-3-small"
    zhipu_embedding_model: str = "embedding-3"
    zhipu_api_key: str | None = None
    document_ingest_token: str | None = None
    eval_run_token: str | None = None
    observability_provider: Literal["auto", "local", "langfuse", "langsmith"] = "auto"
    observability_full_payloads: bool = False
    observability_timeout_seconds: int = Field(default=2, ge=1, le=30)
    langfuse_public_key: str | None = None
    langfuse_secret_key: str | None = None
    langfuse_base_url: str = "https://cloud.langfuse.com"
    langfuse_project_id: str | None = None
    langsmith_tracing: bool = False
    langsmith_api_key: str | None = None
    langsmith_endpoint: str = "https://api.smith.langchain.com"
    langsmith_project: str = "ledger-local"
    langsmith_web_url: str = "https://smith.langchain.com"

    # LLM configuration
    llm_provider: Literal["none", "openai", "anthropic"] = "none"
    llm_model: str = "gpt-4o-mini"
    openai_api_key: str | None = None
    anthropic_api_key: str | None = None
    llm_temperature: float = Field(default=0.1, ge=0.0, le=2.0)
    llm_max_tokens: int = Field(default=1024, ge=1, le=4096)
    llm_timeout_seconds: int = Field(default=30, ge=1, le=120)
    # Bounded ReAct investigation loop (app.agent.loop): maximum number of LLM
    # decisions per run. When exhausted, the loop degrades to the deterministic
    # evidence sweep instead of continuing to spend tokens.
    agent_max_iterations: int = Field(default=8, ge=1, le=20)
    # Operator gate for the agentic loop. With a real LLM provider, setting
    # this to false pins investigations back to the single-shot deterministic
    # pipeline (one final diagnosis call), e.g. for cost control or gradual
    # rollout. LLM_PROVIDER=none always stays deterministic regardless.
    agent_loop_enabled: bool = True

    # Celery / Redis configuration
    celery_broker_url: str = "redis://localhost:6379/0"
    celery_result_backend: str = "redis://localhost:6379/0"

    # Run staleness self-heal (PRD FR-11, NFR-2). The sweep cadence is the
    # Celery beat interval at which the reaper runs; the stale-after threshold
    # is how old a run's last activity must be before the reaper force-fails it.
    # A periodic beat schedule (default 60s) ensures no run stays in ``running``
    # indefinitely, even on a long-lived server with no restart or re-launch.
    run_staleness_sweep_interval_seconds: int = Field(default=60, ge=1)
    active_run_stale_after_seconds: int = Field(default=600, ge=1)

    # Logging configuration
    log_level: str = "INFO"
    log_format: Literal["json", "text"] = "text"

    # Rate limiting configuration
    rate_limit_mutations_per_minute: int = Field(default=1000, ge=1)
    rate_limit_search_per_minute: int = Field(default=1000, ge=1)

    # Optional Stripe test-mode evidence adapter (never live keys; disabled in APP_ENV=demo).
    stripe_api_key: str | None = None
    stripe_webhook_secret: str | None = None
    stripe_webhook_tolerance_seconds: int = Field(default=300, ge=0, le=3600)

    @field_validator("backend_cors_origins", mode="before")
    @classmethod
    def parse_cors_origins(cls, value: str | list[str]) -> list[str]:
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value

    @field_validator("database_url", mode="before")
    @classmethod
    def normalize_database_url(cls, value: str) -> str:
        """Use the installed psycopg v3 driver for managed Postgres URLs.

        Render and similar platforms expose ``postgresql://`` connection
        strings. SQLAlchemy otherwise interprets that scheme as psycopg2,
        which this project intentionally does not install.
        """
        if value.startswith("postgres://"):
            return value.replace("postgres://", "postgresql+psycopg://", 1)
        if value.startswith("postgresql://"):
            return value.replace("postgresql://", "postgresql+psycopg://", 1)
        return value

    @field_validator(
        "document_ingest_token",
        "demo_operator_token",
        "eval_run_token",
        "langfuse_public_key",
        "langfuse_secret_key",
        "langfuse_project_id",
        "langsmith_api_key",
        "openai_api_key",
        "anthropic_api_key",
        "zhipu_api_key",
        "stripe_api_key",
        "stripe_webhook_secret",
        mode="before",
    )
    @classmethod
    def parse_optional_token(cls, value: str | None) -> str | None:
        if isinstance(value, str):
            stripped = value.strip()
            return stripped or None
        return value

    @field_validator("stripe_api_key", mode="after")
    @classmethod
    def reject_live_stripe_secret(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if value.startswith("sk_live_"):
            raise ValueError(
                "Live-mode Stripe secret keys are not permitted. Use sk_test_ only."
            )
        return value

    @field_validator(
        "langfuse_base_url",
        "langsmith_endpoint",
        "langsmith_web_url",
        mode="before",
    )
    @classmethod
    def trim_url(cls, value: str) -> str:
        if isinstance(value, str):
            return value.strip()
        return value

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
