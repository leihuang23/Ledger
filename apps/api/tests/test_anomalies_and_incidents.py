from __future__ import annotations

from collections.abc import Callable, Generator
from concurrent.futures import ThreadPoolExecutor
from datetime import date

import pytest
from fastapi.testclient import TestClient

from sqlalchemy import create_engine, delete, select
from sqlalchemy.orm import Session, sessionmaker

import app.models  # noqa: F401
from app.db.base import Base
from app.db.session import get_db
from app.incidents.constants import incident_id_for_anomaly
from app.core.config import get_settings
from app.incidents.service import detect_revenue_anomalies, get_incident_detail
from app.main import app
from app.models import Incident, Invoice, ProductEvent, Subscription, SupportTicket
from app.seed import reseed_database


@pytest.fixture()
def session_factory(tmp_path) -> Generator[Callable[[], Session], None, None]:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'ops_agent_test.db'}",
        connect_args={"check_same_thread": False},
    )
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    Base.metadata.create_all(engine)

    yield TestingSessionLocal

    Base.metadata.drop_all(engine)
    engine.dispose()


def test_revenue_anomaly_detects_seeded_week_over_week_mrr_drop(
    session_factory: Callable[[], Session],
) -> None:
    with session_factory() as session:
        reseed_database(session)

        anomalies = detect_revenue_anomalies(session)

        assert len(anomalies) == 1
        anomaly = anomalies[0]
        assert anomaly.metric_evidence.previous_value_cents > 0
        assert anomaly.metric_evidence.current_value_cents < (
            anomaly.metric_evidence.previous_value_cents
        )
        assert anomaly.metric_evidence.delta_cents < 0
        assert anomaly.metric_evidence.failed_invoice_count == 6
        assert {account.source_scenario for account in anomaly.affected_accounts} == {
            "checkout_retry_regression"
        }
        assert anomaly.support_signals
        assert anomaly.product_signals
        assert anomaly.incident_id == incident_id_for_anomaly(anomaly.id)


def test_anomalies_endpoint_returns_reviewable_incident_starting_point(
    session_factory: Callable[[], Session],
) -> None:
    with session_factory() as session:
        reseed_database(session)

    def override_get_db() -> Generator[Session, None, None]:
        with session_factory() as db:
            yield db

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)
    try:
        response = client.get("/metrics/anomalies")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    payload = response.json()
    assert len(payload) == 1
    anomaly = payload[0]
    assert anomaly["incident_id"]
    assert anomaly["metric_evidence"]["delta_cents"] < 0
    assert anomaly["affected_accounts"]
    assert anomaly["support_signals"]


def test_incident_creation_from_anomaly_handles_concurrent_requests(
    session_factory: Callable[[], Session],
) -> None:
    with session_factory() as session:
        reseed_database(session)
        anomaly_id = detect_revenue_anomalies(session)[0].id
        session.execute(delete(Incident))
        session.commit()

    def override_get_db() -> Generator[Session, None, None]:
        with session_factory() as db:
            yield db

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            responses = list(
                executor.map(
                    lambda _: client.post("/incidents", json={"anomaly_id": anomaly_id}),
                    range(2),
                )
            )
    finally:
        app.dependency_overrides.clear()

    status_codes = sorted(response.status_code for response in responses)
    assert status_codes == [200, 201]
    incident_ids = {response.json()["id"] for response in responses}
    assert len(incident_ids) == 1


def test_incident_creation_from_anomaly_is_idempotent(
    session_factory: Callable[[], Session],
) -> None:
    with session_factory() as session:
        reseed_database(session)
        anomaly_id = detect_revenue_anomalies(session)[0].id
        session.execute(delete(Incident))
        session.commit()

    def override_get_db() -> Generator[Session, None, None]:
        with session_factory() as db:
            yield db

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)
    try:
        first_response = client.post("/incidents", json={"anomaly_id": anomaly_id})
        second_response = client.post("/incidents", json={"anomaly_id": anomaly_id})
    finally:
        app.dependency_overrides.clear()

    assert first_response.status_code == 201
    assert second_response.status_code == 200
    first_payload = first_response.json()
    second_payload = second_response.json()
    assert first_payload["id"] == second_payload["id"]
    assert first_payload["metric_evidence"]["delta_cents"] < 0
    assert first_payload["affected_accounts"]


