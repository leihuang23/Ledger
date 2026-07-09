from __future__ import annotations

import secrets
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.agents.schemas import (
    AgentCreate,
    AgentVersionCreate,
    AgentVersionUpdate,
    VersionDetail,
    VersionSummary,
)
from app.models import Agent, AgentVersion
from app.tools.scopes import DEFAULT_V1_ALLOWED_SCOPES


class AgentNotFoundError(LookupError):
    pass


class VersionNotFoundError(LookupError):
    pass


class ImmutableVersionError(ValueError):
    pass


class InvalidVersionConfigError(ValueError):
    pass


ALLOWED_MODELS: frozenset[str] = frozenset(
    {
        "gpt-4o-mini",
        "gpt-4o",
        "claude-3-5-sonnet-latest",
        "claude-3-haiku-20240307",
    }
)
DEFAULT_ENABLED_TOOL_IDS: tuple[str, ...] = (
    "query_revenue_metrics",
    "fetch_account_details",
    "search_docs",
    "fetch_support_tickets",
)


def _validate_version_config(
    *,
    model: str | None,
    enabled_tool_ids: list[str] | None,
    allowed_scopes: list[str] | None = None,
) -> None:
    from app.agent.tools import TOOL_IDS
    from app.tools.scopes import ALLOWED_SCOPES

    if model is not None and model not in ALLOWED_MODELS:
        allowed = ", ".join(sorted(ALLOWED_MODELS))
        raise InvalidVersionConfigError(
            f"Unsupported model: {model!r}. Allowed models: {allowed}"
        )
    if enabled_tool_ids is not None:
        unknown = set(enabled_tool_ids) - set(TOOL_IDS)
        if unknown:
            raise InvalidVersionConfigError(
                "Unknown tool ids: " + ", ".join(sorted(unknown))
            )
    if allowed_scopes is not None:
        unknown = set(allowed_scopes) - ALLOWED_SCOPES
        if unknown:
            raise InvalidVersionConfigError(
                "Unknown permission scopes: " + ", ".join(sorted(unknown))
            )


class DuplicateAgentError(ValueError):
    pass


class ConcurrentPublishError(ValueError):
    pass


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _version_summary(version: AgentVersion) -> VersionSummary:
    return VersionSummary(
        id=version.id,
        version_number=version.version_number,
        semantic_version=version.semantic_version,
        status=version.status,
        model=version.model,
        created_at=version.created_at,
        published_at=version.published_at,
        forked_from_version_id=version.forked_from_version_id,
    )


def _version_detail(version: AgentVersion) -> VersionDetail:
    return VersionDetail(
        id=version.id,
        version_number=version.version_number,
        semantic_version=version.semantic_version,
        status=version.status,
        model=version.model,
        created_at=version.created_at,
        published_at=version.published_at,
        forked_from_version_id=version.forked_from_version_id,
        system_prompt=version.system_prompt or "",
        temperature=version.temperature,
        max_tokens=version.max_tokens,
        enabled_tool_ids=list(version.enabled_tool_ids or []),
        allowed_scopes=list(version.allowed_scopes or []),
        published_by=version.published_by,
    )


def _published_version_recency_key(version: AgentVersion) -> tuple[int, int, datetime, str]:
    version_number = version.version_number if version.version_number is not None else -1
    published_at = version.published_at or datetime.min
    return (
        1 if version.version_number is not None else 0,
        version_number,
        published_at,
        version.id,
    )


def _bulk_version_metadata(
    session: Session, agent_ids: list[str]
) -> dict[str, dict[str, Any]]:
    if not agent_ids:
        return {}
    versions = session.scalars(
        select(AgentVersion)
        .where(AgentVersion.agent_id.in_(agent_ids))
        .order_by(AgentVersion.version_number, AgentVersion.created_at.desc())
    ).all()
    result: dict[str, dict[str, Any]] = {
        aid: {"count": 0, "latest_published": None, "latest_draft": None}
        for aid in agent_ids
    }
    for v in versions:
        meta = result[v.agent_id]
        meta["count"] += 1
        if v.status == "published":
            if (
                meta["latest_published"] is None
                or _published_version_recency_key(v)
                > _published_version_recency_key(meta["latest_published"])
            ):
                meta["latest_published"] = v
        elif v.status == "draft":
            if meta["latest_draft"] is None or v.created_at > meta["latest_draft"].created_at:
                meta["latest_draft"] = v
    return result


