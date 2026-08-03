"""Map Stripe sandbox objects onto existing Ledger domain records."""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Account, Invoice, Subscription
from app.stripe_adapter.ids import (
    ledger_account_id,
    ledger_invoice_id,
    ledger_subscription_id,
)

STRIPE_SOURCE_SCENARIO = "stripe_sandbox"

# Event types the adapter applies. Everything else is logged as unsupported.
SUPPORTED_EVENT_TYPES: frozenset[str] = frozenset(
    {
        "customer.created",
        "customer.updated",
        "customer.subscription.created",
        "customer.subscription.updated",
        "customer.subscription.deleted",
        "invoice.paid",
        "invoice.payment_failed",
        "invoice.updated",
        "invoice.finalized",
        "invoice.voided",
        "invoice.marked_uncollectible",
    }
)

# Mutable object events where embedded snapshots may be stale on out-of-order delivery.
REFETCH_EVENT_TYPES: frozenset[str] = frozenset(
    {
        "customer.updated",
        "customer.subscription.created",
        "customer.subscription.updated",
        "customer.subscription.deleted",
        "invoice.paid",
        "invoice.payment_failed",
        "invoice.updated",
        "invoice.finalized",
        "invoice.voided",
        "invoice.marked_uncollectible",
    }
)

SUBSCRIPTION_STATUS_MAP: dict[str, str] = {
    "active": "active",
    "canceled": "canceled",
    "cancelled": "canceled",
    "past_due": "past_due",
    "unpaid": "unpaid",
    "trialing": "trialing",
    "incomplete": "incomplete",
    "incomplete_expired": "canceled",
    "paused": "paused",
}

INVOICE_STATUS_MAP: dict[str, str] = {
    "paid": "paid",
    "open": "open",
    "draft": "draft",
    "void": "void",
    "uncollectible": "failed",
}


def unix_to_datetime(value: int | float | None) -> datetime | None:
    if value is None:
        return None
    return datetime.fromtimestamp(int(value), tz=timezone.utc).replace(tzinfo=None)


def unix_to_date(value: int | float | None) -> date | None:
    dt = unix_to_datetime(value)
    return dt.date() if dt else None


def map_subscription_status(stripe_status: str | None) -> str:
    if not stripe_status:
        return "active"
    return SUBSCRIPTION_STATUS_MAP.get(stripe_status, stripe_status)


def map_invoice_status(
    stripe_status: str | None,
    *,
    event_type: str | None = None,
    attempted: bool | None = None,
) -> str:
    if event_type == "invoice.payment_failed":
        return "failed"
    if event_type == "invoice.paid":
        return "paid"
    if event_type == "invoice.voided":
        return "void"
    if event_type == "invoice.marked_uncollectible":
        return "failed"
    if not stripe_status:
        return "open"
    mapped = INVOICE_STATUS_MAP.get(stripe_status, stripe_status)
    if event_type is None and mapped == "open" and attempted:
        return "failed"
    return mapped


def extract_failure_reason(invoice_obj: dict[str, Any]) -> str | None:
    last_error = invoice_obj.get("last_finalization_error") or {}
    if isinstance(last_error, dict) and last_error.get("message"):
        return str(last_error["message"])[:160]
    charge = invoice_obj.get("charge")
    # Prefer explicit metadata used by Test Clock fixtures.
    metadata = invoice_obj.get("metadata") or {}
    if isinstance(metadata, dict) and metadata.get("failure_reason"):
        return str(metadata["failure_reason"])[:160]
    if invoice_obj.get("attempted") and invoice_obj.get("status") in {
        "open",
        "uncollectible",
    }:
        return "payment_failed"
    if isinstance(charge, dict):
        outcome = charge.get("outcome") or {}
        if isinstance(outcome, dict) and outcome.get("seller_message"):
            return str(outcome["seller_message"])[:160]
        if charge.get("failure_message"):
            return str(charge["failure_message"])[:160]
    return None


def extract_plan_name(subscription_obj: dict[str, Any]) -> str:
    metadata = subscription_obj.get("metadata") or {}
    if isinstance(metadata, dict) and metadata.get("plan"):
        return str(metadata["plan"])[:40]
    items = (subscription_obj.get("items") or {}).get("data") or []
    if items:
        price = items[0].get("price") or {}
        nickname = price.get("nickname")
        if nickname:
            return str(nickname)[:40]
        product = price.get("product")
        if isinstance(product, str) and product:
            return product[:40]
        if isinstance(product, dict) and product.get("name"):
            return str(product["name"])[:40]
    return "stripe_plan"


