from __future__ import annotations

from collections.abc import Callable, Generator
from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

import app.models  # noqa: F401
from app.agent.schemas import (
    InvestigationReport,
    ReportAffectedAccount,
    ReportEvidence,
)
from app.approvals.service import propose_actions_for_report
from app.db.base import Base
from app.db.session import get_db
from app.main import app
from app.models import AgentRun, Incident, MockAction
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
) -> Generator[tuple[TestClient, str], None, None]:
    with session_factory() as session:
        reseed_database(session)
        incident_id = session.scalar(select(Incident.id))
        assert incident_id is not None
        now = datetime(2026, 6, 9, 12, 30, 0)
        run = AgentRun(
            id="run_action_contract",
            incident_id=incident_id,
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
        run_id = run.id

    def override_get_db() -> Generator[Session, None, None]:
        with session_factory() as db:
            yield db

    app.dependency_overrides[get_db] = override_get_db
    try:
        yield TestClient(app), run_id
    finally:
        app.dependency_overrides.clear()


def test_low_risk_draft_action_executes_and_is_audited(
    client: tuple[TestClient, str],
) -> None:
    test_client, run_id = client

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
    assert payload["created_by"] == "agent"
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
    client: tuple[TestClient, str],
) -> None:
    test_client, run_id = client

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


def test_mock_action_payload_rejects_unsupported_fields(
    client: tuple[TestClient, str],
) -> None:
    test_client, run_id = client

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


def test_approving_high_risk_action_executes_and_records_decision(
    client: tuple[TestClient, str],
) -> None:
    test_client, run_id = client
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
    client: tuple[TestClient, str],
) -> None:
    test_client, run_id = client
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
