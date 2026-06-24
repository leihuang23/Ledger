from functools import lru_cache

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

    @field_validator("backend_cors_origins", mode="before")
    @classmethod
    def parse_cors_origins(cls, value: str | list[str]) -> list[str]:
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
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
