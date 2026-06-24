from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

ActionType = Literal[
    "draft_slack_message",
    "draft_customer_email",
    "create_task",
    "update_account_note",
]
RiskLevel = Literal["low", "high"]
MockActionStatus = Literal["pending_approval", "executed", "rejected"]
ApprovalStatus = Literal["pending", "approved", "rejected"]
AuditEventType = Literal["proposed", "approved", "rejected", "executed"]


class MockActionCreate(BaseModel):
    run_id: str = Field(min_length=1, max_length=48)
    action_type: ActionType
    title: str = Field(min_length=1, max_length=180)
    description: str = Field(min_length=1)
    target: str = Field(min_length=1, max_length=180)
    payload: dict[str, Any] = Field(default_factory=dict)


class ApprovalDecisionCreate(BaseModel):
    notes: str | None = None


class ActionAuditEventRead(BaseModel):
    id: str
    run_id: str
    action_id: str
    approval_request_id: str | None
    event_type: AuditEventType
    actor: str
    notes: str | None
    metadata: dict[str, Any]
    created_at: datetime


class ApprovalRequestSummary(BaseModel):
    id: str
    run_id: str
    action_id: str
    status: ApprovalStatus
    risk_level: RiskLevel
    reason: str
    requested_by: str
    decided_by: str | None
    decision_notes: str | None
    created_at: datetime
    decided_at: datetime | None


class MockActionRead(BaseModel):
    id: str
    run_id: str
    action_type: ActionType
    risk_level: RiskLevel
    status: MockActionStatus
    title: str
    description: str
    target: str
    payload: dict[str, Any]
    created_by: str
    created_at: datetime
    updated_at: datetime
    executed_at: datetime | None
    approval_request: ApprovalRequestSummary | None
    audit_events: list[ActionAuditEventRead]


class ApprovalRequestRead(ApprovalRequestSummary):
    action: MockActionRead
