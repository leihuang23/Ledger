from __future__ import annotations

from collections.abc import Callable, Generator
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

import app.models  # noqa: F401
from app.bootstrap import bootstrap_lock, run_startup_bootstrap
from app.db.base import Base
from app.models import Account


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


def test_run_startup_bootstrap_migrates_and_seeds_blank_database(
    session_factory: Callable[[], Session],
    monkeypatch,
) -> None:
    monkeypatch.setattr("app.bootstrap.engine", session_factory().bind)
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
