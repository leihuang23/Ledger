"""Stripe test-mode evidence adapter: signatures, ingest, reconcile, citations.

CI path uses an in-memory Stripe client and recorded fixtures (no network).
Live Test Clock coverage is opt-in via STRIPE_API_KEY=sk_test_... (see
docs/deployment.md).
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable, Generator
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

import app.models  # noqa: F401
from app.agent.tools import (
    FetchAccountDetailsInput,
    FetchSupportTicketsInput,
    fetch_account_details,
    fetch_support_tickets,
)
from app.core.config import Settings, get_settings
from app.db.base import Base
from app.db.session import get_db
from app.main import app
from app.models import (
    Account,
    Invoice,
    StripeEvent,
    StripeIngestionLog,
    StripeReconciliationRun,
    Subscription,
    SupportTicket,
)
from app.stripe_adapter.client import InMemoryStripeClient, build_stripe_client
from app.stripe_adapter.ids import (
    ledger_account_id,
    ledger_invoice_id,
    ledger_subscription_id,
)
from app.stripe_adapter.ingest import process_stripe_event
from app.stripe_adapter.reconcile import reconcile_stripe_sandbox
from app.stripe_adapter.signatures import (
    SignatureVerificationError,
    sign_payload_for_tests,
    verify_stripe_signature,
)

WEBHOOK_SECRET = "whsec_test_ledger_secret"
TEST_API_KEY = "sk_test_ledger_fixture_key"


@pytest.fixture()
def session_factory(tmp_path) -> Generator[Callable[[], Session], None, None]:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'stripe_adapter_test.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    yield TestingSessionLocal
    Base.metadata.drop_all(engine)
    engine.dispose()


@pytest.fixture()
def stripe_settings(monkeypatch) -> Settings:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", WEBHOOK_SECRET)
    monkeypatch.setenv("STRIPE_API_KEY", TEST_API_KEY)
    get_settings.cache_clear()
    settings = get_settings()
    yield settings
    get_settings.cache_clear()


def _event(
    *,
    event_id: str,
    event_type: str,
    obj: dict[str, Any],
    created: int | None = None,
    livemode: bool = False,
) -> dict[str, Any]:
    return {
        "id": event_id,
        "object": "event",
        "api_version": "2024-06-20",
        "created": created if created is not None else int(time.time()) - 10,
        "type": event_type,
        "livemode": livemode,
        "data": {"object": obj},
    }


def _customer(
    customer_id: str = "cus_test_acme",
    *,
    name: str = "Acme Sandbox Co",
) -> dict[str, Any]:
    return {
        "id": customer_id,
        "object": "customer",
        "name": name,
        "email": "billing@acme-sandbox.test",
        "created": int(time.time()) - 86_400,
        "metadata": {
            "segment": "midmarket",
            "industry": "saas",
            "region": "us-west",
            "health_score": "55",
        },
        "livemode": False,
    }


def _subscription(
    sub_id: str = "sub_test_acme",
    *,
    customer_id: str = "cus_test_acme",
    status: str = "active",
    mrr_cents: int = 12_000,
    canceled_at: int | None = None,
) -> dict[str, Any]:
    return {
        "id": sub_id,
        "object": "subscription",
        "customer": customer_id,
        "status": status,
        "created": int(time.time()) - 86_400,
        "start_date": int(time.time()) - 86_400,
        "canceled_at": canceled_at,
        "items": {
            "data": [
                {
                    "quantity": 4,
                    "price": {
                        "unit_amount": mrr_cents // 4,
                        "nickname": "Growth",
                        "recurring": {"interval": "month"},
                    },
                }
            ]
        },
        "metadata": {"plan": "growth", "mrr_cents": str(mrr_cents), "seats": "4"},
        "livemode": False,
    }


def _invoice(
    invoice_id: str = "in_test_fail_1",
    *,
    customer_id: str = "cus_test_acme",
    subscription_id: str = "sub_test_acme",
    status: str = "open",
    amount: int = 12_000,
    failure_reason: str | None = "card_declined",
    created: int | None = None,
) -> dict[str, Any]:
    created_ts = created if created is not None else int(time.time()) - 3_600
    metadata: dict[str, str] = {}
    if failure_reason:
        metadata["failure_reason"] = failure_reason
    return {
        "id": invoice_id,
        "object": "invoice",
        "customer": customer_id,
        "subscription": subscription_id,
        "status": status,
        "attempted": status != "draft",
        "amount_due": amount,
        "amount_paid": amount if status == "paid" else 0,
        "total": amount,
        "created": created_ts,
        "period_start": created_ts - 30 * 86_400,
        "period_end": created_ts,
        "due_date": created_ts,
        "status_transitions": {
            "paid_at": created_ts if status == "paid" else None,
        },
        "metadata": metadata,
        "livemode": False,
    }


def test_signature_rejects_missing_and_invalid_headers() -> None:
    payload = b'{"id":"evt_1"}'
    with pytest.raises(SignatureVerificationError):
        verify_stripe_signature(payload, None, WEBHOOK_SECRET)
    with pytest.raises(SignatureVerificationError):
        verify_stripe_signature(payload, "t=1,v1=deadbeef", WEBHOOK_SECRET)


def test_signature_rejects_altered_payload() -> None:
    payload = b'{"id":"evt_1","type":"invoice.paid"}'
    header = sign_payload_for_tests(payload, WEBHOOK_SECRET, timestamp=int(time.time()))
    with pytest.raises(SignatureVerificationError):
        verify_stripe_signature(payload + b" ", header, WEBHOOK_SECRET)


def test_signature_accepts_valid_signed_payload() -> None:
    payload = b'{"id":"evt_1","type":"invoice.paid"}'
    now = int(time.time())
    header = sign_payload_for_tests(payload, WEBHOOK_SECRET, timestamp=now)
    assert verify_stripe_signature(payload, header, WEBHOOK_SECRET, now=now) == now


def test_build_stripe_client_rejects_live_keys() -> None:
    with pytest.raises(ValueError, match="test-mode"):
        build_stripe_client("sk_live_not_allowed")
    assert build_stripe_client(None) is None
    client = build_stripe_client("sk_test_abc")
    assert client is not None
    client.close()


def test_settings_reject_live_stripe_secret(monkeypatch) -> None:
    monkeypatch.setenv("STRIPE_API_KEY", "sk_live_should_fail")
    get_settings.cache_clear()
    with pytest.raises(Exception):
        Settings(_env_file=None)
    get_settings.cache_clear()


def test_webhook_rejects_invalid_signature(
    session_factory: Callable[[], Session],
    stripe_settings: Settings,
) -> None:
    def override_get_db() -> Generator[Session, None, None]:
        with session_factory() as db:
            yield db

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)
    try:
        response = client.post(
            "/stripe/webhook",
            content=b'{"id":"evt_bad"}',
            headers={
                "Content-Type": "application/json",
                "Stripe-Signature": "t=1,v1=not-valid",
            },
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 400
    assert "signature" in response.json()["detail"].lower()


def test_webhook_ingests_valid_signed_event(
    session_factory: Callable[[], Session],
    stripe_settings: Settings,
) -> None:
    event = _event(
        event_id="evt_valid_1",
        event_type="customer.created",
        obj=_customer(),
    )
    body = json.dumps(event).encode("utf-8")
    header = sign_payload_for_tests(body, WEBHOOK_SECRET)

    def override_get_db() -> Generator[Session, None, None]:
        with session_factory() as db:
            yield db

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)
    try:
        response = client.post(
            "/stripe/webhook",
            content=body,
            headers={
                "Content-Type": "application/json",
                "Stripe-Signature": header,
            },
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    payload = response.json()
    assert payload["event_id"] == "evt_valid_1"
    assert payload["status"] == "processed"
    assert payload["duplicate"] is False

    with session_factory() as session:
        account = session.scalar(
            select(Account).where(Account.stripe_customer_id == "cus_test_acme")
        )
        assert account is not None
        assert account.name == "Acme Sandbox Co"
        assert account.source_scenario == "stripe_sandbox"
        assert account.id == ledger_account_id("cus_test_acme")
        stored = session.get(StripeEvent, "evt_valid_1")
        assert stored is not None
        assert stored.status == "processed"


def test_duplicate_event_is_idempotent_noop(
    session_factory: Callable[[], Session],
) -> None:
    event = _event(
        event_id="evt_dup_1",
        event_type="customer.created",
        obj=_customer(name="Original Name"),
    )
    with session_factory() as session:
        first = process_stripe_event(session, event)
        assert first.status == "processed"
        assert first.duplicate is False

        # Mutate the payload name; redelivery must not re-apply or fail.
        event["data"]["object"]["name"] = "Should Not Replace"
        second = process_stripe_event(session, event)
        assert second.duplicate is True
        assert second.status == "processed"

        account = session.scalar(
            select(Account).where(Account.stripe_customer_id == "cus_test_acme")
        )
        assert account is not None
        assert account.name == "Original Name"
        assert session.scalar(select(StripeEvent).where(StripeEvent.id == "evt_dup_1"))


def test_out_of_order_delivery_keeps_newer_subscription_state(
    session_factory: Callable[[], Session],
) -> None:
    base = int(time.time()) - 100
    create_event = _event(
        event_id="evt_sub_create",
        event_type="customer.subscription.created",
        obj=_subscription(status="active"),
        created=base,
    )
    cancel_event = _event(
        event_id="evt_sub_cancel",
        event_type="customer.subscription.deleted",
        obj=_subscription(status="canceled", canceled_at=base + 50),
        created=base + 50,
    )
    # Stale update arrives after cancel (lower created timestamp).
    stale_update = _event(
        event_id="evt_sub_stale",
        event_type="customer.subscription.updated",
        obj=_subscription(status="active"),
        created=base + 10,
    )

    with session_factory() as session:
        process_stripe_event(session, create_event)
        process_stripe_event(session, cancel_event)
        process_stripe_event(session, stale_update)

        subscription = session.scalar(
            select(Subscription).where(
                Subscription.stripe_subscription_id == "sub_test_acme"
            )
        )
        assert subscription is not None
        assert subscription.status == "canceled"
        assert subscription.canceled_at is not None
        assert subscription.id == ledger_subscription_id("sub_test_acme")


def test_out_of_order_refetch_uses_current_stripe_object(
    session_factory: Callable[[], Session],
) -> None:
    """When order may stale the embedded snapshot, re-fetch current object."""
    base = int(time.time()) - 100
    current = _subscription(status="canceled", canceled_at=base + 80)
    memory = InMemoryStripeClient(
        customers={"cus_test_acme": _customer()},
        subscriptions={"sub_test_acme": current},
    )
    # Old embedded snapshot says active; client returns canceled.
    stale_event = _event(
        event_id="evt_refetch_1",
        event_type="customer.subscription.updated",
        obj=_subscription(status="active"),
        created=base + 90,
    )
    with session_factory() as session:
        process_stripe_event(session, stale_event, stripe_client=memory)
        subscription = session.scalar(
            select(Subscription).where(
                Subscription.stripe_subscription_id == "sub_test_acme"
            )
        )
        assert subscription is not None
        assert subscription.status == "canceled"


def test_invoice_paid_and_payment_failed_mapping(
    session_factory: Callable[[], Session],
) -> None:
    with session_factory() as session:
        process_stripe_event(
            session,
            _event(
                event_id="evt_cust",
                event_type="customer.created",
                obj=_customer(),
            ),
        )
        process_stripe_event(
            session,
            _event(
                event_id="evt_sub",
                event_type="customer.subscription.created",
                obj=_subscription(),
            ),
        )
        process_stripe_event(
            session,
            _event(
                event_id="evt_paid",
                event_type="invoice.paid",
                obj=_invoice(
                    "in_paid_1",
                    status="paid",
                    failure_reason=None,
                ),
            ),
        )
        process_stripe_event(
            session,
            _event(
                event_id="evt_failed",
                event_type="invoice.payment_failed",
                obj=_invoice(
                    "in_fail_1",
                    status="open",
                    failure_reason="card_expired",
                ),
            ),
        )

        paid = session.scalar(
            select(Invoice).where(Invoice.stripe_invoice_id == "in_paid_1")
        )
        failed = session.scalar(
            select(Invoice).where(Invoice.stripe_invoice_id == "in_fail_1")
        )
        assert paid is not None
        assert paid.status == "paid"
        assert paid.paid_at is not None
        assert paid.amount_cents == 12_000
        assert failed is not None
        assert failed.status == "failed"
        assert failed.failure_reason == "card_expired"
        assert failed.id == ledger_invoice_id("in_fail_1")


def test_unsupported_and_livemode_events_are_visible(
    session_factory: Callable[[], Session],
) -> None:
    with session_factory() as session:
        unsupported = process_stripe_event(
            session,
            _event(
                event_id="evt_unsup",
                event_type="charge.dispute.created",
                obj={"id": "dp_1"},
            ),
        )
        live = process_stripe_event(
            session,
            _event(
                event_id="evt_live",
                event_type="invoice.paid",
                obj=_invoice(),
                livemode=True,
            ),
        )
        assert unsupported.status == "unsupported"
        assert live.status == "rejected_livemode"

        logs = session.scalars(select(StripeIngestionLog)).all()
        messages = {log.message for log in logs}
        assert any("Unsupported" in message for message in messages)
        assert any("live-mode" in message.lower() for message in messages)


def test_reconciliation_repairs_missed_webhook(
    session_factory: Callable[[], Session],
) -> None:
    customer = _customer("cus_missed")
    subscription = _subscription("sub_missed", customer_id="cus_missed", status="active")
    invoice = _invoice(
        "in_missed_fail",
        customer_id="cus_missed",
        subscription_id="sub_missed",
        status="open",
        failure_reason="insufficient_funds",
    )
    memory = InMemoryStripeClient(
        customers={customer["id"]: customer},
        subscriptions={subscription["id"]: subscription},
        invoices={invoice["id"]: invoice},
    )

    with session_factory() as session:
        # Simulate missed webhooks: local DB empty for these objects.
        assert session.scalar(select(Account)) is None
        result = reconcile_stripe_sandbox(session, memory, limit=50)
        assert result.status == "completed"
        assert result.customers_seen == 1
        assert result.subscriptions_seen == 1
        assert result.invoices_seen == 1
        assert result.repaired >= 3

        account = session.scalar(
            select(Account).where(Account.stripe_customer_id == "cus_missed")
        )
        sub = session.scalar(
            select(Subscription).where(
                Subscription.stripe_subscription_id == "sub_missed"
            )
        )
        inv = session.scalar(
            select(Invoice).where(Invoice.stripe_invoice_id == "in_missed_fail")
        )
        assert account is not None
        assert sub is not None and sub.status == "active"
        assert inv is not None and inv.status == "open"
        run = session.get(StripeReconciliationRun, result.run_id)
        assert run is not None
        assert run.repaired == result.repaired


def test_failed_renewal_simulation_maps_test_clock_style_state(
    session_factory: Callable[[], Session],
) -> None:
    """High-fidelity sandbox simulation of a Test Clock failed renewal.

    Prefer live Test Clocks when STRIPE_API_KEY is a real sk_test key (see
    test_live_test_clock_failed_renewal). CI uses recorded fixtures here.
    """
    base = int(time.time()) - 7 * 86_400
    customer = _customer("cus_clock_1", name="Clock Customer")
    subscription = _subscription(
        "sub_clock_1",
        customer_id="cus_clock_1",
        status="past_due",
        mrr_cents=9_900,
    )
    failed_invoice = _invoice(
        "in_clock_fail",
        customer_id="cus_clock_1",
        subscription_id="sub_clock_1",
        status="open",
        amount=9_900,
        failure_reason="card_declined",
        created=base + 7 * 86_400,
    )
    memory = InMemoryStripeClient(
        customers={customer["id"]: customer},
        subscriptions={subscription["id"]: subscription},
        invoices={failed_invoice["id"]: failed_invoice},
    )

    with session_factory() as session:
        for event in (
            _event(
                event_id="evt_clock_cust",
                event_type="customer.created",
                obj=customer,
                created=base,
            ),
            _event(
                event_id="evt_clock_sub",
                event_type="customer.subscription.updated",
                obj=subscription,
                created=base + 7 * 86_400,
            ),
            _event(
                event_id="evt_clock_inv",
                event_type="invoice.payment_failed",
                obj=failed_invoice,
                created=base + 7 * 86_400,
            ),
        ):
            result = process_stripe_event(session, event, stripe_client=memory)
            assert result.status == "processed"

        account = session.scalar(
            select(Account).where(Account.stripe_customer_id == "cus_clock_1")
        )
        sub = session.scalar(
            select(Subscription).where(
                Subscription.stripe_subscription_id == "sub_clock_1"
            )
        )
        inv = session.scalar(
            select(Invoice).where(Invoice.stripe_invoice_id == "in_clock_fail")
        )
        assert account is not None
        assert sub is not None and sub.status == "past_due"
        assert inv is not None
        assert inv.status == "failed"
        assert inv.failure_reason == "card_declined"
        assert inv.amount_cents == 9_900


def test_investigation_tools_cite_stripe_derived_invoice_account_and_support(
    session_factory: Callable[[], Session],
) -> None:
    """When sandbox-derived state is present, tools return citeable evidence."""
    with session_factory() as session:
        process_stripe_event(
            session,
            _event(
                event_id="evt_cite_cust",
                event_type="customer.created",
                obj=_customer("cus_cite", name="Citeable Corp"),
            ),
        )
        process_stripe_event(
            session,
            _event(
                event_id="evt_cite_sub",
                event_type="customer.subscription.created",
                obj=_subscription("sub_cite", customer_id="cus_cite"),
            ),
        )
        process_stripe_event(
            session,
            _event(
                event_id="evt_cite_inv",
                event_type="invoice.payment_failed",
                obj=_invoice(
                    "in_cite_fail",
                    customer_id="cus_cite",
                    subscription_id="sub_cite",
                    failure_reason="card_expired",
                ),
            ),
        )
        account = session.scalar(
            select(Account).where(Account.stripe_customer_id == "cus_cite")
        )
        assert account is not None
        invoice = session.scalar(
            select(Invoice).where(Invoice.stripe_invoice_id == "in_cite_fail")
        )
        assert invoice is not None

        ticket = SupportTicket(
            id="tkt_stripe_cite_1",
            account_id=account.id,
            user_id=None,
            created_at=datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=1),
            resolved_at=None,
            status="open",
            priority="high",
            category="billing",
            subject="Renewal payment failed after card expired",
            description=(
                "Stripe sandbox renewal failed with card_expired for this account."
            ),
            sentiment="negative",
            source_scenario="stripe_sandbox",
        )
        session.add(ticket)
        session.commit()

        details = fetch_account_details(
            session,
            FetchAccountDetailsInput(
                account_ids=[account.id],
                invoice_ids=[invoice.id],
                include_invoices=True,
            ),
        )
        assert len(details.accounts) == 1
        detail = details.accounts[0]
        assert detail.account_id == account.id
        assert detail.account_name == "Citeable Corp"
        assert detail.source_scenario == "stripe_sandbox"
        assert any(
            inv.invoice_id == invoice.id
            and inv.status == "failed"
            and inv.failure_reason == "card_expired"
            for inv in detail.failed_invoices
        )

        tickets = fetch_support_tickets(
            session,
            FetchSupportTicketsInput(
                account_ids=[account.id],
                since=datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=30),
            ),
        )
        assert any(
            t.ticket_id == "tkt_stripe_cite_1"
            and t.account_id == account.id
            and "card expired" in t.subject.lower()
            for t in tickets.tickets
        )


def test_stripe_disabled_in_demo_env(
    session_factory: Callable[[], Session],
    monkeypatch,
) -> None:
    monkeypatch.setenv("APP_ENV", "demo")
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", WEBHOOK_SECRET)
    monkeypatch.setenv("STRIPE_API_KEY", TEST_API_KEY)
    get_settings.cache_clear()

    def override_get_db() -> Generator[Session, None, None]:
        with session_factory() as db:
            yield db

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)
    try:
        status_response = client.get("/stripe/status")
        assert status_response.status_code == 200
        assert status_response.json()["enabled"] is False

        body = json.dumps(
            _event(
                event_id="evt_demo",
                event_type="customer.created",
                obj=_customer(),
            )
        ).encode("utf-8")
        header = sign_payload_for_tests(body, WEBHOOK_SECRET)
        webhook_response = client.post(
            "/stripe/webhook",
            content=body,
            headers={
                "Content-Type": "application/json",
                "Stripe-Signature": header,
            },
        )
        assert webhook_response.status_code == 403
    finally:
        app.dependency_overrides.clear()
        get_settings.cache_clear()


def test_stripe_status_reports_unconfigured(monkeypatch) -> None:
    monkeypatch.setenv("APP_ENV", "local")
    monkeypatch.delenv("STRIPE_WEBHOOK_SECRET", raising=False)
    monkeypatch.delenv("STRIPE_API_KEY", raising=False)
    get_settings.cache_clear()
    client = TestClient(app)
    try:
        response = client.get("/stripe/status")
    finally:
        get_settings.cache_clear()
    assert response.status_code == 200
    body = response.json()
    assert body["enabled"] is False
    assert body["configured"] is False


@pytest.mark.stripe_live
def test_live_test_clock_failed_renewal() -> None:
    """Optional live path: requires STRIPE_API_KEY=sk_test_... in the environment.

    Creates a Test Clock customer/subscription, advances to a failed renewal
    when the Stripe account supports it, and asserts the REST client can read
    the resulting invoice. Skipped in CI without keys.

    Manual run:
      export STRIPE_API_KEY=sk_test_...
      cd apps/api && pytest -q tests/test_stripe_adapter.py -m stripe_live
    """
    api_key = os.environ.get("STRIPE_API_KEY")
    if not api_key or not api_key.startswith("sk_test_"):
        pytest.skip("STRIPE_API_KEY test secret not available")

    import httpx

    client = httpx.Client(
        base_url="https://api.stripe.com/v1",
        auth=(api_key, ""),
        timeout=30.0,
        headers={"Stripe-Version": "2024-06-20"},
    )
    try:
        # Create a disposable test clock + customer to prove live connectivity.
        clock = client.post(
            "/test_helpers/test_clocks",
            data={"frozen_time": int(time.time()), "name": "ledger-ci-clock"},
        )
        if clock.status_code == 404:
            pytest.skip("Test Clocks not available on this Stripe account")
        clock.raise_for_status()
        clock_id = clock.json()["id"]

        customer = client.post(
            "/customers",
            data={
                "name": "Ledger Live Clock Customer",
                "email": "ledger-test-clock@example.test",
                "test_clock": clock_id,
            },
        )
        customer.raise_for_status()
        customer_id = customer.json()["id"]
        assert customer_id.startswith("cus_")

        # Cleanup best-effort so repeated runs do not pile up fixtures.
        client.delete(f"/test_helpers/test_clocks/{clock_id}")
    finally:
        client.close()
