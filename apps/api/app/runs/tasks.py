"""Celery task driving a control-plane run to completion (PRD FR-8, S-8, I-15).

Reuses ``execute_investigation_run_with_session`` so the run lifecycle, version
resolution, tool-policy enforcement (PRD FR-7), and finalization are identical
to incident-bound investigations. Not retryable: a partial run corrupts agent
state, so fail-fast is safer than a blind retry. On ``SoftTimeLimitExceeded`` the
run is marked failed with a specific timeout reason immediately rather than
waiting for the orphan reaper.
"""

from __future__ import annotations

from celery.exceptions import SoftTimeLimitExceeded

from app.celery_app import celery_app
from app.db.session import SessionLocal
from app.logging_config import get_logger

logger = get_logger(__name__)


@celery_app.task
def run_control_plane_run(run_id: str) -> dict[str, object]:
    """Execute a control-plane run synchronously within a Celery worker."""
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
        # cleanup itself raises, log it but still re-raise the original
        # SoftTimeLimitExceeded so Celery records the timeout as the task
        # failure. The run row is then left for the orphan reaper to reclaim.
        try:
            with SessionLocal() as session:
                mark_run_failed_on_timeout(
                    session, run_id, reason="soft_time_limit_exceeded"
                )
        except Exception:
            logger.exception(
                "Failed to mark control-plane run failed after soft time limit; "
                "leaving it for the orphan reaper",
                extra={"run_id": run_id},
            )
        raise

    return {
        "run_id": detail.id,
        "status": detail.status,
        "incident_id": detail.incident_id,
    }
