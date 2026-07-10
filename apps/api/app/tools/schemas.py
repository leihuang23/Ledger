from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.tools.scopes import PermissionScope

TOOL_ID_PATTERN = r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$"
IMPLEMENTATION_REF_PATTERN = r"^app(?:\.[A-Za-z_][A-Za-z0-9_]*)+$"


class ToolCreate(BaseModel):
    id: str = Field(min_length=1, max_length=80, pattern=TOOL_ID_PATTERN)
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(min_length=1, max_length=4000)
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    permission_scope: PermissionScope
    implementation_ref: str = Field(
        min_length=1,
        max_length=240,
        pattern=IMPLEMENTATION_REF_PATTERN,
    )

    @field_validator("name", "description", "implementation_ref", mode="before")
    @classmethod
    def strip_text(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value


class ToolRead(ToolCreate):
    model_config = ConfigDict(from_attributes=True)


class ToolList(BaseModel):
    total: int
    tools: list[ToolRead]
