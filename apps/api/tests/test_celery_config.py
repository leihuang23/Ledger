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


def test_celery_beat_schedules_stale_run_reaper() -> None:
    """PRD FR-11 / NFR-2: a periodic beat schedule must exist that drives the
    stale-run reaper. Without it, a crashed worker or a long-lived server with
    no restart leaves runs stuck in ``running`` indefinitely; only the
    per-incident self-heal (on next launch) would recover them. The cadence is
    operator-tunable via ``run_staleness_sweep_interval_seconds`` (default 60 s)."""
    from app.celery_app import celery_app
    from app.core.config import get_settings

    beat = celery_app.conf.beat_schedule
    assert "reap-stale-runs" in beat
    entry = beat["reap-stale-runs"]
    assert entry["task"] == "app.agent.tasks.reap_stale_runs"
    # The schedule must be driven by the configurable setting, not a hardcoded
    # constant, so operators can tune the cadence without code changes.
    assert entry["schedule"] == get_settings().run_staleness_sweep_interval_seconds
    assert entry["schedule"] == 60  # NFR-2 default


def test_reap_stale_runs_task_is_registered() -> None:
    """The reaper task must be registered with Celery so the beat schedule can
    dispatch it. A schedule entry pointing at an unregistered task is silently
    skipped by the beat worker."""
    from app.celery_app import celery_app

    assert "app.agent.tasks.reap_stale_runs" in celery_app.tasks