def list_agents(
    session: Session,
    *,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[dict[str, Any]], int]:
    total = int(session.scalar(select(func.count()).select_from(Agent)) or 0)
    agents = session.scalars(
        select(Agent)
        .order_by(Agent.created_at, Agent.id)
        .limit(limit)
        .offset(offset)
    ).all()

    agent_ids = [a.id for a in agents]
    version_meta = _bulk_version_metadata(session, agent_ids)

    result: list[dict[str, Any]] = []
    for agent in agents:
        meta = version_meta.get(agent.id, {"count": 0, "latest_published": None, "latest_draft": None})
        result.append({
            "id": agent.id,
            "name": agent.name,
            "description": agent.description,
            "default_model": agent.default_model,
            "created_at": agent.created_at,
            "updated_at": agent.updated_at,
            "latest_published_version": (
                _version_summary(meta["latest_published"])
                if meta["latest_published"] is not None
                else None
            ),
            "current_draft_version": (
                _version_summary(meta["latest_draft"])
                if meta["latest_draft"] is not None
                else None
            ),
            "version_count": meta["count"],
        })
    return result, total


def get_agent(session: Session, agent_id: str) -> dict[str, Any] | None:
    agent = session.scalar(
        select(Agent)
        .options(selectinload(Agent.versions))
        .where(Agent.id == agent_id)
    )
    if agent is None:
        return None

    versions = list(agent.versions)
    published = sorted(
        [v for v in versions if v.status == "published"],
        key=_published_version_recency_key,
        reverse=True,
    )
    drafts = sorted(
        [v for v in versions if v.status == "draft"],
        key=lambda v: v.created_at,
        reverse=True,
    )
    ordered = published + drafts
    count = len(versions)

    latest_published = published[0] if published else None
    current_draft = drafts[0] if drafts else None

    return {
        "id": agent.id,
        "name": agent.name,
        "description": agent.description,
        "default_model": agent.default_model,
        "created_at": agent.created_at,
        "updated_at": agent.updated_at,
        "latest_published_version": _version_summary(latest_published) if latest_published else None,
        "current_draft_version": _version_summary(current_draft) if current_draft else None,
        "version_count": count,
        "versions": [_version_summary(v) for v in ordered],
    }


def create_agent(session: Session, payload: AgentCreate) -> dict[str, Any]:
    _validate_version_config(model=payload.default_model, enabled_tool_ids=None)

    now = _utcnow()
    agent = Agent(
        id=payload.id,
        name=payload.name,
        description=payload.description,
        default_model=payload.default_model,
        created_at=now,
        updated_at=now,
    )
    draft_version = AgentVersion(
        id=f"{payload.id}_draft_v0",
        agent_id=payload.id,
        version_number=None,
        semantic_version=None,
        status="draft",
        system_prompt=payload.system_prompt,
        model=payload.default_model,
        temperature=0.1,
        max_tokens=1024,
        enabled_tool_ids=list(DEFAULT_ENABLED_TOOL_IDS),
        # Match the v1 default applied by create_version (line 428), seed.py,
        # and migration 0012's backfill. An empty list here would make a
        # published-from-scratch agent block every data tool via can_call_tool.
        allowed_scopes=list(DEFAULT_V1_ALLOWED_SCOPES),
        published_at=None,
        published_by=None,
        forked_from_version_id=None,
        created_at=now,
        updated_at=now,
    )
    session.add(agent)
    session.add(draft_version)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        existing = session.get(Agent, payload.id)
        if existing is not None:
            raise DuplicateAgentError(f"Agent already exists: {payload.id}")
        raise

    result = get_agent(session, payload.id)
    assert result is not None
    return result


