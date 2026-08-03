from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class WebhookIngestResponse(BaseModel):
    event_id: str
    status: str
    message: str
    duplicate: bool = False


class ReconcileRequest(BaseModel):
    limit: int = Field(default=100, ge=1, le=100)


class ReconcileResponse(BaseModel):
    run_id: str
    status: str
    customers_seen: int
    subscriptions_seen: int
    invoices_seen: int
    repaired: int
    errors: int
    details: list[str] = Field(default_factory=list)


class StripeIngestionLogRead(BaseModel):
    id: str
    event_id: str | None
    event_type: str | None
    level: str
    message: str
    detail: dict[str, Any]
    created_at: datetime


class StripeIngestionLogList(BaseModel):
    total: int
    logs: list[StripeIngestionLogRead]


class StripeEventRead(BaseModel):
    id: str
    event_type: str
    status: str
    livemode: bool
    object_id: str | None
    error_message: str | None
    stripe_created_at: datetime
    received_at: datetime
    processed_at: datetime | None


class StripeEventList(BaseModel):
    total: int
    events: list[StripeEventRead]


class StripeStatusResponse(BaseModel):
    configured: bool
    webhook_configured: bool
    api_key_configured: bool
    enabled: bool
    reason: str | None = None
