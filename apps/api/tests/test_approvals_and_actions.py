from __future__ import annotations

from collections.abc import Callable, Generator
from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.orm.attributes import set_committed_value

import app.models  # noqa: F401
from app.agent.schemas import (
    InvestigationReport,
    ReportAffectedAccount,
    ReportEvidence,
)
from app.agents.service import DEFAULT_AGENT_ID, DEFAULT_AGENT_VERSION_ID
from app.approvals.schemas import ApprovalDecisionCreate, MockActionCreate
from app.approvals.service import (
    approve_request,
    create_low_risk_mock_action,
    propose_actions_for_report,
    reject_request,
    request_high_risk_approval,
)
from app.db.base import Base
from app.db.session import get_db
from app.main import app
from app.models import ActionAuditEvent, AgentRun, ApprovalRequest, Incident, MockAction
from app.seed import reseed_database


@pytest.fixture()
def session_factory(tmp_path) -> Generator[Callable[[], Session], None, None]:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'approvals_and_actions_test.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    yield TestingSessionLocal

    Base.metadata.drop_all(engine)
    engine.dispose()


@pytest.fixture()
def client(
    session_factory: Callable[[], Session],
) -> Generator[tuple[TestClient, dict[str, str]], None, None]:
    """Seed runs in every lifecycle state the operator action API must police.

    Returns ``(client, run_ids)`` where ``run_ids`` maps a lifecycle key
    (``queued``/``running``/``waiting``/``succeeded``/``failed``) to a run id.
    Only the succeeded run is bound to an incident; the rest use a null
    incident_id so the active-run partial unique index never collides."""
    with session_factory() as session:
        reseed_database(session)
        incident_id = session.scalar(select(Incident.id))
        assert incident_id is not None
        now = datetime(2026, 6, 9, 12, 30, 0)

        def make_run(
            run_id: str,
            status: str,
            *,
            bound_incident_id: str | None,
        ) -> str:
            terminal = status in ("succeeded", "failed")
            run = AgentRun(
                id=run_id,
                incident_id=bound_incident_id,
                agent_id=DEFAULT_AGENT_ID,
                agent_version_id=DEFAULT_AGENT_VERSION_ID,
                status=status,
                trace_id=f"local-trace-{run_id}",
                input_payload=(
                    {"incident_id": bound_incident_id}
                    if bound_incident_id is not None
                    else {}
                ),
                final_report=None,
                token_estimate=1,
                cost_estimate_usd=0.0,
                error=None,
                started_at=None if status == "queued" else now,
                completed_at=now if terminal else None,
                created_at=now,
                updated_at=now,
            )
            session.add(run)
            return run.id

        run_ids = {
            "succeeded": make_run(
                "run_action_terminal_ok", "succeeded", bound_incident_id=incident_id
            ),
            "failed": make_run(
                "run_action_terminal_failed", "failed", bound_incident_id=None
            ),
            "waiting": make_run(
                "run_action_checkpoint", "waiting_for_approval", bound_incident_id=None
            ),
            "running": make_run(
                "run_action_in_flight", "running", bound_incident_id=None
            ),
            "queued": make_run("run_action_queued", "queued", bound_incident_id=None),
        }
        session.commit()

    def override_get_db() -> Generator[Session, None, None]:
        with session_factory() as db:
            yield db

    app.dependency_overrides[get_db] = override_get_db
    try:
        yield TestClient(app), run_ids
    finally:
        app.dependency_overrides.clear()


LOW_RISK_ACTION_BODY = {
    "action_type": "draft_slack_message",
    "title": "Draft internal billing update",
    "description": "Post a mock Slack draft for the billing incident channel.",
    "target": "#billing-ops",
    "payload": {"message": "Retry recovery is ready for approval."},
}
HIGH_RISK_ACTION_BODY = {
    "action_type": "draft_customer_email",
    "title": "Draft renewal recovery email",
    "description": "Prepare customer-facing follow-up for failed renewals.",
    "target": "affected billing contacts",
    "payload": {"subject": "Renewal retry update", "body": "We found the issue."},
}


