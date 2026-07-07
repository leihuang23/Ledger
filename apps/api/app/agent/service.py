from __future__ import annotations

from datetime import datetime, timedelta
from uuid import uuid4

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.agent.persistence import AgentRunRecorder
from app.agent.persistence import utcnow_naive
from app.agent.schemas import (
    AgentRunDetail,
    AgentRunStepRead,
    AgentRunSummary,
    InvestigationReport,
)
from app.agent.tracing import AgentTraceHandle, start_agent_trace
from app.agent.workflow import run_investigation_workflow
from app.approvals.service import list_mock_actions_for_run, propose_actions_for_report
from app.cache import Cache
from app.core.config import get_settings
from app.db.session import SessionLocal
from app.llm import AnthropicClient, LLMClient, NoopLLMClient, OpenAIClient
from app.logging_config import get_logger
from app.models import AgentRun, AgentRunStep, Incident

ACTIVE_RUN_STALE_AFTER = timedelta(minutes=10)
ACTIVE_RUN_STATUSES = ("queued", "running")

logger = get_logger(__name__)


def build_llm_client_from_settings() -> LLMClient:
    settings = get_settings()
    if settings.llm_provider == "openai":
        if not settings.openai_api_key:
            return NoopLLMClient()
        return OpenAIClient(
            api_key=settings.openai_api_key,
            model=settings.llm_model,
            temperature=settings.llm_temperature,
            max_tokens=settings.llm_max_tokens,
            timeout_seconds=settings.llm_timeout_seconds,
        )
    if settings.llm_provider == "anthropic":
        if not settings.anthropic_api_key:
            return NoopLLMClient()
        return AnthropicClient(
            api_key=settings.anthropic_api_key,
            model=settings.llm_model,
            temperature=settings.llm_temperature,
            max_tokens=settings.llm_max_tokens,
            timeout_seconds=settings.llm_timeout_seconds,
        )
    return NoopLLMClient()


IDEMPOTENCY_CACHE_TTL_SECONDS = 3600


def create_investigation_run(
    session: Session,
    incident_id: str,
    *,
    force: bool = False,
    idempotency_key: str | None = None,
) -> tuple[AgentRunDetail, bool]:
    incident = session.get(Incident, incident_id)
    if incident is None:
        raise LookupError(f"Unknown incident id: {incident_id}")

    _abandon_orphaned_runs(session, incident_id)

    if idempotency_key:
        cache = Cache()
        existing_run_id = cache.get_idempotency_value(idempotency_key)
        if existing_run_id:
            existing_run = session.get(AgentRun, existing_run_id)
            if existing_run is not None:
                return get_run_detail(session, existing_run_id), False

    if not force:
        reusable_run_id = _latest_reusable_run_id(session, incident_id)
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
        status="queued",
        trace_id=None,
        trace_url=None,
        trace_provider=None,
        trace_metadata={},
        input_payload={"incident_id": incident_id},
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
        reusable_run_id = _latest_reusable_run_id(session, incident_id, active_only=True)
        if reusable_run_id is None:
            raise
        return get_run_detail(session, reusable_run_id), False

    if idempotency_key:
        cache = Cache()
        cache.set_idempotency_value(
            idempotency_key, run.id, IDEMPOTENCY_CACHE_TTL_SECONDS
        )

    return get_run_detail(session, run.id), True


def start_investigation_run(
    session: Session, incident_id: str, *, force: bool = False
) -> tuple[AgentRunDetail, bool]:
    run, created = create_investigation_run(session, incident_id, force=force)
    if not created:
        return run, False

    return execute_investigation_run_with_session(session, run.id), True


def execute_investigation_run(run_id: str) -> None:
    with SessionLocal() as session:
        execute_investigation_run_with_session(session, run_id)


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
        extra={"run_id": run.id, "incident_id": run.incident_id},
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
        _finish_trace(trace, error="Agent run was already claimed.")
        logger.warning(
            "Agent run was already claimed",
            extra={"run_id": run.id},
        )
        return get_run_detail(session, run_id)
    session.commit()
    session.refresh(run)
    _invalidate_run_detail_cache(run_id)

    llm_client = build_llm_client_from_settings()
    try:
        report = run_investigation_workflow(session, run, trace, llm_client=llm_client)
    except Exception as exc:
        _finish_trace(trace, error=str(exc))
        session.rollback()
        failed_run = session.get(AgentRun, run.id)
        if failed_run is None:
            raise
        if failed_run.status != "running":
            return get_run_detail(session, failed_run.id)
        completed_at = utcnow_naive()
        failed_run.status = "failed"
        failed_run.error = str(exc)
        failed_run.completed_at = completed_at
        failed_run.updated_at = completed_at
        session.commit()
        _invalidate_run_detail_cache(failed_run.id)
        logger.error(
            "Investigation run failed",
            extra={
                "run_id": failed_run.id,
                "incident_id": failed_run.incident_id,
                "error": failed_run.error,
            },
        )
        return get_run_detail(session, failed_run.id)

    completed_at = utcnow_naive()
    finished_run = session.get(AgentRun, run.id)
    if finished_run is None:
        raise RuntimeError(f"Agent run disappeared: {run.id}")
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
    finished_run.status = "succeeded"
    finished_run.error = None
    finished_run.final_report = report.model_dump(mode="json")
    finished_run.updated_at = completed_at

    try:
        _propose_report_actions(session, finished_run, report, trace)
    except Exception as exc:
        session.rollback()
        failed_run = session.get(AgentRun, finished_run.id)
        if failed_run is None:
            raise
        completed_at = utcnow_naive()
        failed_run.status = "failed"
        failed_run.error = f"Action proposal failed: {exc}"
        failed_run.completed_at = completed_at
        failed_run.updated_at = completed_at
        _finish_trace(trace, outputs=failed_run.final_report, error=failed_run.error)
        session.commit()
        _invalidate_run_detail_cache(failed_run.id)
        logger.error(
            "Action proposal failed",
            extra={
                "run_id": failed_run.id,
                "incident_id": failed_run.incident_id,
                "error": failed_run.error,
            },
        )
        return get_run_detail(session, failed_run.id)

    finished_run = session.get(AgentRun, run.id)
    if finished_run is None:
        raise RuntimeError(f"Agent run disappeared: {run.id}")
    completed_at = utcnow_naive()
    finished_run.status = "succeeded"
    finished_run.completed_at = completed_at
    finished_run.updated_at = completed_at
    _finish_trace(trace, outputs=finished_run.final_report)
    session.commit()
    _invalidate_run_detail_cache(finished_run.id)
    logger.info(
        "Investigation run succeeded",
        extra={
            "run_id": finished_run.id,
            "incident_id": finished_run.incident_id,
            "trace_id": finished_run.trace_id,
        },
    )

    return get_run_detail(session, finished_run.id)


