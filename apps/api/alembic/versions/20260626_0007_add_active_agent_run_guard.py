"""add active agent run uniqueness guard

Revision ID: 20260626_0007
Revises: 20260625_0006
Create Date: 2026-06-26 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260626_0007"
down_revision: str | None = "20260625_0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        sa.text(
            """
            UPDATE agent_runs
            SET
                status = 'failed',
                error = COALESCE(error, 'Investigation interrupted before completion.'),
                completed_at = COALESCE(completed_at, CURRENT_TIMESTAMP),
                updated_at = CURRENT_TIMESTAMP
            WHERE id IN (
                SELECT id
                FROM (
                    SELECT
                        id,
                        ROW_NUMBER() OVER (
                            PARTITION BY incident_id
                            ORDER BY created_at DESC, id DESC
                        ) AS active_rank
                    FROM agent_runs
                    WHERE status IN ('queued', 'running')
                ) ranked_active_runs
                WHERE active_rank > 1
            )
            """
        )
    )
    op.create_index(
        "uq_agent_runs_active_incident",
        "agent_runs",
        ["incident_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('queued', 'running')"),
        sqlite_where=sa.text("status IN ('queued', 'running')"),
    )


def downgrade() -> None:
    op.drop_index("uq_agent_runs_active_incident", table_name="agent_runs")
