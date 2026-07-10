from __future__ import annotations

import threading
import time
from collections.abc import Callable, Generator
from datetime import datetime, timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

import app.models  # noqa: F401
from app.agent.schemas import (
    InvestigationReport,
    ReportAffectedAccount,
    ReportEvidence,
)
from app.agent.persistence import AgentRunRecorder, utcnow_naive
from app.agents.service import (
    DEFAULT_AGENT_ID,
    DEFAULT_AGENT_VERSION_ID,
)
from app.db.base import Base
from app.db.session import get_db
from app.main import app
from app.models import AgentRun, AgentRunStep, AgentVersion, Incident
from app.seed import reseed_database


@pytest.fixture()
def session_factory(tmp_path) -> Generator[Callable[[], Session], None, None]:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'agent_investigations_test.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    yield TestingSessionLocal

    Base.metadata.drop_all(engine)
    engine.dispose()


def test_investigation_run_produces_structured_evidence_backed_report(
    session_factory: Callable[[], Session],
) -> None:
    with session_factory() as session:
        reseed_database(session)
        incident = session.scalar(select(Incident))
        assert incident is not None
        incident.source_scenario = None
        evidence = dict(incident.evidence)
        for evidence_key in ("affected_accounts", "support_signals", "product_signals"):
            evidence[evidence_key] = [
                {**item, "source_scenario": None}
                for item in evidence.get(evidence_key, [])
            ]
        incident.evidence = evidence
        incident_id = incident.id
        session.commit()

    assert incident_id is not None

    def override_get_db() -> Generator[Session, None, None]:
        with session_factory() as db:
            yield db

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)
    try:
        response = client.post(
            "/agent/investigations",
            json={"incident_id": incident_id, "run_inline": True},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 201
    payload = response.json()
    assert payload["incident_id"] == incident_id
    assert payload["status"] == "succeeded"
    assert payload["trace_id"]
    assert payload["trace_url"]
    assert payload["trace_provider"] in {"langfuse", "langsmith", "local"}
    assert payload["final_report"] is not None

    report = payload["final_report"]
    assert (
        report["root_cause"]
        == "Billing retry webhook regression suppressed second charge attempts."
    )
    assert len(report["affected_accounts"]) == 6
    assert report["confidence"] == "high"
    assert report["next_actions"]
    assert {action["action_type"] for action in payload["mock_actions"]} == {
        "draft_slack_message",
        "draft_customer_email",
        "create_task",
        "update_account_note",
    }
    low_risk_actions = [
        action for action in payload["mock_actions"] if action["risk_level"] == "low"
    ]
    high_risk_actions = [
        action for action in payload["mock_actions"] if action["risk_level"] == "high"
    ]
    assert {action["status"] for action in low_risk_actions} == {"executed"}
    assert {action["status"] for action in high_risk_actions} == {"pending_approval"}
    assert all(action["approval_request"] is None for action in low_risk_actions)
    assert all(action["approval_request"]["status"] == "pending" for action in high_risk_actions)

    evidence_kinds = {item["kind"] for item in report["cited_evidence"]}
    assert "sql" in evidence_kinds
    assert "document" in evidence_kinds
    sql_evidence = [
        item for item in report["cited_evidence"] if item["kind"] == "sql"
    ]
    assert all("SELECT" in item["source_query"] for item in sql_evidence)
    assert all(item["citation"]["parameters"] for item in sql_evidence)
    assert all(item["citation"]["rows"] for item in sql_evidence)
    assert any(
        row["window"] == "current"
        for item in sql_evidence
        for row in item["citation"]["rows"]
        if row.get("window")
    )
    assert any(
        any("Retry webhook" in reason for reason in row["failure_reasons"])
        for item in sql_evidence
        for row in item["citation"]["rows"]
        if row.get("failure_reasons")
    )
    assert any(
        item["citation"]["source_id"] == "kb-runbook-billing-retry-regression"
        for item in report["cited_evidence"]
        if item["kind"] == "document"
    )
    evidence_refs = {item["reference_id"] for item in report["cited_evidence"]}
    assert {claim["category"] for claim in report["claims"]} >= {
        "root_cause",
        "impact",
        "recommendation",
    }
    assert all(claim["citation_refs"] for claim in report["claims"])
    assert all(
        set(claim["citation_refs"]).issubset(evidence_refs)
        for claim in report["claims"]
    )
    cited_recommendations = {
        claim["text"]
        for claim in report["claims"]
        if claim["category"] == "recommendation" and claim["citation_refs"]
    }
    assert set(report["next_actions"]).issubset(cited_recommendations)

    tool_steps = [step for step in payload["steps"] if step["tool_name"]]
    assert {step["tool_name"] for step in tool_steps} >= {
        "query_revenue_metrics",
        "fetch_account_details",
        "search_docs",
        "fetch_support_tickets",
        "create_mock_action",
        "request_approval",
    }
    assert all(step["status"] == "succeeded" for step in tool_steps)


def test_project1_v1_preserves_legacy_actions_without_mutating_snapshot(
    session_factory: Callable[[], Session],
) -> None:
    with session_factory() as session:
        reseed_database(session)
        incident_id = session.scalar(select(Incident.id).order_by(Incident.id))
        version = session.get(AgentVersion, DEFAULT_AGENT_VERSION_ID)
        assert incident_id is not None
        assert version is not None
        original_tool_ids = list(version.enabled_tool_ids)

    def override_get_db() -> Generator[Session, None, None]:
        with session_factory() as db:
            yield db

    app.dependency_overrides[get_db] = override_get_db
    try:
        response = TestClient(app).post(
            "/agent/investigations",
            json={
                "incident_id": incident_id,
                "agent_version_id": DEFAULT_AGENT_VERSION_ID,
                "run_inline": True,
            },
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 201
    payload = response.json()
    assert payload["agent_version_id"] == DEFAULT_AGENT_VERSION_ID
    assert {action["action_type"] for action in payload["mock_actions"]} == {
        "draft_slack_message",
        "draft_customer_email",
        "create_task",
        "update_account_note",
    }
    assert sum(
        action["approval_request"] is not None for action in payload["mock_actions"]
    ) == 2

    with session_factory() as session:
        version = session.get(AgentVersion, DEFAULT_AGENT_VERSION_ID)
        assert version is not None
        assert version.enabled_tool_ids == original_tool_ids
        assert version.enabled_tool_ids == [
            "query_revenue_metrics",
            "fetch_account_details",
            "search_docs",
            "fetch_support_tickets",
        ]


def test_default_investigation_launch_returns_queued_run_then_completes(
    session_factory: Callable[[], Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with session_factory() as session:
        reseed_database(session)
        incident_id = session.scalar(select(Incident.id))

    assert incident_id is not None

    def override_get_db() -> Generator[Session, None, None]:
        with session_factory() as db:
            yield db

    def execute_with_test_session(run_id: str) -> None:
        from app.agent.service import execute_investigation_run_with_session

        with session_factory() as db:
            execute_investigation_run_with_session(db, run_id)

    monkeypatch.setattr(
        "app.agent.router._enqueue_investigation",
        execute_with_test_session,
    )

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)
    try:
        response = client.post(
            "/agent/investigations",
            json={"incident_id": incident_id},
        )
        payload = response.json()
        run_response = client.get(f"/agent/runs/{payload['id']}")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 201
    assert payload["status"] == "queued"
    # PRD AC-6.3: a queued run carries a local placeholder trace link at queue
    # time (not None); start_agent_trace overwrites it with the real provider
    # trace once the run is claimed.
    assert payload["trace_id"]
    assert payload["trace_provider"] == "local"
    assert payload["final_report"] is None

    assert run_response.status_code == 200
    completed = run_response.json()
    assert completed["status"] == "succeeded"
    assert completed["trace_id"]
    assert completed["trace_provider"]
    assert completed["final_report"] is not None
    assert completed["token_estimate"] > 0
    assert completed["cost_estimate_usd"] == 0.0
    assert completed["steps"]
    assert completed["mock_actions"]


def test_investigation_start_restarts_after_orphaned_running_run(
    session_factory: Callable[[], Session],
) -> None:
    with session_factory() as session:
        reseed_database(session)
        incident_id = session.scalar(select(Incident.id))
        assert incident_id is not None
        now = datetime(2026, 6, 9, 12, 30, 0)
        orphaned_run = AgentRun(
            id="run_orphaned_running",
            incident_id=incident_id,
            agent_id=DEFAULT_AGENT_ID,
            agent_version_id=DEFAULT_AGENT_VERSION_ID,
            status="running",
            trace_id="local-test-trace",
            input_payload={"incident_id": incident_id},
            final_report=None,
            token_estimate=0,
            cost_estimate_usd=0.0,
            error=None,
            started_at=now,
            completed_at=None,
            created_at=now,
            updated_at=now,
        )
        session.add(orphaned_run)
        session.commit()

    def override_get_db() -> Generator[Session, None, None]:
        with session_factory() as db:
            yield db

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)
    try:
        response = client.post(
            "/agent/investigations",
            json={"incident_id": incident_id, "run_inline": True},
        )
        orphaned_response = client.get("/agent/runs/run_orphaned_running")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 201
    payload = response.json()
    assert payload["id"] != "run_orphaned_running"
    assert payload["status"] == "succeeded"
    assert payload["final_report"] is not None

    assert orphaned_response.status_code == 200
    orphaned_payload = orphaned_response.json()
    assert orphaned_payload["status"] == "failed"
    assert orphaned_payload["error"] == "Investigation interrupted before completion."


def test_abandoned_run_is_not_resurrected_when_workflow_completes(
    session_factory: Callable[[], Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with session_factory() as session:
        reseed_database(session)
        incident_id = session.scalar(select(Incident.id))
        assert incident_id is not None

    def workflow_abandoned_before_completion(
        session: Session, run: AgentRun, trace: object | None = None, **_kwargs: object
    ) -> InvestigationReport:
        abandoned_run = session.get(AgentRun, run.id)
        assert abandoned_run is not None
        abandoned_run.status = "failed"
        abandoned_run.error = "Investigation interrupted before completion."
        abandoned_run.completed_at = datetime(2026, 6, 9, 12, 40, 0)
        abandoned_run.updated_at = datetime(2026, 6, 9, 12, 40, 0)
        session.commit()
        return sample_report()

    monkeypatch.setattr(
        "app.agent.service.run_investigation_workflow",
        workflow_abandoned_before_completion,
    )

    def override_get_db() -> Generator[Session, None, None]:
        with session_factory() as db:
            yield db

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)
    try:
        response = client.post(
            "/agent/investigations",
            json={"incident_id": incident_id, "run_inline": True},
        )
        run_id = response.json()["id"]
        detail_response = client.get(f"/agent/runs/{run_id}")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 201
    payload = response.json()
    assert payload["status"] == "failed"
    assert payload["error"] == "Investigation interrupted before completion."
    assert payload["final_report"] is None

    assert detail_response.status_code == 200
    detail_payload = detail_response.json()
    assert detail_payload["status"] == "failed"
    assert detail_payload["final_report"] is None


def test_investigation_start_reuses_active_queued_run_by_default(
    session_factory: Callable[[], Session],
) -> None:
    with session_factory() as session:
        reseed_database(session)
        incident_id = session.scalar(select(Incident.id))
        assert incident_id is not None
        now = datetime(2026, 6, 9, 12, 30, 0)
        active_run = AgentRun(
            id="run_active_queued",
            incident_id=incident_id,
            agent_id=DEFAULT_AGENT_ID,
            agent_version_id=DEFAULT_AGENT_VERSION_ID,
            status="queued",
            trace_id=None,
            input_payload={"incident_id": incident_id},
            final_report=None,
            token_estimate=0,
            cost_estimate_usd=0.0,
            error=None,
            started_at=None,
            completed_at=None,
            created_at=now,
            updated_at=utcnow_naive() - timedelta(minutes=1),
        )
        session.add(active_run)
        session.commit()

    def override_get_db() -> Generator[Session, None, None]:
        with session_factory() as db:
            yield db

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)
    try:
        response = client.post(
            "/agent/investigations",
            json={"incident_id": incident_id},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    payload = response.json()
    assert payload["id"] == "run_active_queued"
    assert payload["status"] == "queued"


def test_agent_run_read_exposes_stale_queued_run_without_mutating_status(
    session_factory: Callable[[], Session],
) -> None:
    with session_factory() as session:
        reseed_database(session)
        incident_id = session.scalar(select(Incident.id))
        assert incident_id is not None
        now = datetime(2026, 6, 9, 12, 30, 0)
        stale_run = AgentRun(
            id="run_stale_queued",
            incident_id=incident_id,
            agent_id=DEFAULT_AGENT_ID,
            agent_version_id=DEFAULT_AGENT_VERSION_ID,
            status="queued",
            trace_id=None,
            input_payload={"incident_id": incident_id},
            final_report=None,
            token_estimate=0,
            cost_estimate_usd=0.0,
            error=None,
            started_at=None,
            completed_at=None,
            created_at=now,
            updated_at=utcnow_naive() - timedelta(minutes=11),
        )
        session.add(stale_run)
        session.commit()

    def override_get_db() -> Generator[Session, None, None]:
        with session_factory() as db:
            yield db

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)
    try:
        response = client.get("/agent/runs/run_stale_queued")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "queued"
    assert payload["is_stale"] is True
    assert payload["error"] is None
    with session_factory() as session:
        persisted_status = session.get(AgentRun, "run_stale_queued").status
    assert persisted_status == "queued"


def test_running_run_with_recent_step_activity_is_not_marked_stale(
    session_factory: Callable[[], Session],
) -> None:
    with session_factory() as session:
        reseed_database(session)
        incident_id = session.scalar(select(Incident.id))
        assert incident_id is not None
        old_timestamp = utcnow_naive() - timedelta(minutes=11)
        run = AgentRun(
            id="run_running_with_recent_step",
            incident_id=incident_id,
            agent_id=DEFAULT_AGENT_ID,
            agent_version_id=DEFAULT_AGENT_VERSION_ID,
            status="running",
            trace_id="local-test-trace",
            input_payload={"incident_id": incident_id},
            final_report=None,
            token_estimate=0,
            cost_estimate_usd=0.0,
            error=None,
            started_at=old_timestamp,
            completed_at=None,
            created_at=old_timestamp,
            updated_at=old_timestamp,
        )
        step = AgentRunStep(
            id="step_recent_activity",
            run_id=run.id,
            sequence=1,
            stage="query revenue",
            tool_name="query_revenue_metrics",
            status="running",
            inputs={},
            outputs=None,
            error=None,
            started_at=utcnow_naive() - timedelta(minutes=1),
            completed_at=None,
            created_at=old_timestamp,
        )
        session.add_all([run, step])
        session.commit()

    def override_get_db() -> Generator[Session, None, None]:
        with session_factory() as db:
            yield db

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)
    try:
        response = client.get("/agent/runs/run_running_with_recent_step")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "running"
    assert payload["is_stale"] is False
    assert payload["error"] is None


def test_failed_run_is_terminal_for_background_executor(
    session_factory: Callable[[], Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with session_factory() as session:
        reseed_database(session)
        incident_id = session.scalar(select(Incident.id))
        assert incident_id is not None
        now = utcnow_naive() - timedelta(minutes=11)
        run = AgentRun(
            id="run_failed_terminal",
            incident_id=incident_id,
            agent_id=DEFAULT_AGENT_ID,
            agent_version_id=DEFAULT_AGENT_VERSION_ID,
            status="failed",
            trace_id="local-test-trace",
            input_payload={"incident_id": incident_id},
            final_report=None,
            token_estimate=0,
            cost_estimate_usd=0.0,
            error="Investigation interrupted before completion.",
            started_at=now,
            completed_at=now,
            created_at=now,
            updated_at=now,
        )
        session.add(run)
        session.commit()

        def fail_if_called(*_args: object, **_kwargs: object) -> None:
            raise AssertionError("failed runs must not be executed")

        monkeypatch.setattr(
            "app.agent.service.run_investigation_workflow",
            fail_if_called,
        )

        from app.agent.service import execute_investigation_run_with_session

        detail = execute_investigation_run_with_session(session, run.id)

    assert detail.status == "failed"
    assert detail.error == "Investigation interrupted before completion."
    assert detail.steps == []


def test_concurrent_executors_claim_queued_run_once(
    session_factory: Callable[[], Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with session_factory() as session:
        reseed_database(session)
        incident_id = session.scalar(select(Incident.id))
        assert incident_id is not None
        now = utcnow_naive()
        run = AgentRun(
            id="run_claim_once",
            incident_id=incident_id,
            agent_id=DEFAULT_AGENT_ID,
            agent_version_id=DEFAULT_AGENT_VERSION_ID,
            status="queued",
            trace_id=None,
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
        session.commit()

    workflow_calls = 0
    workflow_lock = threading.Lock()

    def workflow_once(*_args: object, **_kwargs: object) -> InvestigationReport:
        nonlocal workflow_calls
        with workflow_lock:
            workflow_calls += 1
        time.sleep(0.05)
        return sample_report()

    monkeypatch.setattr("app.agent.service.run_investigation_workflow", workflow_once)
    monkeypatch.setattr("app.agent.service.propose_actions_for_report", lambda *_args, **_kwargs: [])

    from app.agent.service import execute_investigation_run_with_session

    errors: list[BaseException] = []

    def execute() -> None:
        try:
            with session_factory() as db:
                execute_investigation_run_with_session(db, "run_claim_once")
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=execute), threading.Thread(target=execute)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    assert workflow_calls == 1
    with session_factory() as session:
        run = session.get(AgentRun, "run_claim_once")
        assert run is not None
        assert run.status == "succeeded"


def test_database_rejects_two_active_runs_for_same_incident_and_version(
    session_factory: Callable[[], Session],
) -> None:
    with session_factory() as session:
        reseed_database(session)
        incident_id = session.scalar(select(Incident.id))
        assert incident_id is not None
        now = utcnow_naive()
        first_run = AgentRun(
            id="run_active_one",
            incident_id=incident_id,
            agent_id=DEFAULT_AGENT_ID,
            agent_version_id=DEFAULT_AGENT_VERSION_ID,
            status="queued",
            trace_id=None,
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
        second_run = AgentRun(
            id="run_active_two",
            incident_id=incident_id,
            agent_id=DEFAULT_AGENT_ID,
            agent_version_id=DEFAULT_AGENT_VERSION_ID,
            status="running",
            trace_id="local-test-trace",
            input_payload={"incident_id": incident_id},
            final_report=None,
            token_estimate=0,
            cost_estimate_usd=0.0,
            error=None,
            started_at=now,
            completed_at=None,
            created_at=now,
            updated_at=now,
        )
        session.add(first_run)
        session.commit()
        session.add(second_run)

        with pytest.raises(IntegrityError):
            session.commit()


def test_investigation_fails_visibly_when_action_proposal_fails(
    session_factory: Callable[[], Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with session_factory() as session:
        reseed_database(session)
        incident_id = session.scalar(select(Incident.id))

    assert incident_id is not None

    def fail_action_proposal(*_args: object, **_kwargs: object) -> object:
        raise RuntimeError("mock action proposal unavailable")

    monkeypatch.setattr(
        "app.agent.service.propose_actions_for_report",
        fail_action_proposal,
    )

    def override_get_db() -> Generator[Session, None, None]:
        with session_factory() as db:
            yield db

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)
    try:
        response = client.post(
            "/agent/investigations",
            json={"incident_id": incident_id, "run_inline": True},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 201
    payload = response.json()
    assert payload["status"] == "failed"
    assert payload["final_report"] is not None
    assert "Action proposal failed: mock action proposal unavailable" in payload["error"]
    assert payload["mock_actions"] == []
    failed_step = next(
        step for step in payload["steps"] if step["tool_name"] == "create_mock_action"
    )
    assert failed_step["status"] == "failed"
    assert "mock action proposal unavailable" in failed_step["error"]


def test_investigation_start_reuses_existing_successful_run_by_default(
    session_factory: Callable[[], Session],
) -> None:
    with session_factory() as session:
        reseed_database(session)
        incident_id = session.scalar(select(Incident.id))

    assert incident_id is not None

    def override_get_db() -> Generator[Session, None, None]:
        with session_factory() as db:
            yield db

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)
    try:
        first_response = client.post(
            "/agent/investigations",
            json={"incident_id": incident_id, "run_inline": True},
        )
        initial_step_ids = [step["id"] for step in first_response.json()["steps"]]
        second_response = client.post(
            "/agent/investigations",
            json={"incident_id": incident_id, "run_inline": True},
        )
        third_response = client.post(
            "/agent/investigations",
            json={"incident_id": incident_id, "run_inline": True},
        )
    finally:
        app.dependency_overrides.clear()

    assert first_response.status_code == 201
    assert second_response.status_code == 200
    assert third_response.status_code == 200
    assert second_response.json()["id"] == first_response.json()["id"]
    assert third_response.json()["id"] == first_response.json()["id"]
    assert [step["id"] for step in second_response.json()["steps"]] == initial_step_ids
    assert [step["id"] for step in third_response.json()["steps"]] == initial_step_ids


def test_investigation_start_backfills_actions_for_existing_successful_run(
    session_factory: Callable[[], Session],
) -> None:
    with session_factory() as session:
        reseed_database(session)
        incident_id = session.scalar(select(Incident.id))
        assert incident_id is not None
        now = datetime(2026, 6, 9, 12, 30, 0)
        report = sample_report()
        run = AgentRun(
            id="run_success_before_actions",
            incident_id=incident_id,
            agent_id=DEFAULT_AGENT_ID,
            agent_version_id=DEFAULT_AGENT_VERSION_ID,
            status="succeeded",
            trace_id="local-test-trace",
            input_payload={"incident_id": incident_id},
            final_report=report.model_dump(mode="json"),
            token_estimate=1,
            cost_estimate_usd=0.0,
            error=None,
            started_at=now,
            completed_at=now,
            created_at=now,
            updated_at=now,
        )
        session.add(run)
        session.commit()

    def override_get_db() -> Generator[Session, None, None]:
        with session_factory() as db:
            yield db

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)
    try:
        response = client.post(
            "/agent/investigations",
            json={
                "incident_id": incident_id,
                "agent_version_id": DEFAULT_AGENT_VERSION_ID,
                "run_inline": True,
            },
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    payload = response.json()
    assert payload["id"] == "run_success_before_actions"
    assert {action["action_type"] for action in payload["mock_actions"]} == {
        "draft_slack_message",
        "draft_customer_email",
        "create_task",
        "update_account_note",
    }
    assert sum(
        action["approval_request"] is not None for action in payload["mock_actions"]
    ) == 2


def test_investigation_with_no_affected_accounts_finishes_with_uncertainty(
    session_factory: Callable[[], Session],
) -> None:
    with session_factory() as session:
        reseed_database(session)
        base_incident = session.scalar(select(Incident))
        assert base_incident is not None
        metric_evidence = {
            **base_incident.evidence["metric_evidence"],
            "current_window_start": "2024-01-01",
            "current_window_end": "2024-01-07",
            "previous_window_start": "2023-12-25",
            "previous_window_end": "2023-12-31",
            "current_value_cents": 0,
            "previous_value_cents": 0,
            "delta_cents": 0,
            "delta_percent": 0.0,
            "failed_invoice_cents": 0,
            "failed_invoice_count": 0,
            "invoice_ids": [],
        }
        incident = Incident(
            id="inc_agent_ambiguous_no_accounts",
            title="Ambiguous paid MRR movement",
            status="open",
            severity="low",
            anomaly_type=base_incident.anomaly_type,
            metric_name=base_incident.metric_name,
            summary="A revenue movement needs investigation but has no affected accounts yet.",
            source_scenario=None,
            detected_at=datetime(2024, 1, 8, 12, 0, 0),
            current_value_cents=0,
            previous_value_cents=0,
            delta_cents=0,
            delta_percent=0.0,
            affected_account_ids=[],
            evidence={
                "metric_evidence": metric_evidence,
                "affected_accounts": [],
                "support_signals": [],
                "product_signals": [],
                "source_queries": [
                    "paid invoice windows have not identified affected accounts"
                ],
            },
            created_at=datetime(2024, 1, 8, 12, 0, 0),
            updated_at=datetime(2024, 1, 8, 12, 0, 0),
        )
        session.add(incident)
        session.commit()
        incident_id = incident.id

    def override_get_db() -> Generator[Session, None, None]:
        with session_factory() as db:
            yield db

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)
    try:
        response = client.post(
            "/agent/investigations",
            json={"incident_id": incident_id, "run_inline": True},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 201
    payload = response.json()
    assert payload["status"] == "succeeded"
    report = payload["final_report"]
    assert report["confidence"] == "low"
    assert report["affected_accounts"] == []
    assert "does not prove a specific operational root cause" in report["root_cause"]


def test_failed_tool_call_is_persisted_and_surfaced(
    session_factory: Callable[[], Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with session_factory() as session:
        reseed_database(session)
        incident_id = session.scalar(select(Incident.id))

    assert incident_id is not None

    def fail_fetch_support_tickets(*_args: object, **_kwargs: object) -> object:
        raise RuntimeError("support ticket store unavailable")

    monkeypatch.setattr(
        "app.agent.workflow.fetch_support_tickets",
        fail_fetch_support_tickets,
    )

    def override_get_db() -> Generator[Session, None, None]:
        with session_factory() as db:
            yield db

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)
    try:
        response = client.post(
            "/agent/investigations",
            json={"incident_id": incident_id, "run_inline": True},
        )
        run_id = response.json()["id"]
        persisted_response = client.get(f"/agent/runs/{run_id}")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 201
    payload = response.json()
    assert payload["status"] == "failed"
    assert "support ticket store unavailable" in payload["error"]
    assert payload["final_report"] is None
    assert "llm_fallback_reason" not in payload["trace_metadata"]

    failed_step = next(
        step for step in payload["steps"] if step["tool_name"] == "fetch_support_tickets"
    )
    assert failed_step["status"] == "failed"
    assert "support ticket store unavailable" in failed_step["error"]
    assert failed_step["inputs"]["account_ids"]

    assert persisted_response.status_code == 200
    persisted_payload = persisted_response.json()
    assert persisted_payload["id"] == run_id
    assert persisted_payload["steps"][-1]["status"] == "failed"
    assert "llm_fallback_reason" not in persisted_payload["trace_metadata"]


def test_disabled_tools_do_not_create_metric_evidence(
    session_factory: Callable[[], Session],
) -> None:
    with session_factory() as session:
        reseed_database(session)
        incident_id = session.scalar(select(Incident.id))

    assert incident_id is not None

    def override_get_db() -> Generator[Session, None, None]:
        with session_factory() as db:
            yield db

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)
    try:
        version_resp = client.post(
            "/agents/revenue-ops-agent/versions",
            json={
                "enabled_tool_ids": [
                    "fetch_account_details",
                    "search_docs",
                ]
            },
        )
        assert version_resp.status_code == 201
        version_id = version_resp.json()["id"]
        publish_resp = client.post(
            f"/agents/revenue-ops-agent/versions/{version_id}/publish"
        )
        assert publish_resp.status_code == 200
        response = client.post(
            "/agent/investigations",
            json={
                "incident_id": incident_id,
                "run_inline": True,
                "force": True,
                "agent_version_id": version_id,
            },
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 201
    payload = response.json()
    disabled_step = next(
        step for step in payload["steps"] if step["tool_name"] == "query_revenue_metrics"
    )
    assert disabled_step["status"] == "blocked"
    assert disabled_step["blocked_reason"] == "tool_not_enabled"
    assert disabled_step["outputs"]["tool_disabled"] is True
    assert disabled_step["outputs"]["sql_evidence"] == []
    assert disabled_step["outputs"]["metric_evidence"]["failed_invoice_count"] == 0
    account_step = next(
        step for step in payload["steps"] if step["tool_name"] == "fetch_account_details"
    )
    assert account_step["inputs"]["account_ids"]
    assert account_step["inputs"]["include_invoices"] is False
    assert all(
        account["failed_invoices"] == []
        for account in account_step["outputs"]["accounts"]
    )
    assert all(
        account["failed_invoice_cents"] == 0
        and account["failed_invoice_ids"] == []
        for account in payload["final_report"]["affected_accounts"]
    )
    assert payload["final_report"]["cited_evidence"]
    assert all(
        item["kind"] != "sql" for item in payload["final_report"]["cited_evidence"]
    )
    tool_disabled_refs = {
        item["reference_id"]
        for item in payload["final_report"]["cited_evidence"]
        if item["kind"] == "tool"
    }
    assert tool_disabled_refs == {
        "tool-disabled:query_revenue_metrics",
        "tool-disabled:fetch_support_tickets",
    }
    assert "failed renewal" not in payload["final_report"]["root_cause"].lower()
    assert "failed renewal evidence" not in payload["final_report"]["summary"].lower()
    assert all(
        "failed renewal evidence" not in claim["text"].lower()
        for claim in payload["final_report"]["claims"]
    )
    assert all(
        set(claim["citation_refs"]) == tool_disabled_refs
        for claim in payload["final_report"]["claims"]
        if claim["category"] in {"root_cause", "impact", "uncertainty"}
    )
    assert payload["final_report"]["confidence"] != "high"


def test_disabled_context_tools_create_tool_evidence(
    session_factory: Callable[[], Session],
) -> None:
    with session_factory() as session:
        reseed_database(session)
        incident_id = session.scalar(select(Incident.id))

    assert incident_id is not None

    def override_get_db() -> Generator[Session, None, None]:
        with session_factory() as db:
            yield db

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)
    try:
        version_resp = client.post(
            "/agents/revenue-ops-agent/versions",
            json={
                "enabled_tool_ids": [
                    "query_revenue_metrics",
                    "fetch_account_details",
                ]
            },
        )
        assert version_resp.status_code == 201
        version_id = version_resp.json()["id"]
        publish_resp = client.post(
            f"/agents/revenue-ops-agent/versions/{version_id}/publish"
        )
        assert publish_resp.status_code == 200
        response = client.post(
            "/agent/investigations",
            json={
                "incident_id": incident_id,
                "run_inline": True,
                "force": True,
                "agent_version_id": version_id,
            },
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 201
    payload = response.json()
    report = payload["final_report"]
    tool_disabled_refs = {
        item["reference_id"]
        for item in report["cited_evidence"]
        if item["kind"] == "tool"
    }
    assert tool_disabled_refs == {
        "tool-disabled:search_docs",
        "tool-disabled:fetch_support_tickets",
    }
    uncertainty_claim = next(
        claim for claim in report["claims"] if claim["category"] == "uncertainty"
    )
    assert set(uncertainty_claim["citation_refs"]) == tool_disabled_refs


def test_single_disabled_context_tool_is_cited_by_a_claim(
    session_factory: Callable[[], Session],
) -> None:
    with session_factory() as session:
        reseed_database(session)
        incident_id = session.scalar(select(Incident.id))

    assert incident_id is not None

    def override_get_db() -> Generator[Session, None, None]:
        with session_factory() as db:
            yield db

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)
    try:
        version_resp = client.post(
            "/agents/revenue-ops-agent/versions",
            json={
                "enabled_tool_ids": [
                    "query_revenue_metrics",
                    "fetch_account_details",
                    "fetch_support_tickets",
                ]
            },
        )
        assert version_resp.status_code == 201
        version_id = version_resp.json()["id"]
        publish_resp = client.post(
            f"/agents/revenue-ops-agent/versions/{version_id}/publish"
        )
        assert publish_resp.status_code == 200
        response = client.post(
            "/agent/investigations",
            json={
                "incident_id": incident_id,
                "run_inline": True,
                "force": True,
                "agent_version_id": version_id,
            },
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 201
    payload = response.json()
    assert payload["status"] == "succeeded"
    report = payload["final_report"]
    disabled_ref = "tool-disabled:search_docs"
    assert any(
        item["reference_id"] == disabled_ref and item["kind"] == "tool"
        for item in report["cited_evidence"]
    )
    cited_by_claims = {
        ref for claim in report["claims"] for ref in claim["citation_refs"]
    }
    assert disabled_ref in cited_by_claims


def test_disabled_account_details_tool_is_cited_by_report(
    session_factory: Callable[[], Session],
) -> None:
    with session_factory() as session:
        reseed_database(session)
        incident_id = session.scalar(select(Incident.id))

    assert incident_id is not None

    def override_get_db() -> Generator[Session, None, None]:
        with session_factory() as db:
            yield db

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)
    try:
        version_resp = client.post(
            "/agents/revenue-ops-agent/versions",
            json={
                "enabled_tool_ids": [
                    "query_revenue_metrics",
                    "search_docs",
                    "fetch_support_tickets",
                ]
            },
        )
        assert version_resp.status_code == 201
        version_id = version_resp.json()["id"]
        publish_resp = client.post(
            f"/agents/revenue-ops-agent/versions/{version_id}/publish"
        )
        assert publish_resp.status_code == 200
        response = client.post(
            "/agent/investigations",
            json={
                "incident_id": incident_id,
                "run_inline": True,
                "force": True,
                "agent_version_id": version_id,
            },
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 201
    payload = response.json()
    assert payload["status"] == "succeeded"
    report = payload["final_report"]
    disabled_ref = "tool-disabled:fetch_account_details"
    assert any(
        item["reference_id"] == disabled_ref and item["kind"] == "tool"
        for item in report["cited_evidence"]
    )
    cited_by_claims = {
        ref for claim in report["claims"] for ref in claim["citation_refs"]
    }
    assert disabled_ref in cited_by_claims


def sample_report() -> InvestigationReport:
    return InvestigationReport(
        root_cause="Billing retry webhook regression suppressed second charge attempts.",
        summary="Incident summary: retry webhook regression.",
        affected_accounts=[
            ReportAffectedAccount(
                account_id="acct_001",
                account_name="Brightline 01",
                segment="growth",
                health_score=61,
                failed_invoice_cents=254000,
                failed_invoice_ids=["inv_001_10"],
                ticket_ids=["tkt_0001"],
            )
        ],
        cited_evidence=[
            ReportEvidence(
                kind="sql",
                title="Current window evidence",
                summary="Structured metric evidence.",
                reference_id="sql-current-window",
                source_query="SELECT 1",
                citation={"rows": [{"window": "current"}]},
            )
        ],
        confidence="high",
        next_actions=["Send an approval-gated status update draft to affected admins."],
        generated_at=datetime(2026, 6, 9, 12, 35, 0),
    )


def test_list_agent_runs_returns_runs_sorted_by_created_desc(
    session_factory: Callable[[], Session],
) -> None:
    with session_factory() as session:
        reseed_database(session)
        incident_id = session.scalar(select(Incident.id))
        assert incident_id is not None

    def override_get_db() -> Generator[Session, None, None]:
        with session_factory() as db:
            yield db

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)
    try:
        first = client.post(
            "/agent/investigations",
            json={"incident_id": incident_id, "run_inline": True},
        )
        second = client.post(
            "/agent/investigations",
            json={"incident_id": incident_id, "run_inline": True, "force": True},
        )
        list_response = client.get("/agent/runs")
    finally:
        app.dependency_overrides.clear()

    assert first.status_code == 201
    assert second.status_code == 201
    assert list_response.status_code == 200
    runs = list_response.json()
    assert len(runs) >= 2
    assert all("incident_id" in run for run in runs)
    assert all("status" in run for run in runs)
    assert all("created_at" in run for run in runs)
    created_at_values = [run["created_at"] for run in runs]
    assert created_at_values == sorted(created_at_values, reverse=True)


def test_idempotency_key_separates_explicit_and_default_version_selectors(
    session_factory: Callable[[], Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with session_factory() as session:
        reseed_database(session)
        incident_id = session.scalar(select(Incident.id))

    assert incident_id is not None

    def override_get_db() -> Generator[Session, None, None]:
        with session_factory() as db:
            yield db

    def execute_with_test_session(run_id: str) -> None:
        from app.agent.service import execute_investigation_run_with_session

        with session_factory() as db:
            execute_investigation_run_with_session(db, run_id)

    monkeypatch.setattr(
        "app.agent.router._enqueue_investigation",
        execute_with_test_session,
    )

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)
    idempotency_key = "idem-test-1"
    try:
        first = client.post(
            "/agent/investigations",
            json={
                "incident_id": incident_id,
                "idempotency_key": idempotency_key,
                "agent_version_id": DEFAULT_AGENT_VERSION_ID,
            },
        )
        second = client.post(
            "/agent/investigations",
            json={
                "incident_id": incident_id,
                "idempotency_key": idempotency_key,
                "force": True,
            },
        )
    finally:
        app.dependency_overrides.clear()

    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["id"] != second.json()["id"]
    assert first.json()["agent_version_id"] == DEFAULT_AGENT_VERSION_ID
    assert second.json()["agent_version_id"] == DEFAULT_AGENT_VERSION_ID


def test_default_version_idempotency_key_is_stable_across_new_publish(
    session_factory: Callable[[], Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with session_factory() as session:
        reseed_database(session)
        incident_id = session.scalar(select(Incident.id))

    assert incident_id is not None

    def override_get_db() -> Generator[Session, None, None]:
        with session_factory() as db:
            yield db

    def execute_with_test_session(run_id: str) -> None:
        from app.agent.service import execute_investigation_run_with_session

        with session_factory() as db:
            execute_investigation_run_with_session(db, run_id)

    monkeypatch.setattr(
        "app.agent.router._enqueue_investigation",
        execute_with_test_session,
    )

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)
    idempotency_key = "idem-default-publish-drift"
    try:
        first = client.post(
            "/agent/investigations",
            json={
                "incident_id": incident_id,
                "idempotency_key": idempotency_key,
            },
        )
        version_resp = client.post(
            "/agents/revenue-ops-agent/versions",
            json={"system_prompt": "New default after first request"},
        )
        assert version_resp.status_code == 201
        version_id = version_resp.json()["id"]
        publish_resp = client.post(
            f"/agents/revenue-ops-agent/versions/{version_id}/publish"
        )
        assert publish_resp.status_code == 200
        second = client.post(
            "/agent/investigations",
            json={
                "incident_id": incident_id,
                "idempotency_key": idempotency_key,
                "force": True,
            },
        )
    finally:
        app.dependency_overrides.clear()

    assert first.status_code == 201
    assert second.status_code == 200
    first_payload = first.json()
    second_payload = second.json()
    assert second_payload["id"] == first_payload["id"]
    assert first_payload["agent_version_id"] == DEFAULT_AGENT_VERSION_ID
    assert second_payload["agent_version_id"] == DEFAULT_AGENT_VERSION_ID


def test_idempotency_key_does_not_reuse_different_agent_version(
    session_factory: Callable[[], Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with session_factory() as session:
        reseed_database(session)
        incident_id = session.scalar(select(Incident.id))

    assert incident_id is not None

    def override_get_db() -> Generator[Session, None, None]:
        with session_factory() as db:
            yield db

    def execute_with_test_session(run_id: str) -> None:
        from app.agent.service import execute_investigation_run_with_session

        with session_factory() as db:
            execute_investigation_run_with_session(db, run_id)

    monkeypatch.setattr(
        "app.agent.router._enqueue_investigation",
        execute_with_test_session,
    )

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)
    try:
        version_resp = client.post(
            "/agents/revenue-ops-agent/versions",
            json={
                "system_prompt": "Different version",
                "enabled_tool_ids": [
                    "query_revenue_metrics",
                    "fetch_account_details",
                    "search_docs",
                    "fetch_support_tickets",
                ],
            },
        )
        assert version_resp.status_code == 201
        version_id = version_resp.json()["id"]
        publish_resp = client.post(
            f"/agents/revenue-ops-agent/versions/{version_id}/publish"
        )
        assert publish_resp.status_code == 200
        idempotency_key = "idem-cross-version"
        first = client.post(
            "/agent/investigations",
            json={
                "incident_id": incident_id,
                "idempotency_key": idempotency_key,
                "agent_version_id": DEFAULT_AGENT_VERSION_ID,
            },
        )
        second = client.post(
            "/agent/investigations",
            json={
                "incident_id": incident_id,
                "idempotency_key": idempotency_key,
                "agent_version_id": version_id,
            },
        )
        third = client.post(
            "/agent/investigations",
            json={
                "incident_id": incident_id,
                "idempotency_key": idempotency_key,
                "agent_version_id": DEFAULT_AGENT_VERSION_ID,
                "force": True,
            },
        )
    finally:
        app.dependency_overrides.clear()

    assert first.status_code == 201
    assert second.status_code == 201
    assert third.status_code == 200
    first_payload = first.json()
    second_payload = second.json()
    third_payload = third.json()
    assert first_payload["id"] != second_payload["id"]
    assert third_payload["id"] == first_payload["id"]
    assert first_payload["agent_version_id"] == DEFAULT_AGENT_VERSION_ID
    assert second_payload["agent_version_id"] == version_id


def test_inline_force_respects_idempotency_key(
    session_factory: Callable[[], Session],
) -> None:
    with session_factory() as session:
        reseed_database(session)
        incident_id = session.scalar(select(Incident.id))

    assert incident_id is not None

    def override_get_db() -> Generator[Session, None, None]:
        with session_factory() as db:
            yield db

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)
    idempotency_key = "idem-inline-force"
    try:
        first = client.post(
            "/agent/investigations",
            json={
                "incident_id": incident_id,
                "run_inline": True,
                "force": True,
                "idempotency_key": idempotency_key,
            },
        )
        second = client.post(
            "/agent/investigations",
            json={
                "incident_id": incident_id,
                "run_inline": True,
                "force": True,
                "idempotency_key": idempotency_key,
            },
        )
    finally:
        app.dependency_overrides.clear()

    assert first.status_code == 201
    assert second.status_code == 200
    assert first.json()["id"] == second.json()["id"]


def _make_test_run(incident_id: str) -> AgentRun:
    return AgentRun(
        id=f"run_{uuid4().hex[:16]}",
        incident_id=incident_id,
        agent_id=DEFAULT_AGENT_ID,
        agent_version_id=DEFAULT_AGENT_VERSION_ID,
        status="running",
        trace_id=None,
        trace_url=None,
        trace_provider=None,
        trace_metadata={},
        input_payload={"incident_id": incident_id},
        final_report=None,
        token_estimate=0,
        prompt_tokens=0,
        completion_tokens=0,
        cost_estimate_usd=0.0,
        error=None,
        started_at=utcnow_naive(),
        completed_at=None,
        created_at=utcnow_naive(),
        updated_at=utcnow_naive(),
    )


def test_recorder_does_not_commit_per_step(
    session_factory: Callable[[], Session],
) -> None:
    """AgentRunRecorder must batch commits instead of committing after each step."""
    commit_count = {"n": 0}

    def _count_commit(session: Session) -> None:
        # Filter out SAVEPOINT releases — only count outer-transaction commits.
        if not session.in_nested_transaction():
            commit_count["n"] += 1

    event.listen(Session, "before_commit", _count_commit)
    try:
        with session_factory() as session:
            reseed_database(session)
            incident = session.scalar(select(Incident))
            assert incident is not None
            run = _make_test_run(incident.id)
            session.add(run)
            session.commit()

            baseline = commit_count["n"]
            recorder = AgentRunRecorder(session, run)
            for i in range(7):
                recorder.record(
                    stage=f"stage_{i}",
                    inputs={"i": i},
                    action=lambda i=i: {"result": i},
                )
            recorder_commits = commit_count["n"] - baseline
            assert recorder_commits < 7, (
                f"Expected batched commits (<7 for 7 steps), got {recorder_commits}"
            )
    finally:
        event.remove(Session, "before_commit", _count_commit)


def test_recorder_failure_does_not_rollback_previous_steps(
    session_factory: Callable[[], Session],
) -> None:
    """A failed step must not roll back previously successful steps."""

    def _boom() -> dict[str, str]:
        raise ValueError("boom")

    with session_factory() as session:
        reseed_database(session)
        incident = session.scalar(select(Incident))
        assert incident is not None
        run = _make_test_run(incident.id)
        session.add(run)
        session.commit()
        run_id = run.id

        recorder = AgentRunRecorder(session, run)
        for i in range(3):
            recorder.record(
                stage=f"success_{i}",
                inputs={"i": i},
                action=lambda i=i: {"result": i},
            )

        with pytest.raises(ValueError, match="boom"):
            recorder.record(
                stage="fail_step",
                inputs={},
                action=_boom,
            )

        session.commit()

    with session_factory() as session:
        steps = session.scalars(
            select(AgentRunStep)
            .where(AgentRunStep.run_id == run_id)
            .order_by(AgentRunStep.sequence)
        ).all()
        assert len(steps) == 4
        assert [s.status for s in steps] == [
            "succeeded",
            "succeeded",
            "succeeded",
            "failed",
        ]
        assert "boom" in (steps[3].error or "")


def test_recorder_commits_periodically_for_mid_run_visibility(
    session_factory: Callable[[], Session],
) -> None:
    """After 5+ steps, some steps must be committed and visible to a new session."""
    with session_factory() as session:
        reseed_database(session)
        incident = session.scalar(select(Incident))
        assert incident is not None
        run = _make_test_run(incident.id)
        session.add(run)
        session.commit()
        run_id = run.id

        recorder = AgentRunRecorder(session, run)
        for i in range(6):
            recorder.record(
                stage=f"stage_{i}",
                inputs={"i": i},
                action=lambda i=i: {"result": i},
            )
        # Do NOT call session.commit() — rely on the recorder's periodic commit.
        # The 6th step is flushed but uncommitted; it will be rolled back when
        # the session closes. The first 5 steps must already be committed.

    with session_factory() as session:
        visible_steps = session.scalars(
            select(AgentRunStep).where(AgentRunStep.run_id == run_id)
        ).all()
        assert len(visible_steps) >= 5, (
            f"Expected >=5 committed steps visible mid-run, got {len(visible_steps)}"
        )
