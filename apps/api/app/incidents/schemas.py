from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel


class MetricEvidence(BaseModel):
    metric_name: str
    current_window_start: date
    current_window_end: date
    previous_window_start: date
    previous_window_end: date
    current_value_cents: int
    previous_value_cents: int
    delta_cents: int
    delta_percent: float
    failed_invoice_cents: int
    failed_invoice_count: int
    invoice_ids: list[str]


class AffectedAccount(BaseModel):
    account_id: str
    account_name: str
    segment: str
    health_score: int
    failed_invoice_cents: int
    failed_invoice_count: int
    failed_invoice_ids: list[str]
    source_scenario: str | None


class SupportSignal(BaseModel):
    ticket_id: str
    account_id: str
    account_name: str
    created_at: datetime
    status: str
    priority: str
    category: str
    subject: str
    sentiment: str
    source_scenario: str | None


class ProductSignal(BaseModel):
    event_name: str
    event_count: int
    affected_accounts: int
    latest_event_at: datetime
    source_scenario: str | None


class RevenueAnomaly(BaseModel):
    id: str
    title: str
    anomaly_type: str
    severity: str
    detected_at: datetime
    summary: str
    metric_evidence: MetricEvidence
    affected_accounts: list[AffectedAccount]
    support_signals: list[SupportSignal]
    product_signals: list[ProductSignal]
    incident_id: str | None


class IncidentCreate(BaseModel):
    anomaly_id: str


class IncidentSummary(BaseModel):
    id: str
    title: str
    status: str
    severity: str
    anomaly_type: str
    detected_at: datetime
    summary: str
    affected_account_count: int


class IncidentDetail(BaseModel):
    id: str
    title: str
    status: str
    severity: str
    anomaly_type: str
    detected_at: datetime
    summary: str
    source_scenario: str | None
    metric_evidence: MetricEvidence
    affected_accounts: list[AffectedAccount]
    support_signals: list[SupportSignal]
    product_signals: list[ProductSignal]
    evidence: dict[str, Any]
