from __future__ import annotations

from collections.abc import Callable, Generator
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

import app.models  # noqa: F401
from app.bootstrap import bootstrap_lock, run_startup_bootstrap
from app.db.base import Base
from app.models import Account, AgentVersion


@pytest.fixture()
def session_factory(tmp_path) -> Generator[Callable[[], Session], None, None]:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'bootstrap_test.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    yield TestingSessionLocal

    Base.metadata.drop_all(engine)
    engine.dispose()


def test_bootstrap_lock_is_noop_for_non_postgres_dialects(
    session_factory: Callable[[], Session],
) -> None:
    with session_factory() as session:
        engine = session.get_bind()

    with bootstrap_lock(engine):
        pass


def test_bootstrap_lock_acquires_and_releases_advisory_lock_for_postgres_dialects() -> None:
    from unittest.mock import MagicMock

    from app.bootstrap import BOOTSTRAP_LOCK_ID

    connection = MagicMock()
    connection.__enter__.return_value = connection
    connection.execution_options.return_value = connection

    engine = MagicMock()
    engine.dialect.name = "postgresql"
    engine.connect.return_value = connection

    with bootstrap_lock(engine):
        pass

    assert connection.execute.call_count == 2
    assert (
        connection.execute.call_args_list[0].args[0].text
        == "SELECT pg_advisory_lock(:lock_id)"
    )
    assert connection.execute.call_args_list[0].args[1] == {"lock_id": BOOTSTRAP_LOCK_ID}
    assert (
        connection.execute.call_args_list[1].args[0].text
        == "SELECT pg_advisory_unlock(:lock_id)"
    )
    assert connection.execute.call_args_list[1].args[1] == {"lock_id": BOOTSTRAP_LOCK_ID}


def test_run_startup_bootstrap_migrates_and_seeds_blank_database(
    session_factory: Callable[[], Session],
    monkeypatch,
) -> None:
    with session_factory() as session:
        monkeypatch.setattr("app.bootstrap.engine", session.get_bind())
    monkeypatch.setattr("app.bootstrap.SessionLocal", session_factory)
    migration_calls: list[str] = []

    def record_migration() -> None:
        migration_calls.append("upgrade")

    monkeypatch.setattr("app.bootstrap.run_migrations", record_migration)

    run_startup_bootstrap()

    assert migration_calls == ["upgrade"]
    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(Account)) == 60


def test_run_startup_bootstrap_skips_reseed_when_data_exists(
    session_factory: Callable[[], Session],
    monkeypatch,
) -> None:
    from app.seed import reseed_database

    with session_factory() as session:
        reseed_database(session)

    with session_factory() as session:
        monkeypatch.setattr("app.bootstrap.engine", session.get_bind())
    monkeypatch.setattr("app.bootstrap.SessionLocal", session_factory)

    with patch("app.bootstrap.run_migrations") as run_migrations:
        run_startup_bootstrap()

    run_migrations.assert_called_once()
    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(Account)) == 60


def test_run_startup_bootstrap_restores_missing_phase6_snapshots_idempotently(
    session_factory: Callable[[], Session],
    monkeypatch,
) -> None:
    from app.seed import reseed_database

    with session_factory() as session:
        reseed_database(session)
        v1 = session.get(AgentVersion, "revenue-ops-agent_v1")
        assert v1 is not None
        v1.system_prompt = "operator-customized v1 prompt"
        v1.model = "custom-model"
        v1.temperature = 0.7
        v1.max_tokens = 2048
        v1.enabled_tool_ids = ["query_revenue_metrics"]
        v1.allowed_scopes = ["read_data"]
        session.delete(
            session.get(AgentVersion, "revenue-ops-agent_phase6_degraded")
        )
        session.flush()
        session.delete(session.get(AgentVersion, "revenue-ops-agent_phase6"))
        session.commit()

    with session_factory() as session:
        monkeypatch.setattr("app.bootstrap.engine", session.get_bind())
    monkeypatch.setattr("app.bootstrap.SessionLocal", session_factory)
    monkeypatch.setattr("app.bootstrap.run_migrations", lambda: None)

    run_startup_bootstrap()
    run_startup_bootstrap()

    with session_factory() as session:
        v1 = session.get(AgentVersion, "revenue-ops-agent_v1")
        phase6 = session.get(AgentVersion, "revenue-ops-agent_phase6")
        degraded = session.get(AgentVersion, "revenue-ops-agent_phase6_degraded")
        assert v1 is not None
        assert phase6 is not None
        assert degraded is not None
        assert phase6.forked_from_version_id == v1.id
        assert phase6.system_prompt == v1.system_prompt
        assert phase6.model == v1.model
        assert phase6.temperature == v1.temperature
        assert phase6.max_tokens == v1.max_tokens
        assert v1.enabled_tool_ids == ["query_revenue_metrics"]
        assert v1.allowed_scopes == ["read_data"]
        assert degraded.forked_from_version_id == phase6.id
        assert degraded.system_prompt == phase6.system_prompt
        assert degraded.model == phase6.model
        assert degraded.temperature == phase6.temperature
        assert degraded.max_tokens == phase6.max_tokens
        fixed_snapshot_count = session.scalar(
            select(func.count())
            .select_from(AgentVersion)
            .where(
                AgentVersion.id.in_(
                    [
                        "revenue-ops-agent_phase6",
                        "revenue-ops-agent_phase6_degraded",
                    ]
                )
            )
        )
        assert fixed_snapshot_count == 2
