"""Token gate for governed MCP tools.

Mirrors the HTTP ``require_demo_operator_access`` semantics from
``app.core.access`` exactly:

- only the demo-data environments (local/test/development/demo) may host
  governed tools; anything else is rejected outright,
- ``demo`` env with ``DEMO_OPERATOR_TOKEN`` unset: governed tools FAIL CLOSED,
- ``DEMO_OPERATOR_TOKEN`` unset in any other env: ungated (local/test/dev),
- ``DEMO_OPERATOR_TOKEN`` set: the client-supplied ``MCP_OPERATOR_TOKEN``
  (read from the MCP server process environment at startup) must match under
  ``secrets.compare_digest``.

Read-only evidence tools (``read_data`` scope) bypass this gate, consistent
with the public demo being read-only. Unknown scopes fail closed: a scope the
gate does not explicitly open can never execute.
"""

from __future__ import annotations

import secrets

from app.core.access import DEMO_DATA_ENVIRONMENTS
from app.core.config import get_settings

# Only these scopes are exposed over MCP at all. ``run_eval`` stays
# HTTP/API-only in v1.
MCP_EXPOSED_SCOPES: frozenset[str] = frozenset(
    {"read_data", "write_mock_action", "request_approval"}
)


class McpAuthorizationError(PermissionError):
    """Raised when a governed MCP tool call is rejected by the token gate."""


def ensure_mcp_authorized(scope: str) -> None:
    """Raise ``McpAuthorizationError`` unless ``scope`` is callable over MCP.

    ``read_data`` is open (public demo is read-only). Governed scopes require
    the operator token gate to pass, and fail closed in ``demo`` when the
    server-side ``DEMO_OPERATOR_TOKEN`` is unset.
    """
    if scope not in MCP_EXPOSED_SCOPES:
        raise McpAuthorizationError(
            f"Scope {scope!r} is not exposed by the Ledger MCP server."
        )
    if scope == "read_data":
        return

    settings = get_settings()
    if settings.app_env not in DEMO_DATA_ENVIRONMENTS:
        raise McpAuthorizationError(
            "Governed MCP tools are only available in local, test, "
            "development, or demo environments."
        )
    if settings.demo_operator_token is None:
        if settings.app_env == "demo":
            raise McpAuthorizationError(
                "Governed MCP tools are disabled: DEMO_OPERATOR_TOKEN is unset "
                "in the demo environment (fail closed)."
            )
        # Non-demo environments run ungated when no token is configured,
        # matching require_demo_operator_access.
        return

    presented = settings.mcp_operator_token
    if presented is None or not secrets.compare_digest(
        presented, settings.demo_operator_token
    ):
        raise McpAuthorizationError(
            "Invalid or missing MCP operator token for a governed tool."
        )
