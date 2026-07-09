"""Control-plane run service (PRD FR-8, FR-9, FR-11).

Reuses Project 1's execution primitives from ``app.agent.service`` (claim,
resolve version, run the workflow, finalize, timeout self-heal) rather than
duplicating them (Decision D5). This module adds the control-plane surface on
top: a list query with agent-version/status filters, an explicit run creator
that validates publication, and an operator/API-level status transition that
enforces the lifecycle state machine.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.agent.persistence import utcnow_naive
from app.agent.schemas import AgentRunDetail, AgentRunSummary
from app.agent.service import (
    abandon_orphaned_runs_for_incident,
    get_run_detail,
    invalidate_run_detail_cache,
)
from app.agents.service import get_published_version
from app.logging_config import get_logger
from app.models import AgentRun, Incident
from app.runs.lifecycle import validate_transition

logger = get_logger(__name__)


class RunConflictError(Exception):
    """A run cannot be created because it conflicts with an existing active run.

    Raised when the partial unique index ``uq_agent_runs_active_incident``
    rejects a concurrent launch against the same still-active incident. The
    control plane has no reuse path (unlike the incident-bound creator), so the
    router maps this to HTTP 409 so the caller can retry or inspect the
    in-flight run rather than receiving an opaque 500.
    """


def list_runs(
    session: Session,
    *,
    agent_version_id: str | None = None,
    status: str | None = None,
    limit: int = 100,
) -> list[AgentRunSummary]:
    """List runs, optionally filtered by agent version and status (PRD FR-8)."""
    stmt = select(AgentRun).order_by(AgentRun.created_at.desc()).limit(limit)
    if agent_version_id is not None:
        stmt = stmt.where(AgentRun.agent_version_id == agent_version_id)
    if status is not None:
        stmt = stmt.where(AgentRun.status == status)
    runs = session.scalars(stmt).all()
    return [
        AgentRunSummary(
            id=run.id,
            incident_id=run.incident_id,
            agent_id=run.agent_id,
            agent_version_id=run.agent_version_id,
            status=run.status,  # type: ignore[arg-type]
            trace_id=run.trace_id,
            trace_url=run.trace_url,
            trace_provider=run.trace_provider,
            token_estimate=run.token_estimate,
            prompt_tokens=run.prompt_tokens,
            completion_tokens=run.completion_tokens,
            cost_estimate_usd=run.cost_estimate_usd,
            error=run.error,
            started_at=run.started_at,
            completed_at=run.completed_at,
            created_at=run.created_at,
            updated_at=run.updated_at,
        )
        for run in runs
    ]


def create_control_plane_run(
    session: Session,
    *,
    agent_version_id: str,
    input_payload: dict[str, Any] | None = None,
    incident_id: str | None = None,
) -> AgentRunDetail:
    """Create a queued control-plane run against a published agent version.

    Validates the version is published (raises ``LookupError`` otherwise,
    mirroring ``app.agent.service.create_investigation_run``). No
    idempotency/force/reuse: the control plane is an explicit launch surface.
    """
    version = get_published_version(session, agent_version_id)
    if version is None:
        raise LookupError(f"Unknown or unpublished agent version id: {agent_version_id}")

    # Validate the incident exists (when supplied) before the insert. Without
    # this, a non-existent incident_id would trip the incidents FK constraint on
    # PostgreSQL and surface as an IntegrityError -- which the partial-index
    # handler below maps to a misleading 409 "active run exists" instead of a
    # 404. Mirrors ``create_investigation_run``'s pre-check. SQLite does not
    # enforce FKs without ``PRAGMA foreign_keys=ON`` (not set here), so this
    # Python-level guard is the only thing that surfaces the 404 in tests.
    if incident_id is not None:
        if session.get(Incident, incident_id) is None:
            raise LookupError(f"Unknown incident id: {incident_id}")
        # Self-heal before insert: if a previous Celery worker crashed mid-run
        # on this incident, a stale ``running`` row would trip the partial
        # unique index below as a 409 with no automatic recovery. Mirrors
        # ``create_investigation_run``'s pre-insert reap so the control-plane
        # surface recovers on the next launch attempt instead of waiting for an
        # API restart or a manual force-fail transition.
        abandon_orphaned_runs_for_incident(session, incident_id)

    now = utcnow_naive()
    run_id = f"run_{uuid4().hex[:16]}"
    payload = dict(input_payload or {})
    if incident_id is not None:
        payload.setdefault("incident_id", incident_id)
    payload["agent_version"] = {
        "id": version.id,
        "agent_id": version.agent_id,
        "version_number": version.version_number,
        "semantic_version": version.semantic_version,
        "model": version.model,
        "system_prompt": version.system_prompt,
        "enabled_tool_ids": list(version.enabled_tool_ids or []),
        "allowed_scopes": list(version.allowed_scopes or []),
    }
    run = AgentRun(
        id=run_id,
        incident_id=incident_id,
        agent_id=version.agent_id,
        agent_version_id=version.id,
        status="queued",
        trace_id=None,
        trace_url=None,
        trace_provider=None,
        trace_metadata={},
        input_payload=payload,
        final_report=None,
        token_estimate=0,
        cost_estimate_usd=0.0,
        error=None,
        started_at=None,
        completed_at=None,
        created_at=now,
        updated_at=now,
    )
    session.add(run)
    try:
        session.commit()
    except IntegrityError as exc:
        # The partial unique index uq_agent_runs_active_incident rejects a
        # concurrent launch against an incident that already has a queued or
        # running run. The control plane has no reuse path (unlike the
        # incident-bound creator), so surface a clear 409 rather than an
        # opaque 500. Roll back so the row is not left in a half-flushed state.
        session.rollback()
        raise RunConflictError(
            "An active run for this incident already exists; wait for it to "
            "finish or transition it before launching another."
        ) from exc
    return get_run_detail(session, run.id)


def transition_run(session: Session, run_id: str, target: str) -> AgentRunDetail:
    """Apply an operator/API-level status transition, validating it first.

    Raises ``LookupError`` if the run does not exist; lets
    :class:`app.runs.lifecycle.IllegalTransition` propagate (the router maps it to
    HTTP 409). A conditional UPDATE guards against a concurrent mutation winning
    the race: if the run's status changed between validate and apply, the
    persisted state is returned unchanged.

    Timestamps are set for the target state so operator-driven transitions are
    consistent with the executor's own finalization: terminal targets
    (``succeeded``/``failed``) record ``completed_at``, and a transition into
    ``running`` records ``started_at`` when the executor hasn't already. Without
    this, an operator force-failing a run would leave ``completed_at`` null and
    the UI would render "In progress" for a terminal run.
    """
    run = session.get(AgentRun, run_id)
    if run is None:
        raise LookupError(f"Unknown agent run id: {run_id}")
    from_status = run.status
    validate_transition(from_status, target)
    now = utcnow_naive()
    values: dict[str, Any] = {"status": target, "updated_at": now}
    if target in ("succeeded", "failed"):
        values["completed_at"] = now
    elif target == "running" and run.started_at is None:
        values["started_at"] = now
    claim = session.execute(
        update(AgentRun)
        .where(AgentRun.id == run.id, AgentRun.status == from_status)
        .values(**values)
    )
    if claim.rowcount == 1:
        session.commit()
        invalidate_run_detail_cache(run.id)
        logger.info(
            "Operator status transition applied",
            extra={
                "run_id": run.id,
                "from_status": from_status,
                "to_status": target,
            },
        )
    else:
        # A concurrent transition won the race; return the persisted state.
        session.rollback()
        # Invalidate before re-reading so a stale cached detail (from a prior
        # GET) is not returned for up to RUN_DETAIL_CACHE_TTL_SECONDS. The
        # executor's own race-loser path does the same; without this a losing
        # transition would surface the pre-race status while the DB holds the
        # winner's new state.
        invalidate_run_detail_cache(run.id)
    return get_run_detail(session, run.id)
