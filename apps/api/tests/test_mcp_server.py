"""Behavior tests for the Ledger MCP server adapter.

Claims under test (product intent):
- Read evidence tools dispatch through the existing registry and return cited
  evidence without any token.
- Governed tools honor the same fail-closed token semantics as the HTTP demo
  gate: ``demo`` env with no ``DEMO_OPERATOR_TOKEN`` rejects them, and a
  mismatched ``MCP_OPERATOR_TOKEN`` is rejected.
- With a valid token, ``request_approval`` routes into the existing approval
  service and produces a pending approval (nothing executes).
- The registry's own guards still apply over MCP (high-risk types cannot use
  the low-risk write tool; malformed payloads fail visibly).
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable, Generator
from datetime import datetime

import pytest
from mcp.server.fastmcp.exceptions import ToolError
from mcp.types import TextContent
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

import app.models  # noqa: F401
from app.agents.service import DEFAULT_AGENT_ID, DEFAULT_AGENT_VERSION_ID
from app.core.config import get_settings
from app.db.base import Base
from app.mcp.server import create_server
from app.models import AgentRun, ApprovalRequest, Incident, MockAction
from app.seed import reseed_database


@pytest.fixture()
def session_factory(tmp_path) -> Generator[Callable[[], Session], None, None]:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'mcp_server_test.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    yield TestingSessionLocal

    Base.metadata.drop_all(engine)
    engine.dispose()


@pytest.fixture()
def seeded_run_id(session_factory: Callable[[], Session]) -> str:
    """Seed the demo dataset plus one succeeded run bound to an incident."""
    with session_factory() as session:
        reseed_database(session)
        incident_id = session.scalar(select(Incident.id))
        assert incident_id is not None
        now = datetime(2026, 6, 9, 12, 30, 0)
        run = AgentRun(
            id="run_mcp_test",
            incident_id=incident_id,
            agent_id=DEFAULT_AGENT_ID,
            agent_version_id=DEFAULT_AGENT_VERSION_ID,
            status="succeeded",
            trace_id="local-trace-run_mcp_test",
            input_payload={"incident_id": incident_id},
            final_report=None,
            token_estimate=1,
            cost_estimate_usd=0.0,
            error=None,
            started_at=now,
            completed_at=now,
            created_at=now,
            updated_at=now,
        )
        session.add(run)
        session.commit()
        return run.id


@pytest.fixture()
def waiting_run_id(
    session_factory: Callable[[], Session], seeded_run_id: str
) -> str:
    """A run parked at the approval checkpoint (the operator-legit window)."""
    with session_factory() as session:
        incident_id = session.get(AgentRun, seeded_run_id).incident_id
        now = datetime(2026, 6, 9, 12, 30, 0)
        run = AgentRun(
            id="run_mcp_waiting",
            incident_id=incident_id,
            agent_id=DEFAULT_AGENT_ID,
            agent_version_id=DEFAULT_AGENT_VERSION_ID,
            status="waiting_for_approval",
            trace_id="local-trace-run_mcp_waiting",
            input_payload={"incident_id": incident_id},
            final_report=None,
            token_estimate=1,
            cost_estimate_usd=0.0,
            error=None,
            started_at=now,
            completed_at=None,
            created_at=now,
            updated_at=now,
        )
        session.add(run)
        session.commit()
        return run.id


def _settings_env(monkeypatch: pytest.MonkeyPatch, **env: str | None) -> None:
    for key, value in env.items():
        if value is None:
            monkeypatch.delenv(key, raising=False)
        else:
            monkeypatch.setenv(key, value)
    get_settings.cache_clear()


def _call(server, name: str, arguments: dict) -> tuple:
    """Return ``(content, structured)`` from FastMCP's ``call_tool``.

    FastMCP raises ``ToolError`` on tool failure when invoked directly, so a
    rejection is normalized into error text content for uniform assertions.
    """
    try:
        return asyncio.run(server.call_tool(name, arguments))
    except ToolError as exc:
        return [TextContent(type="text", text=str(exc))], None


def _result_text(result: tuple) -> str:
    content, _structured = result
    return "\n".join(getattr(item, "text", str(item)) for item in content)


def _is_error(result: tuple) -> bool:
    """FastMCP converts tool exceptions into error text content."""
    return "error" in _result_text(result).lower()


def test_read_tool_returns_cited_evidence_without_token(
    session_factory: Callable[[], Session],
    seeded_run_id: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with session_factory() as session:
        incident_id = session.get(AgentRun, seeded_run_id).incident_id
    _settings_env(monkeypatch, DEMO_OPERATOR_TOKEN=None, MCP_OPERATOR_TOKEN=None)
    try:
        server = create_server(session_factory=session_factory)
        result = _call(
            server,
            "query_revenue_metrics",
            {"payload": {"incident_id": incident_id}},
        )
        payload = json.loads(_result_text(result))
        assert payload["incident_id"] == incident_id
        assert payload["metric_evidence"]
    finally:
        get_settings.cache_clear()


def test_governed_tool_fails_closed_in_demo_without_token(
    session_factory: Callable[[], Session],
    seeded_run_id: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _settings_env(
        monkeypatch,
        APP_ENV="demo",
        DEMO_OPERATOR_TOKEN=None,
        MCP_OPERATOR_TOKEN=None,
    )
    try:
        server = create_server(session_factory=session_factory)
        result = _call(
            server,
            "request_approval",
            {
                "payload": {
                    "run_id": seeded_run_id,
                    "action_type": "draft_customer_email",
                    "title": "Draft follow-up",
                    "description": "Customer follow-up draft.",
                    "target": "billing contact",
                    "payload": {"subject": "s", "body": "b"},
                }
            },
        )
        assert _is_error(result)
        assert "fail closed" in _result_text(result)
        with session_factory() as session:
            assert session.scalar(select(MockAction.id)) is None
    finally:
        get_settings.cache_clear()


def test_governed_tool_rejects_mismatched_token(
    session_factory: Callable[[], Session],
    seeded_run_id: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _settings_env(
        monkeypatch,
        DEMO_OPERATOR_TOKEN="server-token",
        MCP_OPERATOR_TOKEN="wrong-token",
    )
    try:
        server = create_server(session_factory=session_factory)
        result = _call(
            server,
            "create_mock_action",
            {
                "payload": {
                    "run_id": seeded_run_id,
                    "action_type": "draft_slack_message",
                    "title": "Internal update",
                    "description": "Draft Slack update.",
                    "target": "#revenue-ops",
                    "payload": {"message": "hello"},
                }
            },
        )
        assert _is_error(result)
        with session_factory() as session:
            assert session.scalar(select(MockAction.id)) is None
    finally:
        get_settings.cache_clear()


def test_request_approval_creates_pending_approval_with_valid_token(
    session_factory: Callable[[], Session],
    waiting_run_id: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _settings_env(
        monkeypatch,
        DEMO_OPERATOR_TOKEN="server-token",
        MCP_OPERATOR_TOKEN="server-token",
    )
    try:
        server = create_server(session_factory=session_factory)
        result = _call(
            server,
            "request_approval",
            {
                "payload": {
                    "run_id": waiting_run_id,
                    "action_type": "draft_customer_email",
                    "title": "Draft follow-up",
                    "description": "Customer follow-up draft.",
                    "target": "billing contact",
                    "payload": {"subject": "s", "body": "b"},
                }
            },
        )
        action = json.loads(_result_text(result))
        assert action["status"] == "pending_approval"
        assert action["risk_level"] == "high"
        # MCP-originated actions are attributed to the mcp-client actor so the
        # audit trail tells them apart from agent- and operator-injected ones.
        assert action["created_by"] == "mcp-client"

        with session_factory() as session:
            approvals = session.scalars(select(ApprovalRequest)).all()
            assert len(approvals) == 1
            assert approvals[0].status == "pending"

        listed_content, _structured = _call(
            server, "list_pending_approvals", {"payload": {}}
        )
        listed: list[dict] = []
        for item in listed_content:
            parsed = json.loads(item.text)
            listed.extend(parsed.get("items", []))
        assert [item["id"] for item in listed] == [approvals[0].id]
    finally:
        get_settings.cache_clear()


def test_governed_tool_rejects_high_risk_on_terminal_run(
    session_factory: Callable[[], Session],
    seeded_run_id: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A pending approval can never be attached to a terminal run, matching
    the operator API's run-state policy."""
    _settings_env(
        monkeypatch,
        DEMO_OPERATOR_TOKEN="server-token",
        MCP_OPERATOR_TOKEN="server-token",
    )
    try:
        server = create_server(session_factory=session_factory)
        result = _call(
            server,
            "request_approval",
            {
                "payload": {
                    "run_id": seeded_run_id,
                    "action_type": "draft_customer_email",
                    "title": "Draft follow-up",
                    "description": "Customer follow-up draft.",
                    "target": "billing contact",
                    "payload": {"subject": "s", "body": "b"},
                }
            },
        )
        assert _is_error(result)
        assert "terminal" in _result_text(result)
        with session_factory() as session:
            assert session.scalar(select(MockAction.id)) is None
    finally:
        get_settings.cache_clear()


