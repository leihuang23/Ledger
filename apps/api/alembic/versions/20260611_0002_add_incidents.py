"""add incidents

Revision ID: 20260611_0002
Revises: 20260610_0001
Create Date: 2026-06-11 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260611_0002"
down_revision: str | None = "20260610_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "incidents",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("title", sa.String(length=180), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("severity", sa.String(length=32), nullable=False),
        sa.Column("anomaly_type", sa.String(length=80), nullable=False),
        sa.Column("metric_name", sa.String(length=80), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("source_scenario", sa.String(length=80), nullable=True),
        sa.Column("detected_at", sa.DateTime(), nullable=False),
        sa.Column("current_value_cents", sa.Integer(), nullable=False),
        sa.Column("previous_value_cents", sa.Integer(), nullable=False),
        sa.Column("delta_cents", sa.Integer(), nullable=False),
        sa.Column("delta_percent", sa.Float(), nullable=False),
        sa.Column("affected_account_ids", sa.JSON(), nullable=False),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_incidents_anomaly_type"),
        "incidents",
        ["anomaly_type"],
        unique=False,
    )
    op.create_index(
        op.f("ix_incidents_detected_at"),
        "incidents",
        ["detected_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_incidents_severity"),
        "incidents",
        ["severity"],
        unique=False,
    )
    op.create_index(
        op.f("ix_incidents_source_scenario"),
        "incidents",
        ["source_scenario"],
        unique=False,
    )
    op.create_index(
        op.f("ix_incidents_status"),
        "incidents",
        ["status"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_incidents_status"), table_name="incidents")
    op.drop_index(op.f("ix_incidents_source_scenario"), table_name="incidents")
    op.drop_index(op.f("ix_incidents_severity"), table_name="incidents")
    op.drop_index(op.f("ix_incidents_detected_at"), table_name="incidents")
    op.drop_index(op.f("ix_incidents_anomaly_type"), table_name="incidents")
    op.drop_table("incidents")