def extract_mrr_cents(subscription_obj: dict[str, Any]) -> int:
    metadata = subscription_obj.get("metadata") or {}
    if isinstance(metadata, dict) and metadata.get("mrr_cents") is not None:
        try:
            return int(metadata["mrr_cents"])
        except (TypeError, ValueError):
            pass
    items = (subscription_obj.get("items") or {}).get("data") or []
    total = 0
    for item in items:
        price = item.get("price") or {}
        unit_amount = price.get("unit_amount")
        quantity = item.get("quantity") or 1
        if unit_amount is None:
            continue
        interval = (price.get("recurring") or {}).get("interval", "month")
        amount = int(unit_amount) * int(quantity)
        if interval == "year":
            amount = amount // 12
        total += amount
    return total


def extract_seats(subscription_obj: dict[str, Any]) -> int:
    metadata = subscription_obj.get("metadata") or {}
    if isinstance(metadata, dict) and metadata.get("seats") is not None:
        try:
            return max(1, int(metadata["seats"]))
        except (TypeError, ValueError):
            pass
    items = (subscription_obj.get("items") or {}).get("data") or []
    if not items:
        return 1
    quantity = items[0].get("quantity") or 1
    return max(1, int(quantity))


def customer_display_name(customer_obj: dict[str, Any]) -> str:
    name = customer_obj.get("name")
    if name:
        return str(name)[:120]
    email = customer_obj.get("email")
    if email:
        return str(email)[:120]
    return f"Stripe customer {customer_obj.get('id', 'unknown')}"[:120]


def is_stale(
    local_updated_at: datetime | None,
    event_created_at: datetime,
) -> bool:
    """Skip applying when local state reflects a strictly newer Stripe object time.

    Equal-created-time events apply (last-wins); event-ID idempotency handles
    true duplicates, so same-frozen-time Test Clock events all land.
    """
    if local_updated_at is None:
        return False
    return local_updated_at > event_created_at


def upsert_customer(
    session: Session,
    customer_obj: dict[str, Any],
    *,
    event_created_at: datetime,
    placeholder: bool = False,
) -> Account | None:
    stripe_id = customer_obj.get("id")
    if not stripe_id:
        return None

    account = session.scalar(
        select(Account).where(Account.stripe_customer_id == stripe_id)
    )
    if account is None:
        account = session.get(Account, ledger_account_id(stripe_id))

    if account is not None:
        if placeholder:
            return account
        if is_stale(account.stripe_object_updated_at, event_created_at):
            return account

    metadata = customer_obj.get("metadata") or {}
    segment = str(metadata.get("segment") or "smb")[:32]
    industry = str(metadata.get("industry") or "software")[:80]
    region = str(metadata.get("region") or "us")[:80]
    health_score = 70
    if metadata.get("health_score") is not None:
        try:
            health_score = int(metadata["health_score"])
        except (TypeError, ValueError):
            health_score = 70

    if account is None:
        account = Account(
            id=ledger_account_id(stripe_id),
            name=customer_display_name(customer_obj),
            segment=segment,
            industry=industry,
            region=region,
            health_score=health_score,
            source_scenario=STRIPE_SOURCE_SCENARIO,
            stripe_customer_id=stripe_id,
            stripe_object_updated_at=None if placeholder else event_created_at,
            created_at=unix_to_datetime(customer_obj.get("created"))
            or event_created_at,
            is_active=not bool(customer_obj.get("deleted")),
        )
        session.add(account)
        session.flush()
    else:
        account.name = customer_display_name(customer_obj)
        account.segment = segment
        account.industry = industry
        account.region = region
        account.health_score = health_score
        account.source_scenario = account.source_scenario or STRIPE_SOURCE_SCENARIO
        account.stripe_customer_id = stripe_id
        account.stripe_object_updated_at = event_created_at
        account.is_active = not bool(customer_obj.get("deleted"))
    return account


