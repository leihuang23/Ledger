from __future__ import annotations

from collections.abc import Callable, Generator
from concurrent.futures import ThreadPoolExecutor
import threading

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

import app.models  # noqa: F401
from app.db.base import Base
from app.db.session import get_db
from app.main import app
from app.core.config import get_settings
from app.metrics.router import require_demo_metrics_access
from app.metrics.service import get_dashboard_metrics
from app.models import (
    Account,
    Invoice,
    KnowledgeDocument,
    KnowledgeDocumentChunk,
    ProductEvent,
    Subscription,
    SupportTicket,
    User,
)
from app.seed import (
    SCENARIOS,
    SCENARIO_ACCOUNT_NUMBERS,
    dataset_fingerprint,
    ensure_seeded_if_empty,
    reseed_database,
    validate_seed_target,
)


@pytest.fixture()
def session_factory(tmp_path) -> Generator[Callable[[], Session], None, None]:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'ops_agent_test.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    yield TestingSessionLocal

    Base.metadata.drop_all(engine)
    engine.dispose()


def test_ensure_seeded_if_empty_skips_existing_data(
    session_factory: Callable[[], Session],
) -> None:
    with session_factory() as session:
        first_result = reseed_database(session)
        skipped_result = ensure_seeded_if_empty(session)

        assert skipped_result is None
        assert dataset_fingerprint(session) == first_result.fingerprint


def test_ensure_seeded_if_empty_seeds_blank_database(
    session_factory: Callable[[], Session],
) -> None:
    with session_factory() as session:
        result = ensure_seeded_if_empty(session)

        assert result is not None
        assert result.counts["accounts"] == 60
        assert result.counts["product_events"] == 6000


def test_ensure_seeded_if_empty_refuses_production_environment(
    session_factory: Callable[[], Session],
    monkeypatch,
) -> None:
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+psycopg://ops_agent:ops_agent@postgres:5432/ops_agent",
    )
    monkeypatch.setenv("APP_ENV", "production")
    get_settings.cache_clear()
    try:
        with session_factory() as session:
            with pytest.raises(SystemExit, match="Refusing to reseed"):
                ensure_seeded_if_empty(session)
    finally:
        monkeypatch.delenv("DATABASE_URL", raising=False)
        monkeypatch.delenv("APP_ENV", raising=False)
        get_settings.cache_clear()


def test_ensure_seeded_if_empty_refuses_unsafe_remote_database(
    session_factory: Callable[[], Session],
    monkeypatch,
) -> None:
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+psycopg://ops_agent:ops_agent@prod.example.com:5432/ops_agent",
    )
    get_settings.cache_clear()
    try:
        with session_factory() as session:
            with pytest.raises(SystemExit, match="Refusing to reseed"):
                ensure_seeded_if_empty(session)
    finally:
        monkeypatch.delenv("DATABASE_URL", raising=False)
        get_settings.cache_clear()


def test_ensure_seeded_if_empty_allows_remote_database_when_explicitly_overridden(
    session_factory: Callable[[], Session],
    monkeypatch,
) -> None:
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+psycopg://ops_agent:ops_agent@prod.example.com:5432/ops_agent",
    )
    monkeypatch.setenv("ALLOW_UNSAFE_BOOTSTRAP_SEED", "true")
    get_settings.cache_clear()
    try:
        with session_factory() as session:
            result = ensure_seeded_if_empty(session)
            assert result is not None
    finally:
        monkeypatch.delenv("DATABASE_URL", raising=False)
        monkeypatch.delenv("ALLOW_UNSAFE_BOOTSTRAP_SEED", raising=False)
        get_settings.cache_clear()


def test_ensure_seeded_if_empty_handles_concurrent_bootstrap(
    session_factory: Callable[[], Session],
) -> None:
    start_barrier = threading.Barrier(2)

    def bootstrap() -> object:
        with session_factory() as session:
            start_barrier.wait(timeout=5)
            return ensure_seeded_if_empty(session)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: bootstrap(), range(2)))

    assert sum(result is not None for result in results) == 1
    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(Account)) == 60


def test_seed_command_data_is_deterministic(
    session_factory: Callable[[], Session],
) -> None:
    with session_factory() as session:
        first_result = reseed_database(session)
        second_result = reseed_database(session)

        expected_domain_counts = {
            "accounts": 60,
            "users": 300,
            "subscriptions": 60,
            "invoices": 600,
            "product_events": 6000,
            "support_tickets": 240,
            "incidents": 1,
        }
        for table_name, expected_count in expected_domain_counts.items():
            assert first_result.counts[table_name] == expected_count
        assert first_result.counts["knowledge_documents"] >= 20
        assert first_result.counts["knowledge_document_chunks"] >= first_result.counts[
            "knowledge_documents"
        ]
        assert second_result.counts == first_result.counts
        assert second_result.fingerprint == first_result.fingerprint
        assert dataset_fingerprint(session) == first_result.fingerprint
        assert (
            session.scalar(select(Invoice.status).where(Invoice.id == "inv_001_10"))
            == "failed"
        )
        assert (
            session.scalar(
                select(KnowledgeDocument.title).where(
                    KnowledgeDocument.id == "kb-runbook-billing-retry-regression"
                )
            )
            == "Billing Retry Regression Runbook"
        )


