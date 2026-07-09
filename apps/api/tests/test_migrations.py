from __future__ import annotations

import importlib.util
from collections.abc import Generator
from datetime import datetime, timezone
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect
from sqlalchemy.engine import Engine

import app.models  # noqa: F401
from app.db.base import Base

MIGRATION_PATH = (
    Path(__file__).resolve().parent.parent
    / "alembic"
    / "versions"
    / "20260706_0009_add_hot_path_indexes.py"
)
AGENT_RUN_VERSION_MIGRATION_PATH = (
    Path(__file__).resolve().parent.parent
    / "alembic"
    / "versions"
    / "20260708_0011_wire_agent_version_to_runs.py"
)
RUN_ORCHESTRATION_MIGRATION_PATH = (
    Path(__file__).resolve().parent.parent
    / "alembic"
    / "versions"
    / "20260709_0012_run_orchestration.py"
)


def _load_migration_module(
    path: Path = MIGRATION_PATH,
    module_name: str = "migration_20260706_0009_add_hot_path_indexes",
):
    """Load the Alembic migration file as a Python module.

    Migration filenames start with digits, so they cannot be imported as
    regular Python packages. Use importlib to load the file directly.
    """
    if not path.exists():
        pytest.fail(
            f"Expected migration file not found: {path.name}."
        )
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run_in_migration_context(
    engine: Engine,
    fn_name: str,
    *,
    path: Path = MIGRATION_PATH,
    module_name: str = "migration_20260706_0009_add_hot_path_indexes",
) -> None:
    """Bind an Alembic Operations context to the engine and invoke fn_name.

    ``Operations.context`` is a context manager that installs the module-level
    ``alembic.op`` proxy for the duration of the block, so the migration's
    ``op.create_index(...)`` / ``op.drop_index(...)`` calls resolve correctly.
    """
    migration = _load_migration_module(path, module_name)
    fn = getattr(migration, fn_name, None)
    if fn is None:
        pytest.fail(f"Migration module does not define {fn_name}()")
    with engine.begin() as conn:
        ctx = MigrationContext.configure(conn)
        with Operations.context(ctx):
            fn()


@pytest.fixture()
def fresh_engine(tmp_path) -> Generator[Engine, None, None]:
    """A SQLite engine with all tables created from the current ORM metadata.

    Composite indexes are NOT declared on the ORM models (they are added only
    by the migration), so the freshly-created schema will not have them yet.
    """
    engine = create_engine(f"sqlite:///{tmp_path / 'migration_test.db'}")
    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


def test_hot_path_indexes_migration_creates_composite_indexes(
    fresh_engine: Engine,
) -> None:
    _run_in_migration_context(fresh_engine, "upgrade")

    inspector = inspect(fresh_engine)
    invoice_indexes = inspector.get_indexes("invoices")
    subscription_indexes = inspector.get_indexes("subscriptions")

    invoice_names = {ix["name"] for ix in invoice_indexes}
    subscription_names = {ix["name"] for ix in subscription_indexes}

    assert "ix_invoices_status_invoice_date" in invoice_names
    assert "ix_subscriptions_status_canceled_at" in subscription_names

    invoice_target = next(
        ix for ix in invoice_indexes if ix["name"] == "ix_invoices_status_invoice_date"
    )
    assert invoice_target["column_names"] == ["status", "invoice_date"]
    assert not invoice_target["unique"]

    subscription_target = next(
        ix
        for ix in subscription_indexes
        if ix["name"] == "ix_subscriptions_status_canceled_at"
    )
    assert subscription_target["column_names"] == ["status", "canceled_at"]
    assert not subscription_target["unique"]


def test_hot_path_indexes_migration_downgrade_drops_composite_indexes(
    fresh_engine: Engine,
) -> None:
    _run_in_migration_context(fresh_engine, "upgrade")
    _run_in_migration_context(fresh_engine, "downgrade")

    inspector = inspect(fresh_engine)
    invoice_names = {ix["name"] for ix in inspector.get_indexes("invoices")}
    subscription_names = {ix["name"] for ix in inspector.get_indexes("subscriptions")}

    assert "ix_invoices_status_invoice_date" not in invoice_names
    assert "ix_subscriptions_status_canceled_at" not in subscription_names