def upsert_subscription(
    session: Session,
    subscription_obj: dict[str, Any],
    *,
    event_created_at: datetime,
    event_type: str | None = None,
    placeholder: bool = False,
) -> Subscription | None:
    stripe_id = subscription_obj.get("id")
    if not stripe_id:
        return None

    customer_ref = subscription_obj.get("customer")
    customer_id = (
        customer_ref
        if isinstance(customer_ref, str)
        else (customer_ref or {}).get("id")
        if isinstance(customer_ref, dict)
        else None
    )
    if not customer_id:
        return None

    account = session.scalar(
        select(Account).where(Account.stripe_customer_id == customer_id)
    )
    if account is None:
        # Create a minimal account shell so subscription evidence can still land.
        account = upsert_customer(
            session,
            {
                "id": customer_id,
                "name": f"Stripe customer {customer_id}",
                "created": subscription_obj.get("created"),
                "metadata": {},
            },
            event_created_at=event_created_at,
            placeholder=True,
        )
    if account is None:
        return None

    subscription = session.scalar(
        select(Subscription).where(Subscription.stripe_subscription_id == stripe_id)
    )
    if subscription is None:
        subscription = session.get(Subscription, ledger_subscription_id(stripe_id))

    if subscription is not None:
        if placeholder:
            return subscription
        if is_stale(
            subscription.stripe_object_updated_at, event_created_at
        ):
            return subscription

    status = map_subscription_status(subscription_obj.get("status"))
    if event_type == "customer.subscription.deleted":
        status = "canceled"

    canceled_at = unix_to_date(subscription_obj.get("canceled_at"))
    if status == "canceled" and canceled_at is None:
        canceled_at = event_created_at.date()

    cancellation_reason = None
    cancellation_details = subscription_obj.get("cancellation_details") or {}
    if isinstance(cancellation_details, dict):
        cancellation_reason = cancellation_details.get("reason") or cancellation_details.get(
            "comment"
        )
    if cancellation_reason:
        cancellation_reason = str(cancellation_reason)[:160]

    if subscription is None:
        subscription = Subscription(
            id=ledger_subscription_id(stripe_id),
            account_id=account.id,
            plan=extract_plan_name(subscription_obj),
            status=status,
            mrr_cents=extract_mrr_cents(subscription_obj),
            seats=extract_seats(subscription_obj),
            started_at=unix_to_date(subscription_obj.get("start_date"))
            or unix_to_date(subscription_obj.get("created"))
            or event_created_at.date(),
            canceled_at=canceled_at,
            cancellation_reason=cancellation_reason,
            source_scenario=STRIPE_SOURCE_SCENARIO,
            stripe_subscription_id=stripe_id,
            stripe_object_updated_at=None if placeholder else event_created_at,
        )
        session.add(subscription)
    else:
        subscription.account_id = account.id
        subscription.plan = extract_plan_name(subscription_obj)
        subscription.status = status
        subscription.mrr_cents = extract_mrr_cents(subscription_obj)
        subscription.seats = extract_seats(subscription_obj)
        subscription.canceled_at = canceled_at
        subscription.cancellation_reason = cancellation_reason
        subscription.source_scenario = (
            subscription.source_scenario or STRIPE_SOURCE_SCENARIO
        )
        subscription.stripe_subscription_id = stripe_id
        subscription.stripe_object_updated_at = event_created_at
    return subscription


