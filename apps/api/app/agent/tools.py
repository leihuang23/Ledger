from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.incidents.service import get_incident_detail
from app.knowledge.search import search_knowledge
from app.models import Account, Invoice, Subscription, SupportTicket


class SqlEvidence(BaseModel):
    title: str
    reference_id: str
    summary: str
    source_query: str
    citation: dict[str, Any] = Field(default_factory=dict)


class QueryRevenueMetricsInput(BaseModel):
    incident_id: str


class QueryRevenueMetricsOutput(BaseModel):
    incident_id: str
    metric_evidence: dict[str, Any]
    affected_account_ids: list[str]
    affected_accounts: list[dict[str, Any]]
    invoice_ids: list[str]
    sql_evidence: list[SqlEvidence]


class FetchAccountDetailsInput(BaseModel):
    account_ids: list[str] = Field(default_factory=list)
    invoice_ids: list[str] = Field(default_factory=list)


class AccountInvoiceEvidence(BaseModel):
    invoice_id: str
    invoice_date: str
    amount_cents: int
    status: str
    failure_reason: str | None


class AccountDetail(BaseModel):
    account_id: str
    account_name: str
    segment: str
    industry: str
    region: str
    health_score: int
    subscription_plan: str | None
    subscription_status: str | None
    mrr_cents: int | None
    source_scenario: str | None
    failed_invoices: list[AccountInvoiceEvidence]


class FetchAccountDetailsOutput(BaseModel):
    accounts: list[AccountDetail]


class SearchDocsInput(BaseModel):
    query: str = Field(min_length=1)
    limit: int = Field(default=5, ge=1, le=10)


class DocumentEvidence(BaseModel):
    source_id: str
    title: str
    snippet: str
    score: float
    citation: dict[str, Any]


class SearchDocsOutput(BaseModel):
    query: str
    results: list[DocumentEvidence]


class FetchSupportTicketsInput(BaseModel):
    account_ids: list[str] = Field(default_factory=list)
    since: datetime
    limit: int = Field(default=12, ge=1, le=50)


class SupportTicketEvidence(BaseModel):
    ticket_id: str
    account_id: str
    account_name: str
    created_at: str
    status: str
    priority: str
    category: str
    subject: str
    description: str
    sentiment: str
    source_scenario: str | None


class FetchSupportTicketsOutput(BaseModel):
    tickets: list[SupportTicketEvidence]


