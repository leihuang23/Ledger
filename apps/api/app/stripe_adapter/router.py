"""HTTP surface for the optional Stripe test-mode evidence adapter."""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.access import require_demo_operator_access
from app.core.config import Settings, get_settings
from app.db.session import get_db
from app.models import StripeEvent, StripeIngestionLog
from app.stripe_adapter.client import build_stripe_client
from app.stripe_adapter.ingest import process_stripe_event
from app.stripe_adapter.reconcile import reconcile_stripe_sandbox
from app.stripe_adapter.schemas import (
    ReconcileRequest,
    ReconcileResponse,
    StripeEventList,
    StripeEventRead,
    StripeIngestionLogList,
    StripeIngestionLogRead,
    StripeStatusResponse,
    WebhookIngestResponse,
)
from app.stripe_adapter.signatures import SignatureVerificationError, verify_stripe_signature

router = APIRouter(prefix="/stripe", tags=["stripe"])


def _stripe_public_status(settings: Settings) -> StripeStatusResponse:
    if settings.app_env == "demo":
        return StripeStatusResponse(
            configured=False,
            webhook_configured=False,
            api_key_configured=False,
            enabled=False,
            reason="Stripe adapter is disabled in the anonymous public demo (APP_ENV=demo).",
        )
    webhook_ok = settings.stripe_webhook_secret is not None
    api_ok = settings.stripe_api_key is not None
    configured = webhook_ok or api_ok
    return StripeStatusResponse(
        configured=configured,
        webhook_configured=webhook_ok,
        api_key_configured=api_ok,
        enabled=configured,
        reason=None if configured else "Stripe is not configured for this environment.",
    )


def _require_stripe_not_demo(settings: Settings) -> None:
    if settings.app_env == "demo":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "Stripe adapter is disabled in the anonymous public demo. "
                "Use a non-demo environment with test-mode credentials."
            ),
        )


@router.get("/status", response_model=StripeStatusResponse)
def stripe_status(
    settings: Settings = Depends(get_settings),
) -> StripeStatusResponse:
    return _stripe_public_status(settings)


@router.post("/webhook", response_model=WebhookIngestResponse)
async def stripe_webhook(
    request: Request,
    stripe_signature: str | None = Header(default=None, alias="Stripe-Signature"),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> WebhookIngestResponse:
    """Ingest a signed Stripe test-mode webhook event.

    Signature verification uses the raw body. Event ids are unique; redelivery
    returns a successful no-op. Live-mode events are rejected and logged.
    """
    _require_stripe_not_demo(settings)
    if not settings.stripe_webhook_secret:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Stripe webhook secret is not configured.",
        )

    payload = await request.body()
    try:
        verify_stripe_signature(
            payload,
            stripe_signature,
            settings.stripe_webhook_secret,
            tolerance_seconds=settings.stripe_webhook_tolerance_seconds,
        )
    except SignatureVerificationError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid Stripe signature: {exc}",
        ) from exc

    try:
        event = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Webhook body is not valid JSON.",
        ) from exc

    if not isinstance(event, dict):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Webhook body must be a JSON object.",
        )

    client = None
    try:
        client = build_stripe_client(settings.stripe_api_key)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc

    result = process_stripe_event(db, event, stripe_client=client)
    if client is not None and hasattr(client, "close"):
        client.close()

    if result.status == "failed" and not result.event_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=result.message,
        )

    return WebhookIngestResponse(
        event_id=result.event_id,
        status=result.status,
        message=result.message,
        duplicate=result.duplicate,
    )


@router.post(
    "/reconcile",
    response_model=ReconcileResponse,
    dependencies=[Depends(require_demo_operator_access)],
)
def stripe_reconcile(
    body: ReconcileRequest | None = None,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> ReconcileResponse:
    """Bounded repair of missed sandbox webhooks (operator-gated)."""
    _require_stripe_not_demo(settings)
    try:
        client = build_stripe_client(settings.stripe_api_key)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
    if client is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Stripe API key is not configured.",
        )

    request = body or ReconcileRequest()
    try:
        result = reconcile_stripe_sandbox(
            db, client, limit=request.limit
        )
    finally:
        client.close()

    return ReconcileResponse(
        run_id=result.run_id,
        status=result.status,
        customers_seen=result.customers_seen,
        subscriptions_seen=result.subscriptions_seen,
        invoices_seen=result.invoices_seen,
        repaired=result.repaired,
        errors=result.errors,
        details=result.details,
    )


@router.get(
    "/events",
    response_model=StripeEventList,
    dependencies=[Depends(require_demo_operator_access)],
)
def list_stripe_events(
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> StripeEventList:
    _require_stripe_not_demo(settings)
    total = int(db.scalar(select(func.count()).select_from(StripeEvent)) or 0)
    rows = db.scalars(
        select(StripeEvent)
        .order_by(StripeEvent.received_at.desc())
        .limit(limit)
    ).all()
    return StripeEventList(
        total=total,
        events=[
            StripeEventRead(
                id=row.id,
                event_type=row.event_type,
                status=row.status,
                livemode=row.livemode,
                object_id=row.object_id,
                error_message=row.error_message,
                stripe_created_at=row.stripe_created_at,
                received_at=row.received_at,
                processed_at=row.processed_at,
            )
            for row in rows
        ],
    )


@router.get(
    "/ingestion-logs",
    response_model=StripeIngestionLogList,
    dependencies=[Depends(require_demo_operator_access)],
)
def list_stripe_ingestion_logs(
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> StripeIngestionLogList:
    _require_stripe_not_demo(settings)
    total = int(db.scalar(select(func.count()).select_from(StripeIngestionLog)) or 0)
    rows = db.scalars(
        select(StripeIngestionLog)
        .order_by(StripeIngestionLog.created_at.desc())
        .limit(limit)
    ).all()
    return StripeIngestionLogList(
        total=total,
        logs=[
            StripeIngestionLogRead(
                id=row.id,
                event_id=row.event_id,
                event_type=row.event_type,
                level=row.level,
                message=row.message,
                detail=row.detail,
                created_at=row.created_at,
            )
            for row in rows
        ],
    )
