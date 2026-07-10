from __future__ import annotations

import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
import sqlalchemy as sa
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.agents.service import DEFAULT_AGENT_VERSION_ID, PHASE6_AGENT_VERSION_ID
from app.models import Account, AgentVersion, Tool
from app.seed import ensure_seeded_if_empty
from app.tools.service import register_builtin_tools

API_ROOT = Path(__file__).resolve().parent.parent
PROJECT1_HEAD = "20260706_0009"


def _run_alembic(database_url: str, revision: str) -> None:
    env = os.environ.copy()
    env["DATABASE_URL"] = database_url
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", revision],
        cwd=API_ROOT,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )


@pytest.mark.skipif(
    not os.environ.get("DATABASE_URL", "").startswith("postgresql"),
    reason="requires the CI PostgreSQL service",
)
def test_populated_project1_database_upgrades_to_phase6_and_bootstraps() -> None:
    """Exercise the supported 0009 -> HEAD path against real PostgreSQL.

    Migration 0015 runs before startup seeding on a Project 1 installation, so
    its data migration cannot assume the v1 agent row already exists. This test
    preserves a representative Project 1 row, runs every Project 2 migration,
    then executes the same registry/seed services as startup bootstrap.
    """
    configured_url = make_url(os.environ["DATABASE_URL"])
    database_name = f"ops_agent_upgrade_{uuid4().hex[:12]}"
    admin_url = configured_url.set(database="postgres")
    upgrade_url = configured_url.set(database=database_name)
    admin_engine = sa.create_engine(admin_url, isolation_level="AUTOCOMMIT")

    with admin_engine.connect() as connection:
        connection.exec_driver_sql(f'CREATE DATABASE "{database_name}"')

    upgrade_engine = None
    try:
        _run_alembic(upgrade_url.render_as_string(hide_password=False), PROJECT1_HEAD)
        upgrade_engine = sa.create_engine(upgrade_url)
        created_at = datetime(2026, 7, 1, tzinfo=UTC).replace(tzinfo=None)
        with upgrade_engine.begin() as connection:
            connection.execute(
                sa.text(
                    "INSERT INTO accounts "
                    "(id, name, segment, industry, region, health_score, "
                    "source_scenario, created_at, is_active) "
                    "VALUES (:id, :name, :segment, :industry, :region, "
                    ":health_score, :source_scenario, :created_at, :is_active)"
                ),
                {
                    "id": "acct_project1_upgrade",
                    "name": "Project 1 Historical Account",
                    "segment": "enterprise",
                    "industry": "software",
                    "region": "NA",
                    "health_score": 71,
                    "source_scenario": "project1_upgrade_guard",
                    "created_at": created_at,
                    "is_active": True,
                },
            )
        upgrade_engine.dispose()
        upgrade_engine = None

        _run_alembic(upgrade_url.render_as_string(hide_password=False), "head")
        upgrade_engine = sa.create_engine(upgrade_url)
        with Session(upgrade_engine) as session:
            register_builtin_tools(session)
            assert ensure_seeded_if_empty(session) is None

            historical = session.get(Account, "acct_project1_upgrade")
            assert historical is not None
            assert historical.name == "Project 1 Historical Account"

            v1 = session.get(AgentVersion, DEFAULT_AGENT_VERSION_ID)
            phase6 = session.get(AgentVersion, PHASE6_AGENT_VERSION_ID)
            degraded = session.get(
                AgentVersion, "revenue-ops-agent_phase6_degraded"
            )
            assert v1 is not None
            assert phase6 is not None
            assert phase6.forked_from_version_id == v1.id
            assert degraded is not None
            assert degraded.forked_from_version_id == phase6.id
            assert session.scalar(sa.select(sa.func.count()).select_from(Tool)) == 7
    finally:
        if upgrade_engine is not None:
            upgrade_engine.dispose()
        with admin_engine.connect() as connection:
            connection.exec_driver_sql(
                f'DROP DATABASE IF EXISTS "{database_name}" WITH (FORCE)'
            )
        admin_engine.dispose()
