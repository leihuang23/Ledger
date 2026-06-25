from __future__ import annotations

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.accounts.schemas import (
    AccountDetailRead,
    AccountInvoiceRead,
    AccountInvoiceSummary,
    AccountProductEventSummary,
    AccountSubscriptionRead,
    AccountTicketRead,
    AccountUserRead,
)
from app.models import Account, Invoice, ProductEvent, Subscription, SupportTicket, User


def get_account_detail(session: Session, account_id: str) -> AccountDetailRead | None:
    account = session.get(Account, account_id)
    if account is None:
        return None

    subscription = session.scalar(
        select(Subscription)
        .where(Subscription.account_id == account_id)
        .order_by(Subscription.started_at.desc(), Subscription.id.desc())
        .limit(1)
    )
    users = session.scalars(
        select(User).where(User.account_id == account_id).order_by(User.full_name, User.id)
    ).all()
    invoice_summary = _invoice_summary(session, account_id)
    recent_invoices = session.scalars(
        select(Invoice)
        .where(Invoice.account_id == account_id)
        .order_by(Invoice.invoice_date.desc(), Invoice.id.desc())
        .limit(10)
    ).all()
    recent_tickets = session.scalars(
        select(SupportTicket)
        .where(SupportTicket.account_id == account_id)
        .order_by(SupportTicket.created_at.desc(), SupportTicket.id.desc())
        .limit(10)
    ).all()
    event_rows = session.execute(
        select(
            ProductEvent.event_name,
            func.count(ProductEvent.id).label("event_count"),
            func.max(ProductEvent.event_time).label("latest_event_at"),
            ProductEvent.source_scenario,
        )
        .where(ProductEvent.account_id == account_id)
        .group_by(ProductEvent.event_name, ProductEvent.source_scenario)
        .order_by(func.count(ProductEvent.id).desc(), ProductEvent.event_name)
        .limit(10)
    ).all()

    return AccountDetailRead(
        id=account.id,
        name=account.name,
        segment=account.segment,
        industry=account.industry,
        region=account.region,
        health_score=account.health_score,
        source_scenario=account.source_scenario,
        is_active=account.is_active,
        subscription=(
            AccountSubscriptionRead(
                id=subscription.id,
                plan=subscription.plan,
                status=subscription.status,
                mrr_cents=subscription.mrr_cents,
                seats=subscription.seats,
                started_at=subscription.started_at,
                canceled_at=subscription.canceled_at,
                cancellation_reason=subscription.cancellation_reason,
            )
            if subscription is not None
            else None
        ),
        users=[
            AccountUserRead(
                id=user.id,
                email=user.email,
                full_name=user.full_name,
                role=user.role,
                last_seen_at=user.last_seen_at,
                is_active=user.is_active,
            )
            for user in users
        ],
        invoice_summary=invoice_summary,
        recent_invoices=[
            AccountInvoiceRead(
                id=invoice.id,
                invoice_date=invoice.invoice_date,
                due_date=invoice.due_date,
                amount_cents=invoice.amount_cents,
                status=invoice.status,
                failure_reason=invoice.failure_reason,
                paid_at=invoice.paid_at,
                source_scenario=invoice.source_scenario,
            )
            for invoice in recent_invoices
        ],
        recent_tickets=[
            AccountTicketRead(
                id=ticket.id,
                created_at=ticket.created_at,
                status=ticket.status,
                priority=ticket.priority,
                category=ticket.category,
                subject=ticket.subject,
                sentiment=ticket.sentiment,
                source_scenario=ticket.source_scenario,
            )
            for ticket in recent_tickets
        ],
        product_event_summary=[
            AccountProductEventSummary(
                event_name=row.event_name,
                event_count=int(row.event_count),
                latest_event_at=row.latest_event_at,
                source_scenario=row.source_scenario,
            )
            for row in event_rows
        ],
    )


def _invoice_summary(session: Session, account_id: str) -> AccountInvoiceSummary:
    row = session.execute(
        select(
            func.count(Invoice.id).label("total_invoices"),
            func.coalesce(
                func.sum(case((Invoice.status == "paid", 1), else_=0)),
                0,
            ).label("paid_invoices"),
            func.coalesce(
                func.sum(case((Invoice.status == "failed", 1), else_=0)),
                0,
            ).label("failed_invoices"),
            func.coalesce(
                func.sum(case((Invoice.status == "void", 1), else_=0)),
                0,
            ).label("void_invoices"),
            func.coalesce(
                func.sum(case((Invoice.status == "failed", Invoice.amount_cents), else_=0)),
                0,
            ).label("failed_amount_cents"),
        ).where(Invoice.account_id == account_id)
    ).one()

    return AccountInvoiceSummary(
        total_invoices=int(row.total_invoices or 0),
        paid_invoices=int(row.paid_invoices or 0),
        failed_invoices=int(row.failed_invoices or 0),
        void_invoices=int(row.void_invoices or 0),
        failed_amount_cents=int(row.failed_amount_cents or 0),
    )
