from __future__ import annotations

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from app.models import Account, SupportTicket
from app.support.schemas import SupportTicketList, SupportTicketRead


def list_support_tickets(
    session: Session,
    *,
    account_id: str | None = None,
    status: str | None = None,
    category: str | None = None,
    source_scenario: str | None = None,
    limit: int = 50,
) -> SupportTicketList:
    query = (
        select(SupportTicket, Account.name.label("account_name"))
        .join(Account, Account.id == SupportTicket.account_id)
        .order_by(SupportTicket.created_at.desc(), SupportTicket.id.desc())
    )
    query = _apply_filters(
        query,
        account_id=account_id,
        status=status,
        category=category,
        source_scenario=source_scenario,
    )
    count_query = _apply_filters(
        select(func.count(SupportTicket.id))
        .select_from(SupportTicket),
        account_id=account_id,
        status=status,
        category=category,
        source_scenario=source_scenario,
    )
    total = int(session.scalar(count_query) or 0)
    rows = session.execute(query.limit(limit)).all()

    return SupportTicketList(
        total=total,
        tickets=[
            SupportTicketRead(
                id=ticket.id,
                account_id=ticket.account_id,
                account_name=account_name,
                user_id=ticket.user_id,
                created_at=ticket.created_at,
                resolved_at=ticket.resolved_at,
                status=ticket.status,
                priority=ticket.priority,
                category=ticket.category,
                subject=ticket.subject,
                description=ticket.description,
                sentiment=ticket.sentiment,
                source_scenario=ticket.source_scenario,
            )
            for ticket, account_name in rows
        ],
    )


def get_support_ticket(session: Session, ticket_id: str) -> SupportTicketRead | None:
    row = session.execute(
        select(SupportTicket, Account.name.label("account_name"))
        .join(Account, Account.id == SupportTicket.account_id)
        .where(SupportTicket.id == ticket_id)
    ).first()
    if row is None:
        return None
    ticket, account_name = row
    return SupportTicketRead(
        id=ticket.id,
        account_id=ticket.account_id,
        account_name=account_name,
        user_id=ticket.user_id,
        created_at=ticket.created_at,
        resolved_at=ticket.resolved_at,
        status=ticket.status,
        priority=ticket.priority,
        category=ticket.category,
        subject=ticket.subject,
        description=ticket.description,
        sentiment=ticket.sentiment,
        source_scenario=ticket.source_scenario,
    )


def _apply_filters(
    query: Select,
    *,
    account_id: str | None,
    status: str | None,
    category: str | None,
    source_scenario: str | None,
) -> Select:
    if account_id is not None:
        query = query.where(SupportTicket.account_id == account_id)
    if status is not None:
        query = query.where(SupportTicket.status == status)
    if category is not None:
        query = query.where(SupportTicket.category == category)
    if source_scenario is not None:
        query = query.where(SupportTicket.source_scenario == source_scenario)
    return query
