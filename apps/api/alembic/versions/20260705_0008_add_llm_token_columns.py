"""add prompt and completion token columns to agent_runs

Revision ID: 20260705_0008
Revises: 20260626_0007
Create Date: 2026-07-05 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260705_0008"
down_revision: str | None = "20260626_0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "agent_runs",
        sa.Column("prompt_tokens", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "agent_runs",
        sa.Column("completion_tokens", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("agent_runs", "completion_tokens")
    op.drop_column("agent_runs", "prompt_tokens")
