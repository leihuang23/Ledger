from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class AgentInvestigationCreate(BaseModel):
    incident_id: str = Field(min_length=1, max_length=64)
    force: bool = False


class ReportAffectedAccount(BaseModel):
    account_id: str
    account_name: str
    segment: str
    health_score: int
    failed_invoice_cents: int
    failed_invoice_ids: list[str]
    ticket_ids: list[str] = Field(default_factory=list)


class ReportEvidence(BaseModel):
    kind: Literal["sql", "document", "ticket"]
    title: str
    summary: str
    reference_id: str
    source_query: str | None = None
    citation: dict[str, Any] = Field(default_factory=dict)


class InvestigationReport(BaseModel):
    root_cause: str
    summary: str
    affected_accounts: list[ReportAffectedAccount]
    cited_evidence: list[ReportEvidence]
    confidence: Literal["low", "medium", "high"]
    next_actions: list[str]
    generated_at: datetime


class AgentRunStepRead(BaseModel):
    id: str
    run_id: str
    sequence: int
    stage: str
    tool_name: str | None
    status: Literal["running", "succeeded", "failed"]
    inputs: dict[str, Any]
    outputs: dict[str, Any] | None
    error: str | None
    started_at: datetime
    completed_at: datetime | None


class AgentRunDetail(BaseModel):
    id: str
    incident_id: str
    status: Literal["running", "succeeded", "failed"]
    trace_id: str | None
    token_estimate: int
    cost_estimate_usd: float
    input_payload: dict[str, Any]
    final_report: InvestigationReport | None
    error: str | None
    started_at: datetime | None
    completed_at: datetime | None
    created_at: datetime
    updated_at: datetime
    steps: list[AgentRunStepRead]
