from __future__ import annotations

from datetime import datetime
import re
from typing import Literal

from pydantic import BaseModel, Field, field_validator

AGENT_SLUG_PATTERN = r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$"
IDENTIFIER_PATTERN = r"^[a-z][a-z0-9]*(?:[-_][a-z0-9]+)*$"
MAX_LIST_ITEMS = 50
MAX_IDENTIFIER_LENGTH = 80


def _validate_identifier_list(values: list[str] | None, field_name: str) -> list[str] | None:
    if values is None:
        return None
    if len(values) > MAX_LIST_ITEMS:
        raise ValueError(f"{field_name} must contain at most {MAX_LIST_ITEMS} items")
    id_re = re.compile(IDENTIFIER_PATTERN)
    for item in values:
        if not isinstance(item, str):
            raise ValueError(f"{field_name} items must be strings")
        if len(item) < 1 or len(item) > MAX_IDENTIFIER_LENGTH:
            raise ValueError(
                f"{field_name} items must be 1-{MAX_IDENTIFIER_LENGTH} characters"
            )
        if not id_re.match(item):
            raise ValueError(
                f"{field_name} items must be kebab-case or snake_case identifiers"
            )
    return values


AgentVersionStatus = Literal["draft", "published"]


class AgentCreate(BaseModel):
    id: str = Field(min_length=3, max_length=64, pattern=AGENT_SLUG_PATTERN)
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=2000)
    default_model: str = Field(default="gpt-4o-mini", min_length=1, max_length=80)
    system_prompt: str = Field(default="", max_length=50_000)


class AgentVersionCreate(BaseModel):
    fork_from_version_id: str | None = None
    system_prompt: str | None = Field(default=None, max_length=50_000)
    model: str | None = Field(default=None, min_length=1, max_length=80)
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    max_tokens: int | None = Field(default=None, ge=1, le=16384)
    enabled_tool_ids: list[str] | None = None
    allowed_scopes: list[str] | None = None

    @field_validator("enabled_tool_ids")
    @classmethod
    def _validate_tool_ids(cls, v: list[str] | None) -> list[str] | None:
        return _validate_identifier_list(v, "enabled_tool_ids")

    @field_validator("allowed_scopes")
    @classmethod
    def _validate_scopes(cls, v: list[str] | None) -> list[str] | None:
        return _validate_identifier_list(v, "allowed_scopes")


class AgentVersionUpdate(BaseModel):
    system_prompt: str | None = Field(default=None, max_length=50_000)
    model: str | None = Field(default=None, min_length=1, max_length=80)
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    max_tokens: int | None = Field(default=None, ge=1, le=16384)
    enabled_tool_ids: list[str] | None = None
    allowed_scopes: list[str] | None = None

    @field_validator("enabled_tool_ids")
    @classmethod
    def _validate_tool_ids(cls, v: list[str] | None) -> list[str] | None:
        return _validate_identifier_list(v, "enabled_tool_ids")

    @field_validator("allowed_scopes")
    @classmethod
    def _validate_scopes(cls, v: list[str] | None) -> list[str] | None:
        return _validate_identifier_list(v, "allowed_scopes")


class VersionSummary(BaseModel):
    id: str
    version_number: int | None
    semantic_version: str | None
    status: AgentVersionStatus
    model: str
    created_at: datetime
    published_at: datetime | None
    forked_from_version_id: str | None


class VersionDetail(VersionSummary):
    system_prompt: str
    temperature: float
    max_tokens: int
    enabled_tool_ids: list[str]
    allowed_scopes: list[str]
    published_by: str | None


class AgentSummary(BaseModel):
    id: str
    name: str
    description: str
    default_model: str
    created_at: datetime
    updated_at: datetime
    latest_published_version: VersionSummary | None
    current_draft_version: VersionSummary | None
    version_count: int


class AgentDetail(AgentSummary):
    versions: list[VersionSummary]


class AgentList(BaseModel):
    total: int
    agents: list[AgentSummary]


class VersionList(BaseModel):
    total: int
    versions: list[VersionSummary]


class PublishResult(BaseModel):
    version: VersionDetail
