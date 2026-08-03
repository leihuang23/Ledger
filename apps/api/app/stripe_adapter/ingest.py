"""Idempotent Stripe webhook event ingestion into Ledger domain records."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import StripeEvent, StripeIngestionLog
from app.stripe_adapter.client import StripeClient
from app.stripe_adapter.ids import ingestion_log_id
from app.stripe_adapter.mapping import (
    REFETCH_EVENT_TYPES,
    SUPPORTED_EVENT_TYPES,
    unix_to_datetime,
    upsert_customer,
    upsert_invoice,
    upsert_subscription,
)


@dataclass(frozen=True)
class IngestResult:
    event_id: str
    status: str
    message: str
    duplicate: bool = False


def process_stripe_event(
    session: Session,
    event: dict[str, Any],
    *,
    stripe_client: StripeClient | None = None,
    now: datetime | None = None,
) -> IngestResult:
    """Persist event id first, then apply. Redelivery is a successful no-op."""
    received_at = now or datetime.now(timezone.utc).replace(tzinfo=None)
    event_id = str(event.get("id") or "")
    if not event_id:
        _log(
            session,
            level="error",
            message="Stripe event missing id",
            event_type=str(event.get("type") or ""),
            detail={"keys": sorted(event.keys())},
            now=received_at,
        )
        session.commit()
        return IngestResult(
            event_id="",
            status="failed",
            message="Stripe event missing id",
        )

    event_type = str(event.get("type") or "")
    livemode = bool(event.get("livemode"))
    stripe_created = unix_to_datetime(event.get("created")) or received_at
    data_object = ((event.get("data") or {}).get("object")) or {}
    object_id = data_object.get("id") if isinstance(data_object, dict) else None

    existing = session.get(StripeEvent, event_id)
    if existing is not None:
        return IngestResult(
            event_id=event_id,
            status=existing.status,
            message="duplicate event; no-op",
            duplicate=True,
        )

    row = StripeEvent(
        id=event_id,
        event_type=event_type,
        api_version=event.get("api_version"),
        livemode=livemode,
        status="received",
        stripe_created_at=stripe_created,
        received_at=received_at,
        object_id=str(object_id) if object_id else None,
        payload=event,
    )
    session.add(row)
    try:
        session.flush()
    except IntegrityError:
        session.rollback()
        # Concurrent redelivery won the insert race.
        raced = session.get(StripeEvent, event_id)
        return IngestResult(
            event_id=event_id,
            status=raced.status if raced else "processed",
            message="duplicate event; no-op",
            duplicate=True,
        )

    if livemode:
        row.status = "rejected_livemode"
        row.error_message = "Live-mode Stripe events are rejected"
        row.processed_at = received_at
        _log(
            session,
            level="error",
            message="Rejected live-mode Stripe event",
            event_id=event_id,
            event_type=event_type,
            detail={"livemode": True},
            now=received_at,
        )
        session.commit()
        return IngestResult(
            event_id=event_id,
            status=row.status,
            message=row.error_message or "rejected",
        )

    if event_type not in SUPPORTED_EVENT_TYPES:
        row.status = "unsupported"
        row.processed_at = received_at
        _log(
            session,
            level="warning",
            message=f"Unsupported Stripe event type: {event_type}",
            event_id=event_id,
            event_type=event_type,
            detail={"object_id": object_id},
            now=received_at,
        )
        session.commit()
        return IngestResult(
            event_id=event_id,
            status=row.status,
            message=f"unsupported event type: {event_type}",
        )

    try:
        _apply_event(
            session,
            event_type=event_type,
            data_object=data_object if isinstance(data_object, dict) else {},
            event_created_at=stripe_created,
            stripe_client=stripe_client,
        )
        row.status = "processed"
        row.processed_at = datetime.now(timezone.utc).replace(tzinfo=None)
        session.commit()
        return IngestResult(
            event_id=event_id,
            status=row.status,
            message="processed",
        )
    except Exception as exc:  # noqa: BLE001 - surface every apply failure visibly
        session.rollback()
        # Re-attach a failed event row after rollback if the insert survived.
        failed = session.get(StripeEvent, event_id)
        if failed is None:
            failed = StripeEvent(
                id=event_id,
                event_type=event_type,
                api_version=event.get("api_version"),
                livemode=livemode,
                status="failed",
                stripe_created_at=stripe_created,
                received_at=received_at,
                processed_at=datetime.now(timezone.utc).replace(tzinfo=None),
                object_id=str(object_id) if object_id else None,
                error_message=str(exc)[:2000],
                payload=event,
            )
            session.add(failed)
        else:
            failed.status = "failed"
            failed.error_message = str(exc)[:2000]
            failed.processed_at = datetime.now(timezone.utc).replace(tzinfo=None)
        _log(
            session,
            level="error",
            message=f"Failed to apply Stripe event: {exc}",
            event_id=event_id,
            event_type=event_type,
            detail={"error": str(exc)[:500]},
            now=datetime.now(timezone.utc).replace(tzinfo=None),
        )
        session.commit()
        return IngestResult(
            event_id=event_id,
            status="failed",
            message=str(exc),
        )


def _apply_event(
    session: Session,
    *,
    event_type: str,
    data_object: dict[str, Any],
    event_created_at: datetime,
    stripe_client: StripeClient | None,
) -> None:
    obj = data_object
    if stripe_client is not None and event_type in REFETCH_EVENT_TYPES:
        obj = _refetch_current(stripe_client, event_type, data_object) or data_object

    if event_type.startswith("customer.subscription."):
        upsert_subscription(
            session,
            obj,
            event_created_at=event_created_at,
            event_type=event_type,
        )
        return

    if event_type.startswith("customer."):
        upsert_customer(session, obj, event_created_at=event_created_at)
        return

    if event_type.startswith("invoice."):
        upsert_invoice(
            session,
            obj,
            event_created_at=event_created_at,
            event_type=event_type,
        )
        return

    raise RuntimeError(f"No apply handler for supported type {event_type}")


def _refetch_current(
    client: StripeClient,
    event_type: str,
    data_object: dict[str, Any],
) -> dict[str, Any] | None:
    object_id = data_object.get("id")
    if not object_id:
        return None
    try:
        if event_type.startswith("customer.subscription."):
            return client.retrieve_subscription(str(object_id))
        if event_type.startswith("customer."):
            return client.retrieve_customer(str(object_id))
        if event_type.startswith("invoice."):
            return client.retrieve_invoice(str(object_id))
    except Exception:
        # Fall back to embedded snapshot; failure is still better than dropping.
        return None
    return None


def _log(
    session: Session,
    *,
    level: str,
    message: str,
    now: datetime,
    event_id: str | None = None,
    event_type: str | None = None,
    detail: dict[str, Any] | None = None,
) -> None:
    material = f"{event_id or ''}:{event_type or ''}:{message}:{uuid4().hex}"
    session.add(
        StripeIngestionLog(
            id=ingestion_log_id(level, material),
            event_id=event_id,
            event_type=event_type,
            level=level,
            message=message,
            detail=detail or {},
            created_at=now,
        )
    )
