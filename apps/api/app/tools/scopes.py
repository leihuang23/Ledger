"""Fixed permission scopes and the runtime tool-to-scope policy mapping."""

from __future__ import annotations

from typing import Literal

# PRD FR-5: the fixed permission-scope enum.
PermissionScope = Literal[
    "read_data",
    "write_mock_action",
    "request_approval",
    "run_eval",
]

ALLOWED_SCOPES: frozenset[str] = frozenset(
    {
        "read_data",
        "write_mock_action",
        "request_approval",
        "run_eval",
    }
)

# The four data tools from ``app.agent.tools`` all read domain data, so they
# share the ``read_data`` scope. A tool is callable iff its id is in the
# agent version's ``enabled_tool_ids`` AND its scope is in ``allowed_scopes``
# (PRD FR-6).
TOOL_SCOPES: dict[str, str] = {
    "query_revenue_metrics": "read_data",
    "fetch_account_details": "read_data",
    "search_docs": "read_data",
    "fetch_support_tickets": "read_data",
    "create_mock_action": "write_mock_action",
    "request_approval": "request_approval",
    "run_eval": "run_eval",
}

# PRD §9.5: the immutable v1 snapshot has the original three scopes.
DEFAULT_V1_ALLOWED_SCOPES: tuple[str, ...] = (
    "read_data",
    "write_mock_action",
    "request_approval",
)

PHASE6_ALLOWED_SCOPES: tuple[str, ...] = (
    *DEFAULT_V1_ALLOWED_SCOPES,
    "run_eval",
)


def scope_for_tool(tool_id: str) -> str | None:
    """Return the permission scope for ``tool_id``, or None if unknown."""
    return TOOL_SCOPES.get(tool_id)
