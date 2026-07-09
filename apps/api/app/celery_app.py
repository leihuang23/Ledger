from __future__ import annotations

from celery import Celery

from app.core.config import get_settings

# Hard kill after this many seconds; shared so dependents (e.g. the eval-run
# staleness reaper) stay aligned when these limits change.
CELERY_TASK_TIME_LIMIT = 600
CELERY_TASK_SOFT_TIME_LIMIT = 540


def make_celery_app() -> Celery:
    settings = get_settings()
    app = Celery(
        "ops_agent",
        broker=settings.celery_broker_url,
        backend=settings.celery_result_backend,
        include=["app.agent.tasks", "app.evals.tasks", "app.runs.tasks"],
    )
    app.conf.update(
        task_serializer="json",
        accept_content=["json"],
        result_serializer="json",
        timezone="UTC",
        enable_utc=True,
        task_track_started=True,
        task_always_eager=settings.app_env == "test",
        # Hard kill after CELERY_TASK_TIME_LIMIT seconds; soft limit gives the
        # task a 60s grace window to clean up before being terminated.
        task_time_limit=CELERY_TASK_TIME_LIMIT,
        task_soft_time_limit=CELERY_TASK_SOFT_TIME_LIMIT,
    )
    return app


celery_app = make_celery_app()
