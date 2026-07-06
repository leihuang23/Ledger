from __future__ import annotations


def test_celery_has_time_limits() -> None:
    """Celery must have task_time_limit and task_soft_time_limit set so a
    runaway task cannot hang the worker indefinitely (audit P1 #6)."""
    from app.celery_app import celery_app

    assert isinstance(celery_app.conf.task_time_limit, int)
    assert celery_app.conf.task_time_limit > 0
    assert isinstance(celery_app.conf.task_soft_time_limit, int)
    assert celery_app.conf.task_soft_time_limit > 0
    # Soft limit must fire before the hard limit so the task can clean up.
    assert celery_app.conf.task_soft_time_limit < celery_app.conf.task_time_limit