def _propose_report_actions(
    session: Session,
    run: AgentRun,
    report: InvestigationReport,
    trace: AgentTraceHandle,
) -> None:
    recorder = AgentRunRecorder(session, run, trace)

    def propose() -> dict[str, object]:
        actions = propose_actions_for_report(session, run_id=run.id, report=report)
        return {
            "action_count": len(actions),
            "action_types": [action.action_type for action in actions],
            "pending_approval_count": sum(
                1 for action in actions if action.status == "pending_approval"
            ),
        }

    recorder.record(
        stage="propose actions",
        tool_name="propose_actions",
        inputs={
            "run_id": run.id,
            "evidence_count": len(report.cited_evidence),
            "affected_account_count": len(report.affected_accounts),
        },
        action=propose,
    )


def list_agent_runs(session: Session, *, limit: int = 100) -> list[AgentRunSummary]:
    runs = session.scalars(
        select(AgentRun).order_by(AgentRun.created_at.desc()).limit(limit)
    ).all()
    return [
        AgentRunSummary(
            id=run.id,
            incident_id=run.incident_id,
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


def get_run_detail(session: Session, run_id: str) -> AgentRunDetail:
    cache = Cache()
    cache_key = _run_detail_cache_key(run_id)
    cached = cache.get(cache_key)
    if cached is not None:
        return AgentRunDetail.model_validate(cached)

    run = session.get(AgentRun, run_id)
    if run is None:
        raise LookupError(f"Unknown agent run id: {run_id}")
    is_stale = _run_is_stale(session, run)

    steps = session.scalars(
        select(AgentRunStep)
        .where(AgentRunStep.run_id == run.id)
        .order_by(AgentRunStep.sequence)
    ).all()

    detail = AgentRunDetail(
        id=run.id,
        incident_id=run.incident_id,
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
                started_at=step.started_at,
                completed_at=step.completed_at,
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

    for run in orphaned_runs:
        if _run_last_activity_at(session, run) < cutoff:
            _mark_run_interrupted(run, now=now)
    session.commit()


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
    for run in orphaned_runs:
        if _run_last_activity_at(session, run) < cutoff:
            _mark_run_interrupted(run, now=now)
            abandoned_count += 1
    if abandoned_count:
        session.commit()
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
    _mark_run_interrupted(run, now=now)
    session.commit()


def _run_is_stale(
    session: Session, run: AgentRun, *, now: datetime | None = None
) -> bool:
    if run.status not in ACTIVE_RUN_STATUSES:
        return False
    now = now or utcnow_naive()
    return _run_last_activity_at(session, run) < now - ACTIVE_RUN_STALE_AFTER


def _mark_run_interrupted(run: AgentRun, *, now: datetime) -> None:
    run.status = "failed"
    run.error = run.error or "Investigation interrupted before completion."
    run.completed_at = run.completed_at or now
    run.updated_at = now


def _run_last_activity_at(session: Session, run: AgentRun) -> datetime:
    step_activity = session.scalar(
        select(func.max(func.coalesce(AgentRunStep.completed_at, AgentRunStep.started_at)))
        .where(AgentRunStep.run_id == run.id)
    )
    if step_activity is not None and step_activity > run.updated_at:
        return step_activity
    return run.updated_at


def _latest_reusable_run_id(
    session: Session, incident_id: str, *, active_only: bool = False
) -> str | None:
    statuses = ACTIVE_RUN_STATUSES if active_only else (*ACTIVE_RUN_STATUSES, "succeeded")
    return session.scalar(
        select(AgentRun.id)
        .where(
            AgentRun.incident_id == incident_id,
            AgentRun.status.in_(statuses),
        )
        .order_by(AgentRun.created_at.desc(), AgentRun.id.desc())
        .limit(1)
    )


def backfill_report_actions(session: Session, run_id: str) -> None:
    run = session.get(AgentRun, run_id)
    if run is None or run.status != "succeeded" or run.final_report is None:
        return

    report = InvestigationReport.model_validate(run.final_report)
    propose_actions_for_report(session, run_id=run.id, report=report)


def _finish_trace(
    trace: object, *, outputs: object | None = None, error: str | None = None
) -> None:
    try:
        trace.finish(outputs=outputs, error=error)  # type: ignore[attr-defined]
    except Exception:
        return
