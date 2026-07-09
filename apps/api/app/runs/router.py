"""Control-plane run API surface (PRD Phase 3, FR-8..FR-11).

Mirrors ``app.agent.router``'s shape but for control-plane runs: ``POST /runs``
queues (or runs inline) a run against a published agent version, ``GET /runs``
lists with filters, ``GET /runs/{id}`` / ``/steps`` inspect a run, and
``POST /runs/{id}/transitions`` advances the lifecycle (illegal transitions
return 409 per FR-9). Execution primitives are reused from ``app.agent.service``;
this router only adds the control-plane HTTP surface on top.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.agent.schemas import AgentRunDetail, AgentRunStepRead, AgentRunSummary
from app.agent.service import execute_investigation_run_with_session, get_run_detail
from app.core.access import require_demo_data_access, require_demo_operator_access
from app.core.config import get_settings
from app.core.limiter import limiter
from app.db.session import get_db
from app.runs.lifecycle import IllegalTransition
from app.runs.schemas import RunCreate, RunTransitionRequest
from app.runs.service import (
    RunConflictError,
    create_control_plane_run,
    list_runs,
    transition_run,
)
from app.runs.tasks import run_control_plane_run

router = APIRouter(
    prefix="/runs",
    tags=["runs"],
    dependencies=[Depends(require_demo_data_access)],
)

_settings = get_settings()


def _enqueue_run(run_id: str) -> None:
    """Dispatch a control-plane run to Celery (synchronously in the test env)."""
    settings = get_settings()
    if settings.app_env == "test":
        run_control_plane_run.run(run_id)
    else:
        run_control_plane_run.delay(run_id)


@router.post(
    "",
    status_code=status.HTTP_202_ACCEPTED,
    responses={
        404: {"description": "Unknown agent version id or incident id."},
        409: {
            "description": (
                "An active (queued or running) run for this incident already "
                "exists; wait for it to finish or transition it first."
            )
        },
    },
)
@limiter.limit(f"{_settings.rate_limit_mutations_per_minute}/minute")
def create_run(
    request: Request,  # noqa: ARG001  (required by slowapi limiter)
    payload: RunCreate,
    _operator: None = Depends(require_demo_operator_access),
    db: Session = Depends(get_db),
) -> AgentRunDetail:
    """Queue a control-plane run against a published agent version (FR-8).

    With ``run_inline`` the run executes synchronously on the request session
    (used by tests and inline operators); otherwise it is dispatched to Celery
    and the endpoint returns 202 with the run still queued. Returns 404 for an
    unknown/unpublished agent version or an unknown incident id, and 409 when a
    concurrent launch collides on the partial unique index for an active run.
    """
    try:
        run = create_control_plane_run(
            db,
            agent_version_id=payload.agent_version_id,
            input_payload=payload.input_payload,
            incident_id=payload.incident_id,
        )
        if payload.run_inline:
            run = execute_investigation_run_with_session(db, run.id)
        else:
            _enqueue_run(run.id)
    except LookupError as exc:
        # Unknown/unpublished agent version, or an unknown run id surfaced by
        # the executor. Map to 404 rather than a 500.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    except RunConflictError as exc:
        # A concurrent launch against the same still-active incident collided
        # on the partial unique index. Map to 409 so the caller retries or
        # inspects the in-flight run rather than treating it as a server error.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc

    return run


@router.get("")
def list_runs_endpoint(
    agent_version_id: str | None = None,
    status: str | None = None,  # noqa: A002  (query param shadows builtin name)
    db: Session = Depends(get_db),
) -> list[AgentRunSummary]:
    """List runs, optionally filtered by agent version and status (FR-8)."""
    return list_runs(db, agent_version_id=agent_version_id, status=status)


@router.get("/{run_id}", responses={404: {"description": "Unknown run id."}})
def get_run_endpoint(run_id: str, db: Session = Depends(get_db)) -> AgentRunDetail:
    try:
        return get_run_detail(db, run_id)
    except LookupError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc


@router.get(
    "/{run_id}/steps", responses={404: {"description": "Unknown run id."}}
)
def get_run_steps_endpoint(
    run_id: str, db: Session = Depends(get_db)
) -> list[AgentRunStepRead]:
    try:
        return get_run_detail(db, run_id).steps
    except LookupError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc


@router.post(
    "/{run_id}/transitions",
    responses={
        404: {"description": "Unknown run id."},
        409: {"description": "Illegal status transition for the run's current state."},
    },
)
@limiter.limit(f"{_settings.rate_limit_mutations_per_minute}/minute")
def transition_run_endpoint(
    request: Request,  # noqa: ARG001  (required by slowapi limiter)
    run_id: str,
    payload: RunTransitionRequest,
    _operator: None = Depends(require_demo_operator_access),
    db: Session = Depends(get_db),
) -> AgentRunDetail:
    """Apply an operator/API-level status transition (FR-9).

    Illegal transitions return 409; unknown runs return 404.
    """
    try:
        return transition_run(db, run_id, payload.status)
    except IllegalTransition as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    except LookupError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