def test_low_risk_draft_action_executes_and_is_audited(
    client: tuple[TestClient, dict[str, str]],
) -> None:
    test_client, run_ids = client
    run_id = run_ids["succeeded"]

    response = test_client.post(
        "/mock-actions",
        json={
            "run_id": run_id,
            "action_type": "draft_slack_message",
            "title": "Draft internal billing update",
            "description": "Post a mock Slack draft for the billing incident channel.",
            "target": "#billing-ops",
            "payload": {"message": "Retry recovery is ready for approval."},
            "created_by": "forged-actor",
        },
    )

    assert response.status_code == 201
    payload = response.json()
    assert payload["risk_level"] == "low"
    assert payload["status"] == "executed"
    # Operator-API actions are attributed to the operator; a client-supplied
    # created_by is never trusted.
    assert payload["created_by"] == "operator"
    assert payload["executed_at"] is not None
    assert payload["approval_request"] is None
    assert [event["event_type"] for event in payload["audit_events"]] == [
        "proposed",
        "executed",
    ]

    run_response = test_client.get(f"/agent/runs/{run_id}")
    assert run_response.status_code == 200
    run_payload = run_response.json()
    assert [action["id"] for action in run_payload["mock_actions"]] == [payload["id"]]


def test_high_risk_action_is_blocked_until_approved(
    client: tuple[TestClient, dict[str, str]],
) -> None:
    test_client, run_ids = client
    run_id = run_ids["waiting"]

    response = test_client.post(
        "/mock-actions",
        json={
            "run_id": run_id,
            "action_type": "draft_customer_email",
            "title": "Draft renewal recovery email",
            "description": "Prepare customer-facing follow-up for failed renewals.",
            "target": "affected billing contacts",
            "payload": {"subject": "Renewal retry update", "body": "We found the issue."},
        },
    )

    assert response.status_code == 201
    payload = response.json()
    assert payload["risk_level"] == "high"
    assert payload["status"] == "pending_approval"
    assert payload["executed_at"] is None
    assert payload["approval_request"]["status"] == "pending"
    assert [event["event_type"] for event in payload["audit_events"]] == ["proposed"]

    queue_response = test_client.get("/approvals?status=pending")
    assert queue_response.status_code == 200
    queue_payload = queue_response.json()
    assert [item["id"] for item in queue_payload] == [payload["approval_request"]["id"]]
    assert queue_payload[0]["agent_version_id"] == DEFAULT_AGENT_VERSION_ID

    version_filtered = test_client.get(
        f"/approvals?status=pending&agent_version_id={DEFAULT_AGENT_VERSION_ID}"
        "&risk_level=high"
    )
    assert version_filtered.status_code == 200
    assert [item["id"] for item in version_filtered.json()] == [
        payload["approval_request"]["id"]
    ]

    assert test_client.get(
        "/approvals?status=pending&agent_version_id=missing-version"
    ).json() == []
    assert test_client.get("/approvals?status=pending&risk_level=low").json() == []


def test_approval_queue_defaults_to_pending_fr12(
    client: tuple[TestClient, str],
) -> None:
    """FR-12: ``GET /approvals`` lists PENDING approvals by default (the queue),
    not the full history. ``include_decided=true`` opts into approved/rejected
    history; an explicit ``status`` filter always takes precedence.

    Creates two high-risk actions, approves one, then asserts the default list
    excludes the decided one while ``include_decided`` and ``status=approved``
    surface it."""
    test_client, run_ids = client
    run_id = run_ids["waiting"]

    def _create_high_risk() -> str:
        response = test_client.post(
            "/mock-actions",
            json={
                "run_id": run_id,
                "action_type": "draft_customer_email",
                "title": "Draft follow-up",
                "description": "Prepare customer-facing follow-up.",
                "target": "affected billing contacts",
                "payload": {"subject": "Update", "body": "We found the issue."},
            },
        )
        assert response.status_code == 201
        return response.json()["approval_request"]["id"]

    approved_id = _create_high_risk()
    pending_id = _create_high_risk()

    # Decide the first one so there is a mix of pending + approved.
    decision = test_client.post(
        f"/approvals/{approved_id}/approve",
        json={"notes": "Approved."},
    )
    assert decision.status_code == 200

    # Default (no params) -> pending only (FR-12).
    default = test_client.get("/approvals")
    assert default.status_code == 200
    default_ids = {item["id"] for item in default.json()}
    assert default_ids == {pending_id}, default_ids

    # include_decided=true -> all statuses (the approved one surfaces).
    all_statuses = test_client.get("/approvals?include_decided=true")
    assert all_statuses.status_code == 200
    all_ids = {item["id"] for item in all_statuses.json()}
    assert all_ids == {approved_id, pending_id}, all_ids

    # Explicit status=approved -> only the approved one (takes precedence over
    # both the pending default and include_decided).
    approved_only = test_client.get("/approvals?status=approved")
    assert approved_only.status_code == 200
    assert {item["id"] for item in approved_only.json()} == {approved_id}


