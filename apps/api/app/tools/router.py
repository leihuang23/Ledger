from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.orm import Session

from app.core.access import require_demo_data_access, require_demo_operator_access
from app.core.config import get_settings
from app.core.limiter import limiter
from app.db.session import get_db
from app.tools.registry import InvalidImplementationRefError
from app.tools.schemas import ToolCreate, ToolList, ToolRead
from app.tools.service import (
    DuplicateToolError,
    UnsupportedToolRegistrationError,
    create_tool,
    get_tool,
    list_tools,
)

_settings = get_settings()

router = APIRouter(
    prefix="/tools",
    tags=["tools"],
    dependencies=[Depends(require_demo_data_access)],
)


@router.get("", response_model=ToolList)
def tools_list(
    db: Session = Depends(get_db),
    limit: int = Query(100, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> ToolList:
    tools, total = list_tools(db, limit=limit, offset=offset)
    return ToolList(total=total, tools=tools)


@router.post(
    "",
    response_model=ToolRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_demo_operator_access)],
)
@limiter.limit(f"{_settings.rate_limit_mutations_per_minute}/minute")
def register_tool(
    request: Request,
    payload: ToolCreate,
    db: Session = Depends(get_db),
) -> ToolRead:
    try:
        return create_tool(db, payload)
    except (InvalidImplementationRefError, UnsupportedToolRegistrationError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except DuplicateToolError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/{tool_id}", response_model=ToolRead)
def tool_detail(tool_id: str, db: Session = Depends(get_db)) -> ToolRead:
    tool = get_tool(db, tool_id)
    if tool is None:
        raise HTTPException(status_code=404, detail=f"Unknown tool id: {tool_id}")
    return tool
