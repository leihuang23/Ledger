from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class SupportTicketRead(BaseModel):
    id: str
    account_id: str
    account_name: str
    user_id: str | None
    created_at: datetime
    resolved_at: datetime | None
    status: str
    priority: str
    category: str
    subject: str
    description: str
    sentiment: str
    source_scenario: str | None


class SupportTicketList(BaseModel):
    total: int
    tickets: list[SupportTicketRead]