def upsert_invoice(
    session: Session,
    invoice_obj: dict[str, Any],
    *,
    event_created_at: datetime,
    event_type: str | None = None,
) -> Invoice | None:
    stripe_id = invoice_obj.get("id")
    if not stripe_id:
        return None

    customer_ref = invoice_obj.get("customer")
    customer_id = (
        customer_ref
        if isinstance(customer_ref, str)
        else (customer_ref or {}).get("id")
        if isinstance(customer_ref, dict)
        else None
    )
    subscription_ref = invoice_obj.get("subscription")
    subscription_id = (
        subscription_ref
        if isinstance(subscription_ref, str)
        else (subscription_ref or {}).get("id")
        if isinstance(subscription_ref, dict)
        else None
    )

    if not customer_id:
        return None

    account = session.scalar(
        select(Account).where(Account.stripe_customer_id == customer_id)
    )
    if account is None:
        account = upsert_customer(
            session,
            {
                "id": customer_id,
                "name": f"Stripe customer {customer_id}",
                "created": invoice_obj.get("created"),
                "metadata": {},
            },
            event_created_at=event_created_at,
            placeholder=True,
        )
    if account is None:
        return None

    subscription: Subscription | None = None
    if subscription_id:
        subscription = session.scalar(
            select(Subscription).where(
                Subscription.stripe_subscription_id == subscription_id
            )
        )
        if subscription is None:
            subscription = upsert_subscription(
                session,
                {
                    "id": subscription_id,
                    "customer": customer_id,
                    "status": "active",
                    "created": invoice_obj.get("created"),
                    "start_date": invoice_obj.get("period_start"),
                    "items": {"data": []},
                    "metadata": {},
                },
                event_created_at=event_created_at,
                placeholder=True,
            )
    if subscription is None:
        # Invoices without a subscription still need a local parent row.
        synthetic_sub_id = f"orphan_{stripe_id}"
        subscription = session.get(Subscription, ledger_subscription_id(synthetic_sub_id))
        if subscription is None:
            subscription = Subscription(
                id=ledger_subscription_id(synthetic_sub_id),
                account_id=account.id,
                plan="stripe_plan",
                status="active",
                mrr_cents=int(invoice_obj.get("amount_due") or invoice_obj.get("total") or 0),
                seats=1,
                started_at=unix_to_date(invoice_obj.get("created"))
                or event_created_at.date(),
                source_scenario=STRIPE_SOURCE_SCENARIO,
                stripe_subscription_id=None,
                stripe_object_updated_at=event_created_at,
            )
            session.add(subscription)

    invoice = session.scalar(
        select(Invoice).where(Invoice.stripe_invoice_id == stripe_id)
    )
    if invoice is None:
        invoice = session.get(Invoice, ledger_invoice_id(stripe_id))

    if invoice is not None and is_stale(invoice.stripe_object_updated_at, event_created_at):
        return invoice

    status = map_invoice_status(
        invoice_obj.get("status"),
        event_type=event_type,
        attempted=bool(invoice_obj.get("attempted")),
    )
    amount = int(
        invoice_obj.get("amount_paid")
        or invoice_obj.get("amount_due")
        or invoice_obj.get("total")
        or 0
    )
    invoice_date = (
        unix_to_date(invoice_obj.get("created"))
        or unix_to_date(invoice_obj.get("period_start"))
        or event_created_at.date()
    )
    due_date = unix_to_date(invoice_obj.get("due_date")) or invoice_date
    period_start = unix_to_date(invoice_obj.get("period_start")) or invoice_date
    period_end = unix_to_date(invoice_obj.get("period_end")) or invoice_date
    paid_at = unix_to_datetime(invoice_obj.get("status_transitions", {}).get("paid_at"))
    if status == "paid" and paid_at is None:
        paid_at = event_created_at
    failure_reason = extract_failure_reason(invoice_obj) if status == "failed" else None
    if status == "failed" and not failure_reason:
        failure_reason = "payment_failed"
    if invoice is not None and invoice.status == "failed" and status == "open":
        status = "failed"
        failure_reason = invoice.failure_reason or failure_reason

    if invoice is None:
        invoice = Invoice(
            id=ledger_invoice_id(stripe_id),
            account_id=account.id,
            subscription_id=subscription.id,
            invoice_date=invoice_date,
            due_date=due_date,
            period_start=period_start,
            period_end=period_end,
            amount_cents=amount,
            status=status,
            failure_reason=failure_reason,
            paid_at=paid_at,
            source_scenario=STRIPE_SOURCE_SCENARIO,
            stripe_invoice_id=stripe_id,
            stripe_object_updated_at=event_created_at,
        )
        session.add(invoice)
    else:
        invoice.account_id = account.id
        invoice.subscription_id = subscription.id
        invoice.invoice_date = invoice_date
        invoice.due_date = due_date
        invoice.period_start = period_start
        invoice.period_end = period_end
        invoice.amount_cents = amount
        invoice.status = status
        invoice.failure_reason = failure_reason
        invoice.paid_at = paid_at
        invoice.source_scenario = invoice.source_scenario or STRIPE_SOURCE_SCENARIO
        invoice.stripe_invoice_id = stripe_id
        invoice.stripe_object_updated_at = event_created_at
    return invoice
