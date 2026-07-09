"""Tool permission scopes and the in-code tool registry.

Phase 2's ``tools`` table / tool-registry domain was reverted as scope creep
(see ``AGENTS.md``: prefer a narrow, verified system). Phase 3 keeps tool
permission enforcement without a ``tools`` table by holding the scope enum and
the tool→scope mapping in code here, and the policy in ``app.tools.policy``.

Adding a new tool means adding its id to ``TOOL_SCOPES`` here and registering
its callable in ``app.agent.tools``. No DB row, no migration.
"""

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
}

# PRD §9.5: the v1 published version ships with these scopes. ``run_eval`` is
# intentionally excluded for v1 — eval triggering is operator-gated separately
# (EVAL_RUN_TOKEN) and is not part of the investigation run path.
DEFAULT_V1_ALLOWED_SCOPES: tuple[str, ...] = (
    "read_data",
    "write_mock_action",
    "request_approval",
)


def scope_for_tool(tool_id: str) -> str | None:
    """Return the permission scope for ``tool_id``, or None if unknown."""
    return TOOL_SCOPES.get(tool_id)
