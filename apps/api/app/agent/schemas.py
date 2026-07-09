from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.approvals.schemas import MockActionRead


class AgentInvestigationCreate(BaseModel):
    incident_id: str = Field(min_length=1, max_length=64)
    force: bool = False
    run_inline: bool = False
    idempotency_key: str | None = Field(default=None, max_length=128)
    agent_version_id: str | None = None


class ReportAffectedAccount(BaseModel):
    account_id: str
    account_name: str
    segment: str
    health_score: int
    failed_invoice_cents: int
    failed_invoice_ids: list[str]
    ticket_ids: list[str] = Field(default_factory=list)


class ReportEvidence(BaseModel):
    kind: Literal["sql", "document", "ticket", "tool"]
    title: str
    summary: str
    reference_id: str
    source_query: str | None = None
    citation: dict[str, Any] = Field(default_factory=dict)


class ReportClaim(BaseModel):
    category: Literal["root_cause", "impact", "recommendation", "uncertainty"]
    text: str
    citation_refs: list[str] = Field(default_factory=list)


class InvestigationReport(BaseModel):
    root_cause: str
    summary: str
    affected_accounts: list[ReportAffectedAccount]
    cited_evidence: list[ReportEvidence]
    claims: list[ReportClaim] = Field(default_factory=list)
    confidence: Literal["low", "medium", "high"]
    next_actions: list[str]
    generated_at: datetime


class AgentRunStepRead(BaseModel):
    id: str
    run_id: str
    sequence: int
    stage: str
    tool_name: str | None
    status: Literal["running", "succeeded", "failed", "blocked"]
    inputs: dict[str, Any]
    outputs: dict[str, Any] | None
    error: str | None
    blocked_reason: str | None = None
    started_at: datetime
    completed_at: datetime | None


class AgentRunSummary(BaseModel):
    id: str
    incident_id: str | None
    agent_id: str
    agent_version_id: str
    status: Literal[
        "queued", "running", "waiting_for_approval", "succeeded", "failed"
    ]
    trace_id: str | None
    trace_url: str | None
    trace_provider: str | None
    token_estimate: int
    prompt_tokens: int
    completion_tokens: int
    cost_estimate_usd: float
    error: str | None
    started_at: datetime | None
    completed_at: datetime | None
    created_at: datetime
    updated_at: datetime


class AgentRunDetail(BaseModel):
    id: str
    incident_id: str | None
    agent_id: str
    agent_version_id: str
    agent: dict[str, Any] | None = None
    agent_version: dict[str, Any] | None = None
    status: Literal[
        "queued", "running", "waiting_for_approval", "succeeded", "failed"
    ]
    is_stale: bool = False
    trace_id: str | None
    trace_url: str | None
    trace_provider: str | None
    trace_metadata: dict[str, Any] = Field(default_factory=dict)
    token_estimate: int
    prompt_tokens: int
    completion_tokens: int
    cost_estimate_usd: float
    input_payload: dict[str, Any]
    final_report: InvestigationReport | None
    error: str | None
    started_at: datetime | None
    completed_at: datetime | None
    created_at: datetime
    updated_at: datetime
    steps: list[AgentRunStepRead]
    mock_actions: list[MockActionRead] = Field(default_factory=list)