def list_versions(
    session: Session,
    agent_id: str,
    *,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[VersionSummary], int]:
    agent = session.get(Agent, agent_id)
    if agent is None:
        raise AgentNotFoundError(f"Unknown agent id: {agent_id}")

    total = int(
        session.scalar(
            select(func.count(AgentVersion.id)).where(AgentVersion.agent_id == agent_id)
        )
        or 0
    )

    if offset >= total:
        return [], total

    versions: list[AgentVersion] = []
    remaining = limit
    current_offset = offset

    pub_fetch_limit = current_offset + remaining
    pub_versions = session.scalars(
        select(AgentVersion)
        .where(
            AgentVersion.agent_id == agent_id,
            AgentVersion.status == "published",
        )
        .order_by(
            AgentVersion.version_number.is_(None),
            AgentVersion.version_number,
            AgentVersion.published_at,
            AgentVersion.id,
        )
        .limit(pub_fetch_limit)
    ).all()

    if current_offset < len(pub_versions):
        page = pub_versions[current_offset : current_offset + remaining]
        versions.extend(page)
        remaining -= len(page)
        current_offset = 0
    else:
        current_offset -= len(pub_versions)

    if remaining > 0:
        draft_fetch_limit = current_offset + remaining
        draft_versions = session.scalars(
            select(AgentVersion)
            .where(
                AgentVersion.agent_id == agent_id,
                AgentVersion.status == "draft",
            )
            .order_by(AgentVersion.created_at.desc(), AgentVersion.id.desc())
            .limit(draft_fetch_limit)
        ).all()
        if current_offset < len(draft_versions):
            versions.extend(draft_versions[current_offset : current_offset + remaining])

    return [_version_summary(v) for v in versions], total


def get_version(session: Session, agent_id: str, version_id: str) -> VersionDetail | None:
    version = session.get(AgentVersion, version_id)
    if version is None or version.agent_id != agent_id:
        return None
    return _version_detail(version)


def create_version(
    session: Session,
    agent_id: str,
    payload: AgentVersionCreate,
) -> VersionDetail:
    agent = session.scalar(
        select(Agent)
        .options(selectinload(Agent.versions))
        .where(Agent.id == agent_id)
    )
    if agent is None:
        raise AgentNotFoundError(f"Unknown agent id: {agent_id}")

    now = _utcnow()

    source: AgentVersion | None = None
    if payload.fork_from_version_id:
        source = session.get(AgentVersion, payload.fork_from_version_id)
        if source is None or source.agent_id != agent_id:
            raise VersionNotFoundError(
                f"Unknown source version: {payload.fork_from_version_id}"
            )
    else:
        published = [v for v in agent.versions if v.status == "published"]
        if published:
            source = max(published, key=_published_version_recency_key)

    system_prompt = payload.system_prompt
    model = payload.model
    temperature = payload.temperature
    max_tokens = payload.max_tokens
    enabled_tool_ids = payload.enabled_tool_ids
    allowed_scopes = payload.allowed_scopes

    if source is not None:
        if system_prompt is None:
            system_prompt = source.system_prompt
        if model is None:
            model = source.model
        if temperature is None:
            temperature = source.temperature
        if max_tokens is None:
            max_tokens = source.max_tokens
        if enabled_tool_ids is None:
            enabled_tool_ids = list(source.enabled_tool_ids or [])
        if allowed_scopes is None:
            allowed_scopes = list(source.allowed_scopes or [])
        forked_from = source.id
    else:
        if system_prompt is None:
            system_prompt = ""
        if model is None:
            model = agent.default_model
        if temperature is None:
            temperature = 0.1
        if max_tokens is None:
            max_tokens = 1024
        if enabled_tool_ids is None:
            enabled_tool_ids = list(DEFAULT_ENABLED_TOOL_IDS)
        if allowed_scopes is None:
            allowed_scopes = list(DEFAULT_V1_ALLOWED_SCOPES)
        forked_from = None

    _validate_version_config(
        model=model,
        enabled_tool_ids=enabled_tool_ids,
        allowed_scopes=allowed_scopes,
    )

    draft_count = sum(1 for v in agent.versions if v.status == "draft")
    version_id = f"{agent_id}_draft_{draft_count + 1}_{secrets.token_hex(3)}"
    new_version = AgentVersion(
        id=version_id,
        agent_id=agent_id,
        version_number=None,
        semantic_version=None,
        status="draft",
        system_prompt=system_prompt,
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
        enabled_tool_ids=enabled_tool_ids,
        allowed_scopes=allowed_scopes,
        published_at=None,
        published_by=None,
        forked_from_version_id=forked_from,
        created_at=now,
        updated_at=now,
    )
    session.add(new_version)
    agent.updated_at = now
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        raise
    session.refresh(new_version)
    return _version_detail(new_version)


