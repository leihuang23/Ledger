"""End-to-end tests for tool-policy enforcement at run dispatch (P3-T14, I-11).

Closes the ``app.tools.policy`` unit tests: when an agent version enables a tool
but omits the tool's permission scope from ``allowed_scopes``, the run must
record a visible ``blocked`` step with reason ``scope_not_allowed`` instead of
dispatching the tool. The companion regression test guards against the C2 policy
filter over-blocking the happy path.
"""

from __future__ import annotations

from collections.abc import Callable, Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

import app.models  # noqa: F401  (registers mapped classes on Base.metadata)
from app.db.base import Base
from app.db.session import get_db
from app.main import app
from app.models import Incident
from app.seed import reseed_database


@pytest.fixture()
def session_factory(tmp_path) -> Generator[Callable[[], Session], None, None]:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'blocked_tool_steps_test.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    yield TestingSessionLocal

    Base.metadata.drop_all(engine)
    engine.dispose()


def _seed_incident_id(session_factory: Callable[[], Session]) -> str:
    with session_factory() as session:
        reseed_database(session)
        incident_id = session.scalar(select(Incident.id))
    assert incident_id is not None
    return incident_id


def _client_with_session(session_factory: Callable[[], Session]) -> TestClient:
    def override_get_db() -> Generator[Session, None, None]:
        with session_factory() as db:
            yield db

    app.dependency_overrides[get_db] = override_get_db
    return TestClient(app)


_READ_DATA_TOOLS = [
    "query_revenue_metrics",
    "fetch_account_details",
    "search_docs",
    "fetch_support_tickets",
]


def test_scope_removed_records_blocked_step(
    session_factory: Callable[[], Session],
) -> None:
    """P3-T14 / I-11: a tool enabled on the version whose scope is not in
    ``allowed_scopes`` is recorded as a visible ``blocked`` step with reason
    ``scope_not_allowed`` (end-to-end, closing the tool-policy unit test)."""
    incident_id = _seed_incident_id(session_factory)

    client = _client_with_session(session_factory)
    try:
        # All four data tools enabled, but ``read_data`` is revoked from the
        # allowed scopes -> every read tool must be blocked at dispatch.
        version_resp = client.post(
            "/agents/revenue-ops-agent/versions",
            json={
                "enabled_tool_ids": _READ_DATA_TOOLS,
                "allowed_scopes": ["write_mock_action", "request_approval"],
            },
        )
        assert version_resp.status_code == 201
        version_id = version_resp.json()["id"]

        publish_resp = client.post(
            f"/agents/revenue-ops-agent/versions/{version_id}/publish"
        )
        assert publish_resp.status_code == 200

        response = client.post(
            "/agent/investigations",
            json={
                "incident_id": incident_id,
                "run_inline": True,
                "force": True,
                "agent_version_id": version_id,
            },
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 201
    payload = response.json()

    blocked_steps = [step for step in payload["steps"] if step["status"] == "blocked"]
    assert blocked_steps, "expected at least one blocked step"
    blocked_tool_names = {step["tool_name"] for step in blocked_steps}
    # All four read tools are enabled but scope-revoked -> all blocked.
    assert blocked_tool_names == set(_READ_DATA_TOOLS)
    for step in blocked_steps:
        assert step["blocked_reason"] == "scope_not_allowed"
        assert step["outputs"]["tool_disabled"] is True
    # No read-data tool was actually dispatched (``propose_actions`` and other
    # internal stages may still succeed).
    succeeded_read_tools = {
        step["tool_name"]
        for step in payload["steps"]
        if step["status"] == "succeeded" and step["tool_name"] in _READ_DATA_TOOLS
    }
    assert succeeded_read_tools == set()
    # The run still completes with a degraded-evidence final report.
    assert payload["final_report"] is not None


def test_enabled_tool_with_allowed_scope_still_runs(
    session_factory: Callable[[], Session],
) -> None:
    """Regression: the C2 policy filter must not over-block. A v1-forked version
    that inherits the backfilled ``read_data`` scope still dispatches its
    enabled tools as ``succeeded`` steps with no ``blocked`` steps."""
    incident_id = _seed_incident_id(session_factory)

    client = _client_with_session(session_factory)
    try:
        # Inherit allowed_scopes from the seeded v1 (read_data included) by
        # omitting allowed_scopes here.
        version_resp = client.post(
            "/agents/revenue-ops-agent/versions",
            json={"enabled_tool_ids": _READ_DATA_TOOLS},
        )
        assert version_resp.status_code == 201
        version_id = version_resp.json()["id"]

        publish_resp = client.post(
            f"/agents/revenue-ops-agent/versions/{version_id}/publish"
        )
        assert publish_resp.status_code == 200

        response = client.post(
            "/agent/investigations",
            json={
                "incident_id": incident_id,
                "run_inline": True,
                "force": True,
                "agent_version_id": version_id,
            },
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 201
    payload = response.json()
    assert payload["status"] == "succeeded"
    assert [s for s in payload["steps"] if s["status"] == "blocked"] == []
    tool_steps = [s for s in payload["steps"] if s.get("tool_name")]
    assert tool_steps, "expected dispatched tool steps"
    assert all(s["status"] == "succeeded" for s in tool_steps)
