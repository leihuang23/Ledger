from __future__ import annotations

from collections.abc import Callable, Generator
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
import threading

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

import app.models  # noqa: F401
from app.db.base import Base
from app.db.session import get_db
from app.main import app
from app.core.config import get_settings
from app.metrics.router import require_demo_metrics_access
from app.metrics.service import (
    get_active_user_metrics,
    get_churn_metrics,
    get_dashboard_metrics,
    get_dataset_anchor,
    get_failed_invoice_metrics,
    get_mrr_metrics,
    get_ticket_volume_metrics,
)
from app.models import (
    Account,
    AgentVersion,
    Invoice,
    Incident,
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
    account_id,
    dataset_fingerprint,
    ensure_seeded_if_empty,
    reseed_database,
    validate_seed_target,
)


@pytest.fixture()
def session_factory(tmp_path) -> Generator[Callable[[], Session], None, None]:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'ledger_test.db'}",
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
        phase6 = session.get(AgentVersion, "ledger_phase6")
        assert phase6 is not None
        assert phase6.status == "published"
        assert phase6.forked_from_version_id == "ledger_v1"


def test_ensure_seeded_if_empty_refuses_production_environment(
    session_factory: Callable[[], Session],
    monkeypatch,
) -> None:
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+psycopg://ledger:ledger@postgres:5432/ledger",
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
        "postgresql+psycopg://ledger:ledger@prod.example.com:5432/ledger",
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
        "postgresql+psycopg://ledger:ledger@prod.example.com:5432/ledger",
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
            "incidents": 6,
            "eval_cases": 6,
            "eval_results": 0,
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
        # previous_mrr_cents is a window-start snapshot (subscriptions active
        # 30d before the anchor). The seed has no in-window signups, so the
        # snapshot here equals current + churned and the delta is negative;
        # the snapshot semantics themselves are pinned by
        # test_mrr_previous_window_is_snapshot_at_window_start.
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


def _count_queries(session: Session, fn: Callable[[], object]) -> int:
    """Count SQL round-trips executed by fn against the session's engine."""
    engine = session.get_bind()
    counter = {"n": 0}

    @event.listens_for(engine, "before_cursor_execute")
    def _count(*_args, **_kwargs) -> None:
        counter["n"] += 1

    try:
        fn()
    finally:
        event.remove(engine, "before_cursor_execute", _count)

    return counter["n"]


def test_failed_invoice_unresolved_count_matches_failed_count(
    session_factory: Callable[[], Session],
) -> None:
    """unresolved_count_30d currently equals failed_count_30d by design.

    Invoices have no resolved/resolved_at signal, so a true "failed and
    still unresolved" count would be invented semantics. The field is
    retained for API compatibility and documented (schema description,
    README, dashboard card) as "failed invoices in trailing 30d".
    """
    with session_factory() as session:
        reseed_database(session)
        anchor = get_dataset_anchor(session)
        metrics = get_failed_invoice_metrics(session, anchor)
        assert metrics.unresolved_count_30d == metrics.failed_count_30d


def test_mrr_previous_window_is_snapshot_at_window_start(
    session_factory: Callable[[], Session],
) -> None:
    """previous_mrr_cents is a window-start snapshot: the sum of mrr_cents
    over subscriptions active at the start of the trailing 30d window
    (started_at <= window start AND (canceled_at IS NULL OR
    canceled_at > window start)).

    New business signed inside the window raises current MRR but must NOT
    raise previous MRR. Under the old semantics (previous = current +
    churned) delta_cents was always exactly -churned_mrr_cents; with the
    snapshot, in-window signups move the delta upward.
    """
    with session_factory() as session:
        reseed_database(session)
        anchor = get_dataset_anchor(session)
        baseline = get_mrr_metrics(session, anchor)

        # New subscription signed 10 days into the trailing 30d window.
        session.add(
            Subscription(
                id="sub_test_new_business",
                account_id=account_id(42),
                plan="growth",
                status="active",
                mrr_cents=50_000,
                seats=10,
                started_at=anchor.date() - timedelta(days=20),
            )
        )
        session.commit()

        metrics = get_mrr_metrics(session, anchor)

        assert metrics.current_mrr_cents == baseline.current_mrr_cents + 50_000
        # The window-start snapshot is unaffected by in-window signups.
        assert metrics.previous_mrr_cents == baseline.previous_mrr_cents
        assert metrics.delta_cents == baseline.delta_cents + 50_000
        assert metrics.delta_percent == round(
            (metrics.delta_cents / metrics.previous_mrr_cents) * 100, 2
        )
        # churned_mrr_cents is reported independently of the delta.
        assert metrics.churned_mrr_cents == baseline.churned_mrr_cents


