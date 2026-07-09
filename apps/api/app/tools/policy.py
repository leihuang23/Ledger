"""Tool permission policy engine (PRD FR-6, FR-7).

A tool is callable by an agent version iff:
  1. the tool id is in ``version.enabled_tool_ids`` (``tool_not_enabled``
     otherwise), AND
  2. the tool's permission scope is in ``version.allowed_scopes``
     (``scope_not_allowed`` otherwise).

This is a pure function with no I/O; the run path calls it to decide whether
to dispatch a tool or record a visible ``blocked`` step (``app.agent.persistence``
``AgentRunRecorder.record_blocked``).
"""

from __future__ import annotations

from app.models import AgentVersion
from app.tools.scopes import scope_for_tool

# Block reasons (PRD FR-7). Recorded on the ``AgentRunStep.blocked_reason``.
REASON_TOOL_NOT_ENABLED = "tool_not_enabled"
REASON_SCOPE_NOT_ALLOWED = "scope_not_allowed"


def can_call_tool(version: AgentVersion, tool_id: str) -> tuple[bool, str | None]:
    """Return ``(allowed, reason)`` for whether ``version`` may call ``tool_id``.

    ``reason`` is ``None`` when allowed; otherwise one of
    ``tool_not_enabled`` / ``scope_not_allowed``. Unknown tool ids are treated
    as not-enabled (they cannot be in any version's ``enabled_tool_ids``).
    """
    enabled = version.enabled_tool_ids or []
    if tool_id not in enabled:
        return False, REASON_TOOL_NOT_ENABLED

    scope = scope_for_tool(tool_id)
    if scope is None:
        # A tool id present in enabled_tool_ids but unknown to the scope
        # registry: treat as not-enabled so it cannot silently execute.
        return False, REASON_TOOL_NOT_ENABLED

    allowed_scopes = version.allowed_scopes or []
    if scope not in allowed_scopes:
        return False, REASON_SCOPE_NOT_ALLOWED

    return True, None