def test_approve_after_reject_returns_409_at_http_level(
    client: tuple[TestClient, str],
) -> None:
    """FR-13 / AC-4.2: deciding an already-decided approval is rejected with 409
    at the HTTP level (not just the service layer). The service-layer race test
    (``test_concurrent_approve_after_reject_is_blocked``) covers the ValueError;
    this pins the router's ``ValueError -> 409`` mapping so a regression that
    swallows the conflict (e.g. returning 200 or 500) is caught. Covers both
    directions: approve-after-reject and reject-after-approve.

    The run is parked at the approval checkpoint, and a keep-alive pending
    approval stays undecided so the run does not resume (terminal runs reject
    new high-risk actions) mid-test."""
    test_client, run_ids = client
    run_id = run_ids["waiting"]

    def _create_high_risk() -> str:
        response = test_client.post(
            "/mock-actions",
            json={
                "run_id": run_id,
                "action_type": "draft_customer_email",
                "title": "Draft follow-up",
                "description": "Prepare customer-facing follow-up.",
                "target": "affected billing contacts",
                "payload": {"subject": "Update", "body": "We found the issue."},
            },
        )
        assert response.status_code == 201
        return response.json()["approval_request"]["id"]

    # Keep one approval pending for the whole test so the checkpointed run
    # never resumes to a terminal state (which would reject further high-risk
    # action creation with 409 and break the scenario).
    _keep_alive_id = _create_high_risk()

    # Approve-after-reject -> 409.
    rejected_id = _create_high_risk()
    reject_response = test_client.post(
        f"/approvals/{rejected_id}/reject",
        json={"notes": "Rejected first."},
    )
    assert reject_response.status_code == 200
    late_approve = test_client.post(
        f"/approvals/{rejected_id}/approve",
        json={"notes": "Late approve."},
    )
    assert late_approve.status_code == 409
    assert "already" in late_approve.json()["detail"].lower()

    # Reject-after-approve -> 409 (symmetric).
    approved_id = _create_high_risk()
    approve_response = test_client.post(
        f"/approvals/{approved_id}/approve",
        json={"notes": "Approved first."},
    )
    assert approve_response.status_code == 200
    late_reject = test_client.post(
        f"/approvals/{approved_id}/reject",
        json={"notes": "Late reject."},
    )
    assert late_reject.status_code == 409
    assert "already" in late_reject.json()["detail"].lower()


def test_mock_action_payload_rejects_unsupported_fields(
    client: tuple[TestClient, dict[str, str]],
) -> None:
    test_client, run_ids = client
    run_id = run_ids["succeeded"]

    response = test_client.post(
        "/mock-actions",
        json={
            "run_id": run_id,
            "action_type": "draft_slack_message",
            "title": "Draft internal billing update",
            "description": "Post a mock Slack draft for the billing incident channel.",
            "target": "#billing-ops",
            "payload": {
                "message": "Retry recovery is ready for approval.",
                "send_now": True,
            },
        },
    )

    assert response.status_code == 422
    assert "unsupported fields: send_now" in response.json()["detail"]