def test_hot_path_indexes_migration_revision_links_to_previous_head() -> None:
    migration = _load_migration_module()
    assert migration.revision == "20260706_0009"
    assert migration.down_revision == "20260705_0008"


@pytest.mark.parametrize("existing_status", ["draft", "published"])
def test_agent_run_version_migration_normalizes_invalid_default_version(
    tmp_path,
    existing_status: str,
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'agent_version_migration_draft.db'}")
    metadata = sa.MetaData()
    agents = sa.Table(
        "agents",
        metadata,
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("description", sa.Text, nullable=False, default=""),
        sa.Column("default_model", sa.String(80), nullable=False),
        sa.Column("created_at", sa.DateTime, nullable=False),
        sa.Column("updated_at", sa.DateTime, nullable=False),
    )
    agent_versions = sa.Table(
        "agent_versions",
        metadata,
        sa.Column("id", sa.String(128), primary_key=True),
        sa.Column("agent_id", sa.String(64), nullable=False),
        sa.Column("version_number", sa.Integer),
        sa.Column("semantic_version", sa.String(32)),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("system_prompt", sa.Text, nullable=False),
        sa.Column("model", sa.String(80), nullable=False),
        sa.Column("temperature", sa.Float, nullable=False),
        sa.Column("max_tokens", sa.Integer, nullable=False),
        sa.Column("enabled_tool_ids", sa.JSON, nullable=False),
        sa.Column("allowed_scopes", sa.JSON, nullable=False),
        sa.Column("forked_from_version_id", sa.String(128)),
        sa.Column("published_by", sa.String(80)),
        sa.Column("published_at", sa.DateTime),
        sa.Column("created_at", sa.DateTime, nullable=False),
        sa.Column("updated_at", sa.DateTime, nullable=False),
    )
    agent_runs = sa.Table(
        "agent_runs",
        metadata,
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("incident_id", sa.String(32), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("error", sa.Text),
        sa.Column("completed_at", sa.DateTime),
        sa.Column("created_at", sa.DateTime, nullable=False),
        sa.Column("updated_at", sa.DateTime, nullable=False),
    )
    metadata.create_all(engine)

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with engine.begin() as conn:
        conn.execute(
            agents.insert().values(
                id="revenue-ops-agent",
                name="Revenue Ops Agent",
                description="custom description",
                default_model="gpt-4o-mini",
                created_at=now,
                updated_at=now,
            )
        )
        conn.execute(
            agent_versions.insert().values(
                id="revenue-ops-agent_v1",
                agent_id="revenue-ops-agent",
                version_number=None,
                semantic_version=None,
                status=existing_status,
                system_prompt="draft prompt",
                model="not-a-supported-model",
                temperature=0.9,
                max_tokens=64,
                enabled_tool_ids=[],
                allowed_scopes=[],
                forked_from_version_id=None,
                published_by=None,
                published_at=None,
                created_at=now,
                updated_at=now,
            )
        )
        conn.execute(
            agent_runs.insert().values(
                id="run_existing",
                incident_id="inc_existing",
                status="queued",
                error=None,
                completed_at=None,
                created_at=now,
                updated_at=now,
            )
        )
        conn.execute(
            sa.text(
                "CREATE UNIQUE INDEX uq_agent_runs_active_incident "
                "ON agent_runs (incident_id) "
                "WHERE status IN ('queued', 'running')"
            )
        )

    _run_in_migration_context(
        engine,
        "upgrade",
        path=AGENT_RUN_VERSION_MIGRATION_PATH,
        module_name="migration_20260708_0011_wire_agent_version_to_runs",
    )

    with engine.connect() as conn:
        version_row = conn.execute(
            sa.select(agent_versions).where(
                agent_versions.c.id == "revenue-ops-agent_v1"
            )
        ).mappings().one()
        run_row = conn.execute(sa.select(sa.text("*")).select_from(sa.text("agent_runs"))).mappings().one()

    assert version_row["status"] == "published"
    assert version_row["version_number"] == 1
    assert version_row["semantic_version"] == "1.0.0"
    assert version_row["model"] == "gpt-4o-mini"
    assert version_row["system_prompt"] == ""
    assert version_row["enabled_tool_ids"] == [
        "query_revenue_metrics",
        "fetch_account_details",
        "search_docs",
        "fetch_support_tickets",
    ]
    assert version_row["published_by"] == "migration"
    assert version_row["published_at"] is not None
    assert run_row["agent_id"] == "revenue-ops-agent"
    assert run_row["agent_version_id"] == "revenue-ops-agent_v1"
    active_index = next(
        ix
        for ix in inspect(engine).get_indexes("agent_runs")
        if ix["name"] == "uq_agent_runs_active_incident"
    )
    assert active_index["column_names"] == ["incident_id"]
    engine.dispose()


def _build_pre_phase3_schema(tmp_path) -> tuple[Engine, sa.Table]:
    """Build a schema as it looked just before the Phase 3 migration:

    agent_runs.incident_id is NOT NULL, agent_run_steps has no blocked_reason,
    and the seeded v1 has empty allowed_scopes. Returns the engine and the
    ``agent_versions`` table (the only table tests inspect directly).
    """
    engine = create_engine(f"sqlite:///{tmp_path / 'run_orchestration_migration.db'}")
    metadata = sa.MetaData()
    agents = sa.Table(
        "agents",
        metadata,
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("description", sa.Text, nullable=False, default=""),
        sa.Column("default_model", sa.String(80), nullable=False),
        sa.Column("created_at", sa.DateTime, nullable=False),
        sa.Column("updated_at", sa.DateTime, nullable=False),
    )
    agent_versions = sa.Table(
        "agent_versions",
        metadata,
        sa.Column("id", sa.String(128), primary_key=True),
        sa.Column("agent_id", sa.String(64), nullable=False),
        sa.Column("version_number", sa.Integer),
        sa.Column("semantic_version", sa.String(32)),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("system_prompt", sa.Text, nullable=False),
        sa.Column("model", sa.String(80), nullable=False),
        sa.Column("temperature", sa.Float, nullable=False),
        sa.Column("max_tokens", sa.Integer, nullable=False),
        sa.Column("enabled_tool_ids", sa.JSON, nullable=False),
        sa.Column("allowed_scopes", sa.JSON, nullable=False),
        sa.Column("forked_from_version_id", sa.String(128)),
        sa.Column("published_by", sa.String(80)),
        sa.Column("published_at", sa.DateTime),
        sa.Column("created_at", sa.DateTime, nullable=False),
        sa.Column("updated_at", sa.DateTime, nullable=False),
    )
    # agent_runs and agent_run_steps are registered on `metadata` so that
    # create_all builds them in their pre-Phase-3 shape (NOT NULL incident_id,
    # no blocked_reason column). The Table objects themselves are not referenced
    # later, so they are intentionally not assigned to local names.
    sa.Table(
        "agent_runs",
        metadata,
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("incident_id", sa.String(64), nullable=False),
        sa.Column("agent_id", sa.String(64)),
        sa.Column("agent_version_id", sa.String(128)),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("error", sa.Text),
        sa.Column("completed_at", sa.DateTime),
        sa.Column("created_at", sa.DateTime, nullable=False),
        sa.Column("updated_at", sa.DateTime, nullable=False),
    )
    sa.Table(
        "agent_run_steps",
        metadata,
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("run_id", sa.String(32), nullable=False),
        sa.Column("sequence", sa.Integer, nullable=False),
        sa.Column("stage", sa.String(80), nullable=False),
        sa.Column("tool_name", sa.String(80)),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("inputs", sa.JSON, nullable=False),
        sa.Column("outputs", sa.JSON),
        sa.Column("error", sa.Text),
        sa.Column("started_at", sa.DateTime, nullable=False),
        sa.Column("completed_at", sa.DateTime),
        sa.Column("created_at", sa.DateTime, nullable=False),
    )
    metadata.create_all(engine)

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with engine.begin() as conn:
        conn.execute(
            agents.insert().values(
                id="revenue-ops-agent",
                name="Revenue Ops Agent",
                description="",
                default_model="gpt-4o-mini",
                created_at=now,
                updated_at=now,
            )
        )
        conn.execute(
            agent_versions.insert().values(
                id="revenue-ops-agent_v1",
                agent_id="revenue-ops-agent",
                version_number=1,
                semantic_version="1.0.0",
                status="published",
                system_prompt="",
                model="gpt-4o-mini",
                temperature=0.1,
                max_tokens=1024,
                enabled_tool_ids=[
                    "query_revenue_metrics",
                    "fetch_account_details",
                    "search_docs",
                    "fetch_support_tickets",
                ],
                allowed_scopes=[],  # pre-Phase-3 empty scopes
                forked_from_version_id=None,
                published_by="bootstrap",
                published_at=now,
                created_at=now,
                updated_at=now,
            )
        )

    return engine, agent_versions


def test_run_orchestration_migration_revision_links_to_previous_head() -> None:
    migration = _load_migration_module(
        RUN_ORCHESTRATION_MIGRATION_PATH,
        "migration_20260709_0012_run_orchestration",
    )
    assert migration.revision == "20260709_0012"
    assert migration.down_revision == "20260708_0011"


def test_run_orchestration_migration_makes_incident_id_nullable_adds_blocked_reason_and_backfills_scopes(
    tmp_path,
) -> None:
    engine, agent_versions = _build_pre_phase3_schema(tmp_path)

    _run_in_migration_context(
        engine,
        "upgrade",
        path=RUN_ORCHESTRATION_MIGRATION_PATH,
        module_name="migration_20260709_0012_run_orchestration",
    )

    inspector = inspect(engine)
    runs_cols = {col["name"]: col for col in inspector.get_columns("agent_runs")}
    steps_cols = {col["name"]: col for col in inspector.get_columns("agent_run_steps")}

    # incident_id is now nullable.
    assert runs_cols["incident_id"]["nullable"] is True
    # blocked_reason column exists and is nullable.
    assert "blocked_reason" in steps_cols
    assert steps_cols["blocked_reason"]["nullable"] is True

    # v1 scopes backfilled to the PRD §9.5 default.
    with engine.connect() as conn:
        version_row = conn.execute(
            sa.select(agent_versions).where(
                agent_versions.c.id == "revenue-ops-agent_v1"
            )
        ).mappings().one()
    assert version_row["allowed_scopes"] == [
        "read_data",
        "write_mock_action",
        "request_approval",
    ]

    # A non-incident run can now be inserted (incident_id NULL) without error.
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO agent_runs (id, incident_id, agent_id, agent_version_id, "
                "status, created_at, updated_at) "
                "VALUES ('run_no_incident', NULL, 'revenue-ops-agent', "
                "'revenue-ops-agent_v1', 'queued', '2026-07-09', '2026-07-09')"
            )
        )

    engine.dispose()


