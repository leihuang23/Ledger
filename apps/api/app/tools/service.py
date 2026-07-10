from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import Tool
from app.tools.registry import (
    BUILTIN_TOOL_BY_ID,
    BUILTIN_TOOL_DEFINITIONS,
    resolve_implementation_ref,
)
from app.tools.schemas import ToolCreate, ToolRead


class DuplicateToolError(ValueError):
    pass


class UnsupportedToolRegistrationError(ValueError):
    pass


def register_builtin_tools(session: Session, *, commit: bool = True) -> list[ToolRead]:
    """Idempotently synchronize built-in metadata from its real Pydantic contracts."""
    tools: list[Tool] = []
    for definition in BUILTIN_TOOL_DEFINITIONS:
        resolved = resolve_implementation_ref(definition.implementation_ref)
        if resolved is not definition.implementation:
            raise RuntimeError(
                f"Tool binding drift detected for {definition.id}: "
                f"{definition.implementation_ref}"
            )
        values = {
            "name": definition.name,
            "description": definition.description,
            "input_schema": definition.input_schema,
            "output_schema": definition.output_schema,
            "permission_scope": definition.permission_scope,
            "implementation_ref": definition.implementation_ref,
        }
        tool = session.get(Tool, definition.id)
        if tool is None:
            tool = Tool(id=definition.id, **values)
            session.add(tool)
        else:
            for field_name, value in values.items():
                setattr(tool, field_name, value)
        tools.append(tool)

    session.flush()
    if commit:
        session.commit()
    return [ToolRead.model_validate(tool) for tool in tools]


def list_tools(
    session: Session,
    *,
    limit: int = 100,
    offset: int = 0,
) -> tuple[list[ToolRead], int]:
    total = int(session.scalar(select(func.count()).select_from(Tool)) or 0)
    tools = session.scalars(
        select(Tool).order_by(Tool.name, Tool.id).limit(limit).offset(offset)
    ).all()
    return [ToolRead.model_validate(tool) for tool in tools], total


def get_tool(session: Session, tool_id: str) -> ToolRead | None:
    tool = session.get(Tool, tool_id)
    return ToolRead.model_validate(tool) if tool is not None else None


def create_tool(session: Session, payload: ToolCreate) -> ToolRead:
    definition = BUILTIN_TOOL_BY_ID.get(payload.id)
    if definition is None:
        raise UnsupportedToolRegistrationError(
            f"Tool {payload.id!r} is outside the closed built-in catalog."
        )
    canonical = {
        "id": definition.id,
        "name": definition.name,
        "description": definition.description,
        "input_schema": definition.input_schema,
        "output_schema": definition.output_schema,
        "permission_scope": definition.permission_scope,
        "implementation_ref": definition.implementation_ref,
    }
    if payload.model_dump() != canonical:
        raise UnsupportedToolRegistrationError(
            f"Tool {payload.id!r} must match its audited built-in binding."
        )
    resolve_implementation_ref(definition.implementation_ref)
    tool = Tool(**payload.model_dump())
    session.add(tool)
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise DuplicateToolError(
            f"Tool id or name already registered: {payload.id}"
        ) from exc
    session.refresh(tool)
    return ToolRead.model_validate(tool)
