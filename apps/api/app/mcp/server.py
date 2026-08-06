"""FastMCP server exposing Ledger's existing tool registry to external clients.

Design invariants (mirror AGENTS.md's tool-boundary rules):

- Every MCP tool dispatches through ``BUILTIN_TOOL_BY_ID``; this module owns
  no tool logic, schemas, or scope mapping of its own.
- Governed (non-``read_data``) tools pass the fail-closed token gate in
  ``app.mcp.auth`` before dispatch, then enforce the same operator run-state
  policy as ``POST /mock-actions`` so a pending approval can never be attached
  to an in-flight or terminal run. Their actions are attributed to the
  ``mcp-client`` actor in the audit trail.
- ``request_approval`` routes high-risk actions into the existing approval
  service, which creates a ``pending_approval`` action plus a pending
  ``ApprovalRequest`` - nothing executes before a recorded human decision.
- Pydantic input models from the registry are the MCP parameter types, so
  malformed arguments fail visibly instead of being coerced.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.agent.tools import (
    FetchAccountDetailsInput,
    FetchSupportTicketsInput,
    QueryRevenueMetricsInput,
    SearchDocsInput,
)
from app.approvals.schemas import MockActionCreate
from app.approvals.service import (
    MCP_CLIENT_ACTOR,
    validate_operator_action_for_run,
)
from app.db.session import SessionLocal
from app.mcp.auth import McpAuthorizationError, ensure_mcp_authorized
from app.tools.registry import BUILTIN_TOOL_BY_ID, ListPendingApprovalsInput

SERVER_INSTRUCTIONS = (
    "Ledger MCP server: evidence tools for SaaS revenue investigations. "
    "Read tools (scope read_data) return cited SQL, ticket, and document "
    "evidence. Governed tools (create_mock_action, request_approval) require "
    "the operator token and never send anything externally: high-risk actions "
    "become pending approval requests that a human must decide."
)

# Scopes whose implementations write state; they need the operator run-state
# guard on top of the registry implementation's own risk guard.
GOVERNED_SCOPES: frozenset[str] = frozenset(
    {"write_mock_action", "request_approval"}
)


def create_server(
    session_factory: Callable[[], Session] | None = None,
) -> FastMCP:
    """Build the FastMCP instance; ``session_factory`` is injectable for tests."""
    factory = session_factory or SessionLocal
    server = FastMCP("ledger", instructions=SERVER_INSTRUCTIONS)

    def dispatch(tool_id: str, payload: BaseModel) -> dict[str, Any]:
        definition = BUILTIN_TOOL_BY_ID.get(tool_id)
        if definition is None:
            # Registry is the source of truth; unknown ids fail closed.
            raise McpAuthorizationError(f"Unknown tool id: {tool_id}")
        ensure_mcp_authorized(definition.permission_scope)
        with factory() as session:
            if definition.permission_scope in GOVERNED_SCOPES:
                # External clients are operator-like: enforce the same
                # run-state policy as the operator API so a pending approval
                # can never be attached to a terminal or in-flight run.
                validate_operator_action_for_run(
                    session, payload.run_id, payload.action_type
                )
                output = definition.implementation(
                    session, payload, actor=MCP_CLIENT_ACTOR
                )
            else:
                output = definition.implementation(session, payload)
        return output.model_dump(mode="json")

    def _description(tool_id: str) -> str:
        definition = BUILTIN_TOOL_BY_ID[tool_id]
        return (
            f"{definition.description} [scope: {definition.permission_scope}]"
        )

    def query_revenue_metrics(payload: QueryRevenueMetricsInput) -> dict[str, Any]:
        return dispatch("query_revenue_metrics", payload)

    def fetch_account_details(payload: FetchAccountDetailsInput) -> dict[str, Any]:
        return dispatch("fetch_account_details", payload)

    def search_docs(payload: SearchDocsInput) -> dict[str, Any]:
        return dispatch("search_docs", payload)

    def fetch_support_tickets(payload: FetchSupportTicketsInput) -> dict[str, Any]:
        return dispatch("fetch_support_tickets", payload)

    def list_pending_approvals(
        payload: ListPendingApprovalsInput,
    ) -> dict[str, Any]:
        return dispatch("list_pending_approvals", payload)

    def create_mock_action(payload: MockActionCreate) -> dict[str, Any]:
        return dispatch("create_mock_action", payload)

    def request_approval(payload: MockActionCreate) -> dict[str, Any]:
        return dispatch("request_approval", payload)

    for tool_fn in (
        query_revenue_metrics,
        fetch_account_details,
        search_docs,
        fetch_support_tickets,
        list_pending_approvals,
        create_mock_action,
        request_approval,
    ):
        server.add_tool(
            tool_fn, name=tool_fn.__name__, description=_description(tool_fn.__name__)
        )
    return server