def test_run_orchestration_migration_does_not_overwrite_non_empty_v1_scopes(
    tmp_path,
) -> None:
    """If an operator already set non-empty scopes on v1, the backfill is a no-op."""
    engine, agent_versions = _build_pre_phase3_schema(tmp_path)
    custom_scopes = ["read_data"]  # intentionally narrower than the default
    with engine.begin() as conn:
        conn.execute(
            agent_versions.update()
            .where(agent_versions.c.id == "revenue-ops-agent_v1")
            .values(allowed_scopes=custom_scopes)
        )

    _run_in_migration_context(
        engine,
        "upgrade",
        path=RUN_ORCHESTRATION_MIGRATION_PATH,
        module_name="migration_20260709_0012_run_orchestration",
    )

    with engine.connect() as conn:
        version_row = conn.execute(
            sa.select(agent_versions).where(
                agent_versions.c.id == "revenue-ops-agent_v1"
            )
        ).mappings().one()
    assert version_row["allowed_scopes"] == custom_scopes
    engine.dispose()


def test_run_orchestration_migration_downgrade_reverses_schema_changes(
    tmp_path,
) -> None:
    engine, _ = _build_pre_phase3_schema(tmp_path)

    _run_in_migration_context(
        engine,
        "upgrade",
        path=RUN_ORCHESTRATION_MIGRATION_PATH,
        module_name="migration_20260709_0012_run_orchestration",
    )
    _run_in_migration_context(
        engine,
        "downgrade",
        path=RUN_ORCHESTRATION_MIGRATION_PATH,
        module_name="migration_20260709_0012_run_orchestration",
    )

    inspector = inspect(engine)
    runs_cols = {col["name"]: col for col in inspector.get_columns("agent_runs")}
    steps_cols = {col["name"]: col for col in inspector.get_columns("agent_run_steps")}
    assert runs_cols["incident_id"]["nullable"] is False
    assert "blocked_reason" not in steps_cols
    engine.dispose()


