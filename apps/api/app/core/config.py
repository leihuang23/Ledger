from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "ops-agent-api"
    app_env: str = "local"
    app_version: str = "0.1.0"
    database_url: str = Field(
        default="postgresql+psycopg://ops_agent:ops_agent@localhost:5432/ops_agent"
    )
    redis_url: str = "redis://localhost:6379/0"
    backend_cors_origins: list[str] = ["http://localhost:3000"]
    allow_unsafe_bootstrap_seed: bool = False
    embedding_provider: Literal["local"] = "local"
    embedding_model: Literal["local-hashing-v1"] = "local-hashing-v1"
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
    langsmith_project: str = "ops-agent-local"
    langsmith_web_url: str = "https://smith.langchain.com"

    @field_validator("backend_cors_origins", mode="before")
    @classmethod
    def parse_cors_origins(cls, value: str | list[str]) -> list[str]:
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value

    @field_validator(
        "document_ingest_token",
        "eval_run_token",
        "langfuse_public_key",
        "langfuse_secret_key",
        "langfuse_project_id",
        "langsmith_api_key",
        mode="before",
    )
    @classmethod
    def parse_optional_token(cls, value: str | None) -> str | None:
        if isinstance(value, str):
            stripped = value.strip()
            return stripped or None
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
