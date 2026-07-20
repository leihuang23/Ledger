from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.cache import cache_result
from app.models import Account, Invoice, ProductEvent, Subscription, SupportTicket
from app.seed import DATASET_ANCHOR

from .schemas import (
    ActiveUserMetrics,
    CategoryCount,
    ChurnMetrics,
    DashboardMetrics,
    FailedInvoiceMetrics,
    FailedInvoiceSample,
    MrrMetrics,
    TicketVolumeMetrics,
)


def get_dataset_anchor(session: Session) -> datetime:
    anchor = session.scalar(select(func.max(ProductEvent.event_time)))
    return anchor or DATASET_ANCHOR


def get_mrr_metrics(session: Session, anchor: datetime | None = None) -> MrrMetrics:
    anchor = anchor or get_dataset_anchor(session)
    window_start = anchor.date() - timedelta(days=30)

    row = session.execute(
        select(
            func.coalesce(
                func.sum(
                    case(
                        (Subscription.status == "active", Subscription.mrr_cents),
                        else_=0,
                    )
                ),
                0,
            ).label("current_mrr"),
            # Window-start snapshot: MRR of subscriptions that were active at
            # the start of the trailing 30d window (already started and not
            # yet canceled at that moment). This is the honest baseline for
            # the delta: it captures new business, expansion, contraction,
            # and churn instead of collapsing the delta to -churned_mrr.
            func.coalesce(
                func.sum(
                    case(
                        (
                            (Subscription.started_at <= window_start)
                            & (
                                (Subscription.canceled_at.is_(None))
                                | (Subscription.canceled_at > window_start)
                            ),
                            Subscription.mrr_cents,
                        ),
                        else_=0,
                    )
                ),
                0,
            ).label("previous_mrr"),
            func.coalesce(
                func.sum(
                    case(
                        (
                            (Subscription.status == "canceled")
                            & (Subscription.canceled_at >= window_start),
                            Subscription.mrr_cents,
                        ),
                        else_=0,
                    )
                ),
                0,
            ).label("churned_mrr"),
            func.count(
                case((Subscription.status == "active", 1), else_=None)
            ).label("active_subscriptions"),
        )
    ).one()

    current_mrr = int(row.current_mrr or 0)
    previous_mrr = int(row.previous_mrr or 0)
    churned_mrr = int(row.churned_mrr or 0)
    active_subscriptions = int(row.active_subscriptions or 0)
    delta_cents = current_mrr - previous_mrr
    delta_percent = round((delta_cents / previous_mrr) * 100, 2) if previous_mrr else 0.0

    return MrrMetrics(
        current_mrr_cents=current_mrr,
        previous_mrr_cents=previous_mrr,
        delta_cents=delta_cents,
        delta_percent=delta_percent,
        active_subscriptions=active_subscriptions,
        churned_mrr_cents=churned_mrr,
    )


def get_churn_metrics(session: Session, anchor: datetime | None = None) -> ChurnMetrics:
    anchor = anchor or get_dataset_anchor(session)
    churn_cutoff = anchor.date() - timedelta(days=30)

    row = session.execute(
        select(
            func.count(
                case((Subscription.status == "active", 1), else_=None)
            ).label("active_accounts"),
            func.count(
                case(
                    (
                        (Subscription.status == "canceled")
                        & (Subscription.canceled_at >= churn_cutoff),
                        1,
                    ),
                    else_=None,
                )
            ).label("churned_accounts"),
            func.coalesce(
                func.sum(
                    case(
                        (
                            (Subscription.status == "canceled")
                            & (Subscription.canceled_at >= churn_cutoff),
                            Subscription.mrr_cents,
                        ),
                        else_=0,
                    )
                ),
                0,
            ).label("churned_mrr"),
        )
    ).one()

    active_accounts = int(row.active_accounts or 0)
    churned_accounts = int(row.churned_accounts or 0)
    churned_mrr = int(row.churned_mrr or 0)
    denominator = active_accounts + churned_accounts
    churn_rate = round(churned_accounts / denominator, 4) if denominator else 0.0

    return ChurnMetrics(
        churned_accounts_30d=churned_accounts,
        active_accounts=active_accounts,
        churn_rate_30d=churn_rate,
        churned_mrr_cents_30d=churned_mrr,
    )


