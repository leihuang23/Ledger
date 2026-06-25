from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel


class AccountUserRead(BaseModel):
    id: str
    email: str
    full_name: str
    role: str
    last_seen_at: datetime | None
    is_active: bool


class AccountSubscriptionRead(BaseModel):
    id: str
    plan: str
    status: str
    mrr_cents: int
    seats: int
    started_at: date
    canceled_at: date | None
    cancellation_reason: str | None


class AccountInvoiceRead(BaseModel):
    id: str
    invoice_date: date
    due_date: date
    amount_cents: int
    status: str
    failure_reason: str | None
    paid_at: datetime | None
    source_scenario: str | None


class AccountInvoiceSummary(BaseModel):
    total_invoices: int
    paid_invoices: int
    failed_invoices: int
    void_invoices: int
    failed_amount_cents: int


class AccountTicketRead(BaseModel):
    id: str
    created_at: datetime
    status: str
    priority: str
    category: str
    subject: str
    sentiment: str
    source_scenario: str | None


class AccountProductEventSummary(BaseModel):
    event_name: str
    event_count: int
    latest_event_at: datetime
    source_scenario: str | None


class AccountDetailRead(BaseModel):
    id: str
    name: str
    segment: str
    industry: str
    region: str
    health_score: int
    source_scenario: str | None
    is_active: bool
    subscription: AccountSubscriptionRead | None
    users: list[AccountUserRead]
    invoice_summary: AccountInvoiceSummary
    recent_invoices: list[AccountInvoiceRead]
    recent_tickets: list[AccountTicketRead]
    product_event_summary: list[AccountProductEventSummary]