def test_governed_tool_succeeds_in_demo_with_matching_token(
    session_factory: Callable[[], Session],
    waiting_run_id: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The demo gate fails closed only when the token is unset; a matching
    token still authorizes governed tools."""
    _settings_env(
        monkeypatch,
        APP_ENV="demo",
        DEMO_OPERATOR_TOKEN="server-token",
        MCP_OPERATOR_TOKEN="server-token",
    )
    try:
        server = create_server(session_factory=session_factory)
        result = _call(
            server,
            "request_approval",
            {
                "payload": {
                    "run_id": waiting_run_id,
                    "action_type": "draft_customer_email",
                    "title": "Draft follow-up",
                    "description": "Customer follow-up draft.",
                    "target": "billing contact",
                    "payload": {"subject": "s", "body": "b"},
                }
            },
        )
        action = json.loads(_result_text(result))
        assert action["status"] == "pending_approval"
    finally:
        get_settings.cache_clear()


def test_registry_risk_guard_applies_over_mcp(
    session_factory: Callable[[], Session],
    waiting_run_id: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """High-risk action types must be rejected by the low-risk write tool,
    even on a run whose lifecycle would otherwise allow the write."""
    _settings_env(monkeypatch, DEMO_OPERATOR_TOKEN=None, MCP_OPERATOR_TOKEN=None)
    try:
        server = create_server(session_factory=session_factory)
        result = _call(
            server,
            "create_mock_action",
            {
                "payload": {
                    "run_id": waiting_run_id,
                    "action_type": "draft_customer_email",
                    "title": "Draft follow-up",
                    "description": "Customer follow-up draft.",
                    "target": "billing contact",
                    "payload": {"subject": "s", "body": "b"},
                }
            },
        )
        assert _is_error(result)
        assert "request_approval" in _result_text(result)
        with session_factory() as session:
            assert session.scalar(select(MockAction.id)) is None
    finally:
        get_settings.cache_clear()


def test_malformed_payload_fails_visibly(
    session_factory: Callable[[], Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _settings_env(monkeypatch, DEMO_OPERATOR_TOKEN=None, MCP_OPERATOR_TOKEN=None)
    try:
        server = create_server(session_factory=session_factory)
        result = _call(server, "query_revenue_metrics", {"payload": {}})
        assert _is_error(result)
    finally:
        get_settings.cache_clear()


def test_tool_listing_exposes_scopes(
    session_factory: Callable[[], Session],
) -> None:
    server = create_server(session_factory=session_factory)
    tools = asyncio.run(server.list_tools())
    by_name = {tool.name: tool for tool in tools}
    assert set(by_name) == {
        "query_revenue_metrics",
        "fetch_account_details",
        "search_docs",
        "fetch_support_tickets",
        "create_mock_action",
        "request_approval",
        "list_pending_approvals",
    }
    assert "read_data" in by_name["query_revenue_metrics"].description
    assert "write_mock_action" in by_name["create_mock_action"].description
    assert "request_approval" in by_name["request_approval"].description
    # run_eval is intentionally not exposed over MCP in v1.
    assert "run_eval" not in set(by_name)
