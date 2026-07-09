from __future__ import annotations

from collections.abc import Callable, Generator
from datetime import timedelta

import pytest
from celery.exceptions import SoftTimeLimitExceeded
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

import app.models  # noqa: F401
from app.agent.persistence import utcnow_naive
from app.agent.service import (
    ACTIVE_RUN_STALE_AFTER,
    abandon_orphaned_active_runs,
)
from app.agents.service import DEFAULT_AGENT_ID, DEFAULT_AGENT_VERSION_ID
from app.core.config import get_settings
from app.db.base import Base
from app.db.session import get_db
from app.main import app
from app.models import AgentRun
from app.runs.service import create_control_plane_run, transition_run
from app.seed import reseed_database

# The canonical seeded checkout-retry-regression incident (used by docker-smoke
# CI and the demo flow). Guaranteed to exist after ``reseed_database``.
SEEDED_INCIDENT_ID = "inc_rev_mrr_wow_drop_20260603"


@pytest.fixture()
def session_factory(
    tmp_path,
) -> Generator[Callable[[], Session], None, None]:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'runs_api_test.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    yield TestingSessionLocal

    Base.metadata.drop_all(engine)
    engine.dispose()


def _client_with_db(
    session_factory: Callable[[], Session],
) -> tuple[TestClient, Callable[[], Session]]:
    """Wire the app's ``get_db`` dependency to the test session factory."""

    def override_get_db() -> Generator[Session, None, None]:
        with session_factory() as db:
            yield db

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)
    return client, session_factory


def _seed(session_factory: Callable[[], Session]) -> None:
    with session_factory() as session:
        reseed_database(session)


def _make_run(
    session: Session,
    *,
    run_id: str,
    status: str = "queued",
    agent_version_id: str = DEFAULT_AGENT_VERSION_ID,
    incident_id: str | None = SEEDED_INCIDENT_ID,
    started_at=None,
    updated_at=None,
) -> AgentRun:
    now = utcnow_naive()
    run = AgentRun(
        id=run_id,
        incident_id=incident_id,
        agent_id=DEFAULT_AGENT_ID,
        agent_version_id=agent_version_id,
        status=status,
        trace_id=None,
        trace_url=None,
        trace_provider=None,
        trace_metadata={},
        input_payload={},
        final_report=None,
        token_estimate=0,
        prompt_tokens=0,
        completion_tokens=0,
        cost_estimate_usd=0.0,
        error=None,
        started_at=started_at,
        completed_at=None,
        created_at=now,
        updated_at=updated_at or now,
    )
    session.add(run)
    session.commit()
    return run


def test_post_runs_launches_incident_run_to_succeeded(
    session_factory: Callable[[], Session],
) -> None:
    """I-12 / P3-T11: POST /runs against published v1 with a seeded incident
    returns 202, and the inline run reaches ``succeeded`` with a final report."""
    _seed(session_factory)
    client, _ = _client_with_db(session_factory)

    response = client.post(
        "/runs",
        json={
            "agent_version_id": DEFAULT_AGENT_VERSION_ID,
            "incident_id": SEEDED_INCIDENT_ID,
            "run_inline": True,
        },
    )

    assert response.status_code == 202
    payload = response.json()
    assert payload["status"] == "succeeded"
    assert payload["agent_version_id"] == DEFAULT_AGENT_VERSION_ID
    assert payload["incident_id"] == SEEDED_INCIDENT_ID
    assert payload["final_report"] is not None
    assert payload["steps"]
    assert payload["trace_id"]


