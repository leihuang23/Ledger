from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.types import EmbeddingVector

KNOWLEDGE_EMBEDDING_DIMENSIONS = 96


class Account(Base):
    __tablename__ = "accounts"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    segment: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    industry: Mapped[str] = mapped_column(String(80), nullable=False)
    region: Mapped[str] = mapped_column(String(80), nullable=False)
    health_score: Mapped[int] = mapped_column(Integer, nullable=False)
    source_scenario: Mapped[str | None] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    users: Mapped[list[User]] = relationship(
        back_populates="account", cascade="all, delete-orphan"
    )
    subscriptions: Mapped[list[Subscription]] = relationship(
        back_populates="account", cascade="all, delete-orphan"
    )
    invoices: Mapped[list[Invoice]] = relationship(
        back_populates="account", cascade="all, delete-orphan"
    )
    product_events: Mapped[list[ProductEvent]] = relationship(
        back_populates="account", cascade="all, delete-orphan"
    )
    support_tickets: Mapped[list[SupportTicket]] = relationship(
        back_populates="account", cascade="all, delete-orphan"
    )


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    account_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    email: Mapped[str] = mapped_column(String(160), nullable=False, unique=True)
    full_name: Mapped[str] = mapped_column(String(120), nullable=False)
    role: Mapped[str] = mapped_column(String(40), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    account: Mapped[Account] = relationship(back_populates="users")
    product_events: Mapped[list[ProductEvent]] = relationship(back_populates="user")
    support_tickets: Mapped[list[SupportTicket]] = relationship(back_populates="user")


class Subscription(Base):
    __tablename__ = "subscriptions"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    account_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    plan: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    mrr_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    seats: Mapped[int] = mapped_column(Integer, nullable=False)
    started_at: Mapped[date] = mapped_column(Date, nullable=False)
    canceled_at: Mapped[date | None] = mapped_column(Date)
    cancellation_reason: Mapped[str | None] = mapped_column(String(160))
    source_scenario: Mapped[str | None] = mapped_column(String(80))

    account: Mapped[Account] = relationship(back_populates="subscriptions")
    invoices: Mapped[list[Invoice]] = relationship(back_populates="subscription")


class Invoice(Base):
    __tablename__ = "invoices"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    account_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    subscription_id: Mapped[str] = mapped_column(
        String(32),
        ForeignKey("subscriptions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    invoice_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    due_date: Mapped[date] = mapped_column(Date, nullable=False)
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)
    amount_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    failure_reason: Mapped[str | None] = mapped_column(String(160))
    paid_at: Mapped[datetime | None] = mapped_column(DateTime)
    source_scenario: Mapped[str | None] = mapped_column(String(80))

    account: Mapped[Account] = relationship(back_populates="invoices")
    subscription: Mapped[Subscription] = relationship(back_populates="invoices")


class ProductEvent(Base):
    __tablename__ = "product_events"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    account_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    event_time: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    event_name: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    source: Mapped[str] = mapped_column(String(40), nullable=False)
    source_scenario: Mapped[str | None] = mapped_column(String(80), index=True)
    event_metadata: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSON, nullable=False, default=dict
    )

    account: Mapped[Account] = relationship(back_populates="product_events")
    user: Mapped[User | None] = relationship(back_populates="product_events")


class SupportTicket(Base):
    __tablename__ = "support_tickets"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    account_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    priority: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    category: Mapped[str] = mapped_column(String(60), nullable=False, index=True)
    subject: Mapped[str] = mapped_column(String(180), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    sentiment: Mapped[str] = mapped_column(String(32), nullable=False)
    source_scenario: Mapped[str | None] = mapped_column(String(80), index=True)

    account: Mapped[Account] = relationship(back_populates="support_tickets")
    user: Mapped[User | None] = relationship(back_populates="support_tickets")


class Incident(Base):
    __tablename__ = "incidents"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    title: Mapped[str] = mapped_column(String(180), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    severity: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    anomaly_type: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    metric_name: Mapped[str] = mapped_column(String(80), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    source_scenario: Mapped[str | None] = mapped_column(String(80), index=True)
    detected_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    current_value_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    previous_value_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    delta_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    delta_percent: Mapped[float] = mapped_column(Float, nullable=False)
    affected_account_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    evidence: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    agent_runs: Mapped[list[AgentRun]] = relationship(
        back_populates="incident", cascade="all, delete-orphan"
    )


class KnowledgeDocument(Base):
    __tablename__ = "knowledge_documents"

    id: Mapped[str] = mapped_column(String(96), primary_key=True)
    title: Mapped[str] = mapped_column(String(180), nullable=False)
    document_type: Mapped[str] = mapped_column(String(60), nullable=False, index=True)
    owner: Mapped[str | None] = mapped_column(String(120))
    source_path: Mapped[str] = mapped_column(String(240), nullable=False, unique=True)
    source_uri: Mapped[str | None] = mapped_column(String(240))
    checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    document_metadata: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSON, nullable=False, default=dict
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    chunks: Mapped[list[KnowledgeDocumentChunk]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )


class KnowledgeDocumentChunk(Base):
    __tablename__ = "knowledge_document_chunks"

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    document_id: Mapped[str] = mapped_column(
        String(96),
        ForeignKey("knowledge_documents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    heading_path: Mapped[str] = mapped_column(String(240), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    token_count: Mapped[int] = mapped_column(Integer, nullable=False)
    embedding: Mapped[list[float]] = mapped_column(
        EmbeddingVector(KNOWLEDGE_EMBEDDING_DIMENSIONS), nullable=False
    )
    citation_metadata: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False, default=dict
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    document: Mapped[KnowledgeDocument] = relationship(back_populates="chunks")


class AgentRun(Base):
    __tablename__ = "agent_runs"

    id: Mapped[str] = mapped_column(String(48), primary_key=True)
    incident_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("incidents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    trace_id: Mapped[str | None] = mapped_column(String(96), index=True)
    input_payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    final_report: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    token_estimate: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cost_estimate_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    incident: Mapped[Incident] = relationship(back_populates="agent_runs")
    steps: Mapped[list[AgentRunStep]] = relationship(
        back_populates="run", cascade="all, delete-orphan", order_by="AgentRunStep.sequence"
    )
    mock_actions: Mapped[list[MockAction]] = relationship(
        back_populates="run", cascade="all, delete-orphan", order_by="MockAction.created_at"
    )
    approval_requests: Mapped[list[ApprovalRequest]] = relationship(
        back_populates="run",
        cascade="all, delete-orphan",
        order_by="ApprovalRequest.created_at",
    )
    action_audit_events: Mapped[list[ActionAuditEvent]] = relationship(
        back_populates="run",
        cascade="all, delete-orphan",
        order_by="ActionAuditEvent.created_at",
    )


class AgentRunStep(Base):
    __tablename__ = "agent_run_steps"
    __table_args__ = (
        UniqueConstraint("run_id", "sequence", name="uq_agent_run_steps_run_sequence"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    run_id: Mapped[str] = mapped_column(
        String(48), ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    stage: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    tool_name: Mapped[str | None] = mapped_column(String(80), index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    inputs: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    outputs: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    run: Mapped[AgentRun] = relationship(back_populates="steps")


class MockAction(Base):
    __tablename__ = "mock_actions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    run_id: Mapped[str] = mapped_column(
        String(48), ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    action_type: Mapped[str] = mapped_column(String(48), nullable=False, index=True)
    risk_level: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(180), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    target: Mapped[str] = mapped_column(String(180), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_by: Mapped[str] = mapped_column(String(80), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    executed_at: Mapped[datetime | None] = mapped_column(DateTime)

    run: Mapped[AgentRun] = relationship(back_populates="mock_actions")
    approval_request: Mapped[ApprovalRequest | None] = relationship(
        back_populates="action", cascade="all, delete-orphan", uselist=False
    )
    audit_events: Mapped[list[ActionAuditEvent]] = relationship(
        back_populates="action",
        cascade="all, delete-orphan",
        order_by="ActionAuditEvent.created_at",
    )


class ApprovalRequest(Base):
    __tablename__ = "approval_requests"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    run_id: Mapped[str] = mapped_column(
        String(48), ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    action_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("mock_actions.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    risk_level: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    requested_by: Mapped[str] = mapped_column(String(80), nullable=False)
    decided_by: Mapped[str | None] = mapped_column(String(80))
    decision_notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime)

    run: Mapped[AgentRun] = relationship(back_populates="approval_requests")
    action: Mapped[MockAction] = relationship(back_populates="approval_request")
    audit_events: Mapped[list[ActionAuditEvent]] = relationship(
        back_populates="approval_request",
        cascade="all, delete-orphan",
        order_by="ActionAuditEvent.created_at",
    )


class ActionAuditEvent(Base):
    __tablename__ = "action_audit_events"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    run_id: Mapped[str] = mapped_column(
        String(48), ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    action_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("mock_actions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    approval_request_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("approval_requests.id", ondelete="SET NULL"), index=True
    )
    event_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    actor: Mapped[str] = mapped_column(String(80), nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)
    event_metadata: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSON, nullable=False, default=dict
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    run: Mapped[AgentRun] = relationship(back_populates="action_audit_events")
    action: Mapped[MockAction] = relationship(back_populates="audit_events")
    approval_request: Mapped[ApprovalRequest | None] = relationship(
        back_populates="audit_events"
    )


Index("ix_invoices_account_date", Invoice.account_id, Invoice.invoice_date)
Index("ix_product_events_account_time", ProductEvent.account_id, ProductEvent.event_time)
Index("ix_support_tickets_account_created", SupportTicket.account_id, SupportTicket.created_at)
Index(
    "ix_knowledge_document_chunks_document_index",
    KnowledgeDocumentChunk.document_id,
    KnowledgeDocumentChunk.chunk_index,
    unique=True,
)
Index("ix_agent_run_steps_run_stage", AgentRunStep.run_id, AgentRunStep.stage)
Index("ix_mock_actions_run_status", MockAction.run_id, MockAction.status)
Index("ix_approval_requests_run_status", ApprovalRequest.run_id, ApprovalRequest.status)
Index("ix_action_audit_events_run_created", ActionAuditEvent.run_id, ActionAuditEvent.created_at)