def test_run_orchestration_migration_backfills_all_empty_scope_versions(
    tmp_path,
) -> None:
    """A pre-Phase-3 published version other than v1 also has empty scopes; the
    backfill must cover it (not just the seeded v1) or every data tool on it
    would be blocked once scope enforcement activates."""
    engine, agent_versions = _build_pre_phase3_schema(tmp_path)
    other_version_id = "revenue-ops-agent_v2"
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with engine.begin() as conn:
        conn.execute(
            agent_versions.insert().values(
                id=other_version_id,
                agent_id="revenue-ops-agent",
                version_number=2,
                semantic_version="2.0.0",
                status="published",
                system_prompt="",
                model="gpt-4o-mini",
                temperature=0.1,
                max_tokens=1024,
                enabled_tool_ids=[],
                allowed_scopes=[],  # pre-Phase-3 empty scopes
                forked_from_version_id="revenue-ops-agent_v1",
                published_by="bootstrap",
                published_at=now,
                created_at=now,
                updated_at=now,
            )
        )

    _run_in_migration_context(
        engine,
        "upgrade",
        path=RUN_ORCHESTRATION_MIGRATION_PATH,
        module_name="migration_20260709_0012_run_orchestration",
    )

    with engine.connect() as conn:
        rows = {
            row["id"]: row["allowed_scopes"]
            for row in conn.execute(sa.select(agent_versions)).mappings()
        }
    expected = ["read_data", "write_mock_action", "request_approval"]
    assert rows["revenue-ops-agent_v1"] == expected
    assert rows[other_version_id] == expected
    engine.dispose()


