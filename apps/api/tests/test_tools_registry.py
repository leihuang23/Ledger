from __future__ import annotations

from collections.abc import Callable, Generator
from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

import app.models  # noqa: F401
from app.db.base import Base
from app.db.session import get_db
from app.main import app
from app.agents.service import DEFAULT_AGENT_ID, DEFAULT_AGENT_VERSION_ID
from app.approvals.schemas import MockActionCreate
from app.models import AgentRun, AgentVersion, ApprovalRequest, Incident, MockAction, Tool
from app.seed import reseed_database
from app.tools.policy import REASON_SCOPE_NOT_ALLOWED, can_call_tool
from app.tools.registry import (
    BUILTIN_TOOL_BY_ID,
    BUILTIN_TOOL_DEFINITIONS,
    resolve_implementation_ref,
)
from app.tools.service import register_builtin_tools


@pytest.fixture()
def session_factory(tmp_path) -> Generator[Callable[[], Session], None, None]:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'tools_registry_test.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    testing_session_local = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
    )

    with testing_session_local() as session:
        reseed_database(session)

    yield testing_session_local

    Base.metadata.drop_all(engine)
    engine.dispose()


@pytest.fixture()
def client(
    session_factory: Callable[[], Session],
) -> Generator[TestClient, None, None]:
    def override_get_db() -> Generator[Session, None, None]:
        with session_factory() as db:
            yield db

    app.dependency_overrides[get_db] = override_get_db
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def test_tool_registry_lists_the_governed_builtin_surface(client: TestClient) -> None:
    response = client.get("/tools")

    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 8
    assert {tool["id"] for tool in payload["tools"]} == {
        "query_revenue_metrics",
        "fetch_account_details",
        "search_docs",
        "fetch_support_tickets",
        "list_pending_approvals",
        "create_mock_action",
        "request_approval",
        "run_eval",
    }


def test_builtin_registry_schemas_and_callables_match_source_contracts(
    session_factory: Callable[[], Session],
) -> None:
    with session_factory() as session:
        tools = {
            tool.id: tool
            for tool in session.query(Tool).order_by(Tool.id).all()
        }

    assert set(tools) == {definition.id for definition in BUILTIN_TOOL_DEFINITIONS}
    for definition in BUILTIN_TOOL_DEFINITIONS:
        tool = tools[definition.id]
        assert tool.input_schema == definition.input_model.model_json_schema()
        assert tool.output_schema == definition.output_model.model_json_schema()
        assert tool.permission_scope == definition.permission_scope
        assert resolve_implementation_ref(tool.implementation_ref) is definition.implementation


def test_builtin_registration_is_idempotent(
    session_factory: Callable[[], Session],
) -> None:
    with session_factory() as session:
        register_builtin_tools(session)
        register_builtin_tools(session)
        assert session.query(Tool).count() == len(BUILTIN_TOOL_DEFINITIONS)


def test_tool_detail_returns_schema_and_unknown_id_is_404(client: TestClient) -> None:
    response = client.get("/tools/search_docs")
    missing = client.get("/tools/not_registered")

    assert response.status_code == 200
    assert response.json()["permission_scope"] == "read_data"
    assert response.json()["input_schema"]["title"] == "SearchDocsInput"
    assert response.json()["output_schema"]["title"] == "SearchDocsOutput"
    assert missing.status_code == 404


def test_register_tool_rejects_aliases_outside_the_closed_builtin_catalog(
    client: TestClient,
) -> None:
    payload = {
        "id": "search_docs_alias",
        "name": "search_docs_alias",
        "description": "A separately governed alias for the document search callable.",
        "input_schema": {"type": "object"},
        "output_schema": {"type": "object"},
        "permission_scope": "read_data",
        "implementation_ref": "app.agent.tools.search_docs",
    }

    created = client.post("/tools", json=payload)
    dangling = client.post(
        "/tools",
        json={
            **payload,
            "id": "dangling_tool",
            "name": "dangling_tool",
            "implementation_ref": "app.agent.tools.not_a_callable",
        },
    )

    assert created.status_code == 422
    assert "closed built-in catalog" in created.json()["detail"]
    assert dangling.status_code == 422


def test_every_registered_tool_can_be_attached_to_a_version(client: TestClient) -> None:
    tool_ids = [tool["id"] for tool in client.get("/tools").json()["tools"]]

    response = client.post(
        "/agents/ledger/versions",
        json={
            "fork_from_version_id": "ledger_v1",
            "enabled_tool_ids": tool_ids,
            "allowed_scopes": [
                "read_data",
                "write_mock_action",
                "request_approval",
                "run_eval",
            ],
        },
    )

    assert response.status_code == 201
    assert set(response.json()["enabled_tool_ids"]) == set(tool_ids)


