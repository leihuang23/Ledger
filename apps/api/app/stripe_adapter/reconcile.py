"""Bounded reconciliation of Stripe sandbox objects against local Ledger state."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy.orm import Session

from app.models import StripeReconciliationRun
from app.stripe_adapter.client import StripeClient
from app.stripe_adapter.ids import reconciliation_run_id
from app.stripe_adapter.mapping import upsert_customer, upsert_invoice, upsert_subscription


@dataclass
class ReconcileResult:
    run_id: str
    status: str
    customers_seen: int = 0
    subscriptions_seen: int = 0
    invoices_seen: int = 0
    repaired: int = 0
    errors: int = 0
    details: list[str] = field(default_factory=list)


def reconcile_stripe_sandbox(
    session: Session,
    stripe_client: StripeClient,
    *,
    limit: int = 100,
    now: datetime | None = None,
) -> ReconcileResult:
    """Compare recent Stripe sandbox objects with local state and repair misses.

    ``limit`` bounds each object list so reconciliation stays cheap and safe
    for portfolio/demo environments.
    """
    started = now or datetime.now(timezone.utc).replace(tzinfo=None)
    run_id = reconciliation_run_id(f"{started.isoformat()}:{uuid4().hex}")
    run = StripeReconciliationRun(
        id=run_id,
        started_at=started,
        finished_at=None,
        status="running",
        customers_seen=0,
        subscriptions_seen=0,
        invoices_seen=0,
        repaired=0,
        errors=0,
        summary={},
    )
    session.add(run)
    session.flush()

    result = ReconcileResult(run_id=run_id, status="running")
    bound = max(1, min(limit, 100))

    try:
        customers = stripe_client.list_customers(limit=bound)
        result.customers_seen = len(customers)
        for customer in customers:
            try:
                with session.begin_nested():
                    before = _snapshot_account(session, customer.get("id"))
                    upsert_customer(session, customer, event_created_at=started)
                    session.flush()
                    after = _snapshot_account(session, customer.get("id"))
                    if before != after:
                        result.repaired += 1
                        result.details.append(f"repaired customer {customer.get('id')}")
            except Exception as exc:  # noqa: BLE001
                result.errors += 1
                result.details.append(f"customer error: {exc}")

        subscriptions = stripe_client.list_subscriptions(limit=bound)
        result.subscriptions_seen = len(subscriptions)
        for subscription in subscriptions:
            try:
                with session.begin_nested():
                    before = _snapshot_subscription(session, subscription.get("id"))
                    upsert_subscription(
                        session, subscription, event_created_at=started
                    )
                    session.flush()
                    after = _snapshot_subscription(session, subscription.get("id"))
                    if before != after:
                        result.repaired += 1
                        result.details.append(
                            f"repaired subscription {subscription.get('id')}"
                        )
            except Exception as exc:  # noqa: BLE001
                result.errors += 1
                result.details.append(f"subscription error: {exc}")

        invoices = stripe_client.list_invoices(limit=bound)
        result.invoices_seen = len(invoices)
        for invoice in invoices:
            try:
                with session.begin_nested():
                    before = _snapshot_invoice(session, invoice.get("id"))
                    upsert_invoice(session, invoice, event_created_at=started)
                    session.flush()
                    after = _snapshot_invoice(session, invoice.get("id"))
                    if before != after:
                        result.repaired += 1
                        result.details.append(f"repaired invoice {invoice.get('id')}")
            except Exception as exc:  # noqa: BLE001
                result.errors += 1
                result.details.append(f"invoice error: {exc}")

        result.status = "completed" if result.errors == 0 else "completed_with_errors"
    except Exception as exc:  # noqa: BLE001
        result.status = "failed"
        result.errors += 1
        result.details.append(f"reconciliation failed: {exc}")

    finished = datetime.now(timezone.utc).replace(tzinfo=None)
    run.finished_at = finished
    run.status = result.status
    run.customers_seen = result.customers_seen
    run.subscriptions_seen = result.subscriptions_seen
    run.invoices_seen = result.invoices_seen
    run.repaired = result.repaired
    run.errors = result.errors
    run.summary = {
        "limit": bound,
        "details": result.details[:50],
    }
    session.commit()
    return result


def _snapshot_account(session: Session, stripe_customer_id: str | None) -> Any:
    if not stripe_customer_id:
        return None
    from sqlalchemy import select

    from app.models import Account

    account = session.scalar(
        select(Account).where(Account.stripe_customer_id == stripe_customer_id)
    )
    if account is None:
        return None
    return (account.id, account.name, account.is_active)


def _snapshot_subscription(session: Session, stripe_subscription_id: str | None) -> Any:
    if not stripe_subscription_id:
        return None
    from sqlalchemy import select

    from app.models import Subscription

    subscription = session.scalar(
        select(Subscription).where(
            Subscription.stripe_subscription_id == stripe_subscription_id
        )
    )
    if subscription is None:
        return None
    return (
        subscription.id,
        subscription.status,
        subscription.mrr_cents,
        subscription.canceled_at,
    )


def _snapshot_invoice(session: Session, stripe_invoice_id: str | None) -> Any:
    if not stripe_invoice_id:
        return None
    from sqlalchemy import select

    from app.models import Invoice

    invoice = session.scalar(
        select(Invoice).where(Invoice.stripe_invoice_id == stripe_invoice_id)
    )
    if invoice is None:
        return None
    return (invoice.id, invoice.status, invoice.amount_cents, invoice.failure_reason)