def get_failed_invoice_metrics(
    session: Session, anchor: datetime | None = None
) -> FailedInvoiceMetrics:
    anchor = anchor or get_dataset_anchor(session)
    cutoff = anchor.date() - timedelta(days=30)

    agg_row = session.execute(
        select(
            func.count().label("failed_count"),
            func.coalesce(func.sum(Invoice.amount_cents), 0).label("failed_amount"),
        ).where(Invoice.status == "failed", Invoice.invoice_date >= cutoff)
    ).one()

    failed_count = int(agg_row.failed_count or 0)
    failed_amount = int(agg_row.failed_amount or 0)
    # Invoices carry no resolved/resolved_at signal, so a true "failed and
    # still unresolved" count cannot be computed without inventing semantics.
    # unresolved_count_30d is retained for API compatibility and currently
    # reports failed invoices in the trailing 30d (identical to
    # failed_count_30d); this is documented on the schema field, in the
    # README, and on the dashboard card.
    unresolved_count = failed_count

    rows = session.execute(
        select(
            Invoice.id,
            Account.name,
            Invoice.invoice_date,
            Invoice.amount_cents,
            Invoice.failure_reason,
            Invoice.source_scenario,
        )
        .join(Account, Account.id == Invoice.account_id)
        .where(Invoice.status == "failed", Invoice.invoice_date >= cutoff)
        .order_by(Invoice.invoice_date.desc(), Invoice.amount_cents.desc(), Invoice.id)
        .limit(8)
    ).all()

    return FailedInvoiceMetrics(
        failed_count_30d=failed_count,
        failed_amount_cents_30d=failed_amount,
        unresolved_count_30d=unresolved_count,
        recent_failures=[
            FailedInvoiceSample(
                invoice_id=row.id,
                account_name=row.name,
                invoice_date=row.invoice_date,
                amount_cents=row.amount_cents,
                failure_reason=row.failure_reason,
                source_scenario=row.source_scenario,
            )
            for row in rows
        ],
    )


def get_ticket_volume_metrics(
    session: Session, anchor: datetime | None = None
) -> TicketVolumeMetrics:
    anchor = anchor or get_dataset_anchor(session)
    cutoff = anchor - timedelta(days=30)

    agg_row = session.execute(
        select(
            func.count(
                case((SupportTicket.created_at >= cutoff, 1), else_=None)
            ).label("total_30d"),
            func.count(
                case(
                    (SupportTicket.status.in_(("open", "pending")), 1),
                    else_=None,
                )
            ).label("open_tickets"),
            func.count(
                case(
                    (
                        SupportTicket.status.in_(("open", "pending"))
                        & (SupportTicket.priority == "high"),
                        1,
                    ),
                    else_=None,
                )
            ).label("high_priority_open"),
        )
    ).one()

    total_30d = int(agg_row.total_30d or 0)
    open_tickets = int(agg_row.open_tickets or 0)
    high_priority_open = int(agg_row.high_priority_open or 0)

    rows = session.execute(
        select(SupportTicket.category, func.count().label("count"))
        .where(SupportTicket.created_at >= cutoff)
        .group_by(SupportTicket.category)
        .order_by(func.count().desc(), SupportTicket.category)
    ).all()

    return TicketVolumeMetrics(
        total_tickets_30d=total_30d,
        open_tickets=open_tickets,
        high_priority_open_tickets=high_priority_open,
        by_category_30d=[
            CategoryCount(category=row.category, count=row.count) for row in rows
        ],
    )


def get_active_user_metrics(
    session: Session, anchor: datetime | None = None
) -> ActiveUserMetrics:
    anchor = anchor or get_dataset_anchor(session)
    cutoff_7d = anchor - timedelta(days=7)
    cutoff_30d = anchor - timedelta(days=30)

    row = session.execute(
        select(
            func.count(
                func.distinct(
                    case(
                        (ProductEvent.event_time >= cutoff_7d, ProductEvent.user_id),
                        else_=None,
                    )
                )
            ).label("active_users_7d"),
            func.count(
                func.distinct(
                    case(
                        (ProductEvent.event_time >= cutoff_30d, ProductEvent.user_id),
                        else_=None,
                    )
                )
            ).label("active_users_30d"),
            func.count(
                case((ProductEvent.event_time >= cutoff_7d, 1), else_=None)
            ).label("event_count_7d"),
            func.count(
                case((ProductEvent.event_time >= cutoff_30d, 1), else_=None)
            ).label("event_count_30d"),
        )
    ).one()

    return ActiveUserMetrics(
        active_users_7d=int(row.active_users_7d or 0),
        active_users_30d=int(row.active_users_30d or 0),
        event_count_7d=int(row.event_count_7d or 0),
        event_count_30d=int(row.event_count_30d or 0),
    )


def _build_dashboard_metrics(session: Session) -> DashboardMetrics:
    anchor = get_dataset_anchor(session)
    return DashboardMetrics(
        as_of=anchor,
        mrr=get_mrr_metrics(session, anchor),
        churn=get_churn_metrics(session, anchor),
        failed_invoices=get_failed_invoice_metrics(session, anchor),
        ticket_volume=get_ticket_volume_metrics(session, anchor),
        active_users=get_active_user_metrics(session, anchor),
    )


@cache_result(
    key="metrics:dashboard",
    ttl_seconds=60,
    dump=lambda metrics: metrics.model_dump(mode="json"),
    load=DashboardMetrics.model_validate,
)
def get_dashboard_metrics(session: Session) -> DashboardMetrics:
    return _build_dashboard_metrics(session)
