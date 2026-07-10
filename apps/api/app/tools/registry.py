from __future__ import annotations

from dataclasses import dataclass
from importlib import import_module
from typing import Any, Callable

from pydantic import BaseModel

from app.agent.tools import (
    FetchAccountDetailsInput,
    FetchAccountDetailsOutput,
    FetchSupportTicketsInput,
    FetchSupportTicketsOutput,
    QueryRevenueMetricsInput,
    QueryRevenueMetricsOutput,
    SearchDocsInput,
    SearchDocsOutput,
    fetch_account_details,
    fetch_support_tickets,
    query_revenue_metrics,
    search_docs,
)
from app.approvals.schemas import MockActionCreate, MockActionRead
from app.approvals.service import (
    create_low_risk_mock_action,
    request_high_risk_approval,
)
from app.evals.runner import run_eval_suite
from app.evals.schemas import EvalDatasetRunRequest, EvalRunSummary
from app.tools.scopes import PermissionScope


class InvalidImplementationRefError(ValueError):
    pass


@dataclass(frozen=True)
class ToolDefinition:
    id: str
    name: str
    description: str
    input_model: type[BaseModel]
    output_model: type[BaseModel]
    permission_scope: PermissionScope
    implementation_ref: str
    implementation: Callable[..., Any]

    @property
    def input_schema(self) -> dict[str, Any]:
        return self.input_model.model_json_schema()

    @property
    def output_schema(self) -> dict[str, Any]:
        return self.output_model.model_json_schema()


BUILTIN_TOOL_DEFINITIONS: tuple[ToolDefinition, ...] = (
    ToolDefinition(
        id="query_revenue_metrics",
        name="query_revenue_metrics",
        description="Compare incident revenue windows and retrieve cited SQL evidence.",
        input_model=QueryRevenueMetricsInput,
        output_model=QueryRevenueMetricsOutput,
        permission_scope="read_data",
        implementation_ref="app.agent.tools.query_revenue_metrics",
        implementation=query_revenue_metrics,
    ),
    ToolDefinition(
        id="fetch_account_details",
        name="fetch_account_details",
        description="Retrieve account, subscription, and failed-invoice evidence.",
        input_model=FetchAccountDetailsInput,
        output_model=FetchAccountDetailsOutput,
        permission_scope="read_data",
        implementation_ref="app.agent.tools.fetch_account_details",
        implementation=fetch_account_details,
    ),
    ToolDefinition(
        id="search_docs",
        name="search_docs",
        description="Search the knowledge base and return cited document excerpts.",
        input_model=SearchDocsInput,
        output_model=SearchDocsOutput,
        permission_scope="read_data",
        implementation_ref="app.agent.tools.search_docs",
        implementation=search_docs,
    ),
    ToolDefinition(
        id="fetch_support_tickets",
        name="fetch_support_tickets",
        description="Retrieve support-ticket evidence for affected accounts.",
        input_model=FetchSupportTicketsInput,
        output_model=FetchSupportTicketsOutput,
        permission_scope="read_data",
        implementation_ref="app.agent.tools.fetch_support_tickets",
        implementation=fetch_support_tickets,
    ),
    ToolDefinition(
        id="create_mock_action",
        name="create_mock_action",
        description="Create a reversible mock action or draft; never sends externally.",
        input_model=MockActionCreate,
        output_model=MockActionRead,
        permission_scope="write_mock_action",
        implementation_ref="app.approvals.service.create_low_risk_mock_action",
        implementation=create_low_risk_mock_action,
    ),
    ToolDefinition(
        id="request_approval",
        name="request_approval",
        description=(
            "Submit a high-risk mock action to the existing approval-gated action service."
        ),
        input_model=MockActionCreate,
        output_model=MockActionRead,
        permission_scope="request_approval",
        implementation_ref="app.approvals.service.request_high_risk_approval",
        implementation=request_high_risk_approval,
    ),
    ToolDefinition(
        id="run_eval",
        name="run_eval",
        description="Run the deterministic evaluation suite for a published agent version.",
        input_model=EvalDatasetRunRequest,
        output_model=EvalRunSummary,
        permission_scope="run_eval",
        implementation_ref="app.evals.runner.run_eval_suite",
        implementation=run_eval_suite,
    ),
)

BUILTIN_TOOL_BY_ID: dict[str, ToolDefinition] = {
    definition.id: definition for definition in BUILTIN_TOOL_DEFINITIONS
}


def resolve_implementation_ref(implementation_ref: str) -> Callable[..., Any]:
    """Resolve an application-owned dotted reference and reject dangling metadata."""
    module_name, separator, attribute_name = implementation_ref.rpartition(".")
    if not separator or not module_name.startswith("app."):
        raise InvalidImplementationRefError(
            "implementation_ref must point to an app-owned Python callable"
        )
    try:
        module = import_module(module_name)
        implementation = getattr(module, attribute_name)
    except (ImportError, AttributeError) as exc:
        raise InvalidImplementationRefError(
            f"Unresolvable implementation_ref: {implementation_ref}"
        ) from exc
    if not callable(implementation):
        raise InvalidImplementationRefError(
            f"implementation_ref is not callable: {implementation_ref}"
        )
    return implementation
