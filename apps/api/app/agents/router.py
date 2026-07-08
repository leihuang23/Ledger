from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy.orm import Session

from app.agents.schemas import (
    AgentCreate,
    AgentDetail,
    AgentList,
    AgentVersionCreate,
    AgentVersionUpdate,
    PublishResult,
    VersionDetail,
    VersionList,
    VersionSummary,
)
from app.agents.service import (
    AgentNotFoundError,
    ConcurrentPublishError,
    DuplicateAgentError,
    ImmutableVersionError,
    VersionNotFoundError,
    create_agent,
    create_version,
    get_agent,
    get_version,
    list_agents,
    list_versions,
    publish_version,
    update_version,
)
from app.core.access import require_demo_data_access, require_demo_operator_access
from app.core.config import get_settings
from app.core.limiter import limiter
from app.db.session import get_db

_settings = get_settings()

router = APIRouter(
    prefix="/agents",
    tags=["agents"],
    dependencies=[Depends(require_demo_data_access)],
)


@router.get("")
def agents_list(
    db: Session = Depends(get_db),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> AgentList:
    agents, total = list_agents(db, limit=limit, offset=offset)
    return AgentList(total=total, agents=agents)


@router.post("", dependencies=[Depends(require_demo_operator_access)])
@limiter.limit(f"{_settings.rate_limit_mutations_per_minute}/minute")
def create_agent_endpoint(
    request: Request,
    payload: AgentCreate,
    response: Response,
    db: Session = Depends(get_db),
) -> AgentDetail:
    try:
        agent = create_agent(db, payload)
    except DuplicateAgentError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    response.status_code = status.HTTP_201_CREATED
    return AgentDetail(**agent)


@router.get("/{agent_id}")
def agent_detail(
    agent_id: str,
    db: Session = Depends(get_db),
) -> AgentDetail:
    agent = get_agent(db, agent_id)
    if agent is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Unknown agent id: {agent_id}",
        )
    return AgentDetail(**agent)


@router.get("/{agent_id}/versions")
def versions_list(
    agent_id: str,
    db: Session = Depends(get_db),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> VersionList:
    try:
        versions, total = list_versions(db, agent_id, limit=limit, offset=offset)
        return VersionList(total=total, versions=versions)
    except AgentNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc


@router.post(
    "/{agent_id}/versions",
    dependencies=[Depends(require_demo_operator_access)],
)
@limiter.limit(f"{_settings.rate_limit_mutations_per_minute}/minute")
def create_version_endpoint(
    request: Request,
    agent_id: str,
    payload: AgentVersionCreate,
    response: Response,
    db: Session = Depends(get_db),
) -> VersionDetail:
    try:
        version = create_version(db, agent_id, payload)
    except (AgentNotFoundError, VersionNotFoundError) as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    response.status_code = status.HTTP_201_CREATED
    return version


@router.patch(
    "/{agent_id}/versions/{version_id}",
    dependencies=[Depends(require_demo_operator_access)],
)
@limiter.limit(f"{_settings.rate_limit_mutations_per_minute}/minute")
def update_version_endpoint(
    request: Request,
    agent_id: str,
    version_id: str,
    payload: AgentVersionUpdate,
    db: Session = Depends(get_db),
) -> VersionDetail:
    try:
        return update_version(db, agent_id, version_id, payload)
    except VersionNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    except ImmutableVersionError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc


@router.post(
    "/{agent_id}/versions/{version_id}/publish",
    dependencies=[Depends(require_demo_operator_access)],
)
@limiter.limit(f"{_settings.rate_limit_mutations_per_minute}/minute")
def publish_version_endpoint(
    request: Request,
    agent_id: str,
    version_id: str,
    db: Session = Depends(get_db),
) -> PublishResult:
    try:
        version = publish_version(db, agent_id, version_id)
    except (AgentNotFoundError, VersionNotFoundError) as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    except (ImmutableVersionError, ConcurrentPublishError) as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    return PublishResult(version=version)


@router.get("/{agent_id}/versions/{version_id}")
def version_detail(
    agent_id: str,
    version_id: str,
    db: Session = Depends(get_db),
) -> VersionDetail:
    version = get_version(db, agent_id, version_id)
    if version is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Unknown version: {version_id}",
        )
    return version
