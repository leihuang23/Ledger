from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.incidents.constants import (
    PAID_INVOICE_MRR_METRIC,
    REVENUE_MRR_DROP_ANOMALY_TYPE,
    anomaly_id_for_window,
    incident_id_for_anomaly,
    revenue_week_windows,
)
from app.incidents.schemas import (
    AffectedAccount,
    IncidentDetail,
    IncidentSummary,
    MetricEvidence,
    ProductSignal,
    RevenueAnomaly,
    SupportSignal,
)
from app.metrics.service import get_dataset_anchor
from app.models import Account, Incident, Invoice, ProductEvent, Subscription, SupportTicket


def detect_revenue_anomalies(session: Session) -> list[RevenueAnomaly]:
    anchor = get_dataset_anchor(session)
    windows = revenue_week_windows(anchor)
    previous_paid_mrr = _paid_invoice_mrr(
        session, windows.previous_start, windows.current_start
    )
    current_paid_mrr = _paid_invoice_mrr(
        session, windows.current_start, windows.current_end_exclusive
    )
    affected_accounts = _current_failed_invoice_accounts(
        session, windows.current_start, windows.current_end_exclusive
    )

    if previous_paid_mrr <= 0 or current_paid_mrr >= previous_paid_mrr:
        return []
    if not affected_accounts:
        return []

    invoice_ids = [
        invoice_id
        for account in affected_accounts
        for invoice_id in account.failed_invoice_ids
    ]
    failed_invoice_cents = sum(
        account.failed_invoice_cents for account in affected_accounts
    )
    failed_invoice_count = sum(
        account.failed_invoice_count for account in affected_accounts
    )
    delta_cents = current_paid_mrr - previous_paid_mrr
    delta_percent = round((delta_cents / previous_paid_mrr) * 100, 2)
    anomaly_id = anomaly_id_for_window(windows.current_start)
    incident_id = incident_id_for_anomaly(anomaly_id)
    existing_incident = session.get(Incident, incident_id)

    metric_evidence = MetricEvidence(
        metric_name=PAID_INVOICE_MRR_METRIC,
        current_window_start=windows.current_start,
        current_window_end=windows.current_end,
        previous_window_start=windows.previous_start,
        previous_window_end=windows.previous_end,
        current_value_cents=current_paid_mrr,
        previous_value_cents=previous_paid_mrr,
        delta_cents=delta_cents,
        delta_percent=delta_percent,
        failed_invoice_cents=failed_invoice_cents,
        failed_invoice_count=failed_invoice_count,
        invoice_ids=invoice_ids,
    )
    account_ids = [account.account_id for account in affected_accounts]
    support_signals = _support_signals(session, account_ids, anchor)
    product_signals = _product_signals(session, account_ids, anchor)

    return [
        RevenueAnomaly(
            id=anomaly_id,
            title="Week-over-week paid MRR dropped after failed renewals",
            anomaly_type=REVENUE_MRR_DROP_ANOMALY_TYPE,
            severity=_severity(delta_percent, failed_invoice_cents),
            detected_at=anchor,
            summary=(
                "Paid invoice MRR fell week over week while renewal invoices failed "
                "for affected accounts."
            ),
            metric_evidence=metric_evidence,
            affected_accounts=affected_accounts,
            support_signals=support_signals,
            product_signals=product_signals,
            incident_id=existing_incident.id if existing_incident else None,
        )
    ]


def list_incidents(session: Session) -> list[IncidentSummary]:
    rows = session.scalars(
        select(Incident).order_by(Incident.detected_at.desc(), Incident.id)
    ).all()
    return [
        IncidentSummary(
            id=incident.id,
            title=incident.title,
            status=incident.status,
            severity=incident.severity,
            anomaly_type=incident.anomaly_type,
            detected_at=incident.detected_at,
            summary=incident.summary,
            affected_account_count=len(incident.affected_account_ids),
        )
        for incident in rows
    ]