def update_version(
    session: Session,
    agent_id: str,
    version_id: str,
    payload: AgentVersionUpdate,
) -> VersionDetail:
    version = session.scalar(
        select(AgentVersion)
        .where(AgentVersion.id == version_id, AgentVersion.agent_id == agent_id)
        .with_for_update()
    )
    if version is None:
        raise VersionNotFoundError(f"Unknown version: {version_id}")
    if version.status == "published":
        raise ImmutableVersionError(
            "Published versions are immutable. Create a new draft instead."
        )

    now = _utcnow()
    _validate_version_config(
        model=payload.model,
        enabled_tool_ids=payload.enabled_tool_ids,
        allowed_scopes=payload.allowed_scopes,
    )
    if payload.system_prompt is not None:
        version.system_prompt = payload.system_prompt
    if payload.model is not None:
        version.model = payload.model
    if payload.temperature is not None:
        version.temperature = payload.temperature
    if payload.max_tokens is not None:
        version.max_tokens = payload.max_tokens
    if payload.enabled_tool_ids is not None:
        version.enabled_tool_ids = payload.enabled_tool_ids
    if payload.allowed_scopes is not None:
        version.allowed_scopes = payload.allowed_scopes
    version.updated_at = now

    agent = session.get(Agent, agent_id)
    if agent is not None:
        agent.updated_at = now

    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        raise
    session.refresh(version)
    return _version_detail(version)


def publish_version(
    session: Session,
    agent_id: str,
    version_id: str,
    published_by: str = "system",
) -> VersionDetail:
    agent = session.scalar(
        select(Agent).where(Agent.id == agent_id).with_for_update()
    )
    if agent is None:
        raise AgentNotFoundError(f"Unknown agent id: {agent_id}")

    version = session.scalar(
        select(AgentVersion)
        .where(AgentVersion.id == version_id, AgentVersion.agent_id == agent_id)
        .with_for_update()
    )
    if version is None:
        raise VersionNotFoundError(f"Unknown version: {version_id}")
    if version.status == "published":
        raise ImmutableVersionError("Version is already published.")
    _validate_version_config(
        model=version.model,
        enabled_tool_ids=list(version.enabled_tool_ids or []),
        allowed_scopes=list(version.allowed_scopes or []),
    )

    now = _utcnow()
    next_number = (
        int(
            session.scalar(
                select(func.coalesce(func.max(AgentVersion.version_number), 0)).where(
                    AgentVersion.agent_id == agent_id,
                    AgentVersion.status == "published",
                )
            )
            or 0
        )
        + 1
    )

    version.status = "published"
    version.version_number = next_number
    version.semantic_version = f"{next_number}.0.0"
    version.published_at = now
    version.published_by = published_by
    version.updated_at = now
    agent.updated_at = now

    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise ConcurrentPublishError(
            "Another publish occurred concurrently; please retry."
        ) from exc
    session.refresh(version)
    return _version_detail(version)


DEFAULT_AGENT_ID = "revenue-ops-agent"
DEFAULT_AGENT_VERSION_ID = "revenue-ops-agent_v1"


def get_published_version(
    session: Session, agent_version_id: str
) -> AgentVersion | None:
    version = session.get(AgentVersion, agent_version_id)
    if version is None or version.status != "published":
        return None
    return version


def get_default_published_version(session: Session) -> AgentVersion | None:
    return session.scalar(
        select(AgentVersion)
        .where(
            AgentVersion.status == "published",
            AgentVersion.agent_id == DEFAULT_AGENT_ID,
        )
        .order_by(
            AgentVersion.version_number.is_(None),
            AgentVersion.version_number.desc(),
            AgentVersion.published_at.desc(),
            AgentVersion.id.desc(),
        )
        .limit(1)
    )