def test_mrr_snapshot_excludes_subscriptions_canceled_before_window(
    session_factory: Callable[[], Session],
) -> None:
    """A subscription canceled before the window start belongs to neither the
    current MRR nor the window-start snapshot, and is not counted as 30d
    churn."""
    with session_factory() as session:
        reseed_database(session)
        anchor = get_dataset_anchor(session)
        baseline = get_mrr_metrics(session, anchor)

        session.add(
            Subscription(
                id="sub_test_old_churn",
                account_id=account_id(43),
                plan="startup",
                status="canceled",
                mrr_cents=25_000,
                seats=5,
                started_at=anchor.date() - timedelta(days=200),
                canceled_at=anchor.date() - timedelta(days=60),
            )
        )
        session.commit()

        metrics = get_mrr_metrics(session, anchor)

        assert metrics.current_mrr_cents == baseline.current_mrr_cents
        assert metrics.previous_mrr_cents == baseline.previous_mrr_cents
        assert metrics.churned_mrr_cents == baseline.churned_mrr_cents


def test_mrr_metrics_uses_single_aggregation_round_trip(
    session_factory: Callable[[], Session],
) -> None:
    """get_mrr_metrics must consolidate the three scalar queries into one."""
    with session_factory() as session:
        reseed_database(session)
        anchor = get_dataset_anchor(session)
        count = _count_queries(session, lambda: get_mrr_metrics(session, anchor))
        assert count <= 1, f"Expected <=1 query, got {count}"


def test_churn_metrics_uses_single_aggregation_round_trip(
    session_factory: Callable[[], Session],
) -> None:
    """get_churn_metrics must consolidate the three scalar queries into one."""
    with session_factory() as session:
        reseed_database(session)
        anchor = get_dataset_anchor(session)
        count = _count_queries(session, lambda: get_churn_metrics(session, anchor))
        assert count <= 1, f"Expected <=1 query, got {count}"


def test_failed_invoice_metrics_uses_two_round_trips(
    session_factory: Callable[[], Session],
) -> None:
    """get_failed_invoice_metrics must issue one aggregate + one sample query.

    Before consolidation it issued four queries (failed_count, failed_amount,
    unresolved_count duplicate, recent_failures sample).
    """
    with session_factory() as session:
        reseed_database(session)
        anchor = get_dataset_anchor(session)
        count = _count_queries(session, lambda: get_failed_invoice_metrics(session, anchor))
        assert count <= 2, f"Expected <=2 queries, got {count}"


def test_ticket_volume_metrics_uses_two_round_trips(
    session_factory: Callable[[], Session],
) -> None:
    """get_ticket_volume_metrics must issue one aggregate + one group-by query.

    Before consolidation it issued four queries (total, open, high-priority,
    by-category).
    """
    with session_factory() as session:
        reseed_database(session)
        anchor = get_dataset_anchor(session)
        count = _count_queries(session, lambda: get_ticket_volume_metrics(session, anchor))
        assert count <= 2, f"Expected <=2 queries, got {count}"


def test_active_user_metrics_uses_single_aggregation_round_trip(
    session_factory: Callable[[], Session],
) -> None:
    """get_active_user_metrics must consolidate the four scalar queries into one."""
    with session_factory() as session:
        reseed_database(session)
        anchor = get_dataset_anchor(session)
        count = _count_queries(session, lambda: get_active_user_metrics(session, anchor))
        assert count <= 1, f"Expected <=1 query, got {count}"


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
        for scenario_name, scenario in SCENARIOS.items():
            assert scenario["root_cause"]
            assert scenario["expected_evidence"]
            assert scenario["false_leads"]
            # The ambiguity scenario intentionally has no recommended actions:
            # recommending specific actions for an unknown root cause would
            # contradict the agent's uncertainty diagnosis.
            if scenario_name != "unknown_root_cause":
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
        payment_failure_dates = session.scalars(
            select(Invoice.invoice_date)
            .where(
                Invoice.account_id.in_(payment_accounts),
                Invoice.status == "failed",
                Invoice.source_scenario == "payment_method_expiration",
            )
            .order_by(Invoice.invoice_date)
        ).all()
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
        assert payment_failure_dates
        assert set(payment_failure_dates) == {date(2026, 6, 1)}
        assert card_tickets and card_tickets > 0
        payment_incident = session.scalar(
            select(Incident).where(
                Incident.source_scenario == "payment_method_expiration"
            )
        )
        assert payment_incident is not None
        source_queries = payment_incident.evidence["source_queries"]
        assert any("failed June renewal invoices" in query for query in source_queries)
        assert not any("failed current-window renewal invoices" in query for query in source_queries)


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
        "postgresql+psycopg://ledger:ledger@localhost:5432/ledger",
        "local",
    )
    validate_seed_target(
        "postgresql+psycopg://postgres:test@localhost:5432/test_ledger",
        "test",
    )

    with pytest.raises(SystemExit, match="Refusing to reseed outside local"):
        validate_seed_target(
            "postgresql+psycopg://ledger:ledger@postgres:5432/ledger",
            "production",
        )
    with pytest.raises(SystemExit, match="Refusing to reseed a non-local database target"):
        validate_seed_target(
            "postgresql+psycopg://ledger:ledger@db.example.com:5432/prod",
            "local",
        )
    with pytest.raises(SystemExit, match="Refusing to reseed"):
        validate_seed_target(
            "postgresql+psycopg://ledger:ledger@db.example.com:5432/prod",
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
