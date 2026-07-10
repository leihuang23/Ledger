"""model usage tracking + per-version observability indexes

Revision ID: 20260710_0013
Revises: 20260709_0012
Create Date: 2026-07-10 00:00:00.000000

Phase 4 (Trace, Cost & Latency Dashboard) schema changes (PRD §9.2, FR-18..20):

1. Create the ``model_usage`` table capturing per-LLM-call token/latency/cost
   metrics. ``run_id`` is a real FK to ``agent_runs``; ``step_id`` is a real FK
   to ``agent_run_steps``. The reverse link
   (``agent_run_steps.model_usage_id -> model_usage.id``) is intentionally NOT
   declared as an FK so the schema avoids a circular foreign key that breaks
   SQLite ``Base.metadata.create_all`` used by the test fixtures. The recorder
   keeps the plain ``model_usage_id`` column in sync with
   ``model_usage.step_id``.
2. Add ``agent_run_steps.model_usage_id`` as a nullable indexed plain column
   (the back-reference from a step to its persisted model-usage row).
3. Add composite indexes on ``agent_runs`` to support the per-version
   observability dashboard aggregates (NFR-1: p95 <= 500ms for <= 10k runs):
   ``(agent_version_id, status)`` and ``(agent_version_id, created_at)``.

Strictly additive and reversible; portable across SQLite and PostgreSQL via
``batch_alter_table`` and ``create_index``/``drop_index``.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "20260710_0013"
down_revision: str | None = "20260709_0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "model_usage",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column(
            "run_id",
            sa.String(length=48),
            sa.ForeignKey("agent_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "step_id",
            sa.String(length=64),
            sa.ForeignKey("agent_run_steps.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("model", sa.String(length=128), nullable=False),
        sa.Column("prompt_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "completion_tokens", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column("total_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "cost_estimate_usd", sa.Float(), nullable=False, server_default="0.0"
        ),
        sa.Column("latency_ms", sa.Integer(), nullable=False, server_default="0"),
        # Use sa.false() (renders ``false``) rather than the integer literal ``0``:
        # PostgreSQL rejects an integer default for a boolean column, even though
        # SQLite tolerates it. ``false`` is accepted by both dialects.
        sa.Column(
            "used_llm",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column("fallback_reason", sa.Text(), nullable=True),
        sa.Column("recorded_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_model_usage")),
    )
    op.create_index(op.f("ix_model_usage_run_id"), "model_usage", ["run_id"])
    op.create_index(op.f("ix_model_usage_step_id"), "model_usage", ["step_id"])
    op.create_index(
        op.f("ix_model_usage_recorded_at"), "model_usage", ["recorded_at"]
    )

    # Back-reference from a step to its persisted model-usage row. Plain column
    # (no FK) so the schema does not carry a circular FK (see module docstring).
    with op.batch_alter_table("agent_run_steps") as batch_op:
        batch_op.add_column(
            sa.Column("model_usage_id", sa.String(length=64), nullable=True)
        )
    op.create_index(
        op.f("ix_agent_run_steps_model_usage_id"),
        "agent_run_steps",
        ["model_usage_id"],
    )

    # Composite indexes supporting the per-version dashboard aggregates (NFR-1).
    op.create_index(
        "ix_agent_runs_version_status",
        "agent_runs",
        ["agent_version_id", "status"],
    )
    op.create_index(
        "ix_agent_runs_version_created",
        "agent_runs",
        ["agent_version_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_agent_runs_version_created", table_name="agent_runs")
    op.drop_index("ix_agent_runs_version_status", table_name="agent_runs")

    op.drop_index(
        op.f("ix_agent_run_steps_model_usage_id"), table_name="agent_run_steps"
    )
    with op.batch_alter_table("agent_run_steps") as batch_op:
        batch_op.drop_column("model_usage_id")

    op.drop_index(op.f("ix_model_usage_recorded_at"), table_name="model_usage")
    op.drop_index(op.f("ix_model_usage_step_id"), table_name="model_usage")
    op.drop_index(op.f("ix_model_usage_run_id"), table_name="model_usage")
    op.drop_table("model_usage")