def test_incident_detail_endpoint_shows_accounts_and_metric_evidence(
    session_factory: Callable[[], Session],
) -> None:
    with session_factory() as session:
        reseed_database(session)
        incident_id = session.scalar(select(Incident.id))

    def override_get_db() -> Generator[Session, None, None]:
        with session_factory() as db:
            yield db

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)
    try:
        response = client.get(f"/incidents/{incident_id}")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "open"
    assert payload["metric_evidence"]["failed_invoice_count"] == 6
    assert len(payload["affected_accounts"]) == 6
    assert payload["support_signals"]
    assert payload["evidence"]["source_queries"]


def test_incidents_endpoint_lists_seeded_incidents(
    session_factory: Callable[[], Session],
) -> None:
    with session_factory() as session:
        reseed_database(session)

    def override_get_db() -> Generator[Session, None, None]:
        with session_factory() as db:
            yield db

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)
    try:
        response = client.get("/incidents")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    payload = response.json()
    assert len(payload) == 1
    assert payload[0].keys() == {
        "id",
        "title",
        "status",
        "severity",
        "anomaly_type",
        "detected_at",
        "summary",
        "affected_account_count",
    }
    assert payload[0]["affected_account_count"] == 6


def test_incident_endpoints_return_not_found_for_unknown_ids(
    session_factory: Callable[[], Session],
) -> None:
    with session_factory() as session:
        reseed_database(session)

    def override_get_db() -> Generator[Session, None, None]:
        with session_factory() as db:
            yield db

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)
    try:
        incident_response = client.get("/incidents/inc_unknown")
        create_response = client.post("/incidents", json={"anomaly_id": "unknown"})
    finally:
        app.dependency_overrides.clear()

    assert incident_response.status_code == 404
    assert create_response.status_code == 404


def test_incident_routes_fail_closed_outside_demo_environments(monkeypatch) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    get_settings.cache_clear()
    client = TestClient(app)
    try:
        responses = [
            client.get("/incidents"),
            client.get("/incidents/inc_rev_mrr_wow_drop_20260603"),
            client.post("/incidents", json={"anomaly_id": "rev_mrr_wow_drop_20260603"}),
        ]
    finally:
        get_settings.cache_clear()

    assert [response.status_code for response in responses] == [403, 403, 403]


def test_failed_invoice_ids_match_active_subscription_evidence(
    session_factory: Callable[[], Session],
) -> None:
    with session_factory() as session:
        reseed_database(session)
        session.add(
            Subscription(
                id="sub_canceled_extra",
                account_id="acct_001",
                plan="legacy",
                status="canceled",
                mrr_cents=999_00,
                seats=1,
                started_at=date(2025, 1, 1),
                canceled_at=date(2026, 6, 1),
                cancellation_reason="Legacy canceled subscription",
                source_scenario="checkout_retry_regression",
            )
        )
        session.add(
            Invoice(
                id="inv_canceled_extra",
                account_id="acct_001",
                subscription_id="sub_canceled_extra",
                invoice_date=date(2026, 6, 6),
                due_date=date(2026, 6, 21),
                period_start=date(2026, 6, 1),
                period_end=date(2026, 6, 30),
                amount_cents=999_00,
                status="failed",
                failure_reason="Canceled subscription should not affect incident",
                paid_at=None,
                source_scenario="checkout_retry_regression",
            )
        )
        session.commit()

        anomaly = detect_revenue_anomalies(session)[0]

        assert anomaly.metric_evidence.failed_invoice_count == 6
        assert "inv_canceled_extra" not in anomaly.metric_evidence.invoice_ids
        affected_account = next(
            account
            for account in anomaly.affected_accounts
            if account.account_id == "acct_001"
        )
        assert "inv_canceled_extra" not in affected_account.failed_invoice_ids


def test_incident_detail_uses_persisted_signal_evidence_snapshot(
    session_factory: Callable[[], Session],
) -> None:
    with session_factory() as session:
        reseed_database(session)
        incident_id = session.scalar(select(Incident.id))
        incident = session.get(Incident, incident_id)
        assert incident is not None
        saved_support = incident.evidence["support_signals"][0]
        saved_product = incident.evidence["product_signals"][0]

        ticket = session.get(SupportTicket, saved_support["ticket_id"])
        assert ticket is not None
        ticket.subject = "Changed after incident creation"

        product_event = session.scalars(
            select(ProductEvent)
            .where(
                ProductEvent.account_id.in_(incident.affected_account_ids),
                ProductEvent.event_name == saved_product["event_name"],
            )
            .limit(1)
        ).one()
        product_event.event_name = "changed_after_incident_creation"
        session.commit()

        detail = get_incident_detail(session, incident_id)

        assert detail is not None
        assert detail.support_signals[0].subject == saved_support["subject"]
        assert detail.product_signals[0].event_name == saved_product["event_name"]
