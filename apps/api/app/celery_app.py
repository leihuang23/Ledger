from __future__ import annotations

from celery import Celery

from app.core.config import get_settings


def make_celery_app() -> Celery:
    settings = get_settings()
    app = Celery(
        "ops_agent",
        broker=settings.celery_broker_url,
        backend=settings.celery_result_backend,
        include=["app.agent.tasks"],
    )
    app.conf.update(
        task_serializer="json",
        accept_content=["json"],
        result_serializer="json",
        timezone="UTC",
        enable_utc=True,
        task_track_started=True,
        task_always_eager=settings.app_env == "test",
        # Hard kill after 600s; soft limit at 540s gives the task a 60s grace
        # window to clean up (flush partial state, log) before being terminated.
        task_time_limit=600,
        task_soft_time_limit=540,
    )
    return app


celery_app = make_celery_app()