def query_revenue_metrics(
    session: Session, payload: QueryRevenueMetricsInput
) -> QueryRevenueMetricsOutput:
    incident = get_incident_detail(session, payload.incident_id)
    if incident is None:
        raise LookupError(f"Unknown incident id: {payload.incident_id}")

    metric = incident.metric_evidence.model_dump(mode="json")
    affected_accounts = [
        account.model_dump(mode="json") for account in incident.affected_accounts
    ]
    invoice_ids = list(incident.metric_evidence.invoice_ids)
    current_start = date.fromisoformat(metric["current_window_start"])
    current_end_exclusive = (
        date.fromisoformat(metric["current_window_end"]) + timedelta(days=1)
    )
    previous_start = date.fromisoformat(metric["previous_window_start"])
    previous_end_exclusive = (
        date.fromisoformat(metric["previous_window_end"]) + timedelta(days=1)
    )
    current_paid_mrr = _paid_invoice_sum(
        session, current_start, current_end_exclusive
    )
    previous_paid_mrr = _paid_invoice_sum(
        session, previous_start, previous_end_exclusive
    )
    failed_renewal_rows = _failed_renewal_rows(
        session, current_start, current_end_exclusive
    )

    return QueryRevenueMetricsOutput(
        incident_id=incident.id,
        metric_evidence=metric,
        affected_account_ids=[
            account.account_id for account in incident.affected_accounts
        ],
        affected_accounts=affected_accounts,
        invoice_ids=invoice_ids,
        sql_evidence=[
            SqlEvidence(
                title="Paid invoice MRR window comparison",
                reference_id=f"{incident.id}:paid-mrr-window",
                summary=(
                    "Compares paid invoice MRR between the current and previous "
                    "7-day windows for the incident metric."
                ),
                source_query=(
                    "SELECT SUM(invoices.amount_cents) AS paid_mrr_cents "
                    "FROM invoices JOIN subscriptions "
                    "ON subscriptions.id = invoices.subscription_id "
                    "WHERE invoices.status = 'paid' "
                    "AND invoices.invoice_date >= :window_start "
                    "AND invoices.invoice_date < :window_end_exclusive "
                    "AND subscriptions.status IN ('active', 'canceled');"
                ),
                citation={
                    "query_name": "paid_invoice_mrr_window_comparison",
                    "parameters": {
                        "current_window_start": current_start.isoformat(),
                        "current_window_end_exclusive": current_end_exclusive.isoformat(),
                        "previous_window_start": previous_start.isoformat(),
                        "previous_window_end_exclusive": previous_end_exclusive.isoformat(),
                    },
                    "rows": [
                        {
                            "window": "current",
                            "window_start": current_start.isoformat(),
                            "window_end_exclusive": current_end_exclusive.isoformat(),
                            "paid_mrr_cents": current_paid_mrr,
                        },
                        {
                            "window": "previous",
                            "window_start": previous_start.isoformat(),
                            "window_end_exclusive": previous_end_exclusive.isoformat(),
                            "paid_mrr_cents": previous_paid_mrr,
                        },
                    ],
                    "incident_snapshot": {
                        "current_value_cents": metric["current_value_cents"],
                        "previous_value_cents": metric["previous_value_cents"],
                        "delta_cents": metric["delta_cents"],
                        "delta_percent": metric["delta_percent"],
                    },
                },
            ),
            SqlEvidence(
                title="Failed renewal invoices grouped by account",
                reference_id=f"{incident.id}:failed-renewals",
                summary=(
                    f"Found {sum(row['failed_invoice_count'] for row in failed_renewal_rows)} "
                    "failed current-window renewals worth "
                    f"{sum(row['failed_invoice_cents'] for row in failed_renewal_rows)} cents."
                ),
                source_query=(
                    "SELECT invoices.account_id, accounts.name, "
                    "SUM(invoices.amount_cents) AS failed_invoice_cents, "
                    "COUNT(invoices.id) AS failed_invoice_count "
                    "FROM invoices JOIN accounts ON accounts.id = invoices.account_id "
                    "JOIN subscriptions ON subscriptions.id = invoices.subscription_id "
                    "WHERE invoices.status = 'failed' "
                    "AND subscriptions.status = 'active' "
                    "AND invoices.invoice_date >= :current_window_start "
                    "AND invoices.invoice_date < :current_window_end_exclusive "
                    "GROUP BY invoices.account_id, accounts.name;"
                ),
                citation={
                    "query_name": "failed_renewal_invoices_by_account",
                    "parameters": {
                        "current_window_start": current_start.isoformat(),
                        "current_window_end_exclusive": current_end_exclusive.isoformat(),
                    },
                    "rows": failed_renewal_rows,
                    "incident_snapshot": {
                        "failed_invoice_cents": metric["failed_invoice_cents"],
                        "failed_invoice_count": metric["failed_invoice_count"],
                        "invoice_ids": invoice_ids,
                    },
                },
            ),
        ],
    )


def fetch_account_details(
    session: Session, payload: FetchAccountDetailsInput
) -> FetchAccountDetailsOutput:
    if not payload.account_ids:
        return FetchAccountDetailsOutput(accounts=[])

    accounts = session.scalars(
        select(Account).where(Account.id.in_(payload.account_ids)).order_by(Account.name)
    ).all()
    subscriptions = session.scalars(
        select(Subscription).where(Subscription.account_id.in_(payload.account_ids))
    ).all()
    subscription_by_account = {
        subscription.account_id: subscription for subscription in subscriptions
    }

    invoice_query = select(Invoice).where(Invoice.account_id.in_(payload.account_ids))
    if payload.invoice_ids:
        invoice_query = invoice_query.where(Invoice.id.in_(payload.invoice_ids))
    invoices = session.scalars(
        invoice_query.order_by(Invoice.account_id, Invoice.invoice_date.desc(), Invoice.id)
    ).all()
    invoices_by_account: dict[str, list[Invoice]] = {}
    for invoice in invoices:
        invoices_by_account.setdefault(invoice.account_id, []).append(invoice)

    return FetchAccountDetailsOutput(
        accounts=[
            AccountDetail(
                account_id=account.id,
                account_name=account.name,
                segment=account.segment,
                industry=account.industry,
                region=account.region,
                health_score=account.health_score,
                subscription_plan=subscription_by_account.get(account.id).plan
                if account.id in subscription_by_account
                else None,
                subscription_status=subscription_by_account.get(account.id).status
                if account.id in subscription_by_account
                else None,
                mrr_cents=subscription_by_account.get(account.id).mrr_cents
                if account.id in subscription_by_account
                else None,
                source_scenario=account.source_scenario,
                failed_invoices=[
                    AccountInvoiceEvidence(
                        invoice_id=invoice.id,
                        invoice_date=invoice.invoice_date.isoformat(),
                        amount_cents=invoice.amount_cents,
                        status=invoice.status,
                        failure_reason=invoice.failure_reason,
                    )
                    for invoice in invoices_by_account.get(account.id, [])
                ],
            )
            for account in accounts
        ]
    )