def test_operator_cannot_inject_actions_into_in_flight_runs(
    client: tuple[TestClient, dict[str, str]],
) -> None:
    """Audit hardening: POST /mock-actions rejects any action against a queued
    or running run with 409. While a run is in flight the agent owns action
    creation; an injected high-risk action would inflate finalization's
    ``pending_approval_count`` and drag the run into ``waiting_for_approval``."""
    test_client, run_ids = client

    for key in ("queued", "running"):
        run_id = run_ids[key]
        for body in (LOW_RISK_ACTION_BODY, HIGH_RISK_ACTION_BODY):
            response = test_client.post(
                "/mock-actions", json={**body, "run_id": run_id}
            )
            assert response.status_code == 409, (key, body["action_type"])
            assert key in response.json()["detail"]

        run_detail = test_client.get(f"/agent/runs/{run_id}")
        assert run_detail.status_code == 200
        assert run_detail.json()["mock_actions"] == []

    # Nothing leaked into the approval queue either.
    assert test_client.get("/approvals?status=pending").json() == []


def test_terminal_runs_accept_only_low_risk_operator_followups(
    client: tuple[TestClient, dict[str, str]],
) -> None:
    """Product decision (pinned in ``app.approvals.service.create_mock_action``):
    terminal runs accept low-risk operator follow-ups, which execute
    immediately and can never create a pending approval, but reject high-risk
    actions with 409 - a pending approval on a finished run could never gate
    anything and would leave the run in a contradictory state."""
    test_client, run_ids = client

    for key in ("succeeded", "failed"):
        run_id = run_ids[key]
        low = test_client.post(
            "/mock-actions", json={**LOW_RISK_ACTION_BODY, "run_id": run_id}
        )
        assert low.status_code == 201, (key, low.json())
        assert low.json()["status"] == "executed"
        assert low.json()["approval_request"] is None

        high = test_client.post(
            "/mock-actions", json={**HIGH_RISK_ACTION_BODY, "run_id": run_id}
        )
        assert high.status_code == 409, (key, high.json())
        assert key in high.json()["detail"]

    # The rejected high-risk attempts created no pending approvals.
    assert test_client.get("/approvals?status=pending").json() == []


def test_checkpoint_run_accepts_operator_actions_and_resume_completes(
    client: tuple[TestClient, dict[str, str]],
) -> None:
    """A run parked at the approval checkpoint is the operator's legitimate
    window: new low-risk actions execute immediately and new high-risk actions
    join the pending queue. Deciding every pending approval resumes the run
    through the system-managed path to ``succeeded`` - proving the internal
    resume path is unaffected by the operator force-succeed ban."""
    test_client, run_ids = client
    run_id = run_ids["waiting"]

    low = test_client.post(
        "/mock-actions", json={**LOW_RISK_ACTION_BODY, "run_id": run_id}
    )
    assert low.status_code == 201
    assert low.json()["status"] == "executed"

    high = test_client.post(
        "/mock-actions", json={**HIGH_RISK_ACTION_BODY, "run_id": run_id}
    )
    assert high.status_code == 201
    approval_id = high.json()["approval_request"]["id"]

    decision = test_client.post(
        f"/approvals/{approval_id}/approve", json={"notes": "Looks cited."}
    )
    assert decision.status_code == 200

    run_detail = test_client.get(f"/agent/runs/{run_id}")
    assert run_detail.status_code == 200
    assert run_detail.json()["status"] == "succeeded"
    assert run_detail.json()["completed_at"] is not None


