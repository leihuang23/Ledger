"""Bounded ReAct investigation loop (agentic mode).

When the configured LLM client advertises agentic capability, the workflow
delegates evidence gathering to this loop instead of the fixed tool pipeline.
Each iteration the LLM decides either to call one tool (with arguments it
proposes) or to finalize the investigation with a diagnosis proposal.

Safety invariants preserved from the deterministic pipeline:

- **Policy first**: every requested tool is checked against the agent
  version's enabled-tool/scope policy. Blocked tools are never dispatched;
  they are recorded as visible ``blocked`` steps and surfaced back to the
  LLM as observations.
- **Argument sanitization**: LLM-proposed arguments are untrusted. Account
  and invoice ids are filtered against identifiers already present in the
  incident or retrieved evidence (only genuinely failed invoices join the
  known set), limits are clamped, and the incident id is pinned server-side.
  The LLM can choose *which* evidence to fetch and in which order; it cannot
  widen the data scope.
- **Bounded budget**: the loop runs at most ``max_iterations`` decisions.
- **Honest degradation**: LLM transport errors, malformed decisions, or an
  exhausted budget fall back to the deterministic evidence sweep over the
  remaining enabled tools. The deterministic classifier then remains the
  source of truth for the root cause (recorded via ``termination``).
- **No invented evidence**: the loop only returns tool outputs it actually
  dispatched; duplicate calls are short-circuited instead of re-dispatched.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.agent.persistence import AgentRunRecorder
from app.agent.tools import (
    TOOL_IDS,
    FetchAccountDetailsInput,
    FetchSupportTicketsInput,
    QueryRevenueMetricsInput,
    SearchDocsInput,
    disabled_revenue_metrics_fallback,
    doc_query_for_incident,
    fetch_account_details,
    fetch_support_tickets,
    query_revenue_metrics,
    search_docs,
)
from app.llm import build_agent_loop_prompt, parse_agent_decision
from app.llm.schemas import (
    AgentDecision,
    AgentFinalDecision,
    AgentToolCallDecision,
    LLMUsage,
)

TERMINATION_FINAL = "final_decision"
TERMINATION_LLM_ERROR = "llm_error_fallback"
TERMINATION_PARSE_ERROR = "parse_error_fallback"
TERMINATION_BUDGET = "iteration_budget_exhausted"

_MAX_DOC_QUERY_CHARS = 500
_MAX_DOC_RESULTS_PER_CALL = 5
_MAX_TICKETS_PER_CALL = 24

TOOL_CATALOG: list[dict[str, Any]] = [
    {
        "name": "query_revenue_metrics",
        "description": (
            "Retrieve the incident's MRR window comparison and failed-renewal "
            "invoice SQL evidence. Call this first to confirm the metric "
            "movement and get affected account and invoice ids."
        ),
        "arguments": {},
    },
    {
        "name": "fetch_account_details",
        "description": (
            "Fetch account, subscription, and failed-invoice details "
            "(including invoice failure reasons) for known account ids."
        ),
        "arguments": {
            "account_ids": "list of known account ids (optional; defaults to all known)",
            "include_invoices": "boolean, default true",
        },
    },
    {
        "name": "search_docs",
        "description": (
            "Search internal runbooks and policy documents. Use a targeted "
            "query; different queries may surface different runbooks."
        ),
        "arguments": {"query": "non-empty search string", "limit": "1-5, default 5"},
    },
    {
        "name": "fetch_support_tickets",
        "description": (
            "Fetch recent support tickets for known account ids to connect "
            "customer-reported symptoms with the revenue impact."
        ),
        "arguments": {
            "account_ids": "list of known account ids (optional; defaults to all known)",
            "limit": "1-24, default 12",
        },
    },
]


@dataclass
class LoopResult:
    """Everything the agentic loop produced for report synthesis."""

    revenue_metrics: dict[str, Any] | None
    account_details: dict[str, Any] | None
    doc_results: dict[str, Any] | None
    support_tickets: dict[str, Any] | None
    final_decision: AgentFinalDecision | None
    usages: list[LLMUsage] = field(default_factory=list)
    termination: str = TERMINATION_FINAL
    error: str | None = None


class InvestigationAgentLoop:
    """Executes the bounded LLM-driven evidence-gathering loop for one run."""

    def __init__(
        self,
        *,
        session: Session,
        recorder: AgentRunRecorder,
        llm_client: Any,
        incident: dict[str, Any],
        enabled_tool_ids: set[str],
        blocked_reasons: dict[str, str],
        max_iterations: int,
    ) -> None:
        self.session = session
        self.recorder = recorder
        self.llm_client = llm_client
        self.incident = incident
        self.enabled = enabled_tool_ids & set(TOOL_IDS)
        self.blocked_reasons = blocked_reasons
        self.max_iterations = max(1, max_iterations)

        self._evidence: dict[str, dict[str, Any]] = {}
        self._observations: list[dict[str, Any]] = []
        self._decision_history: list[dict[str, Any]] = []
        self._dispatched_keys: set[str] = set()
        self._dispatched_tools: set[str] = set()
        self._blocked_tools: set[str] = set()
        self._known_account_ids: set[str] = {
            account["account_id"]
            for account in incident.get("affected_accounts", [])
            if account.get("account_id")
        }
        self._known_invoice_ids: set[str] = set(
            incident.get("metric_evidence", {}).get("invoice_ids", []) or []
        )
        self._usages: list[LLMUsage] = []

    # ------------------------------------------------------------------ run

    def dispatched_tool_ids(self) -> set[str]:
        """Tools successfully executed at least once (blocked or unknown
        requests do not count). Used by the workflow to surface enabled tools
        the agent never requested."""
        return set(self._dispatched_tools)

    def blocked_tool_ids(self) -> set[str]:
        """Tools blocked by policy at least once. They already carry a visible
        blocked step and a disabled-evidence fallback, so the workflow must
        not additionally record them as not-requested."""
        return set(self._blocked_tools)

    def run(self) -> LoopResult:
        for iteration in range(self.max_iterations):
            prompt = build_agent_loop_prompt(
                incident=self.incident,
                evidence_summaries=self._observations,
                decision_history=self._decision_history,
                tool_catalog=self._tool_catalog(),
                iteration=iteration,
                max_iterations=self.max_iterations,
            )
            try:
                raw, usage = self.llm_client.complete_raw(prompt)
            except Exception as exc:
                self._record_degraded_decision(iteration, error=f"llm_error: {exc}")
                return self._degrade(
                    TERMINATION_LLM_ERROR, iteration=iteration, error=str(exc)
                )
            self._usages.append(usage)

            try:
                decision = parse_agent_decision(raw)
            except ValueError as exc:
                self._record_degraded_decision(iteration, error=f"parse_error: {exc}")
                return self._degrade(
                    TERMINATION_PARSE_ERROR, iteration=iteration, error=str(exc)
                )

            self._record_decision(iteration, decision, usage=usage)

            if isinstance(decision, AgentFinalDecision):
                return LoopResult(
                    revenue_metrics=self._evidence.get("query_revenue_metrics"),
                    account_details=self._evidence.get("fetch_account_details"),
                    doc_results=self._evidence.get("search_docs"),
                    support_tickets=self._evidence.get("fetch_support_tickets"),
                    final_decision=decision,
                    usages=self._usages,
                    termination=TERMINATION_FINAL,
                )

            self._dispatch_tool_call(iteration, decision)

        return self._degrade(TERMINATION_BUDGET, iteration=self.max_iterations - 1)

    # -------------------------------------------------------------- helpers

    def _tool_catalog(self) -> list[dict[str, Any]]:
        """Advertise every registered tool, including blocked ones.

        The LLM must be able to *request* a blocked tool so the block is
        visible in the run timeline and the model can adapt; hiding tools
        would silently shrink the audit trail.
        """
        catalog = []
        for tool in TOOL_CATALOG:
            entry = dict(tool)
            if tool["name"] not in self.enabled:
                entry["description"] = (
                    tool["description"]
                    + " (NOTE: blocked for this agent version; requests will be "
                    "recorded as blocked)"
                )
            catalog.append(entry)
        return catalog

    def _record_decision(
        self, iteration: int, decision: AgentDecision, *, usage: LLMUsage
    ) -> None:
        payload = decision.model_dump(mode="json")
        self._decision_history.append(
            {"iteration": iteration + 1, **payload}
        )

        def capture_decision() -> dict[str, Any]:
            return payload

        self.recorder.record(
            stage="agent decision",
            inputs={
                "iteration": iteration + 1,
                "max_iterations": self.max_iterations,
            },
            action=capture_decision,
        )

    def _record_degraded_decision(self, iteration: int, *, error: str) -> None:
        """Persist a visible step for an LLM turn that produced no usable
        decision (transport error or malformed output) before degrading."""
        self._decision_history.append(
            {"iteration": iteration + 1, "decision": "degraded", "error": error}
        )

        def capture_degraded() -> dict[str, Any]:
            return {"degraded": True, "error": error}

        self.recorder.record(
            stage="agent decision",
            inputs={"iteration": iteration + 1, "degraded": True},
            action=capture_degraded,
        )

    # ------------------------------------------------------- tool dispatch

    def _dispatch_tool_call(
        self, iteration: int, decision: AgentToolCallDecision
    ) -> None:
        tool_name = decision.tool
        raw_arguments = decision.arguments or {}

        if tool_name not in TOOL_IDS:
            self.recorder.record_blocked(
                stage="agent tool call",
                tool_name=tool_name,
                inputs={"iteration": iteration + 1, "arguments": raw_arguments},
                blocked_reason="unknown_tool_requested",
                fallback_output={
                    "error": f"Unknown tool: {tool_name}",
                    "available_tools": sorted(TOOL_IDS),
                },
            )
            self._observations.append(
                {
                    "tool": tool_name,
                    "status": "blocked",
                    "reason": "unknown_tool_requested",
                    "note": f"Unknown tool {tool_name!r}. Available: {sorted(TOOL_IDS)}.",
                }
            )
            return

        if tool_name not in self.enabled:
            reason = self.blocked_reasons.get(tool_name, "tool_not_enabled")
            fallback = self._disabled_fallback(tool_name)
            self.recorder.record_blocked(
                stage="agent tool call",
                tool_name=tool_name,
                inputs={"iteration": iteration + 1, "arguments": raw_arguments},
                blocked_reason=reason,
                fallback_output=fallback,
            )
            self._blocked_tools.add(tool_name)
            # Fill the evidence slot with the same disabled payload the
            # deterministic pipeline produces, so report synthesis and the
            # classifier see identical evidence in both modes (e.g. disabled
            # metrics keep the explicit uncertainty path).
            self._store_evidence(tool_name, {}, fallback)
            self._observations.append(
                {
                    "tool": tool_name,
                    "status": "blocked",
                    "reason": reason,
                    "note": f"{tool_name} is blocked for this agent version; adapt or finalize.",
                }
            )
            return

        sanitized = self._sanitize_arguments(tool_name, raw_arguments)
        dispatch_key = f"{tool_name}:{json.dumps(sanitized, sort_keys=True, default=str)}"
        if dispatch_key in self._dispatched_keys:
            self._observations.append(
                {
                    "tool": tool_name,
                    "status": "duplicate_skipped",
                    "note": (
                        "This exact call was already executed; its evidence is "
                        "still available above. Choose a different action."
                    ),
                }
            )
            return

        output = self._dispatch_sanitized(tool_name, sanitized, iteration=iteration)
        self._dispatched_keys.add(dispatch_key)
        self._dispatched_tools.add(tool_name)
        self._store_evidence(tool_name, sanitized, output)
        self._observations.append(self._observation_summary(tool_name, output))

    def _sanitize_arguments(
        self, tool_name: str, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        """Clamp and filter untrusted LLM arguments against known evidence.

        The LLM decides which evidence to fetch; it never widens the data
        scope beyond identifiers already attached to the incident or returned
        by earlier tool calls.
        """
        if tool_name == "query_revenue_metrics":
            # The incident id is pinned server-side; there is nothing to
            # sanitize and nothing for the LLM to supply.
            return {}

        if tool_name == "fetch_account_details":
            account_ids = self._filter_known_ids(arguments.get("account_ids"))
            include_invoices = bool(arguments.get("include_invoices", True))
            return {
                "account_ids": account_ids,
                "include_invoices": include_invoices,
            }

        if tool_name == "search_docs":
            query = str(arguments.get("query", "")).strip()[:_MAX_DOC_QUERY_CHARS]
            if not query:
                query = doc_query_for_incident(self.incident)
            limit = self._clamp(arguments.get("limit"), 1, _MAX_DOC_RESULTS_PER_CALL, default=5)
            return {"query": query, "limit": limit}

        if tool_name == "fetch_support_tickets":
            account_ids = self._filter_known_ids(arguments.get("account_ids"))
            since = self._parse_since(arguments.get("since"))
            limit = self._clamp(arguments.get("limit"), 1, _MAX_TICKETS_PER_CALL, default=12)
            return {"account_ids": account_ids, "since": since, "limit": limit}

        return {}

    def _filter_known_ids(self, requested: object) -> list[str]:
        """Keep only ids already present in incident or retrieved evidence.

        An empty or fully-unknown selection defaults to every known account so
        a vague LLM request still produces useful evidence instead of a
        fabricated-id lookup.
        """
        if not isinstance(requested, list):
            return sorted(self._known_account_ids)
        filtered = [
            str(value) for value in requested if str(value) in self._known_account_ids
        ]
        return filtered or sorted(self._known_account_ids)

    def _parse_since(self, value: object) -> datetime:
        default = self._default_tickets_since()
        if not isinstance(value, str):
            return default
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return default
        return parsed.replace(tzinfo=None)

    def _default_tickets_since(self) -> datetime:
        detected_at = self.incident.get("detected_at")
        try:
            parsed = datetime.fromisoformat(str(detected_at).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return datetime(2000, 1, 1)
        return parsed.replace(tzinfo=None) - timedelta(days=30)

    @staticmethod
    def _clamp(value: object, minimum: int, maximum: int, *, default: int) -> int:
        try:
            number = int(value)  # type: ignore[call-overload]
        except (TypeError, ValueError):
            return default
        return max(minimum, min(maximum, number))

    def _dispatch_sanitized(
        self, tool_name: str, sanitized: dict[str, Any], *, iteration: int
    ) -> dict[str, Any]:
        session = self.session
        dispatch_inputs: dict[str, Any] = {"iteration": iteration + 1, **sanitized}
        if tool_name == "query_revenue_metrics":
            action = lambda: query_revenue_metrics(  # noqa: E731
                session, QueryRevenueMetricsInput(incident_id=self.incident["id"])
            ).model_dump(mode="json")
        elif tool_name == "fetch_account_details":
            # Restrict invoice evidence to identifiers already known from the
            # incident or earlier tool output; an empty argument would
            # otherwise return every invoice (including paid ones) and
            # contaminate the failed-invoice claims.
            invoice_ids = sorted(self._known_invoice_ids)
            dispatch_inputs["invoice_ids"] = invoice_ids
            action = lambda: fetch_account_details(  # noqa: E731
                session,
                FetchAccountDetailsInput(
                    account_ids=sanitized["account_ids"],
                    invoice_ids=invoice_ids,
                    include_invoices=sanitized["include_invoices"],
                ),
            ).model_dump(mode="json")
        elif tool_name == "search_docs":
            action = lambda: search_docs(  # noqa: E731
                session,
                SearchDocsInput(query=sanitized["query"], limit=sanitized["limit"]),
            ).model_dump(mode="json")
        elif tool_name == "fetch_support_tickets":
            action = lambda: fetch_support_tickets(  # noqa: E731
                session,
                FetchSupportTicketsInput(
                    account_ids=sanitized["account_ids"],
                    since=sanitized["since"],
                    limit=sanitized["limit"],
                ),
            ).model_dump(mode="json")
        else:  # pragma: no cover - guarded by TOOL_IDS check upstream
            raise ValueError(f"Unknown tool: {tool_name}")

        return self.recorder.record(
            stage="agent tool call",
            tool_name=tool_name,
            inputs=dispatch_inputs,
            action=action,
        )

    # ------------------------------------------------------- evidence state

    def _store_evidence(
        self, tool_name: str, sanitized: dict[str, Any], output: dict[str, Any]
    ) -> None:
        existing = self._evidence.get(tool_name)
        if tool_name == "search_docs" and existing:
            # Merge repeated doc searches instead of overwriting: different
            # queries legitimately surface different runbooks.
            seen = {
                (result.get("source_id"), result.get("title"))
                for result in existing["results"]
            }
            merged_results = list(existing["results"])
            for result in output.get("results", []):
                key = (result.get("source_id"), result.get("title"))
                if key not in seen:
                    merged_results.append(result)
                    seen.add(key)
            queries = list(existing.get("queries", [existing.get("query", "")]))
            queries.append(output.get("query", sanitized.get("query", "")))
            self._evidence["search_docs"] = {
                "query": output.get("query", sanitized.get("query", "")),
                "queries": queries,
                "results": merged_results,
            }
        elif tool_name == "fetch_account_details" and existing:
            # Union repeated account fetches so a later narrowed call never
            # silently shrinks previously retrieved evidence.
            by_id = {
                account["account_id"]: dict(account)
                for account in existing.get("accounts", [])
            }
            for account in output.get("accounts", []):
                merged = by_id.get(account["account_id"])
                if merged is None:
                    by_id[account["account_id"]] = dict(account)
                    continue
                seen_invoices = {
                    invoice.get("invoice_id")
                    for invoice in merged.get("failed_invoices", [])
                }
                merged_invoices = list(merged.get("failed_invoices", []))
                for invoice in account.get("failed_invoices", []):
                    if invoice.get("invoice_id") not in seen_invoices:
                        merged_invoices.append(invoice)
                        seen_invoices.add(invoice.get("invoice_id"))
                merged["failed_invoices"] = merged_invoices
            self._evidence["fetch_account_details"] = {
                **output,
                "accounts": list(by_id.values()),
            }
        elif tool_name == "fetch_support_tickets" and existing:
            # Union repeated ticket fetches by ticket id for the same reason.
            seen = {
                ticket.get("ticket_id") for ticket in existing.get("tickets", [])
            }
            merged_tickets = list(existing.get("tickets", []))
            for ticket in output.get("tickets", []):
                if ticket.get("ticket_id") not in seen:
                    merged_tickets.append(ticket)
                    seen.add(ticket.get("ticket_id"))
            self._evidence["fetch_support_tickets"] = {
                **output,
                "tickets": merged_tickets,
            }
        else:
            if tool_name == "search_docs":
                output = {
                    **output,
                    "queries": [output.get("query", sanitized.get("query", ""))],
                }
            self._evidence[tool_name] = output

        if tool_name == "query_revenue_metrics":
            self._known_account_ids.update(output.get("affected_account_ids", []))
            self._known_invoice_ids.update(output.get("invoice_ids", []))
        if tool_name == "fetch_account_details":
            for account in output.get("accounts", []):
                for invoice in account.get("failed_invoices", []):
                    # Only genuinely failed invoices widen the known set; paid
                    # or void rows must never become dispatchable targets.
                    if invoice.get("invoice_id") and invoice.get("status") == "failed":
                        self._known_invoice_ids.add(invoice["invoice_id"])

    def _observation_summary(
        self, tool_name: str, output: dict[str, Any]
    ) -> dict[str, Any]:
        """Compact, bounded observation fed back into the next loop prompt."""
        if tool_name == "query_revenue_metrics":
            metric = output.get("metric_evidence", {})
            return {
                "tool": tool_name,
                "status": "succeeded",
                "metric_name": metric.get("metric_name"),
                "delta_percent": metric.get("delta_percent"),
                "failed_invoice_count": metric.get("failed_invoice_count"),
                "affected_account_ids": output.get("affected_account_ids", []),
                "sql_evidence_titles": [
                    item.get("title") for item in output.get("sql_evidence", [])
                ],
            }
        if tool_name == "fetch_account_details":
            accounts = output.get("accounts", [])
            failure_reasons = sorted(
                {
                    str(invoice.get("failure_reason"))
                    for account in accounts
                    for invoice in account.get("failed_invoices", [])
                    if invoice.get("failure_reason")
                }
            )
            return {
                "tool": tool_name,
                "status": "succeeded",
                "account_count": len(accounts),
                "accounts": [
                    {
                        "account_id": account.get("account_id"),
                        "account_name": account.get("account_name"),
                        "segment": account.get("segment"),
                        "subscription_status": account.get("subscription_status"),
                        "failed_invoice_count": len(account.get("failed_invoices", [])),
                    }
                    for account in accounts[:8]
                ],
                "failure_reasons": failure_reasons,
            }
        if tool_name == "search_docs":
            return {
                "tool": tool_name,
                "status": "succeeded",
                "result_count": len(output.get("results", [])),
                "documents": [
                    {
                        "title": result.get("title"),
                        "source_id": result.get("source_id"),
                        "snippet": str(result.get("snippet", ""))[:400],
                    }
                    for result in output.get("results", [])[:_MAX_DOC_RESULTS_PER_CALL]
                ],
            }
        if tool_name == "fetch_support_tickets":
            tickets = output.get("tickets", [])
            return {
                "tool": tool_name,
                "status": "succeeded",
                "ticket_count": len(tickets),
                "tickets": [
                    {
                        "ticket_id": ticket.get("ticket_id"),
                        "account_id": ticket.get("account_id"),
                        "category": ticket.get("category"),
                        "subject": ticket.get("subject"),
                        "description": str(ticket.get("description", ""))[:300],
                    }
                    for ticket in tickets[:8]
                ],
            }
        return {"tool": tool_name, "status": "succeeded"}

    # ---------------------------------------------------------- degradation

    def _degrade(
        self, termination: str, *, iteration: int, error: str | None = None
    ) -> LoopResult:
        """Deterministic evidence sweep over every tool the loop did not
        successfully execute, mirroring the fixed pipeline's ordering."""
        deterministic_order = (
            "query_revenue_metrics",
            "fetch_account_details",
            "search_docs",
            "fetch_support_tickets",
        )
        for tool_name in deterministic_order:
            if tool_name in self._evidence:
                continue
            if tool_name not in self.enabled:
                reason = self.blocked_reasons.get(tool_name, "tool_not_enabled")
                fallback = self._disabled_fallback(tool_name)
                self.recorder.record_blocked(
                    stage="agent tool call",
                    tool_name=tool_name,
                    inputs={"iteration": iteration + 1, "source": "deterministic_fallback"},
                    blocked_reason=reason,
                    fallback_output=fallback,
                )
                self._blocked_tools.add(tool_name)
                # Same disabled payload as the deterministic pipeline: keeps
                # the classifier's uncertainty guard intact for blocked tools.
                self._store_evidence(tool_name, {}, fallback)
                continue
            sanitized = self._deterministic_arguments(tool_name)
            output = self._dispatch_sanitized(tool_name, sanitized, iteration=iteration)
            self._dispatched_tools.add(tool_name)
            self._store_evidence(tool_name, sanitized, output)
            self._observations.append(self._observation_summary(tool_name, output))

        return LoopResult(
            revenue_metrics=self._evidence.get("query_revenue_metrics"),
            account_details=self._evidence.get("fetch_account_details"),
            doc_results=self._evidence.get("search_docs"),
            support_tickets=self._evidence.get("fetch_support_tickets"),
            final_decision=None,
            usages=self._usages,
            termination=termination,
            error=error,
        )

    def _deterministic_arguments(self, tool_name: str) -> dict[str, Any]:
        if tool_name == "query_revenue_metrics":
            return {}
        if tool_name == "fetch_account_details":
            return {"account_ids": sorted(self._known_account_ids), "include_invoices": True}
        if tool_name == "search_docs":
            return {"query": doc_query_for_incident(self.incident), "limit": 5}
        if tool_name == "fetch_support_tickets":
            return {
                "account_ids": sorted(self._known_account_ids),
                "since": self._default_tickets_since(),
                "limit": _MAX_TICKETS_PER_CALL,
            }
        return {}

    def _disabled_fallback(self, tool_name: str) -> dict[str, Any]:
        """Evidence payload for a policy-blocked tool, identical to what the
        deterministic pipeline records so both modes behave the same."""
        if tool_name == "query_revenue_metrics":
            return disabled_revenue_metrics_fallback(self.incident["id"], self.incident)
        reason = f"{tool_name} was not enabled for this agent version."
        if tool_name == "fetch_account_details":
            return {"accounts": [], "tool_disabled": True, "tool_disabled_reason": reason}
        if tool_name == "search_docs":
            return {
                "query": doc_query_for_incident(self.incident),
                "results": [],
                "tool_disabled": True,
                "tool_disabled_reason": reason,
            }
        if tool_name == "fetch_support_tickets":
            return {"tickets": [], "tool_disabled": True, "tool_disabled_reason": reason}
        return {"tool_disabled": True, "tool_disabled_reason": reason}
