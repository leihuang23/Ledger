"""add stripe test-mode evidence adapter tables and external ids

Revision ID: 20260803_0019
Revises: 20260719_0018
Create Date: 2026-08-03 00:00:00.000000

Optional Stripe test-mode evidence adapter: external id columns on accounts,
subscriptions, and invoices; webhook event idempotency table; visible
ingestion logs; reconciliation run audit rows. Live credentials remain out
of scope; schema only.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "20260803_0019"
down_revision: str | None = "20260719_0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("accounts") as batch_op:
        batch_op.add_column(sa.Column("stripe_customer_id", sa.String(length=128), nullable=True))
        batch_op.add_column(sa.Column("stripe_object_updated_at", sa.DateTime(), nullable=True))
        batch_op.create_index(
            batch_op.f("ix_accounts_stripe_customer_id"),
            ["stripe_customer_id"],
            unique=True,
        )

    with op.batch_alter_table("subscriptions") as batch_op:
        batch_op.add_column(
            sa.Column("stripe_subscription_id", sa.String(length=128), nullable=True)
        )
        batch_op.add_column(sa.Column("stripe_object_updated_at", sa.DateTime(), nullable=True))
        batch_op.create_index(
            batch_op.f("ix_subscriptions_stripe_subscription_id"),
            ["stripe_subscription_id"],
            unique=True,
        )

    with op.batch_alter_table("invoices") as batch_op:
        batch_op.add_column(
            sa.Column("stripe_invoice_id", sa.String(length=128), nullable=True)
        )
        batch_op.add_column(sa.Column("stripe_object_updated_at", sa.DateTime(), nullable=True))
        batch_op.create_index(
            batch_op.f("ix_invoices_stripe_invoice_id"),
            ["stripe_invoice_id"],
            unique=True,
        )

    op.create_table(
        "stripe_events",
        sa.Column("id", sa.String(length=128), nullable=False),
        sa.Column("event_type", sa.String(length=120), nullable=False),
        sa.Column("api_version", sa.String(length=40), nullable=True),
        sa.Column("livemode", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("stripe_created_at", sa.DateTime(), nullable=False),
        sa.Column("received_at", sa.DateTime(), nullable=False),
        sa.Column("processed_at", sa.DateTime(), nullable=True),
        sa.Column("object_id", sa.String(length=128), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_stripe_events")),
    )
    op.create_index(op.f("ix_stripe_events_event_type"), "stripe_events", ["event_type"])
    op.create_index(op.f("ix_stripe_events_status"), "stripe_events", ["status"])
    op.create_index(op.f("ix_stripe_events_object_id"), "stripe_events", ["object_id"])

    op.create_table(
        "stripe_ingestion_logs",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("event_id", sa.String(length=128), nullable=True),
        sa.Column("event_type", sa.String(length=120), nullable=True),
        sa.Column("level", sa.String(length=16), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("detail", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_stripe_ingestion_logs")),
    )
    op.create_index(
        op.f("ix_stripe_ingestion_logs_event_id"), "stripe_ingestion_logs", ["event_id"]
    )
    op.create_index(
        op.f("ix_stripe_ingestion_logs_event_type"),
        "stripe_ingestion_logs",
        ["event_type"],
    )
    op.create_index(
        op.f("ix_stripe_ingestion_logs_level"), "stripe_ingestion_logs", ["level"]
    )
    op.create_index(
        op.f("ix_stripe_ingestion_logs_created_at"),
        "stripe_ingestion_logs",
        ["created_at"],
    )

    op.create_table(
        "stripe_reconciliation_runs",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("customers_seen", sa.Integer(), nullable=False),
        sa.Column("subscriptions_seen", sa.Integer(), nullable=False),
        sa.Column("invoices_seen", sa.Integer(), nullable=False),
        sa.Column("repaired", sa.Integer(), nullable=False),
        sa.Column("errors", sa.Integer(), nullable=False),
        sa.Column("summary", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_stripe_reconciliation_runs")),
    )
    op.create_index(
        op.f("ix_stripe_reconciliation_runs_status"),
        "stripe_reconciliation_runs",
        ["status"],
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_stripe_reconciliation_runs_status"),
        table_name="stripe_reconciliation_runs",
    )
    op.drop_table("stripe_reconciliation_runs")

    op.drop_index(
        op.f("ix_stripe_ingestion_logs_created_at"), table_name="stripe_ingestion_logs"
    )
    op.drop_index(op.f("ix_stripe_ingestion_logs_level"), table_name="stripe_ingestion_logs")
    op.drop_index(
        op.f("ix_stripe_ingestion_logs_event_type"), table_name="stripe_ingestion_logs"
    )
    op.drop_index(
        op.f("ix_stripe_ingestion_logs_event_id"), table_name="stripe_ingestion_logs"
    )
    op.drop_table("stripe_ingestion_logs")

    op.drop_index(op.f("ix_stripe_events_object_id"), table_name="stripe_events")
    op.drop_index(op.f("ix_stripe_events_status"), table_name="stripe_events")
    op.drop_index(op.f("ix_stripe_events_event_type"), table_name="stripe_events")
    op.drop_table("stripe_events")

    with op.batch_alter_table("invoices") as batch_op:
        batch_op.drop_index(batch_op.f("ix_invoices_stripe_invoice_id"))
        batch_op.drop_column("stripe_object_updated_at")
        batch_op.drop_column("stripe_invoice_id")

    with op.batch_alter_table("subscriptions") as batch_op:
        batch_op.drop_index(batch_op.f("ix_subscriptions_stripe_subscription_id"))
        batch_op.drop_column("stripe_object_updated_at")
        batch_op.drop_column("stripe_subscription_id")

    with op.batch_alter_table("accounts") as batch_op:
        batch_op.drop_index(batch_op.f("ix_accounts_stripe_customer_id"))
        batch_op.drop_column("stripe_object_updated_at")
        batch_op.drop_column("stripe_customer_id")
