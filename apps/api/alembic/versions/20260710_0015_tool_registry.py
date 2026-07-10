"""add the governed tool registry and a versioned Phase 6 capability snapshot

Revision ID: 20260710_0015
Revises: 20260710_0014
Create Date: 2026-07-10 00:00:00.000000

The registry is additive metadata over existing Python callables. Existing
published versions remain untouched. The expanded action/eval capability set is
published as a new version so historical runs retain their exact configuration.
"""

from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op


revision: str = "20260710_0015"
down_revision: str | None = "20260710_0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PHASE6_VERSION_ID = "revenue-ops-agent_phase6"
PHASE6_DEGRADED_VERSION_ID = "revenue-ops-agent_phase6_degraded"
PHASE6_SOURCE_VERSION_ID = "revenue-ops-agent_v1"
MIGRATION_PUBLISHER = "migration:20260710_0015"
RELEASE_SNAPSHOT_PUBLISHERS = {
    PHASE6_VERSION_ID: frozenset({MIGRATION_PUBLISHER, "bootstrap:phase6"}),
    PHASE6_DEGRADED_VERSION_ID: frozenset({MIGRATION_PUBLISHER, "bootstrap"}),
}
PHASE6_ENABLED_TOOLS = [
    "query_revenue_metrics",
    "fetch_account_details",
    "search_docs",
    "fetch_support_tickets",
    "create_mock_action",
    "request_approval",
    "run_eval",
]
PHASE6_ALLOWED_SCOPES = [
    "read_data",
    "write_mock_action",
    "request_approval",
    "run_eval",
]


def _publish_phase6_capability_snapshot() -> None:
    conn = op.get_bind()
    agent_versions = sa.table(
        "agent_versions",
        sa.column("id", sa.String(128)),
        sa.column("agent_id", sa.String(64)),
        sa.column("version_number", sa.Integer),
        sa.column("semantic_version", sa.String(32)),
        sa.column("status", sa.String(32)),
        sa.column("system_prompt", sa.Text),
        sa.column("model", sa.String(80)),
        sa.column("temperature", sa.Float),
        sa.column("max_tokens", sa.Integer),
        sa.column("enabled_tool_ids", sa.JSON),
        sa.column("allowed_scopes", sa.JSON),
        sa.column("forked_from_version_id", sa.String(128)),
        sa.column("published_by", sa.String(80)),
        sa.column("published_at", sa.DateTime),
        sa.column("created_at", sa.DateTime),
        sa.column("updated_at", sa.DateTime),
    )
    if conn.execute(
        sa.select(agent_versions.c.id).where(
            agent_versions.c.id == PHASE6_VERSION_ID
        )
    ).first() is not None:
        return

    source = conn.execute(
        sa.select(agent_versions)
        .where(
            agent_versions.c.id == PHASE6_SOURCE_VERSION_ID,
            agent_versions.c.agent_id == "revenue-ops-agent",
            agent_versions.c.status == "published",
        )
    ).mappings().first()
    if source is None:
        return

    next_version_number = int(
        conn.scalar(
            sa.select(sa.func.coalesce(sa.func.max(agent_versions.c.version_number), 0))
            .where(agent_versions.c.agent_id == "revenue-ops-agent")
        )
        or 0
    ) + 1
    now = datetime.now(UTC).replace(tzinfo=None)
    conn.execute(
        agent_versions.insert().values(
            id=PHASE6_VERSION_ID,
            agent_id="revenue-ops-agent",
            version_number=next_version_number,
            semantic_version=f"{next_version_number}.0.0",
            status="published",
            system_prompt=source["system_prompt"] or "",
            model=source["model"],
            temperature=source["temperature"],
            max_tokens=source["max_tokens"],
            enabled_tool_ids=PHASE6_ENABLED_TOOLS,
            allowed_scopes=PHASE6_ALLOWED_SCOPES,
            forked_from_version_id=source["id"],
            published_by=MIGRATION_PUBLISHER,
            published_at=now,
            created_at=now,
            updated_at=now,
        )
    )


