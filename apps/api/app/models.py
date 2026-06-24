from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import Boolean, Date, DateTime, Float, ForeignKey, Index, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


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


Index("ix_invoices_account_date", Invoice.account_id, Invoice.invoice_date)
Index("ix_product_events_account_time", ProductEvent.account_id, ProductEvent.event_time)
Index("ix_support_tickets_account_created", SupportTicket.account_id, SupportTicket.created_at)
