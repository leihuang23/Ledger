from __future__ import annotations

from collections.abc import Callable, Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

import app.models  # noqa: F401
from app.db.base import Base
from app.db.session import get_db
from app.main import app
from app.models import Account
from app.seed import reseed_database


@pytest.fixture()
def session_factory(tmp_path) -> Generator[Callable[[], Session], None, None]:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'accounts_test.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    yield TestingSessionLocal

    Base.metadata.drop_all(engine)
    engine.dispose()


def test_list_accounts_returns_seeded_accounts(
    session_factory: Callable[[], Session],
) -> None:
    with session_factory() as session:
        reseed_database(session)
        account = session.scalar(select(Account))
        assert account is not None
        account_id = account.id
        account_name = account.name

    def override_get_db() -> Generator[Session, None, None]:
        with session_factory() as db:
            yield db

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)
    try:
        response = client.get("/accounts")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] > 0
    assert isinstance(payload["accounts"], list)
    assert any(a["id"] == account_id for a in payload["accounts"])

    matched = next(a for a in payload["accounts"] if a["id"] == account_id)
    assert matched["name"] == account_name
    assert matched["segment"]
    assert "health_score" in matched
    assert "is_active" in matched


def test_account_detail_returns_404_for_unknown_account(
    session_factory: Callable[[], Session],
) -> None:
    def override_get_db() -> Generator[Session, None, None]:
        with session_factory() as db:
            yield db

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)
    try:
        response = client.get("/accounts/acc_unknown")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 404