def _publish_phase6_degraded_snapshot() -> None:
    """Add an eval candidate without rewriting the legacy degraded snapshot."""
    conn = op.get_bind()
    agent_versions = sa.table(
        "agent_versions",
        sa.column("id", sa.String(128)),
        sa.column("agent_id", sa.String(64)),
        sa.column("version_number", sa.Integer),
        sa.column("semantic_version", sa.String(32)),
        sa.column("status", sa.String(32)),
        sa.column("system_prompt", sa.Text),
        sa.column("model", sa.String(80)),
        sa.column("temperature", sa.Float),
        sa.column("max_tokens", sa.Integer),
        sa.column("enabled_tool_ids", sa.JSON),
        sa.column("allowed_scopes", sa.JSON),
        sa.column("forked_from_version_id", sa.String(128)),
        sa.column("published_by", sa.String(80)),
        sa.column("published_at", sa.DateTime),
        sa.column("created_at", sa.DateTime),
        sa.column("updated_at", sa.DateTime),
    )
    if conn.execute(
        sa.select(agent_versions.c.id).where(
            agent_versions.c.id == PHASE6_DEGRADED_VERSION_ID
        )
    ).first() is not None:
        return

    source = conn.execute(
        sa.select(agent_versions).where(
            agent_versions.c.id == PHASE6_VERSION_ID,
            agent_versions.c.status == "published",
        )
    ).mappings().first()
    if source is None:
        return

    minimum_version_number = int(
        conn.scalar(
            sa.select(sa.func.coalesce(sa.func.min(agent_versions.c.version_number), 0))
            .where(agent_versions.c.agent_id == "revenue-ops-agent")
        )
        or 0
    )
    variant_version_number = min(minimum_version_number, 0) - 1
    now = datetime.now(UTC).replace(tzinfo=None)
    conn.execute(
        agent_versions.insert().values(
            id=PHASE6_DEGRADED_VERSION_ID,
            agent_id="revenue-ops-agent",
            version_number=variant_version_number,
            semantic_version="0.8.0-phase6-degraded",
            status="published",
            system_prompt=source["system_prompt"] or "",
            model=source["model"],
            temperature=source["temperature"],
            max_tokens=source["max_tokens"],
            enabled_tool_ids=[
                tool_id
                for tool_id in PHASE6_ENABLED_TOOLS
                if tool_id != "search_docs"
            ],
            allowed_scopes=PHASE6_ALLOWED_SCOPES,
            forked_from_version_id=PHASE6_VERSION_ID,
            published_by=MIGRATION_PUBLISHER,
            published_at=now,
            created_at=now,
            updated_at=now,
        )
    )


def upgrade() -> None:
    op.create_table(
        "tools",
        sa.Column("id", sa.String(length=80), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("input_schema", sa.JSON(), nullable=False),
        sa.Column("output_schema", sa.JSON(), nullable=False),
        sa.Column("permission_scope", sa.String(length=32), nullable=False),
        sa.Column("implementation_ref", sa.String(length=240), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tools")),
        sa.UniqueConstraint("name", name=op.f("uq_tools_name")),
    )
    op.create_index(
        op.f("ix_tools_permission_scope"),
        "tools",
        ["permission_scope"],
    )
    _publish_phase6_capability_snapshot()
    _publish_phase6_degraded_snapshot()


def _release_owned_snapshot_ids(
    conn: sa.engine.Connection, agent_versions: sa.TableClause
) -> tuple[str, ...]:
    rows = conn.execute(
        sa.select(agent_versions.c.id, agent_versions.c.published_by).where(
            agent_versions.c.id.in_(
                (PHASE6_VERSION_ID, PHASE6_DEGRADED_VERSION_ID)
            )
        )
    )
    return tuple(
        sorted(
            row.id
            for row in rows
            if row.published_by in RELEASE_SNAPSHOT_PUBLISHERS[row.id]
        )
    )


def _assert_snapshots_unreferenced(
    conn: sa.engine.Connection,
    agent_versions: sa.TableClause,
    owned_ids: tuple[str, ...],
) -> None:
    if not owned_ids:
        return

    agent_runs = sa.table(
        "agent_runs",
        sa.column("agent_version_id", sa.String(128)),
    )
    eval_results = sa.table(
        "eval_results",
        sa.column("agent_version_id", sa.String(128)),
    )
    reference_queries = {
        "agent_runs": sa.select(sa.literal(1))
        .select_from(agent_runs)
        .where(agent_runs.c.agent_version_id.in_(owned_ids))
        .limit(1),
        "eval_results": sa.select(sa.literal(1))
        .select_from(eval_results)
        .where(eval_results.c.agent_version_id.in_(owned_ids))
        .limit(1),
        "agent_versions": sa.select(sa.literal(1))
        .select_from(agent_versions)
        .where(
            agent_versions.c.forked_from_version_id.in_(owned_ids),
            ~agent_versions.c.id.in_(owned_ids),
        )
        .limit(1),
    }
    referenced_by = [
        table_name
        for table_name, query in reference_queries.items()
        if conn.execute(query).first() is not None
    ]
    if referenced_by:
        raise RuntimeError(
            "Cannot downgrade 20260710_0015: release-owned Phase 6 snapshots "
            f"are referenced by {', '.join(referenced_by)}."
        )


def downgrade() -> None:
    conn = op.get_bind()
    agent_versions = sa.table(
        "agent_versions",
        sa.column("id", sa.String(128)),
        sa.column("published_by", sa.String(80)),
        sa.column("forked_from_version_id", sa.String(128)),
    )
    owned_ids = _release_owned_snapshot_ids(conn, agent_versions)
    _assert_snapshots_unreferenced(conn, agent_versions, owned_ids)
    for version_id in (PHASE6_DEGRADED_VERSION_ID, PHASE6_VERSION_ID):
        if version_id in owned_ids:
            conn.execute(
                agent_versions.delete().where(agent_versions.c.id == version_id)
            )
    op.drop_index(op.f("ix_tools_permission_scope"), table_name="tools")
    op.drop_table("tools")
