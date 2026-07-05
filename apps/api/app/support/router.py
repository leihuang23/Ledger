from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.access import require_demo_data_access
from app.db.session import get_db
from app.support.schemas import SupportTicketList, SupportTicketRead
from app.support.service import get_support_ticket, list_support_tickets

router = APIRouter(
    prefix="/support",
    tags=["support"],
    dependencies=[Depends(require_demo_data_access)],
)


@router.get("/tickets/{ticket_id}")
def support_ticket_detail(ticket_id: str, db: Session = Depends(get_db)) -> SupportTicketRead:
    ticket = get_support_ticket(db, ticket_id)
    if ticket is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Unknown ticket id: {ticket_id}",
        )
    return ticket


@router.get("/tickets")
def support_tickets(
    account_id: str | None = None,
    status: str | None = None,
    category: str | None = None,
    source_scenario: str | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    db: Session = Depends(get_db),
) -> SupportTicketList:
    return list_support_tickets(
        db,
        account_id=account_id,
        status=status,
        category=category,
        source_scenario=source_scenario,
        limit=limit,
    )
