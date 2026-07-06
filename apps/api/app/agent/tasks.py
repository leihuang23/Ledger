from __future__ import annotations

from app.celery_app import celery_app
from app.db.session import SessionLocal


@celery_app.task
def investigate_incident(run_id: str) -> dict[str, object]:
    """Run an investigation synchronously within a Celery worker.

    Not retryable: a partial run corrupts agent state, so fail-fast is safer
    than a blind retry. The Celery ``task_time_limit`` / ``task_soft_time_limit``
    guard against hangs.
    """
    from app.agent.service import execute_investigation_run_with_session

    with SessionLocal() as session:
        detail = execute_investigation_run_with_session(session, run_id)

    return {
        "run_id": detail.id,
        "status": detail.status,
        "incident_id": detail.incident_id,
    }
