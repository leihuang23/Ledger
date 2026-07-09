"""run orchestration: nullable incident_id, blocked_reason, v1 scopes

Revision ID: 20260709_0012
Revises: 20260708_0011
Create Date: 2026-07-09 00:00:00.000000

Phase 3 (Run Orchestration & Status) schema changes:

1. Make ``agent_runs.incident_id`` nullable so control-plane runs need not be
   incident-bound (PRD FR-8). The existing partial unique index
   ``uq_agent_runs_active_incident`` excludes NULLs, so this is safe.
2. Add ``agent_run_steps.blocked_reason`` so a permission-blocked tool call is
   recorded as a visible ``blocked`` step with a reason (PRD FR-7, AC-2.4).
3. Backfill every ``agent_version`` whose ``allowed_scopes`` is empty/NULL to the
   v1 default scopes (PRD §9.5). Pre-Phase-3 versions had no scopes field, so
   all of them -- not just the seeded ``revenue-ops-agent_v1`` -- must receive
   the default or every data tool would be blocked once scope enforcement
   activates.

Strictly additive and reversible; portable across SQLite and PostgreSQL via
``batch_alter_table``. The downgrade is reversible even after non-incident
runs were created: it deletes the NULL-incident control-plane runs (which
cannot exist under the pre-Phase-3 NOT NULL constraint) before re-imposing
the constraint, and only resets scopes that still equal the v1 default so an
operator's post-upgrade customizations are preserved.
"""

from collections.abc import Sequence
from datetime import datetime, timezone

from alembic import op
import sqlalchemy as sa


revision: str = "20260709_0012"
down_revision: str | None = "20260708_0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# PRD §9.5: v1 ships with read_data + write_mock_action + request_approval.
# run_eval is intentionally excluded for v1 (eval triggering is operator-gated).
DEFAULT_V1_ALLOWED_SCOPES: list[str] = [
    "read_data",
    "write_mock_action",
    "request_approval",
]


def _scopes_are_empty(current: object) -> bool:
    """Return True if a version's allowed_scopes is unset or empty."""
    return current is None or current == []


def _agent_versions_table() -> sa.Table:
    return sa.table(
        "agent_versions",
        sa.column("id", sa.String(128)),
        sa.column("allowed_scopes", sa.JSON),
        sa.column("updated_at", sa.DateTime),
    )


def _agent_runs_table() -> sa.Table:
    return sa.table(
        "agent_runs",
        sa.column("id", sa.String(48)),
        sa.column("incident_id", sa.String(64)),
    )


def _backfill_empty_scopes(conn: sa.engine.Connection) -> None:
    """Backfill every agent_version with empty/None allowed_scopes to the v1
    default.

    A pre-Phase-3 version had no scopes field at all (empty list/NULL after the
    column was added). Leaving any such version empty would make scope
    enforcement block every data tool on it once FR-7 activates, so all empty
    versions -- not just the seeded v1 -- are backfilled. Idempotent: a version
    with non-empty scopes (operator-customized) is left untouched.
    """
    agent_versions = _agent_versions_table()
    rows = conn.execute(
        sa.select(agent_versions.c.id, agent_versions.c.allowed_scopes)
    ).all()
    to_backfill = [row[0] for row in rows if _scopes_are_empty(row[1])]
    if not to_backfill:
        return
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    conn.execute(
        sa.update(agent_versions)
        .where(agent_versions.c.id.in_(to_backfill))
        .values(allowed_scopes=DEFAULT_V1_ALLOWED_SCOPES, updated_at=now)
    )


def _reset_backfilled_scopes(conn: sa.engine.Connection) -> None:
    """Best-effort downgrade: restore scopes to the pre-Phase-3 empty default.

    Only rows whose scopes still equal the v1 default are reset, so an
    operator who customized scopes after the upgrade does not lose their
    changes. Rows the upgrade did not touch (already non-empty) are never
    matched here either, so the downgrade is symmetric with the upgrade.
    """
    agent_versions = _agent_versions_table()
    rows = conn.execute(
        sa.select(agent_versions.c.id, agent_versions.c.allowed_scopes)
    ).all()
    to_reset = [
        row[0]
        for row in rows
        if list(row[1] or []) == list(DEFAULT_V1_ALLOWED_SCOPES)
    ]
    if not to_reset:
        return
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    conn.execute(
        sa.update(agent_versions)
        .where(agent_versions.c.id.in_(to_reset))
        .values(allowed_scopes=[], updated_at=now)
    )


def _delete_non_incident_runs(conn: sa.engine.Connection) -> None:
    """Delete control-plane runs with a NULL incident_id before re-imposing the
    NOT NULL constraint on downgrade.

    These rows cannot exist under the pre-Phase-3 schema. Dependent rows
    (steps, mock actions, approval requests, audit events) are removed by the
    ``ON DELETE CASCADE`` foreign keys declared on those tables.
    """
    agent_runs = _agent_runs_table()
    conn.execute(sa.delete(agent_runs).where(agent_runs.c.incident_id.is_(None)))


def upgrade() -> None:
    # 1. incident_id nullable on agent_runs.
    with op.batch_alter_table("agent_runs") as batch_op:
        batch_op.alter_column(
            "incident_id",
            existing_type=sa.String(length=64),
            nullable=True,
        )

    # 2. blocked_reason on agent_run_steps.
    with op.batch_alter_table("agent_run_steps") as batch_op:
        batch_op.add_column(sa.Column("blocked_reason", sa.Text(), nullable=True))

    # 3. Backfill empty allowed_scopes to the PRD §9.5 v1 default.
    conn = op.get_bind()
    _backfill_empty_scopes(conn)


def downgrade() -> None:
    conn = op.get_bind()
    _reset_backfilled_scopes(conn)

    with op.batch_alter_table("agent_run_steps") as batch_op:
        batch_op.drop_column("blocked_reason")

    # Control-plane runs with a NULL incident_id cannot exist under the
    # pre-Phase-3 NOT NULL constraint. Remove them before re-imposing NOT NULL
    # so the downgrade is reversible on both SQLite and PostgreSQL.
    _delete_non_incident_runs(conn)

    with op.batch_alter_table("agent_runs") as batch_op:
        batch_op.alter_column(
            "incident_id",
            existing_type=sa.String(length=64),
            nullable=False,
        )