def search_docs(session: Session, payload: SearchDocsInput) -> SearchDocsOutput:
    results = search_knowledge(session, payload.query, limit=payload.limit)
    return SearchDocsOutput(
        query=payload.query,
        results=[
            DocumentEvidence(
                source_id=result.source_id,
                title=result.title,
                snippet=result.snippet,
                score=result.score,
                citation=result.citation,
            )
            for result in results
        ],
    )


def fetch_support_tickets(
    session: Session, payload: FetchSupportTicketsInput
) -> FetchSupportTicketsOutput:
    if not payload.account_ids:
        return FetchSupportTicketsOutput(tickets=[])

    priority_rank = case(
        (SupportTicket.priority == "high", 0),
        (SupportTicket.priority == "normal", 1),
        else_=2,
    )
    rows = session.execute(
        select(SupportTicket, Account.name.label("account_name"))
        .join(Account, Account.id == SupportTicket.account_id)
        .where(
            SupportTicket.account_id.in_(payload.account_ids),
            SupportTicket.created_at >= payload.since,
        )
        .order_by(
            priority_rank,
            SupportTicket.created_at.desc(),
            SupportTicket.id,
        )
        .limit(payload.limit)
    ).all()

    return FetchSupportTicketsOutput(
        tickets=[
            SupportTicketEvidence(
                ticket_id=ticket.id,
                account_id=ticket.account_id,
                account_name=account_name,
                created_at=ticket.created_at.isoformat(),
                status=ticket.status,
                priority=ticket.priority,
                category=ticket.category,
                subject=ticket.subject,
                description=ticket.description,
                sentiment=ticket.sentiment,
                source_scenario=ticket.source_scenario,
            )
            for ticket, account_name in rows
        ]
    )


def _paid_invoice_sum(session: Session, start_date: date, end_date: date) -> int:
    return int(
        session.scalar(
            select(func.coalesce(func.sum(Invoice.amount_cents), 0))
            .join(Subscription, Subscription.id == Invoice.subscription_id)
            .where(
                Invoice.status == "paid",
                Invoice.invoice_date >= start_date,
                Invoice.invoice_date < end_date,
                Subscription.status.in_(("active", "canceled")),
            )
        )
        or 0
    )


def _failed_renewal_rows(
    session: Session, start_date: date, end_date: date
) -> list[dict[str, Any]]:
    rows = session.execute(
        select(
            Invoice.account_id.label("account_id"),
            Account.name.label("account_name"),
            func.coalesce(func.sum(Invoice.amount_cents), 0).label(
                "failed_invoice_cents"
            ),
            func.count(Invoice.id).label("failed_invoice_count"),
        )
        .join(Account, Account.id == Invoice.account_id)
        .join(Subscription, Subscription.id == Invoice.subscription_id)
        .where(
            Invoice.status == "failed",
            Invoice.invoice_date >= start_date,
            Invoice.invoice_date < end_date,
            Subscription.status == "active",
        )
        .group_by(Invoice.account_id, Account.name)
        .order_by(func.sum(Invoice.amount_cents).desc(), Account.name)
    ).all()
    invoice_rows = session.execute(
        select(
            Invoice.account_id,
            Invoice.id,
            Invoice.failure_reason,
        )
        .join(Subscription, Subscription.id == Invoice.subscription_id)
        .where(
            Invoice.status == "failed",
            Invoice.invoice_date >= start_date,
            Invoice.invoice_date < end_date,
            Subscription.status == "active",
        )
        .order_by(Invoice.account_id, Invoice.id)
    ).all()

    invoice_ids_by_account: dict[str, list[str]] = {}
    failure_reasons_by_account: dict[str, list[str]] = {}
    for account_id, invoice_id, failure_reason in invoice_rows:
        invoice_ids_by_account.setdefault(account_id, []).append(invoice_id)
        if failure_reason:
            failure_reasons_by_account.setdefault(account_id, []).append(failure_reason)

    return [
        {
            "account_id": row.account_id,
            "account_name": row.account_name,
            "failed_invoice_cents": int(row.failed_invoice_cents or 0),
            "failed_invoice_count": int(row.failed_invoice_count or 0),
            "invoice_ids": invoice_ids_by_account.get(row.account_id, []),
            "failure_reasons": failure_reasons_by_account.get(row.account_id, []),
        }
        for row in rows
    ]
