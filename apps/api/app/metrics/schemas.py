from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, Field


class MrrMetrics(BaseModel):
    current_mrr_cents: int
    previous_mrr_cents: int = Field(
        description=(
            "Window-start snapshot: sum of mrr_cents over subscriptions "
            "active at the start of the trailing 30d window (started_at <= "
            "window start and not yet canceled at that moment). Not derived "
            "from churn."
        )
    )
    delta_cents: int = Field(
        description=(
            "current_mrr_cents - previous_mrr_cents; captures new business, "
            "expansion, contraction, and churn over the trailing 30d window."
        )
    )
    delta_percent: float = Field(
        description="delta_cents as a percentage of previous_mrr_cents."
    )
    active_subscriptions: int
    churned_mrr_cents: int = Field(
        description=(
            "MRR of subscriptions canceled within the trailing 30d window, "
            "reported independently of the delta."
        )
    )


class ChurnMetrics(BaseModel):
    churned_accounts_30d: int
    active_accounts: int
    churn_rate_30d: float
    churned_mrr_cents_30d: int


class FailedInvoiceSample(BaseModel):
    invoice_id: str
    account_name: str
    invoice_date: date
    amount_cents: int
    failure_reason: str | None
    source_scenario: str | None


class FailedInvoiceMetrics(BaseModel):
    failed_count_30d: int
    failed_amount_cents_30d: int
    unresolved_count_30d: int = Field(
        description=(
            "Invoices carry no resolved signal; this field currently reports "
            "failed invoices in the trailing 30d (identical to "
            "failed_count_30d) and is retained for API compatibility."
        )
    )
    recent_failures: list[FailedInvoiceSample]


class CategoryCount(BaseModel):
    category: str
    count: int


class TicketVolumeMetrics(BaseModel):
    total_tickets_30d: int
    open_tickets: int
    high_priority_open_tickets: int
    by_category_30d: list[CategoryCount]


class ActiveUserMetrics(BaseModel):
    active_users_7d: int
    active_users_30d: int
    event_count_7d: int
    event_count_30d: int


class DashboardMetrics(BaseModel):
    as_of: datetime
    mrr: MrrMetrics
    churn: ChurnMetrics
    failed_invoices: FailedInvoiceMetrics
    ticket_volume: TicketVolumeMetrics
    active_users: ActiveUserMetrics