def test_reseed_rolls_back_existing_data_when_insert_fails(
    session_factory: Callable[[], Session],
    monkeypatch,
) -> None:
    with session_factory() as session:
        first_result = reseed_database(session)

        def fail_before_insert(_objects) -> None:
            raise SQLAlchemyError("simulated insert failure")

        monkeypatch.setattr(session, "add_all", fail_before_insert)

        with pytest.raises(SQLAlchemyError, match="simulated insert failure"):
            reseed_database(session)

        assert dataset_fingerprint(session) == first_result.fingerprint


def test_core_metric_queries_match_seeded_incidents(
    session_factory: Callable[[], Session],
) -> None:
    with session_factory() as session:
        reseed_database(session)

        dashboard = get_dashboard_metrics(session)

        assert dashboard.mrr.current_mrr_cents > 0
        assert dashboard.mrr.previous_mrr_cents > dashboard.mrr.current_mrr_cents
        assert dashboard.mrr.delta_cents < 0
        assert dashboard.churn.churned_accounts_30d == 5
        assert dashboard.churn.active_accounts == 55
        assert dashboard.failed_invoices.failed_count_30d >= 16
        assert dashboard.failed_invoices.failed_amount_cents_30d > 0
        assert dashboard.ticket_volume.open_tickets > 0
        assert dashboard.ticket_volume.high_priority_open_tickets > 0
        assert dashboard.active_users.active_users_7d > 0
        assert dashboard.active_users.event_count_30d > dashboard.active_users.event_count_7d
        assert {item.category for item in dashboard.ticket_volume.by_category_30d} >= {
            "billing",
            "product",
        }


def test_seeded_records_are_referentially_consistent(
    session_factory: Callable[[], Session],
) -> None:
    with session_factory() as session:
        reseed_database(session)

        invoice_orphans = session.scalar(
            select(func.count())
            .select_from(Invoice)
            .outerjoin(Account, Invoice.account_id == Account.id)
            .outerjoin(Subscription, Invoice.subscription_id == Subscription.id)
            .where((Account.id.is_(None)) | (Subscription.id.is_(None)))
        )
        event_orphans = session.scalar(
            select(func.count())
            .select_from(ProductEvent)
            .outerjoin(Account, ProductEvent.account_id == Account.id)
            .outerjoin(User, ProductEvent.user_id == User.id)
            .where((Account.id.is_(None)) | (User.id.is_(None)))
        )
        ticket_orphans = session.scalar(
            select(func.count())
            .select_from(SupportTicket)
            .outerjoin(Account, SupportTicket.account_id == Account.id)
            .outerjoin(User, SupportTicket.user_id == User.id)
            .where((Account.id.is_(None)) | (User.id.is_(None)))
        )
        mismatched_invoice_amounts = session.scalar(
            select(func.count())
            .select_from(Invoice)
            .join(Subscription, Invoice.subscription_id == Subscription.id)
            .where(Invoice.amount_cents != Subscription.mrr_cents)
        )
        chunk_orphans = session.scalar(
            select(func.count())
            .select_from(KnowledgeDocumentChunk)
            .outerjoin(
                KnowledgeDocument,
                KnowledgeDocumentChunk.document_id == KnowledgeDocument.id,
            )
            .where(KnowledgeDocument.id.is_(None))
        )

        assert invoice_orphans == 0
        assert event_orphans == 0
        assert ticket_orphans == 0
        assert chunk_orphans == 0
        assert mismatched_invoice_amounts == 0