def create_or_get_incident_from_anomaly(
    session: Session, anomaly_id: str
) -> tuple[IncidentDetail, bool]:
    incident_id = incident_id_for_anomaly(anomaly_id)
    existing_incident = session.get(Incident, incident_id)
    if existing_incident:
        return _incident_detail(session, existing_incident), False

    anomaly = next(
        (
            candidate
            for candidate in detect_revenue_anomalies(session)
            if candidate.id == anomaly_id
        ),
        None,
    )
    if anomaly is None:
        raise LookupError(f"Unknown anomaly id: {anomaly_id}")

    now = datetime.now(UTC).replace(tzinfo=None)
    source_scenario = _source_scenario(anomaly.affected_accounts)
    incident = Incident(
        id=incident_id,
        title=anomaly.title,
        status="open",
        severity=anomaly.severity,
        anomaly_type=anomaly.anomaly_type,
        metric_name=anomaly.metric_evidence.metric_name,
        summary=anomaly.summary,
        source_scenario=source_scenario,
        detected_at=anomaly.detected_at,
        current_value_cents=anomaly.metric_evidence.current_value_cents,
        previous_value_cents=anomaly.metric_evidence.previous_value_cents,
        delta_cents=anomaly.metric_evidence.delta_cents,
        delta_percent=anomaly.metric_evidence.delta_percent,
        affected_account_ids=[
            account.account_id for account in anomaly.affected_accounts
        ],
        evidence=_anomaly_evidence(anomaly),
        created_at=now,
        updated_at=now,
    )
    session.add(incident)
    try:
        session.commit()
        session.refresh(incident)
    except IntegrityError:
        session.rollback()
        raced_incident = session.get(Incident, incident_id)
        if raced_incident is None:
            raise
        return _incident_detail(session, raced_incident), False

    return _incident_detail(session, incident), True


def get_incident_detail(session: Session, incident_id: str) -> IncidentDetail | None:
    incident = session.get(Incident, incident_id)
    if incident is None:
        return None
    return _incident_detail(session, incident)


def _paid_invoice_mrr(session: Session, start_date: object, end_date: object) -> int:
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


