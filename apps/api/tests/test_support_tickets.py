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
from app.models import SupportTicket
from app.seed import reseed_database


@pytest.fixture()
def session_factory(tmp_path) -> Generator[Callable[[], Session], None, None]:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'support_tickets_test.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    yield TestingSessionLocal

    Base.metadata.drop_all(engine)
    engine.dispose()


def test_list_support_tickets_and_detail(
    session_factory: Callable[[], Session],
) -> None:
    with session_factory() as session:
        reseed_database(session)
        ticket = session.scalar(select(SupportTicket))
        assert ticket is not None
        ticket_id = ticket.id

    def override_get_db() -> Generator[Session, None, None]:
        with session_factory() as db:
            yield db

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)
    try:
        list_response = client.get("/support/tickets")
        detail_response = client.get(f"/support/tickets/{ticket_id}")
    finally:
        app.dependency_overrides.clear()

    assert list_response.status_code == 200
    payload = list_response.json()
    assert payload["total"] > 0
    assert any(t["id"] == ticket_id for t in payload["tickets"])

    assert detail_response.status_code == 200
    detail = detail_response.json()
    assert detail["id"] == ticket_id
    assert detail["subject"]
    assert detail["description"]
    assert detail["account_id"]
    assert detail["account_name"]
    assert detail["status"]


def test_support_ticket_detail_returns_404_for_unknown_ticket(
    session_factory: Callable[[], Session],
) -> None:
    def override_get_db() -> Generator[Session, None, None]:
        with session_factory() as db:
            yield db

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)
    try:
        response = client.get("/support/tickets/tkt_unknown")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 404