def test_seeded_scenarios_include_evidence_for_future_investigations(
    session_factory: Callable[[], Session],
) -> None:
    with session_factory() as session:
        reseed_database(session)

        scenario_account_counts = dict(
            session.execute(
                select(Account.source_scenario, func.count())
                .where(Account.source_scenario.is_not(None))
                .group_by(Account.source_scenario)
            ).all()
        )
        failed_invoice_scenarios = {
            scenario
            for (scenario,) in session.execute(
                select(Invoice.source_scenario)
                .where(
                    Invoice.status == "failed",
                    Invoice.source_scenario.is_not(None),
                )
                .distinct()
            )
        }
        ticket_scenarios = {
            scenario
            for (scenario,) in session.execute(
                select(SupportTicket.source_scenario)
                .where(SupportTicket.source_scenario.is_not(None))
                .distinct()
            )
        }
        event_scenarios = {
            scenario
            for (scenario,) in session.execute(
                select(ProductEvent.source_scenario)
                .where(ProductEvent.source_scenario.is_not(None))
                .distinct()
            )
        }
        churned_scenarios = {
            scenario
            for (scenario,) in session.execute(
                select(Subscription.source_scenario)
                .where(
                    Subscription.status == "canceled",
                    Subscription.source_scenario.is_not(None),
                )
                .distinct()
            )
        }

        assert scenario_account_counts == {
            scenario: len(account_numbers)
            for scenario, account_numbers in SCENARIO_ACCOUNT_NUMBERS.items()
        }
        for scenario in SCENARIOS.values():
            assert scenario["root_cause"]
            assert scenario["expected_evidence"]
            assert scenario["false_leads"]
            assert scenario["recommended_actions"]

        assert failed_invoice_scenarios >= {
            "checkout_retry_regression",
            "payment_method_expiration",
        }
        assert ticket_scenarios == set(SCENARIOS)
        assert event_scenarios >= {
            "usage_drop_after_import_outage",
            "support_backlog_export_bug",
        }
        assert churned_scenarios == {"enterprise_churn_wave"}


def test_each_seeded_scenario_has_concrete_expected_evidence(
    session_factory: Callable[[], Session],
) -> None:
    with session_factory() as session:
        reseed_database(session)

        for scenario in SCENARIOS:
            account_ids = [
                account_id
                for (account_id,) in session.execute(
                    select(Account.id).where(Account.source_scenario == scenario)
                )
            ]
            assert account_ids, scenario

            scenario_tickets = session.scalar(
                select(func.count())
                .select_from(SupportTicket)
                .where(
                    SupportTicket.account_id.in_(account_ids),
                    SupportTicket.source_scenario == scenario,
                )
            )
            assert scenario_tickets and scenario_tickets > 0, scenario

        checkout_accounts = [
            account_id
            for (account_id,) in session.execute(
                select(Account.id).where(
                    Account.source_scenario == "checkout_retry_regression"
                )
            )
        ]
        checkout_failures = session.scalar(
            select(func.count())
            .select_from(Invoice)
            .where(
                Invoice.account_id.in_(checkout_accounts),
                Invoice.status == "failed",
                Invoice.source_scenario == "checkout_retry_regression",
                Invoice.failure_reason.ilike("%retry webhook%"),
            )
        )
        assert checkout_failures == len(checkout_accounts)

        churn_accounts = [
            account_id
            for (account_id,) in session.execute(
                select(Account.id).where(Account.source_scenario == "enterprise_churn_wave")
            )
        ]
        churned_subscriptions = session.scalar(
            select(func.count())
            .select_from(Subscription)
            .where(
                Subscription.account_id.in_(churn_accounts),
                Subscription.status == "canceled",
                Subscription.cancellation_reason.ilike("%onboarding%"),
            )
        )
        void_invoices = session.scalar(
            select(func.count())
            .select_from(Invoice)
            .where(
                Invoice.account_id.in_(churn_accounts),
                Invoice.status == "void",
                Invoice.source_scenario == "enterprise_churn_wave",
            )
        )
        assert churned_subscriptions == len(churn_accounts)
        assert void_invoices == len(churn_accounts)

        usage_accounts = [
            account_id
            for (account_id,) in session.execute(
                select(Account.id).where(
                    Account.source_scenario == "usage_drop_after_import_outage"
                )
            )
        ]
        import_failures = session.scalar(
            select(func.count())
            .select_from(ProductEvent)
            .where(
                ProductEvent.account_id.in_(usage_accounts),
                ProductEvent.event_name == "import_failed",
                ProductEvent.source_scenario == "usage_drop_after_import_outage",
            )
        )
        integration_tickets = session.scalar(
            select(func.count())
            .select_from(SupportTicket)
            .where(
                SupportTicket.account_id.in_(usage_accounts),
                SupportTicket.category == "integration",
                SupportTicket.source_scenario == "usage_drop_after_import_outage",
            )
        )
        assert import_failures and import_failures > 0
        assert integration_tickets and integration_tickets > 0

        export_accounts = [
            account_id
            for (account_id,) in session.execute(
                select(Account.id).where(
                    Account.source_scenario == "support_backlog_export_bug"
                )
            )
        ]
        report_exports = session.scalar(
            select(func.count())
            .select_from(ProductEvent)
            .where(
                ProductEvent.account_id.in_(export_accounts),
                ProductEvent.event_name == "report_export",
                ProductEvent.source_scenario == "support_backlog_export_bug",
            )
        )
        product_tickets = session.scalar(
            select(func.count())
            .select_from(SupportTicket)
            .where(
                SupportTicket.account_id.in_(export_accounts),
                SupportTicket.category == "product",
                SupportTicket.source_scenario == "support_backlog_export_bug",
            )
        )
        assert report_exports and report_exports > 0
        assert product_tickets and product_tickets > 0

        payment_accounts = [
            account_id
            for (account_id,) in session.execute(
                select(Account.id).where(
                    Account.source_scenario == "payment_method_expiration"
                )
            )
        ]
        payment_failures = session.scalar(
            select(func.count())
            .select_from(Invoice)
            .where(
                Invoice.account_id.in_(payment_accounts),
                Invoice.status == "failed",
                Invoice.source_scenario == "payment_method_expiration",
                Invoice.failure_reason.ilike("%Expired cards%"),
            )
        )
        card_tickets = session.scalar(
            select(func.count())
            .select_from(SupportTicket)
            .where(
                SupportTicket.account_id.in_(payment_accounts),
                SupportTicket.subject.ilike("%Card expiration%"),
                SupportTicket.source_scenario == "payment_method_expiration",
            )
        )
        assert payment_failures == len(payment_accounts)
        assert card_tickets and card_tickets > 0


