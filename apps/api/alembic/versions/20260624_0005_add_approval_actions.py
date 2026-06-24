"""add approval-gated mock actions

Revision ID: 20260624_0005
Revises: 20260613_0004
Create Date: 2026-06-24 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260624_0005"
down_revision: str | None = "20260613_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "mock_actions",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("run_id", sa.String(length=48), nullable=False),
        sa.Column("action_type", sa.String(length=48), nullable=False),
        sa.Column("risk_level", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("title", sa.String(length=180), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("target", sa.String(length=180), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_by", sa.String(length=80), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("executed_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["run_id"], ["agent_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_mock_actions_action_type"), "mock_actions", ["action_type"])
    op.create_index(op.f("ix_mock_actions_risk_level"), "mock_actions", ["risk_level"])
    op.create_index(op.f("ix_mock_actions_run_id"), "mock_actions", ["run_id"])
    op.create_index("ix_mock_actions_run_status", "mock_actions", ["run_id", "status"])
    op.create_index(op.f("ix_mock_actions_status"), "mock_actions", ["status"])

    op.create_table(
        "approval_requests",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("run_id", sa.String(length=48), nullable=False),
        sa.Column("action_id", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("risk_level", sa.String(length=16), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("requested_by", sa.String(length=80), nullable=False),
        sa.Column("decided_by", sa.String(length=80), nullable=True),
        sa.Column("decision_notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("decided_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["action_id"], ["mock_actions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["run_id"], ["agent_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_approval_requests_action_id"),
        "approval_requests",
        ["action_id"],
        unique=True,
    )
    op.create_index(op.f("ix_approval_requests_risk_level"), "approval_requests", ["risk_level"])
    op.create_index(op.f("ix_approval_requests_run_id"), "approval_requests", ["run_id"])
    op.create_index(
        "ix_approval_requests_run_status",
        "approval_requests",
        ["run_id", "status"],
    )
    op.create_index(op.f("ix_approval_requests_status"), "approval_requests", ["status"])

    op.create_table(
        "action_audit_events",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("run_id", sa.String(length=48), nullable=False),
        sa.Column("action_id", sa.String(length=64), nullable=False),
        sa.Column("approval_request_id", sa.String(length=64), nullable=True),
        sa.Column("event_type", sa.String(length=32), nullable=False),
        sa.Column("actor", sa.String(length=80), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["action_id"], ["mock_actions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["approval_request_id"], ["approval_requests.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["run_id"], ["agent_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_action_audit_events_action_id"),
        "action_audit_events",
        ["action_id"],
    )
    op.create_index(
        op.f("ix_action_audit_events_approval_request_id"),
        "action_audit_events",
        ["approval_request_id"],
    )
    op.create_index(
        op.f("ix_action_audit_events_event_type"),
        "action_audit_events",
        ["event_type"],
    )
    op.create_index(
        "ix_action_audit_events_run_created",
        "action_audit_events",
        ["run_id", "created_at"],
    )
    op.create_index(
        op.f("ix_action_audit_events_run_id"),
        "action_audit_events",
        ["run_id"],
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_action_audit_events_run_id"), table_name="action_audit_events")
    op.drop_index("ix_action_audit_events_run_created", table_name="action_audit_events")
    op.drop_index(
        op.f("ix_action_audit_events_event_type"), table_name="action_audit_events"
    )
    op.drop_index(
        op.f("ix_action_audit_events_approval_request_id"),
        table_name="action_audit_events",
    )
    op.drop_index(
        op.f("ix_action_audit_events_action_id"), table_name="action_audit_events"
    )
    op.drop_table("action_audit_events")
    op.drop_index(op.f("ix_approval_requests_status"), table_name="approval_requests")
    op.drop_index("ix_approval_requests_run_status", table_name="approval_requests")
    op.drop_index(op.f("ix_approval_requests_run_id"), table_name="approval_requests")
    op.drop_index(op.f("ix_approval_requests_risk_level"), table_name="approval_requests")
    op.drop_index(op.f("ix_approval_requests_action_id"), table_name="approval_requests")
    op.drop_table("approval_requests")
    op.drop_index(op.f("ix_mock_actions_status"), table_name="mock_actions")
    op.drop_index("ix_mock_actions_run_status", table_name="mock_actions")
    op.drop_index(op.f("ix_mock_actions_run_id"), table_name="mock_actions")
    op.drop_index(op.f("ix_mock_actions_risk_level"), table_name="mock_actions")
    op.drop_index(op.f("ix_mock_actions_action_type"), table_name="mock_actions")
    op.drop_table("mock_actions")
