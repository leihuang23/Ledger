"""add agents and agent_versions tables

Revision ID: 20260708_0010
Revises: 20260706_0009
Create Date: 2026-07-08 00:00:00.000000

Adds the control-plane agent registry: agents (named, slug-keyed objects) and
agent_versions (immutable-once-published configurations holding system prompt,
model, temperature, enabled tool ids, and allowed permission scopes).

This migration is strictly additive and does not modify any Project 1 tables.
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260708_0010"
down_revision: str | None = "20260706_0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "agents",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("default_model", sa.String(length=80), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "agent_versions",
        sa.Column("id", sa.String(length=128), nullable=False),
        sa.Column(
            "agent_id",
            sa.String(length=64),
            sa.ForeignKey("agents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("version_number", sa.Integer(), nullable=True),
        sa.Column("semantic_version", sa.String(length=32), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("system_prompt", sa.Text(), nullable=False, server_default=""),
        sa.Column("model", sa.String(length=80), nullable=False),
        sa.Column("temperature", sa.Float(), nullable=False, server_default="0.1"),
        sa.Column("max_tokens", sa.Integer(), nullable=False, server_default="1024"),
        sa.Column("enabled_tool_ids", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("allowed_scopes", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("published_at", sa.DateTime(), nullable=True),
        sa.Column("published_by", sa.String(length=80), nullable=True),
        sa.Column(
            "forked_from_version_id",
            sa.String(length=128),
            sa.ForeignKey("agent_versions.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_index("ix_agent_versions_agent_id", "agent_versions", ["agent_id"])
    op.create_index("ix_agent_versions_status", "agent_versions", ["status"])
    op.create_index(
        "ix_agent_versions_forked_from_version_id",
        "agent_versions",
        ["forked_from_version_id"],
    )
    op.create_index(
        "ix_agent_versions_agent_status",
        "agent_versions",
        ["agent_id", "status"],
    )
    op.create_index(
        "uq_agent_versions_published_number",
        "agent_versions",
        ["agent_id", "version_number"],
        unique=True,
        postgresql_where=sa.text("status = 'published'"),
        sqlite_where=sa.text("status = 'published'"),
    )


def downgrade() -> None:
    op.drop_index("uq_agent_versions_published_number", table_name="agent_versions")
    op.drop_index("ix_agent_versions_agent_status", table_name="agent_versions")
    op.drop_index("ix_agent_versions_forked_from_version_id", table_name="agent_versions")
    op.drop_index("ix_agent_versions_status", table_name="agent_versions")
    op.drop_index("ix_agent_versions_agent_id", table_name="agent_versions")
    op.drop_table("agent_versions")
    op.drop_table("agents")