def test_dashboard_metrics_endpoint_returns_seeded_summary(
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
        response = client.get("/metrics/dashboard")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    payload = response.json()
    assert payload["mrr"]["delta_cents"] < 0
    assert payload["churn"]["churned_accounts_30d"] == 5
    assert payload["failed_invoices"]["failed_count_30d"] >= 16


def test_individual_metric_endpoints_return_reviewable_contracts(
    session_factory: Callable[[], Session],
) -> None:
    with session_factory() as session:
        reseed_database(session)

    def override_get_db() -> Generator[Session, None, None]:
        with session_factory() as db:
            yield db

    expected_keys = {
        "/metrics/mrr": {
            "current_mrr_cents",
            "previous_mrr_cents",
            "delta_cents",
            "delta_percent",
            "active_subscriptions",
            "churned_mrr_cents",
        },
        "/metrics/churn": {
            "churned_accounts_30d",
            "active_accounts",
            "churn_rate_30d",
            "churned_mrr_cents_30d",
        },
        "/metrics/failed-invoices": {
            "failed_count_30d",
            "failed_amount_cents_30d",
            "unresolved_count_30d",
            "recent_failures",
        },
        "/metrics/ticket-volume": {
            "total_tickets_30d",
            "open_tickets",
            "high_priority_open_tickets",
            "by_category_30d",
        },
        "/metrics/active-users": {
            "active_users_7d",
            "active_users_30d",
            "event_count_7d",
            "event_count_30d",
        },
    }

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)
    try:
        responses = {path: client.get(path) for path in expected_keys}
    finally:
        app.dependency_overrides.clear()

    for path, response in responses.items():
        assert response.status_code == 200, path
        assert set(response.json()) == expected_keys[path]

    failed_invoice_payload = responses["/metrics/failed-invoices"].json()
    assert failed_invoice_payload["unresolved_count_30d"] == (
        failed_invoice_payload["failed_count_30d"]
    )
    assert failed_invoice_payload["recent_failures"]
    assert failed_invoice_payload["recent_failures"][0].keys() >= {
        "invoice_id",
        "account_name",
        "invoice_date",
        "amount_cents",
        "failure_reason",
        "source_scenario",
    }


def test_seed_cli_refuses_unsafe_database_targets() -> None:
    validate_seed_target(
        "postgresql+psycopg://ops_agent:ops_agent@localhost:5432/ops_agent",
        "local",
    )
    validate_seed_target(
        "postgresql+psycopg://postgres:test@localhost:5432/test_ops_agent",
        "test",
    )

    with pytest.raises(SystemExit, match="Refusing to reseed outside local"):
        validate_seed_target(
            "postgresql+psycopg://ops_agent:ops_agent@postgres:5432/ops_agent",
            "production",
        )
    with pytest.raises(SystemExit, match="Refusing to reseed a non-local database target"):
        validate_seed_target(
            "postgresql+psycopg://ops_agent:ops_agent@db.example.com:5432/prod",
            "local",
        )
    with pytest.raises(SystemExit, match="Refusing to reseed"):
        validate_seed_target(
            "postgresql+psycopg://ops_agent:ops_agent@db.example.com:5432/prod",
            "production",
        )


def test_metrics_routes_fail_closed_outside_demo_environments(monkeypatch) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    get_settings.cache_clear()
    try:
        with pytest.raises(Exception) as exc_info:
            require_demo_metrics_access()
    finally:
        get_settings.cache_clear()

    assert getattr(exc_info.value, "status_code", None) == 403