def test_post_runs_self_heals_stale_run_before_insert(
    session_factory: Callable[[], Session],
) -> None:
    """A stale ``running`` run on the same incident would normally trip the
    partial unique index as a 409. ``create_control_plane_run`` mirrors
    ``create_investigation_run`` by reaping stale runs for the incident before
    insert, so a crashed worker doesn't leave the incident 409-blocked until an
    API restart. A new launch against the stale incident should succeed (202),
    not 409."""
    _seed(session_factory)
    client, _ = _client_with_db(session_factory)

    stale = utcnow_naive() - ACTIVE_RUN_STALE_AFTER - timedelta(seconds=1)
    with session_factory() as session:
        _make_run(
            session,
            run_id="run_stale_cp_blocker",
            status="running",
            started_at=stale,
            updated_at=stale,
            incident_id=SEEDED_INCIDENT_ID,
        )

    response = client.post(
        "/runs",
        json={
            "agent_version_id": DEFAULT_AGENT_VERSION_ID,
            "incident_id": SEEDED_INCIDENT_ID,
            "run_inline": True,
        },
    )
    assert response.status_code == 202
    # The stale run was reaped to ``failed`` (interrupted), not left blocking.
    with session_factory() as session:
        stale_run = session.get(AgentRun, "run_stale_cp_blocker")
    assert stale_run is not None
    assert stale_run.status == "failed"


def test_control_plane_run_allows_nullable_incident_id(
    session_factory: Callable[[], Session],
) -> None:
    """I-13 / P3-T12 (row shape): a control-plane run row can be created with a
    null incident_id (forward-compatible schema); agent_version_id persists."""
    _seed(session_factory)
    with session_factory() as session:
        detail = create_control_plane_run(
            session,
            agent_version_id=DEFAULT_AGENT_VERSION_ID,
            incident_id=None,
        )

    assert detail.incident_id is None
    assert detail.agent_version_id == DEFAULT_AGENT_VERSION_ID
    assert detail.status == "queued"


def test_illegal_run_transition_returns_409(
    session_factory: Callable[[], Session],
) -> None:
    """I-14 / P3-T12: transitioning a terminal run to a non-terminal state is
    rejected with 409 (FR-9); a legal queued -> running transition succeeds."""
    _seed(session_factory)
    client, _ = _client_with_db(session_factory)

    with session_factory() as session:
        _make_run(
            session,
            run_id="run_terminal_409",
            status="succeeded",
            incident_id=SEEDED_INCIDENT_ID,
        )
        _make_run(
            session,
            run_id="run_queued_ok",
            status="queued",
            incident_id=SEEDED_INCIDENT_ID,
        )

    illegal = client.post(
        "/runs/run_terminal_409/transitions",
        json={"status": "running"},
    )
    assert illegal.status_code == 409

    legal = client.post(
        "/runs/run_queued_ok/transitions",
        json={"status": "running"},
    )
    assert legal.status_code == 200
    assert legal.json()["status"] == "running"


def test_transition_unknown_run_returns_404(
    session_factory: Callable[[], Session],
) -> None:
    _seed(session_factory)
    client, _ = _client_with_db(session_factory)

    response = client.post(
        "/runs/run_does_not_exist/transitions",
        json={"status": "running"},
    )
    assert response.status_code == 404


def test_transition_to_waiting_for_approval_rejected_by_api(
    session_factory: Callable[[], Session],
) -> None:
    """Phase 3 does not ship workflow pausing on ``waiting_for_approval`` (only
    the state-machine helper is forward-compatible). The operator transition API
    must reject it (422) so a run cannot be stranded in an unrecoverable state
    with no reaper coverage and no resume worker."""
    _seed(session_factory)
    client, _ = _client_with_db(session_factory)

    with session_factory() as session:
        _make_run(
            session,
            run_id="run_wfa_rejected",
            status="running",
            incident_id=SEEDED_INCIDENT_ID,
            started_at=utcnow_naive(),
        )

    response = client.post(
        "/runs/run_wfa_rejected/transitions",
        json={"status": "waiting_for_approval"},
    )
    assert response.status_code == 422
    # The state machine still permits it (forward-compat); only the API surface
    # omits it. Verify the helper itself still accepts the transition.
    from app.runs.lifecycle import validate_transition

    validate_transition("running", "waiting_for_approval")  # no raise


