"""add agent runs

Revision ID: 20260613_0004
Revises: 20260612_0003
Create Date: 2026-06-13 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260613_0004"
down_revision: str | None = "20260612_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "agent_runs",
        sa.Column("id", sa.String(length=48), nullable=False),
        sa.Column("incident_id", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("trace_id", sa.String(length=96), nullable=True),
        sa.Column("input_payload", sa.JSON(), nullable=False),
        sa.Column("final_report", sa.JSON(), nullable=True),
        sa.Column("token_estimate", sa.Integer(), nullable=False),
        sa.Column("cost_estimate_usd", sa.Float(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["incident_id"], ["incidents.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_agent_runs_incident_id"),
        "agent_runs",
        ["incident_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_agent_runs_status"), "agent_runs", ["status"], unique=False
    )
    op.create_index(
        op.f("ix_agent_runs_trace_id"), "agent_runs", ["trace_id"], unique=False
    )

    op.create_table(
        "agent_run_steps",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("run_id", sa.String(length=48), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("stage", sa.String(length=80), nullable=False),
        sa.Column("tool_name", sa.String(length=80), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("inputs", sa.JSON(), nullable=False),
        sa.Column("outputs", sa.JSON(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["agent_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", "sequence", name="uq_agent_run_steps_run_sequence"),
    )
    op.create_index(
        op.f("ix_agent_run_steps_run_id"),
        "agent_run_steps",
        ["run_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_agent_run_steps_stage"),
        "agent_run_steps",
        ["stage"],
        unique=False,
    )
    op.create_index(
        op.f("ix_agent_run_steps_status"),
        "agent_run_steps",
        ["status"],
        unique=False,
    )
    op.create_index(
        op.f("ix_agent_run_steps_tool_name"),
        "agent_run_steps",
        ["tool_name"],
        unique=False,
    )
    op.create_index(
        "ix_agent_run_steps_run_stage",
        "agent_run_steps",
        ["run_id", "stage"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_agent_run_steps_run_stage", table_name="agent_run_steps")
    op.drop_index(op.f("ix_agent_run_steps_tool_name"), table_name="agent_run_steps")
    op.drop_index(op.f("ix_agent_run_steps_status"), table_name="agent_run_steps")
    op.drop_index(op.f("ix_agent_run_steps_stage"), table_name="agent_run_steps")
    op.drop_index(op.f("ix_agent_run_steps_run_id"), table_name="agent_run_steps")
    op.drop_table("agent_run_steps")
    op.drop_index(op.f("ix_agent_runs_trace_id"), table_name="agent_runs")
    op.drop_index(op.f("ix_agent_runs_status"), table_name="agent_runs")
    op.drop_index(op.f("ix_agent_runs_incident_id"), table_name="agent_runs")
    op.drop_table("agent_runs")