def test_approving_high_risk_action_executes_and_records_decision(
    client: tuple[TestClient, dict[str, str]],
) -> None:
    test_client, run_ids = client
    run_id = run_ids["waiting"]
    create_response = test_client.post(
        "/mock-actions",
        json={
            "run_id": run_id,
            "action_type": "update_account_note",
            "title": "Update CRM note",
            "description": "Record the cited retry incident on affected accounts.",
            "target": "affected accounts",
            "payload": {"note": "Billing retry regression identified."},
        },
    )
    approval_id = create_response.json()["approval_request"]["id"]

    response = test_client.post(
        f"/approvals/{approval_id}/approve",
        json={"decided_by": "forged-approver", "notes": "Evidence is cited."},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "approved"
    assert payload["decided_by"] == "demo-approver"
    assert payload["action"]["status"] == "executed"
    assert payload["action"]["executed_at"] is not None
    assert [event["event_type"] for event in payload["action"]["audit_events"]] == [
        "proposed",
        "approved",
        "executed",
    ]


def test_rejected_high_risk_action_does_not_execute(
    client: tuple[TestClient, dict[str, str]],
) -> None:
    test_client, run_ids = client
    run_id = run_ids["waiting"]
    create_response = test_client.post(
        "/mock-actions",
        json={
            "run_id": run_id,
            "action_type": "draft_customer_email",
            "title": "Draft external email",
            "description": "Prepare customer-facing incident update.",
            "target": "affected billing contacts",
            "payload": {"subject": "Incident update", "body": "We are following up."},
        },
    )
    approval_id = create_response.json()["approval_request"]["id"]

    response = test_client.post(
        f"/approvals/{approval_id}/reject",
        json={"decided_by": "forged-approver", "notes": "Tone needs support review."},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "rejected"
    assert payload["decided_by"] == "demo-approver"
    assert payload["action"]["status"] == "rejected"
    assert payload["action"]["executed_at"] is None
    assert [event["event_type"] for event in payload["action"]["audit_events"]] == [
        "proposed",
        "rejected",
    ]

    run_response = test_client.get(f"/agent/runs/{run_id}")
    assert run_response.status_code == 200
    rejected_action = run_response.json()["mock_actions"][0]
    assert rejected_action["status"] == "rejected"
    assert rejected_action["executed_at"] is None


def test_report_action_proposal_repairs_partial_action_sets(
    session_factory: Callable[[], Session],
) -> None:
    with session_factory() as session:
        reseed_database(session)
        incident_id = session.scalar(select(Incident.id))
        assert incident_id is not None
        now = datetime(2026, 6, 9, 12, 30, 0)
        run = AgentRun(
            id="run_partial_actions",
            incident_id=incident_id,
            agent_id=DEFAULT_AGENT_ID,
            agent_version_id=DEFAULT_AGENT_VERSION_ID,
            status="succeeded",
            trace_id="local-test-trace",
            input_payload={"incident_id": incident_id},
            final_report=None,
            token_estimate=1,
            cost_estimate_usd=0.0,
            error=None,
            started_at=now,
            completed_at=now,
            created_at=now,
            updated_at=now,
        )
        session.add(run)
        session.add(
            MockAction(
                id="act_existing_slack",
                run_id=run.id,
                action_type="draft_slack_message",
                risk_level="low",
                status="executed",
                title="Existing Slack draft",
                description="Existing partial action.",
                target="#revenue-ops",
                payload={"message": "Already created."},
                created_by="agent",
                created_at=now,
                updated_at=now,
                executed_at=now,
            )
        )
        session.commit()

        actions = propose_actions_for_report(
            session,
            run_id=run.id,
            report=sample_report(),
        )

    assert {action.action_type for action in actions} == {
        "draft_slack_message",
        "draft_customer_email",
        "create_task",
        "update_account_note",
    }
    assert len([action for action in actions if action.action_type == "draft_slack_message"]) == 1


def test_customer_email_payload_carries_report_evidence_references(
    session_factory: Callable[[], Session],
) -> None:
    with session_factory() as session:
        reseed_database(session)
        incident_id = session.scalar(select(Incident.id))
        assert incident_id is not None
        now = datetime(2026, 6, 9, 12, 30, 0)
        run = AgentRun(
            id="run_evidence_payload",
            incident_id=incident_id,
            agent_id=DEFAULT_AGENT_ID,
            agent_version_id=DEFAULT_AGENT_VERSION_ID,
            status="succeeded",
            trace_id="local-test-trace",
            input_payload={"incident_id": incident_id},
            final_report=None,
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

        actions = propose_actions_for_report(
            session,
            run_id=run.id,
            report=sample_report(root_cause="CSV import instability reduced recent active usage."),
        )

    customer_email = next(
        action for action in actions if action.action_type == "draft_customer_email"
    )
    assert customer_email.payload["evidence_ids"] == [
        "sql-current-window",
        "ticket-csv-import",
    ]
    assert "CSV import instability" in customer_email.payload["body"]
    assert "renewal" not in customer_email.payload["subject"].lower()


def test_agent_action_paths_bypass_operator_run_state_guard(
    session_factory: Callable[[], Session],
) -> None:
    """The operator run-state guard lives only in the operator API entry point
    (``create_mock_action``). The agent's own action creation happens while the
    run is still ``running`` and must never be blocked by it:

    - ``propose_actions_for_report`` is invoked by the executor during a
      ``running`` run (its output feeds finalization's pending-approval count);
    - the registry tool bindings ``create_low_risk_mock_action`` and
      ``request_high_risk_approval`` are agent-actor entries and must stay
      callable mid-run, so they bypass the operator guard as well.
    """
    with session_factory() as session:
        reseed_database(session)
        incident_id = session.scalar(select(Incident.id))
        assert incident_id is not None
        now = datetime(2026, 6, 9, 12, 30, 0)
        run = AgentRun(
            id="run_agent_path_running",
            incident_id=None,
            agent_id=DEFAULT_AGENT_ID,
            agent_version_id=DEFAULT_AGENT_VERSION_ID,
            status="running",
            trace_id="local-agent-path",
            input_payload={"incident_id": incident_id},
            final_report=None,
            token_estimate=1,
            cost_estimate_usd=0.0,
            error=None,
            started_at=now,
            completed_at=None,
            created_at=now,
            updated_at=now,
        )
        session.add(run)
        session.commit()

        proposed = propose_actions_for_report(
            session,
            run_id=run.id,
            report=sample_report(),
        )
        assert len(proposed) == 4
        assert any(action.status == "pending_approval" for action in proposed)

        low = create_low_risk_mock_action(
            session,
            MockActionCreate(
                run_id=run.id,
                action_type="draft_slack_message",
                title="Agent tool draft",
                description="Created via the registry tool binding mid-run.",
                target="#revenue-ops",
                payload={"message": "Mid-run tool dispatch."},
            ),
        )
        assert low.status == "executed"
        assert low.created_by == "agent"

        high = request_high_risk_approval(
            session,
            MockActionCreate(
                run_id=run.id,
                action_type="draft_customer_email",
                title="Agent tool approval request",
                description="Created via the registry approval binding mid-run.",
                target="affected billing contacts",
                payload={"subject": "Update", "body": "Mid-run approval request."},
            ),
        )
        assert high.status == "pending_approval"
        assert high.created_by == "agent"


def sample_report(
    *,
    root_cause: str = "Billing retry webhook regression suppressed second charge attempts.",
) -> InvestigationReport:
    return InvestigationReport(
        root_cause=root_cause,
        summary=f"Incident summary: {root_cause}",
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
            ),
            ReportEvidence(
                kind="ticket",
                title="CSV import failure",
                summary="Customer reported import failure.",
                reference_id="ticket-csv-import",
                citation={"ticket_id": "tkt_0001"},
            ),
        ],
        confidence="high",
        next_actions=[
            "Send an approval-gated status update draft to affected admins.",
            "Prioritize the import stability fix.",
        ],
        generated_at=datetime(2026, 6, 9, 12, 35, 0),
    )


def _seed_pending_high_risk_approval(
    session_factory: Callable[[], Session],
    *,
    run_id: str,
    action_id: str,
    approval_id: str,
) -> str:
    with session_factory() as session:
        reseed_database(session)
        now = datetime(2026, 6, 9, 12, 0, 0)
        incident_id = session.scalar(select(Incident.id))
        assert incident_id is not None
        run = AgentRun(
            id=run_id,
            incident_id=incident_id,
            agent_id=DEFAULT_AGENT_ID,
            agent_version_id=DEFAULT_AGENT_VERSION_ID,
            status="succeeded",
            trace_id="local-test-trace",
            input_payload={"incident_id": incident_id},
            final_report=None,
            token_estimate=1,
            cost_estimate_usd=0.0,
            error=None,
            started_at=now,
            completed_at=now,
            created_at=now,
            updated_at=now,
        )
        action = MockAction(
            id=action_id,
            run_id=run_id,
            action_type="draft_customer_email",
            risk_level="high",
            status="pending_approval",
            title="Draft customer email",
            description="Draft a follow-up email to affected admins.",
            target="affected admins",
            payload={"subject": "Heads up", "body": "We are investigating."},
            created_by="agent",
            created_at=now,
            updated_at=now,
            executed_at=None,
        )
        approval = ApprovalRequest(
            id=approval_id,
            run_id=run_id,
            action_id=action_id,
            status="pending",
            risk_level="high",
            reason="High-risk action requires explicit approval before execution.",
            requested_by="agent",
            decided_by=None,
            decision_notes=None,
            created_at=now,
            decided_at=None,
        )
        session.add_all([run, action, approval])
        session.commit()
        return approval.id


def _audit_event_types(
    session_factory: Callable[[], Session], approval_id: str
) -> list[str]:
    with session_factory() as session:
        events = session.scalars(
            select(ActionAuditEvent)
            .where(ActionAuditEvent.approval_request_id == approval_id)
            .order_by(ActionAuditEvent.created_at, ActionAuditEvent.id)
        ).all()
        return [event.event_type for event in events]


def test_concurrent_double_approval_does_not_duplicate_audit_events(
    session_factory: Callable[[], Session],
) -> None:
    approval_id = _seed_pending_high_risk_approval(
        session_factory,
        run_id="run_race_approve",
        action_id="act_race_approve",
        approval_id="apr_race_approve",
    )

    # Session B loads the approval as "pending" before the winner commits.
    # expire_on_commit=False keeps the cached object alive across the commit.
    stale_session = session_factory()
    stale_session.expire_on_commit = False
    stale_session.get(ApprovalRequest, approval_id)
    stale_session.commit()

    # Winner (Session A) approves and commits; the DB row is now "approved".
    with session_factory() as session_a:
        approve_request(session_a, approval_id, ApprovalDecisionCreate(notes="first"))

    # Force the stale session's cached object back to "pending" to simulate the
    # stale read a concurrent approver would have made before the winner
    # committed. SQLAlchemy's identity map does not guarantee a stale view
    # across a post-winner transaction on SQLite, so set_committed_value
    # deterministically reproduces the read-modify-write race the audit flagged.
    stale_approval = stale_session.get(ApprovalRequest, approval_id)
    set_committed_value(stale_approval, "status", "pending")

    # The stale session must not double-execute. It should raise and leave the
    # audit trail intact rather than appending a second approved/executed pair.
    try:
        with pytest.raises(ValueError, match="already"):
            approve_request(
                stale_session, approval_id, ApprovalDecisionCreate(notes="second")
            )
    finally:
        stale_session.close()

    event_types = _audit_event_types(session_factory, approval_id)
    assert event_types.count("approved") == 1
    assert event_types.count("executed") == 1
    with session_factory() as session:
        action = session.get(MockAction, "act_race_approve")
        assert action.status == "executed"
        assert action.executed_at is not None


def test_concurrent_approve_after_reject_is_blocked(
    session_factory: Callable[[], Session],
) -> None:
    approval_id = _seed_pending_high_risk_approval(
        session_factory,
        run_id="run_race_mixed",
        action_id="act_race_mixed",
        approval_id="apr_race_mixed",
    )

    stale_session = session_factory()
    stale_session.expire_on_commit = False
    stale_session.get(ApprovalRequest, approval_id)
    stale_session.commit()

    # Winner rejects; action must stay rejected and not flip to executed.
    with session_factory() as session_a:
        reject_request(session_a, approval_id, ApprovalDecisionCreate(notes="rejected first"))

    # Force the stale session's cached object back to "pending" to simulate the
    # stale read a concurrent approver would have made before the winner rejected.
    stale_approval = stale_session.get(ApprovalRequest, approval_id)
    set_committed_value(stale_approval, "status", "pending")

    try:
        with pytest.raises(ValueError, match="already"):
            approve_request(
                stale_session, approval_id, ApprovalDecisionCreate(notes="late approve")
            )
    finally:
        stale_session.close()

    event_types = _audit_event_types(session_factory, approval_id)
    assert event_types.count("rejected") == 1
    assert "approved" not in event_types
    assert "executed" not in event_types
    with session_factory() as session:
        action = session.get(MockAction, "act_race_mixed")
        assert action.status == "rejected"
        assert action.executed_at is None
