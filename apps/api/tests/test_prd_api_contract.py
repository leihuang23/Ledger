from __future__ import annotations

from collections.abc import Callable, Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

import app.models  # noqa: F401
from app.core.config import get_settings
from app.db.base import Base
from app.db.session import get_db
from app.main import app
from app.seed import reseed_database


@pytest.fixture()
def session_factory(tmp_path) -> Generator[Callable[[], Session], None, None]:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'prd_contract_test.db'}",
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
) -> Generator[TestClient, None, None]:
    with session_factory() as session:
        reseed_database(session)

    def override_get_db() -> Generator[Session, None, None]:
        with session_factory() as db:
            yield db

    app.dependency_overrides[get_db] = override_get_db
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def test_prd_core_api_routes_are_exposed() -> None:
    implemented_routes = {
        (method.upper(), path)
        for path, path_item in app.openapi()["paths"].items()
        for method in path_item
    }

    expected_routes = {
        ("GET", "/health"),
        ("GET", "/metrics/revenue"),
        ("GET", "/metrics/anomalies"),
        ("GET", "/accounts/{account_id}"),
        ("GET", "/support/tickets"),
        ("POST", "/documents/ingest"),
        ("POST", "/incidents"),
        ("POST", "/agent/investigations"),
        ("GET", "/agent/runs/{run_id}"),
        ("POST", "/approvals/{approval_id}/approve"),
        ("POST", "/approvals/{approval_id}/reject"),
        ("POST", "/evals/run"),
    }

    assert expected_routes.issubset(implemented_routes)


def test_revenue_metrics_route_matches_existing_mrr_contract(client: TestClient) -> None:
    revenue_response = client.get("/metrics/revenue")
    mrr_response = client.get("/metrics/mrr")

    assert revenue_response.status_code == 200
    assert mrr_response.status_code == 200
    assert revenue_response.json() == mrr_response.json()


def test_account_detail_route_returns_operational_context(client: TestClient) -> None:
    response = client.get("/accounts/acct_001")

    assert response.status_code == 200
    payload = response.json()
    assert payload["id"] == "acct_001"
    assert payload["name"]
    assert payload["source_scenario"] == "checkout_retry_regression"
    assert payload["subscription"]["id"] == "sub_001"
    assert payload["subscription"]["status"] == "active"
    assert len(payload["users"]) == 5
    assert payload["invoice_summary"]["total_invoices"] == 10
    assert payload["invoice_summary"]["failed_invoices"] >= 1
    assert payload["recent_invoices"][0]["invoice_date"] >= payload["recent_invoices"][-1][
        "invoice_date"
    ]
    assert any(ticket["category"] == "billing" for ticket in payload["recent_tickets"])
    assert any(event["event_name"] for event in payload["product_event_summary"])


def test_support_tickets_route_filters_seeded_ticket_context(client: TestClient) -> None:
    response = client.get(
        "/support/tickets",
        params={
            "account_id": "acct_001",
            "status": "open",
            "category": "billing",
            "source_scenario": "checkout_retry_regression",
            "limit": 3,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 3
    assert len(payload["tickets"]) == 3
    assert all(ticket["account_id"] == "acct_001" for ticket in payload["tickets"])
    assert all(ticket["status"] == "open" for ticket in payload["tickets"])
    assert all(ticket["category"] == "billing" for ticket in payload["tickets"])
    assert all(
        ticket["source_scenario"] == "checkout_retry_regression"
        for ticket in payload["tickets"]
    )
    assert payload["tickets"][0]["created_at"] >= payload["tickets"][-1]["created_at"]


def test_account_detail_route_returns_404_for_unknown_account(client: TestClient) -> None:
    response = client.get("/accounts/acct_missing")

    assert response.status_code == 404


def test_new_prd_demo_routes_fail_closed_outside_demo_environments(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    get_settings.cache_clear()
    client = TestClient(app)
    try:
        responses = [
            client.get("/metrics/revenue"),
            client.get("/accounts/acct_001"),
            client.get("/support/tickets"),
        ]
    finally:
        get_settings.cache_clear()

    assert [response.status_code for response in responses] == [403, 403, 403]


def test_public_demo_mutation_routes_require_operator_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_ENV", "demo")
    get_settings.cache_clear()
    client = TestClient(app)
    try:
        responses = [
            client.post("/incidents", json={"anomaly_id": "rev_anomaly_week_20260603"}),
            client.post(
                "/agent/investigations",
                json={"incident_id": "inc_rev_mrr_wow_drop_20260603"},
            ),
            client.post(
                "/mock-actions",
                json={
                    "run_id": "run_demo",
                    "action_type": "draft_slack_message",
                    "title": "Draft",
                    "description": "Draft an update.",
                    "target": "#ops",
                    "payload": {"message": "hello"},
                },
            ),
            client.post("/approvals/apr_demo/approve", json={}),
            client.post("/approvals/apr_demo/reject", json={}),
        ]
    finally:
        monkeypatch.delenv("APP_ENV", raising=False)
        get_settings.cache_clear()

    assert [response.status_code for response in responses] == [403, 403, 403, 403, 403]
    assert all("DEMO_OPERATOR_TOKEN" in response.json()["detail"] for response in responses)


def test_public_demo_mutation_route_validates_configured_operator_token(
    session_factory: Callable[[], Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_ENV", "demo")
    monkeypatch.setenv("DEMO_OPERATOR_TOKEN", "operator-secret")
    get_settings.cache_clear()
    with session_factory() as session:
        reseed_database(session)

    def override_get_db() -> Generator[Session, None, None]:
        with session_factory() as db:
            yield db

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)
    try:
        missing_response = client.post(
            "/incidents",
            json={"anomaly_id": "rev_mrr_wow_drop_20260603"},
        )
        wrong_response = client.post(
            "/incidents",
            headers={"X-Demo-Operator-Token": "wrong-secret"},
            json={"anomaly_id": "rev_mrr_wow_drop_20260603"},
        )
        allowed_response = client.post(
            "/incidents",
            headers={"X-Demo-Operator-Token": "operator-secret"},
            json={"anomaly_id": "rev_mrr_wow_drop_20260603"},
        )
    finally:
        app.dependency_overrides.clear()
        monkeypatch.delenv("APP_ENV", raising=False)
        monkeypatch.delenv("DEMO_OPERATOR_TOKEN", raising=False)
        get_settings.cache_clear()

    assert missing_response.status_code == 403
    assert wrong_response.status_code == 403
    assert allowed_response.status_code in {200, 201}
    assert missing_response.json()["detail"] == "Invalid demo operator token."
    assert wrong_response.json()["detail"] == "Invalid demo operator token."
