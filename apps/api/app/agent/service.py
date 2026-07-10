from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
from uuid import uuid4

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from celery.exceptions import SoftTimeLimitExceeded

from app.agent.persistence import AgentRunRecorder
from app.agent.persistence import utcnow_naive
from app.agent.schemas import (
    AgentRunDetail,
    AgentRunStepRead,
    AgentRunSummary,
    InvestigationReport,
    ModelUsageRead,
)
from app.agent.tracing import AgentTraceHandle, start_agent_trace
from app.agent.workflow import run_investigation_workflow
from app.agents.service import (
    DEFAULT_AGENT_ID,
    DEFAULT_AGENT_VERSION_ID,
    get_published_version,
)
from app.approvals.service import list_mock_actions_for_run, propose_actions_for_report
from app.cache import Cache
from app.db.session import SessionLocal
from app.llm import build_llm_client_for_version
from app.logging_config import get_logger
from app.models import (
    AgentRun,
    AgentRunStep,
    AgentVersion,
    ApprovalRequest,
    Incident,
    ModelUsage,
)
from app.tools.policy import REASON_SCOPE_NOT_ALLOWED, can_call_tool
from app.tools.scopes import TOOL_SCOPES

ACTIVE_RUN_STALE_AFTER = timedelta(minutes=10)
ACTIVE_RUN_STATUSES = ("queued", "running")
BLOCKING_RUN_STATUSES = (*ACTIVE_RUN_STATUSES, "waiting_for_approval")

logger = get_logger(__name__)


IDEMPOTENCY_CACHE_TTL_SECONDS = 3600


def _idempotency_request_key(
    key: str,
    *,
    incident_id: str,
    requested_agent_version_id: str | None,
) -> str:
    version_selector = (
        f"explicit:{requested_agent_version_id}"
        if requested_agent_version_id is not None
        else "default"
    )
    return f"{incident_id}:{version_selector}:{key}"