def _current_failed_invoice_accounts(
    session: Session, start_date: object, end_date: object
) -> list[AffectedAccount]:
    rows = session.execute(
        select(
            Invoice.account_id.label("account_id"),
            Account.name.label("account_name"),
            Account.segment.label("segment"),
            Account.health_score.label("health_score"),
            Account.source_scenario.label("source_scenario"),
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
        .group_by(
            Invoice.account_id,
            Account.name,
            Account.segment,
            Account.health_score,
            Account.source_scenario,
        )
        .order_by(func.sum(Invoice.amount_cents).desc(), Account.name)
    ).all()
    invoice_ids_by_account = _failed_invoice_ids_by_account(session, start_date, end_date)

    return [
        AffectedAccount(
            account_id=row.account_id,
            account_name=row.account_name,
            segment=row.segment,
            health_score=row.health_score,
            failed_invoice_cents=int(row.failed_invoice_cents or 0),
            failed_invoice_count=int(row.failed_invoice_count or 0),
            failed_invoice_ids=invoice_ids_by_account.get(row.account_id, []),
            source_scenario=row.source_scenario,
        )
        for row in rows
    ]


def _failed_invoice_ids_by_account(
    session: Session, start_date: object, end_date: object
) -> dict[str, list[str]]:
    rows = session.execute(
        select(Invoice.account_id, Invoice.id)
        .where(
            Invoice.status == "failed",
            Invoice.invoice_date >= start_date,
            Invoice.invoice_date < end_date,
        )
        .order_by(Invoice.account_id, Invoice.id)
    ).all()
    invoice_ids: dict[str, list[str]] = {}
    for account_id, invoice_id in rows:
        invoice_ids.setdefault(account_id, []).append(invoice_id)
    return invoice_ids


def _support_signals(
    session: Session, account_ids: list[str], anchor: datetime
) -> list[SupportSignal]:
    if not account_ids:
        return []

    rows = session.execute(
        select(
            SupportTicket.id.label("ticket_id"),
            SupportTicket.account_id.label("account_id"),
            Account.name.label("account_name"),
            SupportTicket.created_at.label("created_at"),
            SupportTicket.status.label("status"),
            SupportTicket.priority.label("priority"),
            SupportTicket.category.label("category"),
            SupportTicket.subject.label("subject"),
            SupportTicket.sentiment.label("sentiment"),
            SupportTicket.source_scenario.label("source_scenario"),
        )
        .join(Account, Account.id == SupportTicket.account_id)
        .where(
            SupportTicket.account_id.in_(account_ids),
            SupportTicket.created_at >= anchor - timedelta(days=30),
        )
        .order_by(SupportTicket.created_at.desc(), SupportTicket.id)
        .limit(12)
    ).all()

    return [
        SupportSignal(
            ticket_id=row.ticket_id,
            account_id=row.account_id,
            account_name=row.account_name,
            created_at=row.created_at,
            status=row.status,
            priority=row.priority,
            category=row.category,
            subject=row.subject,
            sentiment=row.sentiment,
            source_scenario=row.source_scenario,
        )
        for row in rows
    ]


def _product_signals(
    session: Session, account_ids: list[str], anchor: datetime
) -> list[ProductSignal]:
    if not account_ids:
        return []

    rows = session.execute(
        select(
            ProductEvent.event_name.label("event_name"),
            ProductEvent.source_scenario.label("source_scenario"),
            func.count(ProductEvent.id).label("event_count"),
            func.count(func.distinct(ProductEvent.account_id)).label(
                "affected_accounts"
            ),
            func.max(ProductEvent.event_time).label("latest_event_at"),
        )
        .where(
            ProductEvent.account_id.in_(account_ids),
            ProductEvent.event_time >= anchor - timedelta(days=30),
        )
        .group_by(ProductEvent.event_name, ProductEvent.source_scenario)
        .order_by(func.count(ProductEvent.id).desc(), ProductEvent.event_name)
        .limit(8)
    ).all()

    return [
        ProductSignal(
            event_name=row.event_name,
            event_count=int(row.event_count or 0),
            affected_accounts=int(row.affected_accounts or 0),
            latest_event_at=row.latest_event_at,
            source_scenario=row.source_scenario,
        )
        for row in rows
    ]


def _incident_detail(session: Session, incident: Incident) -> IncidentDetail:
    evidence = incident.evidence or {}
    account_ids = list(incident.affected_account_ids or [])
    metric_evidence = MetricEvidence(**evidence["metric_evidence"])
    affected_accounts = [
        AffectedAccount(**account) for account in evidence.get("affected_accounts", [])
    ]

    return IncidentDetail(
        id=incident.id,
        title=incident.title,
        status=incident.status,
        severity=incident.severity,
        anomaly_type=incident.anomaly_type,
        detected_at=incident.detected_at,
        summary=incident.summary,
        source_scenario=incident.source_scenario,
        metric_evidence=metric_evidence,
        affected_accounts=affected_accounts,
        support_signals=_support_signals(session, account_ids, incident.detected_at),
        product_signals=_product_signals(session, account_ids, incident.detected_at),
        evidence=evidence,
    )


def _anomaly_evidence(anomaly: RevenueAnomaly) -> dict[str, Any]:
    return {
        "anomaly_id": anomaly.id,
        "metric_evidence": anomaly.metric_evidence.model_dump(mode="json"),
        "affected_accounts": [
            account.model_dump(mode="json") for account in anomaly.affected_accounts
        ],
        "support_ticket_ids": [
            signal.ticket_id for signal in anomaly.support_signals
        ],
        "product_event_names": [
            signal.event_name for signal in anomaly.product_signals
        ],
        "source_queries": [
            "paid invoices joined to subscriptions in current and previous 7-day windows",
            "failed current-window renewal invoices grouped by account",
            "support tickets and product events for affected accounts in the last 30 days",
        ],
    }


def _severity(delta_percent: float, failed_invoice_cents: int) -> str:
    if delta_percent <= -40 or failed_invoice_cents >= 5_000_000:
        return "high"
    if delta_percent <= -15 or failed_invoice_cents >= 1_000_000:
        return "medium"
    return "low"


def _source_scenario(accounts: list[AffectedAccount]) -> str | None:
    scenarios = {
        account.source_scenario
        for account in accounts
        if account.source_scenario is not None
    }
    if len(scenarios) == 1:
        return next(iter(scenarios))
    if len(scenarios) > 1:
        return "multiple"
    return None