def test_run_orchestration_migration_downgrade_reverses_after_non_incident_runs(
    tmp_path,
) -> None:
    """The downgrade must remain reversible after NULL-incident control-plane
    runs were created: it deletes them before re-imposing NOT NULL."""
    engine, _ = _build_pre_phase3_schema(tmp_path)

    _run_in_migration_context(
        engine,
        "upgrade",
        path=RUN_ORCHESTRATION_MIGRATION_PATH,
        module_name="migration_20260709_0012_run_orchestration",
    )

    # Insert a non-incident control-plane run (only legal post-upgrade).
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO agent_runs (id, incident_id, agent_id, agent_version_id, "
                "status, created_at, updated_at) "
                "VALUES ('run_no_incident', NULL, 'revenue-ops-agent', "
                "'revenue-ops-agent_v1', 'queued', '2026-07-09', '2026-07-09')"
            )
        )

    # Downgrade must succeed (delete the NULL row, restore NOT NULL).
    _run_in_migration_context(
        engine,
        "downgrade",
        path=RUN_ORCHESTRATION_MIGRATION_PATH,
        module_name="migration_20260709_0012_run_orchestration",
    )

    inspector = inspect(engine)
    runs_cols = {col["name"]: col for col in inspector.get_columns("agent_runs")}
    assert runs_cols["incident_id"]["nullable"] is False
    # The NULL-incident row was deleted by the downgrade.
    with engine.connect() as conn:
        remaining = conn.execute(
            sa.text("SELECT COUNT(*) FROM agent_runs WHERE id = 'run_no_incident'")
        ).scalar()
    assert remaining == 0
    engine.dispose()


def test_run_orchestration_migration_downgrade_preserves_customized_scopes(
    tmp_path,
) -> None:
    """The downgrade only resets scopes that still equal the v1 default; an
    operator's post-upgrade customization is preserved."""
    engine, agent_versions = _build_pre_phase3_schema(tmp_path)

    _run_in_migration_context(
        engine,
        "upgrade",
        path=RUN_ORCHESTRATION_MIGRATION_PATH,
        module_name="migration_20260709_0012_run_orchestration",
    )

    custom_scopes = ["read_data"]  # operator narrowed it after upgrade
    with engine.begin() as conn:
        conn.execute(
            agent_versions.update()
            .where(agent_versions.c.id == "revenue-ops-agent_v1")
            .values(allowed_scopes=custom_scopes)
        )

    _run_in_migration_context(
        engine,
        "downgrade",
        path=RUN_ORCHESTRATION_MIGRATION_PATH,
        module_name="migration_20260709_0012_run_orchestration",
    )

    with engine.connect() as conn:
        row = conn.execute(
            sa.select(agent_versions).where(
                agent_versions.c.id == "revenue-ops-agent_v1"
            )
        ).mappings().one()
    assert row["allowed_scopes"] == custom_scopes
    engine.dispose()

