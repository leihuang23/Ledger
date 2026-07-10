"""Tool permission scopes and policy (in-code, no ``tools`` table).

See ``scopes.py`` for the scope enum and tool→scope map, and ``policy.py``
for the ``can_call_tool`` policy used by the run path.
"""

from __future__ import annotations

from app.tools.policy import can_call_tool
from app.tools.scopes import (
    ALLOWED_SCOPES,
    DEFAULT_V1_ALLOWED_SCOPES,
    PHASE6_ALLOWED_SCOPES,
    TOOL_SCOPES,
    PermissionScope,
    scope_for_tool,
)

__all__ = [
    "ALLOWED_SCOPES",
    "DEFAULT_V1_ALLOWED_SCOPES",
    "PHASE6_ALLOWED_SCOPES",
    "PermissionScope",
    "TOOL_SCOPES",
    "can_call_tool",
    "scope_for_tool",
]