def test_register_tool_fails_closed_in_demo_without_operator_token(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core.config import get_settings

    monkeypatch.setenv("APP_ENV", "demo")
    monkeypatch.delenv("DEMO_OPERATOR_TOKEN", raising=False)
    get_settings.cache_clear()
    try:
        response = client.post(
            "/tools",
            json={
                "id": "demo_blocked_tool",
                "name": "demo_blocked_tool",
                "description": "Must not be created without the demo operator token.",
                "input_schema": {"type": "object"},
                "output_schema": {"type": "object"},
                "permission_scope": "read_data",
                "implementation_ref": "app.agent.tools.search_docs",
            },
        )
    finally:
        monkeypatch.setenv("APP_ENV", "local")
        get_settings.cache_clear()

    assert response.status_code == 403


def test_run_eval_scope_is_enforced_by_the_existing_runtime_policy() -> None:
    version = AgentVersion(
        id="eval-policy-version",
        agent_id="ledger",
        status="published",
        model="gpt-4o-mini",
        enabled_tool_ids=["run_eval"],
        allowed_scopes=["read_data"],
    )

    assert can_call_tool(version, "run_eval") == (False, REASON_SCOPE_NOT_ALLOWED)
    version.allowed_scopes = ["run_eval"]
    assert can_call_tool(version, "run_eval") == (True, None)


def test_create_only_tool_cannot_generate_a_high_risk_approval(
    session_factory: Callable[[], Session],
) -> None:
    create_only_version = AgentVersion(
        id="create-only-version",
        agent_id=DEFAULT_AGENT_ID,
        status="published",
        model="gpt-4o-mini",
        enabled_tool_ids=["create_mock_action"],
        allowed_scopes=["write_mock_action"],
    )
    assert can_call_tool(create_only_version, "create_mock_action") == (True, None)
    assert can_call_tool(create_only_version, "request_approval") == (
        False,
        "tool_not_enabled",
    )

    with session_factory() as session:
        incident_id = session.scalar(select(Incident.id).limit(1))
        assert incident_id is not None
        now = datetime(2026, 7, 10, 12, 0, 0)
        run = AgentRun(
            id="run_create_only_registry",
            incident_id=incident_id,
            agent_id=DEFAULT_AGENT_ID,
            agent_version_id=DEFAULT_AGENT_VERSION_ID,
            status="succeeded",
            trace_id="local-create-only",
            input_payload={"incident_id": incident_id},
            final_report=None,
            token_estimate=0,
            cost_estimate_usd=0.0,
            error=None,
            started_at=now,
            completed_at=now,
            created_at=now,
            updated_at=now,
        )
        session.add(run)
        session.commit()
        payload = MockActionCreate(
            run_id=run.id,
            action_type="draft_customer_email",
            title="Draft customer email",
            description="Must require the approval tool.",
            target="affected customers",
            payload={"subject": "Status", "body": "Draft only."},
        )

        with pytest.raises(ValueError, match="requires the request_approval tool"):
            BUILTIN_TOOL_BY_ID["create_mock_action"].implementation(session, payload)

        assert session.query(MockAction).count() == 0
        assert session.query(ApprovalRequest).count() == 0


def test_tool_rows_carry_created_at_and_updated_at_timestamps(
    session_factory: Callable[[], Session],
) -> None:
    """PRD §9.1: the tools table must carry created_at/updated_at timestamps.
    Every seeded built-in tool must have both set after bootstrap registration."""
    with session_factory() as session:
        tools = session.query(Tool).order_by(Tool.id).all()
        assert len(tools) == len(BUILTIN_TOOL_DEFINITIONS)
        for tool in tools:
            assert tool.created_at is not None
            assert tool.updated_at is not None


def test_register_builtin_tools_preserves_created_at_and_bumps_updated_at(
    session_factory: Callable[[], Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Re-registering built-in tools (the idempotent bootstrap path) must keep
    ``created_at`` stable while advancing ``updated_at`` to reflect the sync."""
    from datetime import timedelta

    import app.tools.service as tools_service

    with session_factory() as session:
        original = session.get(Tool, "search_docs")
        assert original is not None
        original_created = original.created_at
        original_updated = original.updated_at

    # Advance the clock so updated_at must move forward past the original.
    future = original_updated + timedelta(seconds=10)
    monkeypatch.setattr(tools_service, "utcnow_naive", lambda: future)

    register_builtin_tools(session_factory())

    with session_factory() as session:
        refreshed = session.get(Tool, "search_docs")
        assert refreshed is not None
        assert refreshed.created_at == original_created
        assert refreshed.updated_at == future


def test_tool_permission_scope_check_rejects_invalid_scope(
    session_factory: Callable[[], Session],
) -> None:
    """PRD §9.1: permission_scope is a DB-level enum (CHECK constraint) so a
    raw DB insert outside the Pydantic-validated API cannot land an invalid scope.

    Explicit ``created_at``/``updated_at`` are passed so the only possible
    violation is the CHECK constraint on ``permission_scope`` (not the NOT NULL
    on the timestamp columns), making the assertion unambiguous. The raw-SQL
    migration-level test in ``test_migrations.py`` covers the same constraint
    at the DB layer.
    """
    now = datetime.now()
    with session_factory() as session:
        invalid = Tool(
            id="invalid_scope_tool",
            name="invalid_scope_tool",
            description="Should be rejected by the CHECK constraint.",
            input_schema={"type": "object"},
            output_schema={"type": "object"},
            permission_scope="delete_production",
            implementation_ref="app.agent.tools.search_docs",
            created_at=now,
            updated_at=now,
        )
        session.add(invalid)
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()


def test_tool_read_exposes_timestamps(client: TestClient) -> None:
    """The tool registry API surface exposes created_at/updated_at (PRD §9.1)."""
    response = client.get("/tools/search_docs")
    assert response.status_code == 200
    payload = response.json()
    assert "created_at" in payload
    assert "updated_at" in payload
    assert payload["created_at"] is not None
    assert payload["updated_at"] is not None
