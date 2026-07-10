from __future__ import annotations

import pytest

from app.models import AgentVersion
from app.tools.policy import (
    REASON_SCOPE_NOT_ALLOWED,
    REASON_TOOL_NOT_ENABLED,
    can_call_tool,
)
from app.tools.scopes import ALLOWED_SCOPES, TOOL_SCOPES

# The data tools that carry the ``read_data`` scope.
READ_DATA_TOOLS = [tool_id for tool_id, scope in TOOL_SCOPES.items() if scope == "read_data"]


def _version(
    *,
    enabled_tool_ids: list[str] | None = None,
    allowed_scopes: list[str] | None = None,
) -> AgentVersion:
    """Build a detached AgentVersion for unit-testing the pure policy."""
    return AgentVersion(
        id="test-version",
        agent_id="revenue-ops-agent",
        version_number=1,
        semantic_version="1.0.0",
        status="published",
        system_prompt="",
        model="gpt-4o-mini",
        temperature=0.1,
        max_tokens=1024,
        enabled_tool_ids=list(enabled_tool_ids) if enabled_tool_ids is not None else [],
        allowed_scopes=list(allowed_scopes) if allowed_scopes is not None else [],
    )


def test_can_call_tool_allows_enabled_tool_with_scope_allowed() -> None:
    """U-1: a tool in enabled_tool_ids whose scope is in allowed_scopes is callable."""
    version = _version(
        enabled_tool_ids=list(TOOL_SCOPES.keys()),
        allowed_scopes=["read_data", "write_mock_action", "request_approval"],
    )
    for tool_id in READ_DATA_TOOLS:
        allowed, reason = can_call_tool(version, tool_id)
        assert allowed is True, tool_id
        assert reason is None, tool_id


def test_can_call_tool_blocks_tool_not_in_enabled_tool_ids() -> None:
    """U-2 / U-4: a tool absent from enabled_tool_ids is blocked as tool_not_enabled."""
    version = _version(
        enabled_tool_ids=["fetch_account_details", "search_docs"],
        allowed_scopes=["read_data"],
    )
    allowed, reason = can_call_tool(version, "query_revenue_metrics")
    assert allowed is False
    assert reason == REASON_TOOL_NOT_ENABLED


def test_can_call_tool_blocks_enabled_tool_when_scope_removed() -> None:
    """U-3: a tool in enabled_tool_ids but whose scope is not in allowed_scopes
    is blocked as scope_not_allowed (PRD AC-2.5)."""
    version = _version(
        enabled_tool_ids=list(TOOL_SCOPES.keys()),
        allowed_scopes=["write_mock_action", "request_approval"],  # read_data removed
    )
    for tool_id in READ_DATA_TOOLS:
        allowed, reason = can_call_tool(version, tool_id)
        assert allowed is False, tool_id
        assert reason == REASON_SCOPE_NOT_ALLOWED, tool_id


def test_can_call_tool_blocks_any_tool_when_enabled_tool_ids_empty() -> None:
    """U-4: empty enabled_tool_ids blocks every tool as tool_not_enabled."""
    version = _version(enabled_tool_ids=[], allowed_scopes=["read_data"])
    for tool_id in TOOL_SCOPES:
        allowed, reason = can_call_tool(version, tool_id)
        assert allowed is False, tool_id
        assert reason == REASON_TOOL_NOT_ENABLED, tool_id


def test_can_call_tool_blocks_unknown_tool_id() -> None:
    """U-5: an unknown tool id is blocked as tool_not_enabled (it cannot be in
    enabled_tool_ids, and is unknown to the scope registry)."""
    version = _version(
        enabled_tool_ids=list(TOOL_SCOPES.keys()) + ["does_not_exist"],
        allowed_scopes=list(ALLOWED_SCOPES),
    )
    allowed, reason = can_call_tool(version, "does_not_exist")
    assert allowed is False
    assert reason == REASON_TOOL_NOT_ENABLED


def test_can_call_tool_treats_none_fields_as_empty() -> None:
    """A version with None enabled_tool_ids / allowed_scopes does not raise."""
    version = AgentVersion(
        id="test-version",
        agent_id="revenue-ops-agent",
        version_number=1,
        semantic_version="1.0.0",
        status="published",
        system_prompt="",
        model="gpt-4o-mini",
        temperature=0.1,
        max_tokens=1024,
        enabled_tool_ids=None,  # type: ignore[arg-type]
        allowed_scopes=None,  # type: ignore[arg-type]
    )
    allowed, reason = can_call_tool(version, "query_revenue_metrics")
    assert allowed is False
    assert reason == REASON_TOOL_NOT_ENABLED


def test_default_v1_scopes_allow_all_data_tools() -> None:
    """The seeded v1 scopes (PRD §9.5) permit every read_data tool."""
    from app.tools.scopes import DEFAULT_V1_ALLOWED_SCOPES

    version = _version(
        enabled_tool_ids=list(TOOL_SCOPES.keys()),
        allowed_scopes=list(DEFAULT_V1_ALLOWED_SCOPES),
    )
    for tool_id in READ_DATA_TOOLS:
        allowed, reason = can_call_tool(version, tool_id)
        assert allowed is True, (tool_id, reason)
        assert reason is None, tool_id


def test_phase6_scopes_include_audited_run_eval_capability() -> None:
    """Phase 6 exposes run_eval in addition to the endpoint's operator token gate."""
    from app.tools.scopes import DEFAULT_V1_ALLOWED_SCOPES, PHASE6_ALLOWED_SCOPES

    assert "run_eval" not in DEFAULT_V1_ALLOWED_SCOPES
    assert "run_eval" in PHASE6_ALLOWED_SCOPES
    version = _version(
        enabled_tool_ids=["run_eval"],
        allowed_scopes=list(PHASE6_ALLOWED_SCOPES),
    )
    assert can_call_tool(version, "run_eval") == (True, None)


if __name__ == "__main__":
    pytest.main([__file__, "-q"])