def test_transition_sets_timestamps_for_target_state(
    session_factory: Callable[[], Session],
) -> None:
    """Operator-driven transitions set timestamps consistent with the target
    state: a ``queued -> running`` transition records ``started_at`` (when not
    already set), and a terminal transition (``running -> failed``) records
    ``completed_at``. Without this an operator force-failing a run leaves
    ``completed_at`` null and the UI renders "In progress" for a terminal run."""
    _seed(session_factory)
    client, _ = _client_with_db(session_factory)

    with session_factory() as session:
        _make_run(
            session,
            run_id="run_ts_queued",
            status="queued",
            incident_id=SEEDED_INCIDENT_ID,
        )

    to_running = client.post(
        "/runs/run_ts_queued/transitions",
        json={"status": "running"},
    )
    assert to_running.status_code == 200
    assert to_running.json()["started_at"] is not None

    to_failed = client.post(
        "/runs/run_ts_queued/transitions",
        json={"status": "failed"},
    )
    assert to_failed.status_code == 200
    failed_payload = to_failed.json()
    assert failed_payload["status"] == "failed"
    assert failed_payload["completed_at"] is not None


def test_control_plane_task_marks_run_failed_on_soft_time_limit(
    session_factory: Callable[[], Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """I-15 / P3-T13 / S-8: the control-plane Celery task catches
    SoftTimeLimitExceeded, marks the run failed with ``soft_time_limit_exceeded``
    immediately, and re-raises."""
    from app.runs.tasks import run_control_plane_run

    _seed(session_factory)
    with session_factory() as session:
        _make_run(
            session,
            run_id="run_cp_timeout",
            status="running",
            started_at=utcnow_naive(),
            incident_id=SEEDED_INCIDENT_ID,
        )

    def raising_execute(_session: Session, _run_id: str) -> None:
        raise SoftTimeLimitExceeded()

    monkeypatch.setattr(
        "app.agent.service.execute_investigation_run_with_session",
        raising_execute,
    )
    # The task opens its own session via SessionLocal; point it at the test DB.
    monkeypatch.setattr("app.runs.tasks.SessionLocal", session_factory)

    with pytest.raises(SoftTimeLimitExceeded):
        run_control_plane_run.run("run_cp_timeout")

    with session_factory() as session:
        run = session.get(AgentRun, "run_cp_timeout")

    assert run is not None
    assert run.status == "failed"
    assert run.error == "soft_time_limit_exceeded"
    assert run.completed_at is not None


def test_stale_control_plane_run_self_heals_to_failed(
    session_factory: Callable[[], Session],
) -> None:
    """I-16 / P3-T5 / S-8: a control-plane run stuck in ``running`` past the
    staleness threshold is reclaimed by the global reaper."""
    _seed(session_factory)
    with session_factory() as session:
        stale = utcnow_naive() - ACTIVE_RUN_STALE_AFTER - timedelta(seconds=1)
        _make_run(
            session,
            run_id="run_cp_stale",
            status="running",
            started_at=stale,
            updated_at=stale,
            incident_id=SEEDED_INCIDENT_ID,
        )
        abandoned = abandon_orphaned_active_runs(session)

    assert abandoned >= 1
    with session_factory() as session:
        run = session.get(AgentRun, "run_cp_stale")
    assert run is not None
    assert run.status == "failed"


def test_reaper_does_not_overwrite_concurrent_force_succeed(
    session_factory: Callable[[], Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pass 9 Fix 1: the orphan reaper's conditional UPDATE does not overwrite
    a concurrent operator force-succeed. The reaper reads the run as stale
    (running, old updated_at), but a concurrent ``transition_run`` force-succeeds
    it before the reaper's conditional UPDATE fires. The ``WHERE status IN
    (active)`` guard prevents overwriting the succeeded run."""
    from app.agent.service import _run_last_activity_at

    _seed(session_factory)
    stale = utcnow_naive() - ACTIVE_RUN_STALE_AFTER - timedelta(seconds=1)
    with session_factory() as session:
        _make_run(
            session,
            run_id="run_reaper_race",
            status="running",
            started_at=stale,
            updated_at=stale,
            incident_id=SEEDED_INCIDENT_ID,
        )

    original_activity = _run_last_activity_at

    def racing_activity(session, run):
        # Force-succeed the run in a separate session DURING the reaper's
        # staleness check, simulating a concurrent operator transition.
        with session_factory() as other:
            transition_run(other, "run_reaper_race", "succeeded")
        return original_activity(session, run)

    monkeypatch.setattr("app.agent.service._run_last_activity_at", racing_activity)

    with session_factory() as session:
        abandoned = abandon_orphaned_active_runs(session)

    assert abandoned == 0

    with session_factory() as session:
        run = session.get(AgentRun, "run_reaper_race")
    assert run is not None
    assert run.status == "succeeded"
    assert run.error is None


def test_list_runs_filters_by_version_and_status(
    session_factory: Callable[[], Session],
) -> None:
    """I-17 / P3-T15: GET /runs filters by agent_version_id and status; an
    unfiltered request returns all runs ordered by created_at desc."""
    _seed(session_factory)
    client, _ = _client_with_db(session_factory)

    other_version = "agent_v1_draft_999_xxxxxx"
    with session_factory() as session:
        _make_run(
            session,
            run_id="run_succeeded_v1",
            status="succeeded",
            agent_version_id=DEFAULT_AGENT_VERSION_ID,
            incident_id=SEEDED_INCIDENT_ID,
        )
        _make_run(
            session,
            run_id="run_failed_v1",
            status="failed",
            agent_version_id=DEFAULT_AGENT_VERSION_ID,
            incident_id=SEEDED_INCIDENT_ID,
        )
        _make_run(
            session,
            run_id="run_succeeded_other",
            status="succeeded",
            agent_version_id=other_version,
            incident_id=SEEDED_INCIDENT_ID,
        )

    filtered = client.get(
        "/runs",
        params={
            "agent_version_id": DEFAULT_AGENT_VERSION_ID,
            "status": "succeeded",
        },
    )
    assert filtered.status_code == 200
    filtered_ids = {run["id"] for run in filtered.json()}
    assert filtered_ids == {"run_succeeded_v1"}

    all_runs = client.get("/runs")
    assert all_runs.status_code == 200
    all_ids = [run["id"] for run in all_runs.json()]
    assert {"run_succeeded_v1", "run_failed_v1", "run_succeeded_other"} <= set(all_ids)
    # Newest first: the three we inserted are the newest rows after the seed.
    assert all_ids[0] in {"run_succeeded_v1", "run_failed_v1", "run_succeeded_other"}


def test_get_run_steps_returns_step_history(
    session_factory: Callable[[], Session],
) -> None:
    """GET /runs/{id}/steps returns the run's step list; 404 for unknown runs."""
    _seed(session_factory)
    client, _ = _client_with_db(session_factory)

    create = client.post(
        "/runs",
        json={
            "agent_version_id": DEFAULT_AGENT_VERSION_ID,
            "incident_id": SEEDED_INCIDENT_ID,
            "run_inline": True,
        },
    )
    run_id = create.json()["id"]

    steps = client.get(f"/runs/{run_id}/steps")
    assert steps.status_code == 200
    assert isinstance(steps.json(), list)
    assert steps.json()

    unknown = client.get("/runs/run_no_such/steps")
    assert unknown.status_code == 404


def test_post_runs_rejects_unknown_version_with_404(
    session_factory: Callable[[], Session],
) -> None:
    _seed(session_factory)
    client, _ = _client_with_db(session_factory)

    response = client.post(
        "/runs",
        json={
            "agent_version_id": "version_does_not_exist",
            "incident_id": SEEDED_INCIDENT_ID,
            "run_inline": True,
        },
    )
    assert response.status_code == 404


def test_post_runs_rejects_unknown_incident_with_404(
    session_factory: Callable[[], Session],
) -> None:
    """A non-existent incident_id is rejected with 404 before the insert, rather
    than tripping the incidents FK constraint (which the IntegrityError handler
    would otherwise surface as a misleading 409 "active run exists"). SQLite does
    not enforce FKs without PRAGMA foreign_keys=ON, so this Python-level guard is
    the only path that surfaces the 404."""
    _seed(session_factory)
    client, _ = _client_with_db(session_factory)

    response = client.post(
        "/runs",
        json={
            "agent_version_id": DEFAULT_AGENT_VERSION_ID,
            "incident_id": "inc_does_not_exist",
            "run_inline": True,
        },
    )
    assert response.status_code == 404
    assert "incident" in response.json()["detail"].lower()


def test_post_runs_token_gated_in_demo_env(
    session_factory: Callable[[], Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """P3-T7: in the demo env with DEMO_OPERATOR_TOKEN unset, POST /runs and
    POST /runs/{id}/transitions fail closed with 403; a valid token is accepted."""
    _seed(session_factory)
    client, _ = _client_with_db(session_factory)

    # Seed a queued run to target with the transition call.
    with session_factory() as session:
        _make_run(
            session,
            run_id="run_demo_gate",
            status="queued",
            incident_id=SEEDED_INCIDENT_ID,
        )

    monkeypatch.setenv("APP_ENV", "demo")
    monkeypatch.delenv("DEMO_OPERATOR_TOKEN", raising=False)
    get_settings.cache_clear()

    try:
        unauthed_create = client.post(
            "/runs",
            json={
                "agent_version_id": DEFAULT_AGENT_VERSION_ID,
                "incident_id": SEEDED_INCIDENT_ID,
                "run_inline": True,
            },
        )
        assert unauthed_create.status_code == 403

        unauthed_transition = client.post(
            "/runs/run_demo_gate/transitions",
            json={"status": "running"},
        )
        assert unauthed_transition.status_code == 403
    finally:
        # Restore the cached settings for the rest of the suite.
        get_settings.cache_clear()

    # Positive case: a valid token is accepted and the run executes inline.
    # Clear the gate-test run first: agent_runs.incident_id has a unique
    # constraint (Project 1 idempotency), and the control-plane creator has no
    # reuse path by design, so a second insert against the same incident would
    # collide. run_demo_gate has already served its purpose for the 403 asserts.
    with session_factory() as session:
        gate_run = session.get(AgentRun, "run_demo_gate")
        if gate_run is not None:
            session.delete(gate_run)
            session.commit()

    monkeypatch.setenv("APP_ENV", "demo")
    monkeypatch.setenv("DEMO_OPERATOR_TOKEN", "demo-secret")
    get_settings.cache_clear()
    try:
        authed = client.post(
            "/runs",
            json={
                "agent_version_id": DEFAULT_AGENT_VERSION_ID,
                "incident_id": SEEDED_INCIDENT_ID,
                "run_inline": True,
            },
            headers={"X-Demo-Operator-Token": "demo-secret"},
        )
        assert authed.status_code == 202
        assert authed.json()["status"] == "succeeded"
    finally:
        get_settings.cache_clear()


def test_concurrent_null_incident_runs_coexist_under_partial_unique_index(
    session_factory: Callable[[], Session],
) -> None:
    """The partial unique index ``uq_agent_runs_active_incident`` excludes NULL
    incident_ids, so multiple active non-incident control-plane runs coexist;
    duplicate active non-null incident_ids still collide (Project 1 idempotency).
    """
    _seed(session_factory)
    with session_factory() as session:
        _make_run(
            session, run_id="run_no_incident_a", status="queued", incident_id=None
        )
        _make_run(
            session, run_id="run_no_incident_b", status="queued", incident_id=None
        )

    with session_factory() as session:
        a = session.get(AgentRun, "run_no_incident_a")
        b = session.get(AgentRun, "run_no_incident_b")
    assert a is not None and b is not None
    assert a.status == "queued"
    assert b.status == "queued"

    # Two active runs against the same non-null incident must collide.
    with session_factory() as session:
        _make_run(
            session,
            run_id="run_incident_first",
            status="queued",
            incident_id=SEEDED_INCIDENT_ID,
        )
    with pytest.raises(IntegrityError):
        with session_factory() as session:
            _make_run(
                session,
                run_id="run_incident_dup",
                status="queued",
                incident_id=SEEDED_INCIDENT_ID,
            )


def test_post_runs_concurrent_same_incident_returns_409(
    session_factory: Callable[[], Session],
) -> None:
    """A second POST /runs against an incident that already has an active run
    collides on the partial unique index and returns 409 (not 500)."""
    _seed(session_factory)
    client, _ = _client_with_db(session_factory)

    with session_factory() as session:
        _make_run(
            session,
            run_id="run_active_incident",
            status="queued",
            incident_id=SEEDED_INCIDENT_ID,
        )

    response = client.post(
        "/runs",
        json={
            "agent_version_id": DEFAULT_AGENT_VERSION_ID,
            "incident_id": SEEDED_INCIDENT_ID,
            "run_inline": False,
        },
    )
    assert response.status_code == 409
    assert "active run" in response.json()["detail"].lower()


def test_control_plane_task_records_clean_timeout_when_workflow_raises_soft_limit(
    session_factory: Callable[[], Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The executor re-raises SoftTimeLimitExceeded (rather than swallowing it
    via the generic ``except Exception``), so the task's timeout handler marks
    the run failed with the clean ``soft_time_limit_exceeded`` reason.

    This exercises the real executor path by monkeypatching the workflow (not
    the whole executor), so it verifies the re-raise wiring end-to-end.
    """
    from app.runs.tasks import run_control_plane_run

    _seed(session_factory)
    with session_factory() as session:
        _make_run(
            session,
            run_id="run_cp_timeout_real",
            status="queued",
            incident_id=SEEDED_INCIDENT_ID,
        )

    def raising_workflow(*_args, **_kwargs):
        raise SoftTimeLimitExceeded()

    # Spy on _finish_trace so we can assert the root trace is finalized with the
    # clean timeout reason before the re-raise (otherwise the observation is
    # left dangling on observability backends).
    finish_calls: list[dict[str, object]] = []

    def spy_finish(_trace, *, outputs=None, error=None):
        finish_calls.append({"error": error})

    monkeypatch.setattr("app.agent.service._finish_trace", spy_finish)
    monkeypatch.setattr(
        "app.agent.service.run_investigation_workflow", raising_workflow
    )
    # The task opens its own session via SessionLocal; point it at the test DB.
    monkeypatch.setattr("app.runs.tasks.SessionLocal", session_factory)

    with pytest.raises(SoftTimeLimitExceeded):
        run_control_plane_run.run("run_cp_timeout_real")

    with session_factory() as session:
        run = session.get(AgentRun, "run_cp_timeout_real")

    assert run is not None
    assert run.status == "failed"
    assert run.error == "soft_time_limit_exceeded"
    assert run.completed_at is not None
    # The trace was finalized with the clean timeout reason before the re-raise.
    assert any(
        call["error"] == "soft_time_limit_exceeded" for call in finish_calls
    )


def test_post_runs_with_null_incident_id_fails_with_clear_reason(
    session_factory: Callable[[], Session],
) -> None:
    """A control-plane run with no incident_id cannot drive the v1 incident-bound
    workflow; it fails gracefully (202 + failed) with a clear reason instead of
    the opaque 'Unknown incident id: None' from the workflow intake node."""
    _seed(session_factory)
    client, _ = _client_with_db(session_factory)

    response = client.post(
        "/runs",
        json={
            "agent_version_id": DEFAULT_AGENT_VERSION_ID,
            "incident_id": None,
            "run_inline": True,
        },
    )
    assert response.status_code == 202
    payload = response.json()
    assert payload["status"] == "failed"
    assert payload["incident_id"] is None
    assert "incident_id" in payload["error"].lower()
    assert payload["final_report"] is None


def test_executor_success_does_not_overwrite_concurrent_force_fail(
    session_factory: Callable[[], Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pass 5 regression: when an operator force-fails a run via the transition
    API while the executor is finalizing a successful workflow result, the
    conditional finalization must NOT overwrite the operator's ``failed`` status
    with ``succeeded`` (and must not commit the workflow's report).

    Before the fix the executor used an unconditional ORM commit
    (``finished_run.status = 'succeeded'``) that clobbered the concurrent
    force-fail, because ``session.get`` returned a stale identity-map object and
    no conditional UPDATE guarded the success write. The fix adds
    ``session.refresh`` (sync the identity map with the DB) plus a conditional
    UPDATE ``WHERE status = 'running'`` so a concurrent terminal transition wins.

    The wrapper commits the executor's session (mirroring the real recorder's
    periodic commits) so the executor's subsequent SELECTs observe the committed
    force-fail instead of a stale snapshot, then force-fails the run in a
    separate session to model the concurrent operator action."""
    from app.agent.schemas import InvestigationReport
    from app.agent.service import execute_investigation_run_with_session

    _seed(session_factory)
    with session_factory() as session:
        _make_run(
            session,
            run_id="run_concurrent_forcefail",
            status="queued",
            incident_id=SEEDED_INCIDENT_ID,
        )

    fake_report = InvestigationReport(
        root_cause="seeded root cause",
        summary="workflow completed",
        affected_accounts=[],
        cited_evidence=[],
        confidence="high",
        next_actions=[],
        generated_at=utcnow_naive(),
    )

    finish_calls: list[dict[str, object]] = []

    def spy_finish(_trace, *, outputs=None, error=None):
        finish_calls.append({"outputs": outputs, "error": error})

    def workflow_with_concurrent_forcefail(session, _run, _trace, **_kwargs):
        # Mirror the real recorder's periodic commit so the executor's later
        # SELECTs see committed concurrent changes instead of a stale snapshot.
        session.commit()
        # Concurrent operator force-fail in a separate session (running -> failed
        # is a legal transition). This is the race window the fix guards.
        with session_factory() as other:
            transition_run(other, "run_concurrent_forcefail", "failed")
        return fake_report

    monkeypatch.setattr("app.agent.service._finish_trace", spy_finish)
    monkeypatch.setattr(
        "app.agent.service.run_investigation_workflow",
        workflow_with_concurrent_forcefail,
    )

    with session_factory() as session:
        detail = execute_investigation_run_with_session(
            session, "run_concurrent_forcefail"
        )

    # The operator's force-fail must win; the success finalization must not
    # overwrite it with succeeded or commit the workflow's report.
    assert detail.status == "failed"
    assert detail.final_report is None
    # The trace was finalized with the ACTUAL persisted state (error from the
    # force-failed run, outputs=None since no report was committed).
    assert any(call["error"] for call in finish_calls)
    assert all(call["outputs"] is None for call in finish_calls)

    # Confirm the persisted row itself is failed (not just the returned detail).
    with session_factory() as session:
        run = session.get(AgentRun, "run_concurrent_forcefail")
    assert run is not None
    assert run.status == "failed"
    assert run.final_report is None


def test_executor_failure_does_not_overwrite_concurrent_force_succeed(
    session_factory: Callable[[], Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pass 7 fix: when an operator force-succeeds a run while the executor is
    failing it (workflow raises), the conditional UPDATE + status check in
    ``_fail_running_run`` must NOT overwrite the operator's ``succeeded`` status
    with ``failed``.

    Pass 10 Fix C: the trace is finalized with the ACTUAL persisted state
    (``outputs=None, error=None`` for a force-succeeded run), NOT the
    workflow's ``RuntimeError`` error string — proving the ``_finish_trace``
    reordering works correctly.

    Symmetric to ``test_executor_success_does_not_overwrite_concurrent_force_fail``.
    Before the fix, ``_fail_running_run`` used an unconditional ORM commit
    (``failed_run.status = 'failed'``) that would clobber a concurrent
    force-succeed. The fix uses a conditional UPDATE ``WHERE status = 'running'``
    so a concurrent terminal transition wins."""
    from app.agent.service import execute_investigation_run_with_session

    _seed(session_factory)
    with session_factory() as session:
        _make_run(
            session,
            run_id="run_concurrent_succeed",
            status="queued",
            incident_id=SEEDED_INCIDENT_ID,
        )

    finish_calls: list[dict[str, object]] = []

    def spy_finish(_trace, *, outputs=None, error=None):
        finish_calls.append({"outputs": outputs, "error": error})

    def workflow_with_concurrent_succeed(session, _run, _trace, **_kwargs):
        session.commit()
        with session_factory() as other:
            transition_run(other, "run_concurrent_succeed", "succeeded")
        raise RuntimeError("workflow failed after operator force-succeed")

    monkeypatch.setattr("app.agent.service._finish_trace", spy_finish)
    monkeypatch.setattr(
        "app.agent.service.run_investigation_workflow",
        workflow_with_concurrent_succeed,
    )

    with session_factory() as session:
        detail = execute_investigation_run_with_session(
            session, "run_concurrent_succeed"
        )

    assert detail.status == "succeeded"
    assert detail.error is None

    # The trace must reflect the ACTUAL persisted state (succeeded, no error,
    # no report), NOT the workflow's RuntimeError.
    assert len(finish_calls) >= 1
    last_call = finish_calls[-1]
    assert last_call["error"] is None
    assert last_call["outputs"] is None

    with session_factory() as session:
        run = session.get(AgentRun, "run_concurrent_succeed")
    assert run is not None
    assert run.status == "succeeded"
    assert run.error is None


def test_post_runs_async_returns_queued_and_enqueues_task(
    session_factory: Callable[[], Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """P3-T11 async path: POST /runs with run_inline=False returns 202 with the
    run still ``queued`` and dispatches the Celery task (not the inline executor).
    Every prior 202 test used run_inline=True; this locks in the async contract."""
    _seed(session_factory)
    client, _ = _client_with_db(session_factory)

    enqueued: list[str] = []

    def spy_enqueue(run_id: str) -> None:
        enqueued.append(run_id)

    monkeypatch.setattr("app.runs.router._enqueue_run", spy_enqueue)

    response = client.post(
        "/runs",
        json={
            "agent_version_id": DEFAULT_AGENT_VERSION_ID,
            "incident_id": SEEDED_INCIDENT_ID,
            "run_inline": False,
        },
    )
    assert response.status_code == 202
    payload = response.json()
    assert payload["status"] == "queued"
    assert payload["final_report"] is None
    # The task was dispatched with the new run's id.
    assert enqueued == [payload["id"]]


def test_control_plane_task_drives_queued_run_to_succeeded(
    session_factory: Callable[[], Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """P3-T3/T11 happy path: ``run_control_plane_run`` opens its own session,
    loads the queued run, and drives it to ``succeeded`` via the executor. The
    timeout path was tested but the success path (session lifecycle, transition
    to succeeded, final_report persisted) had zero coverage."""
    from app.runs.tasks import run_control_plane_run

    _seed(session_factory)
    with session_factory() as session:
        _make_run(
            session,
            run_id="run_cp_happy",
            status="queued",
            incident_id=SEEDED_INCIDENT_ID,
        )

    # The task opens its own session via SessionLocal; point it at the test DB.
    monkeypatch.setattr("app.runs.tasks.SessionLocal", session_factory)

    run_control_plane_run.run("run_cp_happy")

    with session_factory() as session:
        run = session.get(AgentRun, "run_cp_happy")

    assert run is not None
    assert run.status == "succeeded"
    assert run.final_report is not None
    assert run.completed_at is not None