def create_investigation_run(
    session: Session,
    incident_id: str,
    *,
    force: bool = False,
    idempotency_key: str | None = None,
    agent_version_id: str | None = None,
) -> tuple[AgentRunDetail, bool]:
    incident = session.get(Incident, incident_id)
    if incident is None:
        raise LookupError(f"Unknown incident id: {incident_id}")

    if agent_version_id is not None:
        agent_version = get_published_version(session, agent_version_id)
        if agent_version is None:
            raise LookupError(
                f"Unknown or unpublished agent version id: {agent_version_id}"
            )
    else:
        # Preserve the Project1 route contract: callers that do not opt into a
        # control-plane version still execute the immutable v1 snapshot. Newer
        # versions are selected explicitly through the versioned run surface.
        agent_version = get_published_version(session, DEFAULT_AGENT_VERSION_ID)
        if agent_version is None:
            raise LookupError("The Project1 v1 agent version is unavailable")
    resolved_agent_id = agent_version.agent_id
    resolved_version_id = agent_version.id

    _abandon_orphaned_runs(session, incident_id)

    if idempotency_key:
        cache = Cache()
        request_key = _idempotency_request_key(
            idempotency_key,
            incident_id=incident_id,
            requested_agent_version_id=agent_version_id,
        )
        existing_run_id = cache.get_idempotency_value(request_key)
        if existing_run_id:
            existing_run = session.get(AgentRun, existing_run_id)
            if existing_run is not None and existing_run.incident_id == incident_id:
                return get_run_detail(session, existing_run_id), False

    if not force:
        reusable_run_id = _latest_reusable_run_id(
            session, incident_id, agent_version_id=resolved_version_id
        )
        if reusable_run_id is not None:
            reusable_run = session.get(AgentRun, reusable_run_id)
            if reusable_run is not None and reusable_run.status == "succeeded":
                backfill_report_actions(session, reusable_run_id)
            return get_run_detail(session, reusable_run_id), False

    now = utcnow_naive()
    run_id = f"run_{uuid4().hex[:16]}"
    run = AgentRun(
        id=run_id,
        incident_id=incident_id,
        agent_id=resolved_agent_id,
        agent_version_id=resolved_version_id,
        status="queued",
        trace_id=None,
        trace_url=None,
        trace_provider=None,
        trace_metadata={},
        input_payload={
            "incident_id": incident_id,
            "agent_version": {
                "id": agent_version.id,
                "agent_id": agent_version.agent_id,
                "version_number": agent_version.version_number,
                "semantic_version": agent_version.semantic_version,
                "model": agent_version.model,
                "temperature": agent_version.temperature,
                "max_tokens": agent_version.max_tokens,
                "system_prompt": agent_version.system_prompt,
                "enabled_tool_ids": list(agent_version.enabled_tool_ids or []),
            },
        },
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
    except IntegrityError:
        session.rollback()
        reusable_run_id = _latest_reusable_run_id(
            session, incident_id, active_only=True, agent_version_id=resolved_version_id
        )
        if reusable_run_id is None:
            raise
        return get_run_detail(session, reusable_run_id), False

    if idempotency_key:
        cache = Cache()
        request_key = _idempotency_request_key(
            idempotency_key,
            incident_id=incident_id,
            requested_agent_version_id=agent_version_id,
        )
        cache.set_idempotency_value(
            request_key, run.id, IDEMPOTENCY_CACHE_TTL_SECONDS
        )

    return get_run_detail(session, run.id), True


def start_investigation_run(
    session: Session,
    incident_id: str,
    *,
    force: bool = False,
    idempotency_key: str | None = None,
    agent_version_id: str | None = None,
) -> tuple[AgentRunDetail, bool]:
    run, created = create_investigation_run(
        session,
        incident_id,
        force=force,
        idempotency_key=idempotency_key,
        agent_version_id=agent_version_id,
    )
    if not created:
        return run, False

    return execute_investigation_run_with_session(session, run.id), True


def execute_investigation_run(run_id: str) -> None:
    with SessionLocal() as session:
        execute_investigation_run_with_session(session, run_id)


def _fail_running_run(
    session: Session,
    *,
    run_id: str,
    trace: AgentTraceHandle,
    error: Exception,
    log_message: str,
) -> AgentRunDetail:
    session.rollback()
    failed_run = session.get(AgentRun, run_id)
    if failed_run is None:
        _finish_trace(trace, error=str(error))
        raise error
    if failed_run.status != "running":
        _invalidate_run_detail_cache(failed_run.id)
        current = get_run_detail(session, failed_run.id)
        _finish_trace(
            trace,
            outputs=current.final_report,
            error=current.error,
        )
        return current
    incident_id = failed_run.incident_id
    completed_at = utcnow_naive()
    # Conditional UPDATE so a concurrent operator transition (e.g. force-succeed
    # via POST /runs/{id}/transitions) in the TOCTOU window between the status
    # check above and the commit is not overwritten by this failure finalization.
    # Mirrors the success path (below) and mark_run_failed_on_timeout.
    claim = session.execute(
        update(AgentRun)
        .where(AgentRun.id == run_id, AgentRun.status == "running")
        .values(
            status="failed",
            error=str(error),
            completed_at=completed_at,
            updated_at=completed_at,
        )
    )
    if claim.rowcount != 1:
        session.rollback()
        _invalidate_run_detail_cache(run_id)
        current = get_run_detail(session, run_id)
        _finish_trace(
            trace,
            outputs=current.final_report,
            error=current.error,
        )
        return current
    session.commit()
    _invalidate_run_detail_cache(run_id)
    _finish_trace(trace, error=str(error))
    logger.error(
        log_message,
        extra={
            "run_id": run_id,
            "incident_id": incident_id,
            "error": str(error),
        },
    )
    return get_run_detail(session, run_id)


def execute_investigation_run_with_session(
    session: Session, run_id: str
) -> AgentRunDetail:
    run = session.get(AgentRun, run_id)
    if run is None:
        raise LookupError(f"Unknown agent run id: {run_id}")
    _abandon_orphaned_run(session, run)
    session.refresh(run)
    if run.status == "running":
        return get_run_detail(session, run_id)
    if run.status != "queued":
        return get_run_detail(session, run_id)

    now = utcnow_naive()
    trace = start_agent_trace(run_id=run.id, incident_id=run.incident_id)
    logger.info(
        "Starting investigation run",
        extra={
            "run_id": run.id,
            "incident_id": run.incident_id,
            "agent_id": run.agent_id,
            "agent_version_id": run.agent_version_id,
        },
    )
    claim = session.execute(
        update(AgentRun)
        .where(AgentRun.id == run.id, AgentRun.status == "queued")
        .values(
            status="running",
            trace_id=trace.trace_id,
            trace_url=trace.trace_url,
            trace_provider=trace.provider,
            trace_metadata=trace.metadata,
            started_at=run.started_at or now,
            completed_at=None,
            error=None,
            updated_at=now,
        )
    )
    if claim.rowcount != 1:
        session.rollback()
        _invalidate_run_detail_cache(run_id)
        _finish_trace(trace, error="Agent run was already claimed.")
        logger.warning(
            "Agent run was already claimed",
            extra={"run_id": run.id},
        )
        return get_run_detail(session, run_id)
    session.commit()
    session.refresh(run)
    _invalidate_run_detail_cache(run_id)

    # Phase 3 execution is incident-bound (P3-T3): the v1 investigation workflow
    # starts at intake_node which loads the incident by id. A run with no
    # incident_id cannot drive that workflow, so fail it gracefully with a clear
    # reason instead of letting the workflow raise an opaque
    # "Unknown incident id: None". The schema stays nullable (FR-8) so this is a
    # forward-compatible guard, not a schema constraint.
    if run.incident_id is None:
        reason = (
            "Control-plane runs without an incident_id are not supported by "
            "the v1 investigation workflow."
        )
        now = utcnow_naive()
        claim = session.execute(
            update(AgentRun)
            .where(AgentRun.id == run.id, AgentRun.status == "running")
            .values(
                status="failed",
                error=reason,
                completed_at=now,
                updated_at=now,
            )
        )
        if claim.rowcount != 1:
            session.rollback()
            _invalidate_run_detail_cache(run.id)
            current = get_run_detail(session, run.id)
            _finish_trace(
                trace,
                outputs=current.final_report,
                error=current.error,
            )
            return current
        session.commit()
        _invalidate_run_detail_cache(run.id)
        _finish_trace(trace, error=reason)
        logger.warning(
            "Control-plane run has no incident_id; cannot execute v1 workflow",
            extra={"run_id": run.id},
        )
        return get_run_detail(session, run.id)

    agent_version = session.get(AgentVersion, run.agent_version_id) if run.agent_version_id else None
    version_warning: str | None = None
    fallback_version_id: str | None = None
    if agent_version is None:
        from app.agents.service import get_default_published_version
        fallback = get_default_published_version(session)
        if fallback is None:
            trace_error = (
                f"Agent version {run.agent_version_id!r} not found and no default "
                "published version is available; cannot start investigation."
            )
            db_error = (
                f"Agent version {run.agent_version_id!r} not found and no default "
                "published version is available. Seed the database or create a "
                "default agent before running investigations."
            )
            now = utcnow_naive()
            claim = session.execute(
                update(AgentRun)
                .where(AgentRun.id == run.id, AgentRun.status == "running")
                .values(
                    status="failed",
                    error=db_error,
                    completed_at=now,
                    updated_at=now,
                )
            )
            if claim.rowcount != 1:
                session.rollback()
                _invalidate_run_detail_cache(run.id)
                current = get_run_detail(session, run.id)
                _finish_trace(
                    trace,
                    outputs=current.final_report,
                    error=current.error,
                )
                return current
            session.commit()
            _invalidate_run_detail_cache(run.id)
            _finish_trace(trace, error=trace_error)
            logger.error(
                "Cannot start investigation: no agent version available",
                extra={"run_id": run.id, "requested_version_id": run.agent_version_id},
            )
            return get_run_detail(session, run.id)
        llm_client = build_llm_client_for_version(fallback)
        resolved_version = fallback
        requested_version_id = run.agent_version_id
        version_warning = (
            f"Agent version {requested_version_id!r} not found; falling back "
            f"to default published version {fallback.id!r}."
        )
        fallback_version_id = fallback.id
        run.agent_id = fallback.agent_id
        run.agent_version_id = fallback.id
        run.trace_metadata = {
            **(run.trace_metadata or {}),
            "version_fallback": {
                "requested_version_id": requested_version_id,
                "fallback_version_id": fallback.id,
                "reason": "requested_version_not_found",
            },
        }
        if isinstance(run.input_payload, dict):
            run.input_payload = {
                **run.input_payload,
                "agent_version_id": fallback.id,
                "agent_version": {
                    "id": fallback.id,
                    "version_number": fallback.version_number,
                    "semantic_version": fallback.semantic_version,
                    "model": fallback.model,
                    "system_prompt": fallback.system_prompt,
                    "enabled_tool_ids": list(fallback.enabled_tool_ids or []),
                },
                "version_fallback": {
                    "from": requested_version_id,
                    "to": fallback.id,
                },
            }
        session.flush()
    else:
        llm_client = build_llm_client_for_version(agent_version)
        resolved_version = agent_version
    if version_warning:
        logger.warning(
            "Agent version fallback during execution",
            extra={
                "run_id": run.id,
                "requested_version_id": requested_version_id,
                "fallback_version_id": fallback_version_id,
                "warning": version_warning,
            },
        )
        session.commit()
        session.refresh(run)
    # PRD FR-7: enforce the agent version's tool permission policy at dispatch
    # time. A tool is callable iff it is in ``enabled_tool_ids`` AND its scope is
    # in ``allowed_scopes`` (app.tools.policy.can_call_tool). Tools failing either
    # check are recorded as visible ``blocked`` steps with a granular reason
    # rather than dispatched. Filtering ``effective_enabled`` here (not just
    # collecting reasons) ensures a scope-revoked tool hits the blocked path
    # instead of being dispatched.
    effective_enabled: set[str] = set()
    blocked_reasons: dict[str, str] = {}
    for tool_id in TOOL_SCOPES:
        allowed, reason = can_call_tool(resolved_version, tool_id)
        if allowed:
            effective_enabled.add(tool_id)
        else:
            blocked_reasons[tool_id] = reason

    try:
        report = run_investigation_workflow(
            session, run, trace, llm_client=llm_client,
            enabled_tool_ids=effective_enabled,
            blocked_reasons=blocked_reasons,
        )
    except SoftTimeLimitExceeded:
        # The Celery soft time limit fired mid-workflow. Finish the trace with
        # the clean timeout reason before re-raising so the root observation is
        # finalized on observability backends (Langfuse/Langsmith) instead of
        # being left dangling -- the task's timeout handler runs in a fresh
        # session without access to this trace handle. Then re-raise so the
        # task marks the run failed with ``soft_time_limit_exceeded`` and
        # Celery records the timeout as the task failure.
        _finish_trace(trace, error="soft_time_limit_exceeded")
        raise
    except Exception as exc:
        return _fail_running_run(
            session,
            run_id=run.id,
            trace=trace,
            error=exc,
            log_message="Investigation run failed",
        )

    report_ready_at = utcnow_naive()
    finished_run = session.get(AgentRun, run.id)
    if finished_run is None:
        raise RuntimeError(f"Agent run disappeared: {run.id}")
    # Sync the identity map with the DB so a concurrent operator transition
    # (e.g. force-fail via POST /runs/{id}/transitions) is detected instead of
    # being silently overwritten by the success finalization below. Without
    # this refresh, session.get may return a stale "running" status after the
    # recorder's periodic commits expired the object.
    session.refresh(finished_run)
    if finished_run.status != "running":
        _finish_trace(
            trace,
            outputs=finished_run.final_report,
            error=(finished_run.error or "Investigation interrupted before completion.")
            if finished_run.status == "failed"
            else None,
        )
        _invalidate_run_detail_cache(finished_run.id)
        return get_run_detail(session, finished_run.id)
    # Persist the report before action proposal, but deliberately keep the run
    # in ``running``. A control-plane run may need to enter the Phase 5 approval
    # checkpoint after its high-risk mock actions are created; marking it
    # succeeded here would make that terminal state impossible to leave.
    claim = session.execute(
        update(AgentRun)
        .where(AgentRun.id == run.id, AgentRun.status == "running")
        .values(
            error=None,
            final_report=report.model_dump(mode="json"),
            updated_at=report_ready_at,
        )
    )
    if claim.rowcount != 1:
        session.rollback()
        _invalidate_run_detail_cache(run.id)
        current = get_run_detail(session, run.id)
        _finish_trace(
            trace,
            outputs=current.final_report,
            error=current.error
            or "Concurrent status change prevented success finalization.",
        )
        return current
    session.commit()
    # Invalidate immediately so a GET between report persistence and action
    # proposal can at least surface the completed report while the run remains
    # non-terminal.
    _invalidate_run_detail_cache(run.id)
    session.refresh(finished_run)

    try:
        _propose_report_actions(
            session,
            finished_run,
            report,
            trace,
            agent_version=resolved_version,
        )
    except Exception as exc:
        session.rollback()
        failed_run = session.get(AgentRun, finished_run.id)
        if failed_run is None:
            raise
        error_msg = f"Action proposal failed: {exc}"
        completed_at = utcnow_naive()
        claim = session.execute(
            update(AgentRun)
            .where(AgentRun.id == finished_run.id, AgentRun.status == "running")
            .values(
                status="failed",
                error=error_msg,
                completed_at=completed_at,
                updated_at=completed_at,
            )
        )
        _finish_trace(
            trace, outputs=failed_run.final_report, error=error_msg
        )
        if claim.rowcount != 1:
            session.rollback()
            _invalidate_run_detail_cache(finished_run.id)
            return get_run_detail(session, finished_run.id)
        session.commit()
        _invalidate_run_detail_cache(finished_run.id)
        logger.error(
            "Action proposal failed",
            extra={
                "run_id": finished_run.id,
                "incident_id": failed_run.incident_id,
                "error": error_msg,
            },
        )
        return get_run_detail(session, finished_run.id)

    # Serialize the approval checkpoint with approval decisions. Without this
    # row lock, the last decision can observe ``running`` just before this code
    # writes ``waiting_for_approval``, stranding a run with zero pending items.
    locked_run = session.scalar(
        select(AgentRun)
        .where(AgentRun.id == finished_run.id)
        .with_for_update()
    )
    if locked_run is None:
        raise LookupError(f"Unknown agent run id: {finished_run.id}")
    pending_approval_count = int(
        session.scalar(
            select(func.count(ApprovalRequest.id)).where(
                ApprovalRequest.run_id == finished_run.id,
                ApprovalRequest.status == "pending",
            )
        )
        or 0
    )
    is_control_plane_run = (
        isinstance(locked_run.input_payload, dict)
        and locked_run.input_payload.get("run_surface") == "control_plane"
    )
    final_status = (
        "waiting_for_approval"
        if is_control_plane_run and pending_approval_count > 0
        else "succeeded"
    )
    finalized_at = utcnow_naive()
    final_values: dict[str, Any] = {
        "status": final_status,
        "updated_at": finalized_at,
    }
    if final_status == "succeeded":
        final_values["completed_at"] = finalized_at
    else:
        final_values["completed_at"] = None
    final_claim = session.execute(
        update(AgentRun)
        .where(AgentRun.id == locked_run.id, AgentRun.status == "running")
        .values(**final_values)
    )
    if final_claim.rowcount != 1:
        session.rollback()
        _invalidate_run_detail_cache(finished_run.id)
        current = get_run_detail(session, finished_run.id)
        _finish_trace(
            trace,
            outputs=current.final_report,
            error=current.error,
        )
        return current

    session.commit()
    _invalidate_run_detail_cache(finished_run.id)
    session.refresh(finished_run)
    _finish_trace(trace, outputs=finished_run.final_report)
    logger.info(
        "Investigation run reached approval checkpoint"
        if final_status == "waiting_for_approval"
        else "Investigation run succeeded",
        extra={
            "run_id": finished_run.id,
            "incident_id": finished_run.incident_id,
            "trace_id": finished_run.trace_id,
            "status": final_status,
            "pending_approval_count": pending_approval_count,
        },
    )

    return get_run_detail(session, finished_run.id)


def _propose_report_actions(
    session: Session,
    run: AgentRun,
    report: InvestigationReport,
    trace: AgentTraceHandle | None,
    *,
    agent_version: AgentVersion,
) -> None:
    recorder = AgentRunRecorder(session, run, trace)
    action_inputs = {
        "run_id": run.id,
        "evidence_count": len(report.cited_evidence),
        "affected_account_count": len(report.affected_accounts),
    }
    create_allowed, create_reason = _can_propose_report_action(
        agent_version, "create_mock_action"
    )
    approval_allowed, approval_reason = _can_propose_report_action(
        agent_version, "request_approval"
    )
    if not create_allowed:
        recorder.record_blocked(
            stage="propose actions",
            tool_name="create_mock_action",
            inputs=action_inputs,
            blocked_reason=create_reason or "tool_not_enabled",
            fallback_output={
                "tool_disabled": True,
                "action_count": 0,
                "pending_approval_count": 0,
            },
        )
    if not approval_allowed:
        recorder.record_blocked(
            stage="propose actions",
            tool_name="request_approval",
            inputs=action_inputs,
            blocked_reason=approval_reason or "tool_not_enabled",
            fallback_output={
                "tool_disabled": True,
                "high_risk_action_count": 0,
                "pending_approval_count": 0,
            },
        )

    def propose(action_types: set[str], *, allow_approval_requests: bool) -> dict[str, object]:
        actions = propose_actions_for_report(
            session,
            run_id=run.id,
            report=report,
            allow_approval_requests=allow_approval_requests,
            action_types=action_types,
        )
        selected_actions = [
            action for action in actions if action.action_type in action_types
        ]
        return {
            "action_count": len(selected_actions),
            "action_types": [action.action_type for action in selected_actions],
            "pending_approval_count": sum(
                1 for action in selected_actions if action.status == "pending_approval"
            ),
        }

    if create_allowed:
        recorder.record(
            stage="create mock actions",
            tool_name="create_mock_action",
            inputs=action_inputs,
            action=lambda: propose(
                {"draft_slack_message", "create_task"},
                allow_approval_requests=False,
            ),
        )
    if approval_allowed:
        recorder.record(
            stage="request action approvals",
            tool_name="request_approval",
            inputs=action_inputs,
            action=lambda: propose(
                {"draft_customer_email", "update_account_note"},
                allow_approval_requests=True,
            ),
        )


def _can_propose_report_action(
    version: AgentVersion, tool_id: str
) -> tuple[bool, str | None]:
    """Preserve Project1 v1 action behavior without changing its snapshot.

    The exact seeded ``revenue-ops-agent_v1`` snapshot predates governed action
    tool IDs, but its immutable scopes explicitly authorize mock actions and
    approval requests. Only this snapshot may use those two legacy capabilities
    by scope alone. Every other version remains subject to ``can_call_tool``'s
    tool-and-scope checks.
    """
    allowed, reason = can_call_tool(version, tool_id)
    if allowed:
        return True, None
    if (
        version.id != DEFAULT_AGENT_VERSION_ID
        or version.agent_id != DEFAULT_AGENT_ID
        or version.status != "published"
        or tool_id not in {"create_mock_action", "request_approval"}
    ):
        return False, reason

    required_scope = TOOL_SCOPES[tool_id]
    if required_scope not in (version.allowed_scopes or []):
        return False, REASON_SCOPE_NOT_ALLOWED
    return True, None


def list_agent_runs(session: Session, *, limit: int = 100) -> list[AgentRunSummary]:
    runs = session.scalars(
        select(AgentRun).order_by(AgentRun.created_at.desc()).limit(limit)
    ).all()
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


RUN_DETAIL_CACHE_TTL_SECONDS = 10


def _run_detail_cache_key(run_id: str) -> str:
    return f"agent:run:{run_id}"


def _serialize_run_detail(detail: AgentRunDetail) -> dict[str, object]:
    return detail.model_dump(mode="json")


def _invalidate_run_detail_cache(run_id: str) -> None:
    Cache().delete(_run_detail_cache_key(run_id))


def invalidate_run_detail_cache(run_id: str) -> None:
    """Public cache-invalidation hook for cross-domain callers (e.g. runs)."""
    _invalidate_run_detail_cache(run_id)


def _step_duration_ms(step: AgentRunStep) -> int | None:
    """Wall-clock duration of a step in milliseconds. ``None`` while the step is
    still running (no ``completed_at``). Derived from the persisted timestamps
    so it stays correct for old runs and does not require a stored column."""
    if step.started_at is None or step.completed_at is None:
        return None
    delta = step.completed_at - step.started_at
    return int(delta.total_seconds() * 1000)


def get_run_detail(session: Session, run_id: str) -> AgentRunDetail:
    cache = Cache()
    cache_key = _run_detail_cache_key(run_id)
    cached = cache.get(cache_key)
    if cached is not None:
        return AgentRunDetail.model_validate(cached)

    run = session.scalar(
        select(AgentRun)
        .options(
            selectinload(AgentRun.agent),
            selectinload(AgentRun.agent_version),
        )
        .where(AgentRun.id == run_id)
    )
    if run is None:
        raise LookupError(f"Unknown agent run id: {run_id}")
    is_stale = _run_is_stale(session, run)

    agent = run.agent
    agent_version = run.agent_version

    agent_summary: dict[str, Any] | None = None
    if agent is not None:
        agent_summary = {
            "id": agent.id,
            "name": agent.name,
            "description": agent.description,
        }

    agent_version_summary: dict[str, Any] | None = None
    if agent_version is not None:
        agent_version_summary = {
            "id": agent_version.id,
            "agent_id": agent_version.agent_id,
            "version_number": agent_version.version_number,
            "semantic_version": agent_version.semantic_version,
            "status": agent_version.status,
            "model": agent_version.model,
        }

    steps = session.scalars(
        select(AgentRunStep)
        .where(AgentRunStep.run_id == run.id)
        .order_by(AgentRunStep.sequence)
    ).all()

    # Load all ModelUsage rows for the run once and group by step_id so each
    # step carries its own usage slice without an N+1 per step (PRD §9.2 / FR-20).
    usages = session.scalars(
        select(ModelUsage)
        .where(ModelUsage.run_id == run.id)
        .order_by(ModelUsage.recorded_at)
    ).all()
    usage_by_step: dict[str, list[ModelUsageRead]] = {}
    for usage in usages:
        if usage.step_id is None:
            continue
        usage_by_step.setdefault(usage.step_id, []).append(
            ModelUsageRead(
                id=usage.id,
                run_id=usage.run_id,
                step_id=usage.step_id,
                provider=usage.provider,
                model=usage.model,
                prompt_tokens=usage.prompt_tokens,
                completion_tokens=usage.completion_tokens,
                total_tokens=usage.total_tokens,
                cost_estimate_usd=usage.cost_estimate_usd,
                latency_ms=usage.latency_ms,
                used_llm=usage.used_llm,
                fallback_reason=usage.fallback_reason,
                recorded_at=usage.recorded_at,
            )
        )

    detail = AgentRunDetail(
        id=run.id,
        incident_id=run.incident_id,
        agent_id=run.agent_id,
        agent_version_id=run.agent_version_id,
        agent=agent_summary,
        agent_version=agent_version_summary,
        status=run.status,
        is_stale=is_stale,
        trace_id=run.trace_id,
        trace_url=run.trace_url,
        trace_provider=run.trace_provider,
        trace_metadata=run.trace_metadata,
        token_estimate=run.token_estimate,
        prompt_tokens=run.prompt_tokens,
        completion_tokens=run.completion_tokens,
        cost_estimate_usd=run.cost_estimate_usd,
        input_payload=run.input_payload,
        final_report=InvestigationReport.model_validate(run.final_report)
        if run.final_report is not None
        else None,
        error=run.error,
        started_at=run.started_at,
        completed_at=run.completed_at,
        created_at=run.created_at,
        updated_at=run.updated_at,
        steps=[
            AgentRunStepRead(
                id=step.id,
                run_id=step.run_id,
                sequence=step.sequence,
                stage=step.stage,
                tool_name=step.tool_name,
                status=step.status,
                inputs=step.inputs,
                outputs=step.outputs,
                error=step.error,
                blocked_reason=step.blocked_reason,
                started_at=step.started_at,
                completed_at=step.completed_at,
                duration_ms=_step_duration_ms(step),
                model_usage=usage_by_step.get(step.id, []),
            )
            for step in steps
        ],
        mock_actions=list_mock_actions_for_run(session, run.id),
    )
    cache.set(cache_key, _serialize_run_detail(detail), RUN_DETAIL_CACHE_TTL_SECONDS)
    return detail


def _abandon_orphaned_runs(session: Session, incident_id: str) -> None:
    now = utcnow_naive()
    cutoff = now - ACTIVE_RUN_STALE_AFTER
    orphaned_runs = session.scalars(
        select(AgentRun).where(
            AgentRun.incident_id == incident_id,
            AgentRun.status.in_(ACTIVE_RUN_STATUSES),
            AgentRun.updated_at < cutoff,
        )
    ).all()
    if not orphaned_runs:
        return

    claimed_ids: list[str] = []
    for run in orphaned_runs:
        if _run_last_activity_at(session, run) < cutoff:
            if _mark_run_interrupted(session, run, now=now):
                claimed_ids.append(run.id)
    if claimed_ids:
        session.commit()
        for rid in claimed_ids:
            _invalidate_run_detail_cache(rid)


def abandon_orphaned_runs_for_incident(
    session: Session, incident_id: str
) -> None:
    """Reap stale active runs for a single incident before a new launch.

    Thin public wrapper around ``_abandon_orphaned_runs`` so the control-plane
    creator (``app.runs.service.create_control_plane_run``) can mirror the
    incident-bound creator's pre-insert self-heal without importing a
    module-private symbol. A crashed worker leaves a run stuck in ``running``;
    without this, ``POST /runs`` against that incident returns 409 (partial
    unique index) with no recovery until an API restart or a manual
    ``POST /runs/{id}/transitions`` to force-fail.
    """
    _abandon_orphaned_runs(session, incident_id)


def abandon_orphaned_active_runs(session: Session) -> int:
    now = utcnow_naive()
    cutoff = now - ACTIVE_RUN_STALE_AFTER
    orphaned_runs = session.scalars(
        select(AgentRun).where(
            AgentRun.status.in_(ACTIVE_RUN_STATUSES),
            AgentRun.updated_at < cutoff,
        )
    ).all()
    abandoned_count = 0
    claimed_ids: list[str] = []
    for run in orphaned_runs:
        if _run_last_activity_at(session, run) < cutoff:
            if _mark_run_interrupted(session, run, now=now):
                claimed_ids.append(run.id)
                abandoned_count += 1
    if claimed_ids:
        session.commit()
        for rid in claimed_ids:
            _invalidate_run_detail_cache(rid)
    return abandoned_count


def mark_run_failed_on_timeout(
    session: Session, run_id: str, *, reason: str
) -> None:
    """Mark an in-flight agent run failed when its Celery task is timed out.

    The orphan reaper would eventually flip stale runs to failed, but doing it
    here records a specific timeout reason immediately instead of waiting up to
    ``ACTIVE_RUN_STALE_AFTER``. Uses a conditional update so a run that already
    reached a terminal state (e.g. succeeded just before the limit fired) is
    not overwritten.
    """
    now = utcnow_naive()
    claim = session.execute(
        update(AgentRun)
        .where(AgentRun.id == run_id, AgentRun.status.in_(ACTIVE_RUN_STATUSES))
        .values(
            status="failed",
            error=reason,
            completed_at=now,
            updated_at=now,
        )
    )
    if claim.rowcount == 1:
        session.commit()
        _invalidate_run_detail_cache(run_id)
        logger.warning(
            "Agent run marked failed on celery timeout",
            extra={"run_id": run_id, "reason": reason},
        )
    else:
        session.rollback()


def _abandon_orphaned_run(session: Session, run: AgentRun) -> None:
    now = utcnow_naive()
    if not _run_is_stale(session, run, now=now):
        return
    if _mark_run_interrupted(session, run, now=now):
        session.commit()
        _invalidate_run_detail_cache(run.id)


def _run_is_stale(
    session: Session, run: AgentRun, *, now: datetime | None = None
) -> bool:
    if run.status not in ACTIVE_RUN_STATUSES:
        return False
    now = now or utcnow_naive()
    return _run_last_activity_at(session, run) < now - ACTIVE_RUN_STALE_AFTER


def _mark_run_interrupted(
    session: Session, run: AgentRun, *, now: datetime
) -> bool:
    """Conditionally mark a stale active run as failed (interrupted).

    Uses a conditional UPDATE so a concurrent transition (e.g. operator
    force-succeed via POST /runs/{id}/transitions) is not overwritten —
    mirroring ``mark_run_failed_on_timeout``. Returns ``True`` if this call
    claimed the run, ``False`` if a concurrent writer already moved it out of
    the active set.
    """
    claim = session.execute(
        update(AgentRun)
        .where(
            AgentRun.id == run.id,
            AgentRun.status.in_(ACTIVE_RUN_STATUSES),
        )
        .values(
            status="failed",
            error=run.error or "Investigation interrupted before completion.",
            completed_at=run.completed_at or now,
            updated_at=now,
        )
    )
    return claim.rowcount == 1


def _run_last_activity_at(session: Session, run: AgentRun) -> datetime:
    step_activity = session.scalar(
        select(func.max(func.coalesce(AgentRunStep.completed_at, AgentRunStep.started_at)))
        .where(AgentRunStep.run_id == run.id)
    )
    if step_activity is not None and step_activity > run.updated_at:
        return step_activity
    return run.updated_at


def _latest_reusable_run_id(
    session: Session, incident_id: str, *, active_only: bool = False, agent_version_id: str | None = None
) -> str | None:
    statuses = (
        BLOCKING_RUN_STATUSES
        if active_only
        else (*BLOCKING_RUN_STATUSES, "succeeded")
    )
    stmt = (
        select(AgentRun.id)
        .where(
            AgentRun.incident_id == incident_id,
            AgentRun.status.in_(statuses),
        )
        .order_by(AgentRun.created_at.desc(), AgentRun.id.desc())
        .limit(1)
    )
    if agent_version_id is not None:
        stmt = stmt.where(AgentRun.agent_version_id == agent_version_id)
    return session.scalar(stmt)


def backfill_report_actions(session: Session, run_id: str) -> None:
    run = session.get(AgentRun, run_id)
    if run is None or run.status != "succeeded" or run.final_report is None:
        return

    if run.agent_version_id is None:
        return
    version = session.get(AgentVersion, run.agent_version_id)
    if version is None:
        return
    create_allowed, _ = _can_propose_report_action(version, "create_mock_action")
    approval_allowed, _ = _can_propose_report_action(version, "request_approval")
    if not create_allowed and not approval_allowed:
        return
    report = InvestigationReport.model_validate(run.final_report)
    # This compatibility path may add missing actions to a pre-action run, but
    # must never append execution steps to a timeline that already completed.
    if create_allowed:
        propose_actions_for_report(
            session,
            run_id=run.id,
            report=report,
            allow_approval_requests=False,
            action_types={"draft_slack_message", "create_task"},
        )
    if approval_allowed:
        propose_actions_for_report(
            session,
            run_id=run.id,
            report=report,
            action_types={"draft_customer_email", "update_account_note"},
        )


def _finish_trace(
    trace: object, *, outputs: object | None = None, error: str | None = None
) -> None:
    try:
        trace.finish(outputs=outputs, error=error)  # type: ignore[attr-defined]
    except Exception:
        return
