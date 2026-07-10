from __future__ import annotations

from celery.exceptions import SoftTimeLimitExceeded

from app.celery_app import celery_app
from app.db.session import SessionLocal
from app.logging_config import get_logger

logger = get_logger(__name__)


@celery_app.task
def investigate_incident(run_id: str) -> dict[str, object]:
    """Run an investigation synchronously within a Celery worker.

    Not retryable: a partial run corrupts agent state, so fail-fast is safer
    than a blind retry. The Celery ``task_time_limit`` / ``task_soft_time_limit``
    guard against hangs; if the soft limit fires we mark the run failed
    immediately (with a specific timeout reason) so it does not appear
    "running" while waiting for the orphan reaper, then re-raise so Celery
    records the task failure.
    """
    from app.agent.service import (
        execute_investigation_run_with_session,
        mark_run_failed_on_timeout,
    )

    try:
        with SessionLocal() as session:
            detail = execute_investigation_run_with_session(session, run_id)
    except SoftTimeLimitExceeded:
        # The in-flight session is rolled back when its ``with`` block exits;
        # use a fresh session to record the timeout on the run row. If the
        # cleanup itself raises (DB unavailable, pool exhausted), log it but
        # still re-raise the *original* SoftTimeLimitExceeded so Celery records
        # the timeout as the task failure and monitoring keyed on it still
        # fires. The run row is then left for the orphan reaper to reclaim.
        try:
            with SessionLocal() as session:
                mark_run_failed_on_timeout(
                    session, run_id, reason="celery soft time limit exceeded"
                )
        except Exception:
            logger.exception(
                "Failed to mark agent run failed after soft time limit; "
                "leaving it for the orphan reaper",
                extra={"run_id": run_id},
            )
        raise

    return {
        "run_id": detail.id,
        "status": detail.status,
        "incident_id": detail.incident_id,
    }


@celery_app.task
def reap_stale_runs() -> dict[str, object]:
    """Periodically fail active runs that have gone stale (PRD FR-11, NFR-2).

    Scheduled by the Celery beat schedule (default 60 s) so a crashed worker or a
    long-lived server does not leave runs stuck in ``running`` until the next API
    restart or next incident-bound launch. The staleness threshold itself is
    operator-tunable via ``ACTIVE_RUN_STALE_AFTER_SECONDS`` (default 600 s).

    Idempotent: ``abandon_orphaned_active_runs`` uses conditional updates keyed on
    ``status in ACTIVE_RUN_STATUSES``, so concurrent reapers or operator transitions
    are safe. Returns the count for monitoring/observability.
    """
    from app.agent.service import abandon_orphaned_active_runs

    try:
        with SessionLocal() as session:
            abandoned = abandon_orphaned_active_runs(session)
    except Exception:
        # A transient DB failure should not kill the beat schedule. Log and let
        # the next tick retry; the per-incident self-heal still covers the
        # common "new launch against a stale incident" path.
        logger.exception("Stale-run reaper failed; will retry on next beat tick")
        return {"abandoned": 0, "error": True}

    if abandoned:
        logger.info(
            "Stale-run reaper abandoned runs",
            extra={"abandoned_count": abandoned},
        )
    return {"abandoned": abandoned, "error": False}
