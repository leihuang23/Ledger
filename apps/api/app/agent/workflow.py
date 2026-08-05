from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph
from sqlalchemy.orm import Session

from app.agent.loop import (
    TERMINATION_FINAL,
    InvestigationAgentLoop,
)
from app.agent.persistence import AgentRunRecorder, utcnow_naive
from app.agent.tracing import AgentTraceHandle
from app.agent.schemas import (
    InvestigationReport,
    ReportAffectedAccount,
    ReportClaim,
    ReportEvidence,
)
from app.llm import (
    LLMClient,
    NoopLLMClient,
    build_investigation_prompt,
    estimate_cost_usd,
)
from app.llm.schemas import AgentFinalDecision, LLMUsage
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
from app.incidents.service import get_incident_detail
from app.models import AgentRun


class InvestigationState(TypedDict, total=False):
    run_id: str
    incident_id: str
    incident: dict[str, Any]
    plan: dict[str, Any]
    revenue_metrics: dict[str, Any]
    account_details: dict[str, Any]
    doc_results: dict[str, Any]
    support_tickets: dict[str, Any]
    agentic_result: dict[str, Any]
    final_report: dict[str, Any]


@dataclass(frozen=True)
class Diagnosis:
    root_cause: str
    next_actions: list[str]
    is_specific: bool


def run_investigation_workflow(
    session: Session,
    run: AgentRun,
    trace: AgentTraceHandle | None = None,
    llm_client: LLMClient | None = None,
    enabled_tool_ids: set[str] | frozenset[str] | None = None,
    blocked_reasons: dict[str, str] | None = None,
    max_iterations: int | None = None,
) -> InvestigationReport:
    recorder = AgentRunRecorder(session, run, trace)
    client = llm_client or NoopLLMClient()
    from app.core.config import get_settings

    settings = get_settings()
    # Mode selection is capability-based AND operator-gated: only clients that
    # advertise agentic support (``complete_raw`` for the decision loop) enter
    # the bounded ReAct loop, and only when AGENT_LOOP_ENABLED is on. Noop
    # clients and legacy single-shot clients stay on the deterministic
    # pipeline, which keeps evals and the public demo reproducible with
    # LLM_PROVIDER=none. Set AGENT_LOOP_ENABLED=false to pin a real provider
    # back to single-shot diagnosis.
    agentic_mode = (
        settings.agent_loop_enabled
        and bool(getattr(client, "agentic", False))
        and hasattr(client, "complete_raw")
    )
    if max_iterations is None:
        max_iterations = settings.agent_max_iterations

    if enabled_tool_ids is None:
        enabled = set(TOOL_IDS)
    else:
        enabled = set(enabled_tool_ids) & set(TOOL_IDS)

    # PRD FR-7: a tool call blocked by the agent version's permission policy is
    # recorded as a visible ``blocked`` step with a granular reason. When the
    # caller supplies ``blocked_reasons`` (from the policy engine), each blocked
    # tool gets its specific reason; otherwise the legacy default
    # ``tool_not_enabled`` applies for backward compatibility.
    if blocked_reasons is None:
        def reason_for(tool_id: str) -> str:
            return "tool_not_enabled"
    else:
        def reason_for(tool_id: str) -> str:
            return blocked_reasons.get(tool_id, "tool_not_enabled")

    builder = StateGraph(InvestigationState)

    def intake_node(state: InvestigationState) -> dict[str, Any]:
        incident_id = state["incident_id"]

        def load_incident() -> dict[str, Any]:
            incident = get_incident_detail(session, incident_id)
            if incident is None:
                raise LookupError(f"Unknown incident id: {incident_id}")
            return incident.model_dump(mode="json")

        incident = recorder.record(
            stage="intake",
            inputs={"incident_id": incident_id},
            action=load_incident,
        )
        return {"incident": incident}

    def plan_node(state: InvestigationState) -> dict[str, Any]:
        incident = state["incident"]

        def build_plan() -> dict[str, Any]:
            doc_query = doc_query_for_incident(incident)
            account_ids = [
                account["account_id"] for account in incident["affected_accounts"]
            ]
            tool_calls: list[dict[str, Any]] = []
            if "query_revenue_metrics" in enabled:
                tool_calls.append({
                    "tool_name": "query_revenue_metrics",
                    "reason": "Confirm the metric movement and failed invoice evidence.",
                })
            if "fetch_account_details" in enabled:
                tool_calls.append({
                    "tool_name": "fetch_account_details",
                    "reason": "Attach account, subscription, and invoice context.",
                })
            if "search_docs" in enabled:
                tool_calls.append({
                    "tool_name": "search_docs",
                    "reason": "Retrieve internal runbooks and policy citations.",
                    "query": doc_query,
                })
            if "fetch_support_tickets" in enabled:
                tool_calls.append({
                    "tool_name": "fetch_support_tickets",
                    "reason": "Connect account impact with recent customer signals.",
                })
            disabled_tools = sorted(set(TOOL_IDS) - enabled)
            return {
                "objective": (
                    "Explain the paid MRR drop, identify affected accounts, "
                    "cite retrieved evidence, and propose approval-safe next actions."
                ),
                "hypotheses": _hypotheses_for_incident(incident),
                "tool_calls": tool_calls,
                "disabled_tool_ids": disabled_tools,
                "account_ids": account_ids,
                "doc_query": doc_query,
            }

        plan = recorder.record(
            stage="plan",
            inputs={
                "incident_id": incident["id"],
                "metric_name": incident["metric_evidence"]["metric_name"],
                "enabled_tool_ids": sorted(enabled),
            },
            action=build_plan,
        )
        return {"plan": plan}

    def agent_loop_node(state: InvestigationState) -> dict[str, Any]:
        """Agentic mode: the LLM drives a bounded evidence-gathering loop.

        The loop enforces the same tool policy, sanitizes LLM-proposed
        arguments against known identifiers, and degrades to the
        deterministic evidence sweep on LLM errors or budget exhaustion.
        """
        loop = InvestigationAgentLoop(
            session=session,
            recorder=recorder,
            llm_client=client,
            incident=state["incident"],
            enabled_tool_ids=enabled,
            blocked_reasons=blocked_reasons or {},
            max_iterations=max_iterations or 8,
        )
        result = loop.run()

        # Enabled tools the agent never requested are recorded as visible
        # NEUTRAL steps (not ``blocked``: nothing denied them, so the
        # blocked-step contract and UI keep meaning "policy violation").
        # Policy-blocked tools already have their own blocked step and are
        # excluded here. Degraded runs skip this: the deterministic sweep
        # already executed every enabled tool.
        if result.termination == TERMINATION_FINAL:
            not_requested = (
                enabled - loop.dispatched_tool_ids() - loop.blocked_tool_ids()
            )
            for tool_id in sorted(not_requested):

                def capture_skip(tid: str = tool_id) -> dict[str, Any]:
                    return {
                        "skipped_by_agent": True,
                        "note": (
                            f"{tid} was enabled but the agent finalized "
                            "without requesting it."
                        ),
                    }

                recorder.record(
                    stage="agent tool call",
                    tool_name=tool_id,
                    inputs={"source": "agent_finalized_without_request"},
                    action=capture_skip,
                )

        updates: dict[str, Any] = {
            "agentic_result": {
                "final_decision": (
                    result.final_decision.model_dump(mode="json")
                    if result.final_decision is not None
                    else None
                ),
                "usages": [usage.model_dump(mode="json") for usage in result.usages],
                "termination": result.termination,
                "error": result.error,
            }
        }
        if result.revenue_metrics is not None:
            updates["revenue_metrics"] = result.revenue_metrics
        if result.account_details is not None:
            updates["account_details"] = result.account_details
        if result.doc_results is not None:
            updates["doc_results"] = result.doc_results
        if result.support_tickets is not None:
            updates["support_tickets"] = result.support_tickets
        return updates

    def query_metrics_node(state: InvestigationState) -> dict[str, Any]:
        incident_id = state["incident_id"]
        if "query_revenue_metrics" in enabled:
            metrics = recorder.record(
                stage="query metrics",
                tool_name="query_revenue_metrics",
                inputs={"incident_id": incident_id},
                action=lambda: query_revenue_metrics(
                    session, QueryRevenueMetricsInput(incident_id=incident_id)
                ).model_dump(mode="json"),
            )
        else:
            # Blocked by the agent version's tool policy (PRD FR-7). Record a
            # visible ``blocked`` step with the reason, but still produce the
            # degraded-evidence fallback so report synthesis can cite it.
            fallback = disabled_revenue_metrics_fallback(incident_id, state["incident"])
            recorder.record_blocked(
                stage="query metrics",
                tool_name="query_revenue_metrics",
                inputs={"incident_id": incident_id},
                blocked_reason=reason_for("query_revenue_metrics"),
                fallback_output=fallback,
            )
            metrics = fallback

        if "fetch_account_details" in enabled:
            account_invoice_ids = [] if metrics.get("tool_disabled") else metrics["invoice_ids"]
            include_invoice_evidence = not bool(metrics.get("tool_disabled"))
            account_details = recorder.record(
                stage="query metrics",
                tool_name="fetch_account_details",
                inputs={
                    "account_ids": metrics["affected_account_ids"],
                    "invoice_ids": account_invoice_ids,
                    "include_invoices": include_invoice_evidence,
                },
                action=lambda: fetch_account_details(
                    session,
                    FetchAccountDetailsInput(
                        account_ids=metrics["affected_account_ids"],
                        invoice_ids=account_invoice_ids,
                        include_invoices=include_invoice_evidence,
                    ),
                ).model_dump(mode="json"),
            )
        else:
            fallback = {
                "accounts": [],
                "tool_disabled": True,
                "tool_disabled_reason": "fetch_account_details was not enabled for this agent version.",
            }
            recorder.record_blocked(
                stage="query metrics",
                tool_name="fetch_account_details",
                inputs={
                    "account_ids": metrics["affected_account_ids"],
                    "invoice_ids": metrics["invoice_ids"],
                },
                blocked_reason=reason_for("fetch_account_details"),
                fallback_output=fallback,
            )
            account_details = fallback

        return {"revenue_metrics": metrics, "account_details": account_details}

    def search_docs_node(state: InvestigationState) -> dict[str, Any]:
        plan = state["plan"]
        if "search_docs" in enabled:
            doc_results = recorder.record(
                stage="search docs",
                tool_name="search_docs",
                inputs={"query": plan["doc_query"], "limit": 5},
                action=lambda: search_docs(
                    session, SearchDocsInput(query=plan["doc_query"], limit=5)
                ).model_dump(mode="json"),
            )
        else:
            # Blocked by the agent version's tool policy (PRD FR-7). Record a
            # visible ``blocked`` step with the reason, but still produce the
            # degraded-evidence fallback so report synthesis can cite it.
            fallback = {
                "query": plan.get("doc_query", ""),
                "results": [],
                "tool_disabled": True,
                "tool_disabled_reason": "search_docs was not enabled for this agent version.",
            }
            recorder.record_blocked(
                stage="search docs",
                tool_name="search_docs",
                inputs={"query": plan.get("doc_query", ""), "limit": 5},
                blocked_reason=reason_for("search_docs"),
                fallback_output=fallback,
            )
            doc_results = fallback
        return {"doc_results": doc_results}

    def fetch_tickets_node(state: InvestigationState) -> dict[str, Any]:
        incident = state["incident"]
        plan = state["plan"]
        since = _parse_datetime(incident["detected_at"]) - timedelta(days=30)
        if "fetch_support_tickets" in enabled:
            support_tickets = recorder.record(
                stage="fetch tickets",
                tool_name="fetch_support_tickets",
                inputs={
                    "account_ids": plan["account_ids"],
                    "since": since,
                    "limit": 24,
                },
                action=lambda: fetch_support_tickets(
                    session,
                    FetchSupportTicketsInput(
                        account_ids=plan["account_ids"],
                        since=since,
                        limit=24,
                    ),
                ).model_dump(mode="json"),
            )
        else:
            # Blocked by the agent version's tool policy (PRD FR-7). Record a
            # visible ``blocked`` step with the reason, but still produce the
            # degraded-evidence fallback so report synthesis can cite it.
            fallback = {
                "tickets": [],
                "tool_disabled": True,
                "tool_disabled_reason": "fetch_support_tickets was not enabled for this agent version.",
            }
            recorder.record_blocked(
                stage="fetch tickets",
                tool_name="fetch_support_tickets",
                inputs={
                    "account_ids": plan["account_ids"],
                    "since": since,
                    "limit": 24,
                },
                blocked_reason=reason_for("fetch_support_tickets"),
                fallback_output=fallback,
            )
            support_tickets = fallback
        return {"support_tickets": support_tickets}

    def synthesize_report_node(state: InvestigationState) -> dict[str, Any]:
        # usage_box: a closure-captured list that _synthesize_report appends the
        # captured LLMUsage to. recorder.record reads it (as model_usage) only
        # AFTER the action returns, so the box is populated by then. This keeps
        # step.outputs == the report dict (eval/report consumers) and leaves the
        # run-level token/cost writes intact (test_agent_llm_integration.py).
        usage_box: list[LLMUsage] = []
        report = recorder.record(
            stage="synthesize report",
            inputs={
                "incident_id": state["incident_id"],
                "evidence_sets": [
                    "revenue_metrics",
                    "account_details",
                    "doc_results",
                    "support_tickets",
                ],
                "enabled_tool_ids": sorted(enabled),
            },
            model_usage=usage_box,
            action=lambda: _synthesize_report(
                state,
                llm_client=client,
                run=recorder.run,
                enabled_tool_ids=enabled,
                usage_box=usage_box,
            ).model_dump(mode="json"),
        )
        return {"final_report": report}

    builder.add_node("intake", intake_node)
    if agentic_mode:
        builder.add_node("agent_loop", agent_loop_node)
        builder.add_node("synthesize_report", synthesize_report_node)
        builder.add_edge(START, "intake")
        builder.add_edge("intake", "agent_loop")
        builder.add_edge("agent_loop", "synthesize_report")
        builder.add_edge("synthesize_report", END)
    else:
        builder.add_node("plan", plan_node)
        builder.add_node("query_metrics", query_metrics_node)
        builder.add_node("search_docs", search_docs_node)
        builder.add_node("fetch_tickets", fetch_tickets_node)
        builder.add_node("synthesize_report", synthesize_report_node)
        builder.add_edge(START, "intake")
        builder.add_edge("intake", "plan")
        builder.add_edge("plan", "query_metrics")
        builder.add_edge("query_metrics", "search_docs")
        builder.add_edge("search_docs", "fetch_tickets")
        builder.add_edge("fetch_tickets", "synthesize_report")
        builder.add_edge("synthesize_report", END)

    graph = builder.compile()
    final_state = graph.invoke({"run_id": run.id, "incident_id": run.incident_id})
    return InvestigationReport.model_validate(final_state["final_report"])


def _disabled_revenue_metrics(
    incident_id: str, incident: dict[str, Any]
) -> dict[str, Any]:
    """Backward-compatible alias; canonical implementation lives in
    ``app.agent.tools`` so the agentic loop and the deterministic pipeline
    share one disabled-tool payload."""
    return disabled_revenue_metrics_fallback(incident_id, incident)


def _synthesize_report(
    state: InvestigationState,
    *,
    llm_client: LLMClient,
    run: AgentRun,
    enabled_tool_ids: set[str] | frozenset[str] | None = None,
    usage_box: list[LLMUsage] | None = None,
) -> InvestigationReport:
    incident = state["incident"]
    revenue_metrics = state.get("revenue_metrics") or {
        "incident_id": incident["id"],
        "metric_evidence": incident["metric_evidence"],
        "affected_account_ids": [a["account_id"] for a in incident["affected_accounts"]],
        "affected_accounts": incident["affected_accounts"],
        "invoice_ids": incident["metric_evidence"].get("invoice_ids", []),
        "sql_evidence": [],
    }
    account_details = state.get("account_details", {"accounts": []})
    doc_results = state.get("doc_results", {"query": "", "results": []})
    support_tickets = state.get("support_tickets", {"tickets": []})

    agentic_result = state.get("agentic_result")
    if agentic_result is not None:
        diagnosis, llm_usage = _resolve_agentic_diagnosis(
            agentic_result=agentic_result,
            incident=incident,
            revenue_metrics=revenue_metrics,
            account_details=account_details,
            doc_results=doc_results,
            support_tickets=support_tickets,
            llm_client=llm_client,
        )
    else:
        diagnosis, llm_usage = diagnose_with_llm_or_fallback(
            llm_client=llm_client,
            incident=incident,
            revenue_metrics=revenue_metrics,
            account_details=account_details,
            doc_results=doc_results,
            support_tickets=support_tickets,
        )
    run.trace_metadata = {
        **run.trace_metadata,
        "agent_version_id": run.agent_version_id,
        "agent_mode": "agentic" if agentic_result is not None else "deterministic",
        "llm_provider": llm_usage.provider,
        "llm_model": llm_usage.model,
        "llm_latency_ms": llm_usage.latency_ms,
        "llm_used": llm_usage.used_llm,
        "llm_fallback_reason": llm_usage.fallback_reason,
    }
    run.prompt_tokens = llm_usage.prompt_tokens
    run.completion_tokens = llm_usage.completion_tokens
    run.token_estimate = llm_usage.prompt_tokens + llm_usage.completion_tokens
    if llm_usage.used_llm:
        run.cost_estimate_usd = estimate_cost_usd(
            prompt_tokens=llm_usage.prompt_tokens,
            completion_tokens=llm_usage.completion_tokens,
            model=llm_usage.model,
        )
    else:
        run.cost_estimate_usd = 0.0

    # Hand the captured LLMUsage back to the recorder via the usage_box so it
    # gets persisted as a ModelUsage row linked to this step (PRD §9.2 / FR-20).
    # The same object fed the run-level writes above, so per-step and run-level
    # token counts stay consistent. Appended AFTER the run-level writes so a
    # failure here does not corrupt the run row.
    if usage_box is not None:
        usage_box.append(llm_usage)

    tickets_by_account: dict[str, list[dict[str, Any]]] = {}
    for ticket in support_tickets.get("tickets", []):
        tickets_by_account.setdefault(ticket["account_id"], []).append(ticket)

    if account_details.get("accounts"):
        affected_accounts = [
            ReportAffectedAccount(
                account_id=account["account_id"],
                account_name=account["account_name"],
                segment=account["segment"],
                health_score=account["health_score"],
                failed_invoice_cents=sum(
                    invoice["amount_cents"] for invoice in account.get("failed_invoices", [])
                ),
                failed_invoice_ids=[
                    invoice["invoice_id"] for invoice in account.get("failed_invoices", [])
                ],
                ticket_ids=[
                    ticket["ticket_id"]
                    for ticket in tickets_by_account.get(account["account_id"], [])
                ],
            )
            for account in account_details["accounts"]
        ]
    else:
        affected_accounts = [
            ReportAffectedAccount(
                account_id=account["account_id"],
                account_name=account["account_name"],
                segment=account.get("segment", "unknown"),
                health_score=account.get("health_score", 0),
                failed_invoice_cents=account.get("failed_invoice_cents", 0),
                failed_invoice_ids=account.get("failed_invoice_ids", []),
                ticket_ids=[
                    ticket["ticket_id"]
                    for ticket in tickets_by_account.get(account["account_id"], [])
                ],
            )
            for account in revenue_metrics.get("affected_accounts", incident["affected_accounts"])
        ]

    evidence = _report_evidence(
        revenue_metrics=revenue_metrics,
        account_details=account_details,
        doc_results=doc_results,
        support_tickets=support_tickets,
    )
    confidence = _confidence_for_report(
        revenue_metrics=revenue_metrics,
        doc_results=doc_results,
        support_tickets=support_tickets,
        diagnosis=diagnosis,
    )
    affected_count = len(affected_accounts)
    confirmed_invoice_account_count = sum(
        1 for account in affected_accounts if account.failed_invoice_ids
    )
    if revenue_metrics.get("tool_disabled"):
        account_context = (
            f"{affected_count} incident accounts were reviewed without revenue metric evidence."
            if affected_count
            else "No affected accounts were confirmed by the available evidence."
        )
    elif confirmed_invoice_account_count:
        account_context = (
            f"{confirmed_invoice_account_count} accounts have failed renewal evidence."
        )
    else:
        account_context = "No affected accounts were confirmed by the available evidence."

    raw_report = {
        "root_cause": diagnosis.root_cause,
        "summary": f"{incident['title']}: {diagnosis.root_cause} {account_context}",
        "affected_accounts": [
            account.model_dump(mode="json") for account in affected_accounts
        ],
        "cited_evidence": [item.model_dump(mode="json") for item in evidence],
        "claims": [
            claim.model_dump(mode="json")
            for claim in _report_claims(
                diagnosis=diagnosis,
                evidence=evidence,
                revenue_metrics=revenue_metrics,
                affected_count=affected_count,
                account_context=account_context,
            )
        ],
        "confidence": confidence,
        "next_actions": diagnosis.next_actions,
        "generated_at": utcnow_naive(),
    }
    return InvestigationReport.model_validate(raw_report)


def _report_claims(
    *,
    diagnosis: Diagnosis,
    evidence: list[ReportEvidence],
    revenue_metrics: dict[str, Any],
    affected_count: int,
    account_context: str,
) -> list[ReportClaim]:
    refs_by_kind: dict[str, list[str]] = {}
    for item in evidence:
        refs_by_kind.setdefault(item.kind, []).append(item.reference_id)

    all_refs = [item.reference_id for item in evidence]
    root_cause_refs = _first_refs(
        refs_by_kind.get("sql", []),
        refs_by_kind.get("document", []),
        refs_by_kind.get("ticket", []),
    )
    impact_refs = _first_refs(
        refs_by_kind.get("sql", []),
        refs_by_kind.get("ticket", []),
        refs_by_kind.get("tool", []),
    )
    recommendation_refs = _first_refs(
        refs_by_kind.get("document", []),
        refs_by_kind.get("ticket", []),
        refs_by_kind.get("sql", []),
        refs_by_kind.get("tool", []),
    )
    tool_refs = refs_by_kind.get("tool", [])
    metrics_disabled = bool(revenue_metrics.get("tool_disabled"))
    if metrics_disabled:
        root_cause_refs = _first_refs(tool_refs)
        impact_refs = _first_refs(tool_refs)
        recommendation_refs = _first_refs(
            tool_refs,
            refs_by_kind.get("document", []),
            refs_by_kind.get("ticket", []),
        )

    claims = [
        ReportClaim(
            category="root_cause",
            text=diagnosis.root_cause,
            citation_refs=root_cause_refs or all_refs[:1],
        ),
        ReportClaim(
            category="impact",
            text=account_context,
            citation_refs=impact_refs or all_refs[:1],
        ),
    ]

    for action in diagnosis.next_actions:
        claims.append(
            ReportClaim(
                category="recommendation",
                text=action,
                citation_refs=recommendation_refs or all_refs[:1],
            )
        )

    if not diagnosis.is_specific:
        claims.append(
            ReportClaim(
                category="uncertainty",
                text=(
                    "The available evidence is insufficient to prove a specific "
                    "operational root cause."
                ),
                citation_refs=tool_refs or all_refs[:2],
            )
        )
    elif tool_refs:
        claims.append(
            ReportClaim(
                category="uncertainty",
                text=(
                    "Some evidence-producing tools were disabled for this agent "
                    "version and are cited separately from retrieved evidence."
                ),
                citation_refs=tool_refs,
            )
        )

    return claims


def _first_refs(*groups: list[str], limit: int = 4) -> list[str]:
    refs: list[str] = []
    for group in groups:
        for ref in group:
            if ref not in refs:
                refs.append(ref)
            if len(refs) >= limit:
                return refs
    return refs


def _report_evidence(
    *,
    revenue_metrics: dict[str, Any],
    account_details: dict[str, Any],
    doc_results: dict[str, Any],
    support_tickets: dict[str, Any],
) -> list[ReportEvidence]:
    evidence: list[ReportEvidence] = []
    if revenue_metrics.get("tool_disabled"):
        evidence.append(_tool_disabled_evidence("query_revenue_metrics", revenue_metrics))
    if account_details.get("tool_disabled"):
        evidence.append(_tool_disabled_evidence("fetch_account_details", account_details))
    if doc_results.get("tool_disabled"):
        evidence.append(_tool_disabled_evidence("search_docs", doc_results))
    if support_tickets.get("tool_disabled"):
        evidence.append(
            _tool_disabled_evidence("fetch_support_tickets", support_tickets)
        )
    for item in revenue_metrics["sql_evidence"]:
        evidence.append(
            ReportEvidence(
                kind="sql",
                title=item["title"],
                summary=item["summary"],
                reference_id=item["reference_id"],
                source_query=item["source_query"],
                citation=item.get("citation", {}),
            )
        )

    for result in doc_results["results"][:3]:
        evidence.append(
            ReportEvidence(
                kind="document",
                title=result["title"],
                summary=result["snippet"],
                reference_id=result.get("citation", {}).get("chunk_id") or result["source_id"],
                source_query=None,
                citation=result.get("citation", {}),
            )
        )

    for ticket in support_tickets["tickets"][:3]:
        evidence.append(
            ReportEvidence(
                kind="ticket",
                title=ticket["subject"],
                summary=ticket["description"],
                reference_id=ticket["ticket_id"],
                source_query=None,
                citation={
                    "ticket_id": ticket["ticket_id"],
                    "account_id": ticket["account_id"],
                    "account_name": ticket["account_name"],
                    "created_at": ticket["created_at"],
                    "category": ticket["category"],
                    "priority": ticket["priority"],
                    "status": ticket["status"],
                },
            )
        )
    return evidence


def _tool_disabled_evidence(tool_name: str, payload: dict[str, Any]) -> ReportEvidence:
    reason = payload.get(
        "tool_disabled_reason",
        f"{tool_name} was not enabled for this agent version.",
    )
    return ReportEvidence(
        kind="tool",
        title=f"{tool_name} disabled",
        summary=reason,
        reference_id=f"tool-disabled:{tool_name}",
        source_query=None,
        citation={
            "tool_name": tool_name,
            "status": "disabled",
            "reason": reason,
        },
    )


def _confidence_for_report(
    *,
    revenue_metrics: dict[str, Any],
    doc_results: dict[str, Any],
    support_tickets: dict[str, Any],
    diagnosis: Diagnosis,
) -> str:
    has_failed_invoices = revenue_metrics["metric_evidence"]["failed_invoice_count"] > 0
    has_docs = len(doc_results["results"]) > 0
    has_tickets = len(support_tickets["tickets"]) > 0
    if has_failed_invoices and has_docs and has_tickets and diagnosis.is_specific:
        return "high"
    if has_failed_invoices and (has_docs or has_tickets) and diagnosis.is_specific:
        return "medium"
    return "low"


def _hypotheses_for_incident(incident: dict[str, Any]) -> list[str]:
    """Derive working hypotheses from the incident's own signals.

    Every hypothesis names the concrete metric movement, ticket categories, or
    product events attached to this incident, so two different incidents never
    surface the same checklist in the run timeline.
    """
    metric = incident["metric_evidence"]
    delta_percent = metric.get("delta_percent")
    movement = (
        f"{delta_percent:+.1f}%"
        if isinstance(delta_percent, (int, float))
        else "the observed"
    )
    hypotheses = [
        f"Failed or delayed renewal invoices explain {movement} paid MRR movement "
        f"in {metric['metric_name']}.",
    ]

    ticket_categories = sorted(
        {signal["category"] for signal in incident.get("support_signals", [])}
    )
    if ticket_categories:
        hypotheses.append(
            "Recent "
            + ", ".join(ticket_categories[:3])
            + " tickets from affected accounts point at the operational cause."
        )
    else:
        hypotheses.append(
            "No support signals are attached, so the cause must come from "
            "invoice and product evidence alone."
        )

    event_names = sorted(
        {signal["event_name"] for signal in incident.get("product_signals", [])}
    )
    if event_names:
        hypotheses.append(
            "Product events ("
            + ", ".join(event_names[:3])
            + ") are a contributing factor rather than the primary revenue cause."
        )
    hypotheses.append(
        "Retrieved runbooks match the observed invoice and ticket pattern."
    )
    return hypotheses


def _resolve_agentic_diagnosis(
    *,
    agentic_result: dict[str, Any],
    incident: dict[str, Any],
    revenue_metrics: dict[str, Any],
    account_details: dict[str, Any],
    doc_results: dict[str, Any],
    support_tickets: dict[str, Any],
    llm_client: LLMClient,
) -> tuple[Diagnosis, LLMUsage]:
    """Resolve the loop's outcome into the report diagnosis.

    The deterministic evidence classifier is computed over whatever evidence the
    loop actually gathered. A final LLM proposal is adopted only when it
    passes the same evidence-support gate as the single-shot path; degraded
    terminations (LLM error, malformed decisions, exhausted budget) always
    yield the deterministic diagnosis with an honest fallback reason.
    """
    deterministic_diagnosis = _diagnose_from_evidence(
        revenue_metrics=revenue_metrics,
        account_details=account_details,
        doc_results=doc_results,
        support_tickets=support_tickets,
    )
    usages = [
        LLMUsage.model_validate(item) for item in agentic_result.get("usages", [])
    ]
    termination = str(agentic_result.get("termination", "final_decision"))
    usage = _aggregate_loop_usages(
        usages,
        provider=getattr(llm_client, "provider", "unknown"),
        model=getattr(llm_client, "model", "unknown"),
        termination=termination,
        error=agentic_result.get("error"),
    )

    final_payload = agentic_result.get("final_decision")
    if final_payload is None:
        return deterministic_diagnosis, usage

    decision = AgentFinalDecision.model_validate(final_payload)
    llm_diagnosis = Diagnosis(
        root_cause=decision.root_cause,
        next_actions=decision.next_actions or _fallback_next_actions(),
        is_specific=decision.confidence in {"medium", "high"},
    )
    if _diagnosis_is_supported_by_evidence(
        llm_diagnosis=llm_diagnosis,
        deterministic_diagnosis=deterministic_diagnosis,
    ):
        return llm_diagnosis, usage
    return deterministic_diagnosis, usage.model_copy(
        update={"fallback_reason": "unsupported_llm_diagnosis: deterministic_fallback"}
    )


def _aggregate_loop_usages(
    usages: list[LLMUsage],
    *,
    provider: str,
    model: str,
    termination: str,
    error: object | None,
) -> LLMUsage:
    """Aggregate per-decision loop usage into the run-level usage record.

    Degraded terminations keep ``used_llm`` honest (tokens were still spent)
    while surfacing the degradation reason in ``fallback_reason``.
    """
    used_llm = any(usage.used_llm for usage in usages)
    fallback_reason: str | None = None
    if termination != TERMINATION_FINAL:
        fallback_reason = f"agent_loop_degraded: {termination}"
        if error:
            fallback_reason += f" ({error})"
    elif not used_llm:
        fallback_reason = "llm_provider=none"
    return LLMUsage(
        provider=provider,
        model=model,
        prompt_tokens=sum(usage.prompt_tokens for usage in usages),
        completion_tokens=sum(usage.completion_tokens for usage in usages),
        latency_ms=sum(usage.latency_ms for usage in usages),
        used_llm=used_llm,
        fallback_reason=fallback_reason,
    )


def diagnose_with_llm_or_fallback(
    *,
    llm_client: LLMClient,
    incident: dict[str, Any],
    revenue_metrics: dict[str, Any],
    account_details: dict[str, Any],
    doc_results: dict[str, Any],
    support_tickets: dict[str, Any],
) -> tuple[Diagnosis, LLMUsage]:
    """Try LLM diagnosis first, then fall back to deterministic evidence matching."""
    deterministic_diagnosis = _diagnose_from_evidence(
        revenue_metrics=revenue_metrics,
        account_details=account_details,
        doc_results=doc_results,
        support_tickets=support_tickets,
    )
    prompt = build_investigation_prompt(
        incident=incident,
        revenue_metrics=revenue_metrics,
        account_details=account_details,
        doc_results=doc_results,
        support_tickets=support_tickets,
    )

    llm_diagnosis, usage = _diagnose_with_llm(
        llm_client=llm_client,
        prompt=prompt,
    )
    if llm_diagnosis is not None and _diagnosis_is_supported_by_evidence(
        llm_diagnosis=llm_diagnosis,
        deterministic_diagnosis=deterministic_diagnosis,
    ):
        return llm_diagnosis, usage

    fallback_reason = usage.fallback_reason
    if llm_diagnosis is not None and fallback_reason is None:
        fallback_reason = "unsupported_llm_diagnosis: deterministic_fallback"

    fallback_usage = usage.model_copy(
        update={
            "used_llm": usage.used_llm,
            "fallback_reason": fallback_reason or "deterministic_fallback",
        }
    ) if usage else LLMUsage(
        provider=getattr(llm_client, "provider", "unknown"),
        model=getattr(llm_client, "model", "unknown"),
        used_llm=False,
        fallback_reason="deterministic_fallback",
    )
    return deterministic_diagnosis, fallback_usage


def _diagnose_with_llm(
    *,
    llm_client: LLMClient,
    prompt: str,
) -> tuple[Diagnosis | None, LLMUsage]:
    """Return a Diagnosis from the LLM, or None when the response is unusable."""
    try:
        llm_response, usage = llm_client.complete(prompt)
    except Exception as exc:
        return None, LLMUsage(
            provider=getattr(llm_client, "provider", "unknown"),
            model=getattr(llm_client, "model", "unknown"),
            used_llm=False,
            fallback_reason=f"llm_error: {exc}",
        )

    if not llm_response.root_cause or llm_response.root_cause.strip().lower().startswith(
        "llm is disabled"
    ):
        return None, usage

    diagnosis = Diagnosis(
        root_cause=llm_response.root_cause,
        next_actions=llm_response.next_actions or _fallback_next_actions(),
        is_specific=llm_response.confidence in {"medium", "high"},
    )
    return diagnosis, usage


def _diagnosis_is_supported_by_evidence(
    *,
    llm_diagnosis: Diagnosis,
    deterministic_diagnosis: Diagnosis,
) -> bool:
    """Gate an LLM diagnosis by agreement with the deterministic classifier.

    The deterministic evidence classifier is the source of truth for root
    cause in this product slice: an LLM diagnosis is adopted only when it
    lands on the same scenario signature, or when it honestly declines to
    name a specific cause. Anything else is discarded and the run records
    ``unsupported_llm_diagnosis: deterministic_fallback``.
    """
    if not llm_diagnosis.is_specific:
        return _contains_any(
            llm_diagnosis.root_cause,
            ["insufficient", "does not prove", "unknown", "cannot prove"],
        )
    if not deterministic_diagnosis.is_specific:
        return False
    return _root_cause_signature(llm_diagnosis.root_cause) == _root_cause_signature(
        deterministic_diagnosis.root_cause
    )


def _root_cause_signature(root_cause: str) -> str | None:
    """Map a root-cause sentence to its scenario signature bucket.

    This is the shared vocabulary between the deterministic classifier and the
    LLM gate: both sides must land on the same bucket for an LLM diagnosis to
    be adopted.
    """
    text = root_cause.lower()
    signatures: dict[str, tuple[tuple[str, ...], ...]] = {
        "retry_webhook": (("retry", "webhook"),),
        "expired_payment_method": (
            ("expired", "payment"),
            ("expired", "card"),
            ("card", "expiration"),
        ),
        "enterprise_onboarding": (("onboarding",), ("procurement",)),
        "csv_import": (("csv", "import"),),
        "report_export": (("report", "export"),),
    }
    for name, alternatives in signatures.items():
        if any(all(marker in text for marker in markers) for markers in alternatives):
            return name
    return None


def _fallback_next_actions() -> list[str]:
    return [
        "Collect additional account, support, and product evidence before naming a root cause.",
        "Review failed invoice rows and support tickets for affected accounts.",
        "Keep any customer follow-up in approval-gated drafts.",
    ]


def _diagnose_from_evidence(
    *,
    revenue_metrics: dict[str, Any],
    account_details: dict[str, Any],
    doc_results: dict[str, Any],
    support_tickets: dict[str, Any],
) -> Diagnosis:
    """Deterministic evidence classifier: the source of truth for root cause.

    Branches on concrete evidence signals (failed-invoice reasons plus
    ticket/document keywords) over the seeded scenarios. Evidence that matches
    no known signature yields the explicit uncertainty diagnosis rather than
    a guess.
    """
    evidence_text = _evidence_text(
        account_details=account_details,
        doc_results=doc_results,
        support_tickets=support_tickets,
    )
    invoice_failure_text = _invoice_failure_text(account_details)
    has_failed_invoices = revenue_metrics["metric_evidence"]["failed_invoice_count"] > 0
    has_context = bool(doc_results["results"] or support_tickets["tickets"])
    has_card_expiration_failure = _contains_any(
        invoice_failure_text,
        ["expired card", "expired cards", "card expiration", "payment method"],
    )
    has_retry_webhook_failure = "retry webhook" in invoice_failure_text

    if revenue_metrics.get("tool_disabled"):
        return Diagnosis(
            root_cause=(
                "MRR dropped, but the available evidence does not prove a specific "
                "operational root cause."
            ),
            next_actions=[
                "Collect revenue metrics before naming a root cause.",
                "Review account records and support tickets for affected accounts.",
                "Keep any customer follow-up in approval-gated drafts.",
            ],
            is_specific=False,
        )

    if has_failed_invoices and has_context and has_card_expiration_failure:
        return Diagnosis(
            root_cause="Expired payment methods were not refreshed before renewal.",
            next_actions=[
                "Draft billing contact reminders.",
                "Audit card-expiration notices.",
                "Create approval-gated billing follow-up drafts for affected accounts.",
            ],
            is_specific=True,
        )

    if has_failed_invoices and has_context and (
        has_retry_webhook_failure or "retry webhook" in evidence_text
    ):
        return Diagnosis(
            root_cause="Billing retry webhook regression suppressed second charge attempts.",
            next_actions=[
                "Repair retry workflow.",
                "Replay failed retry jobs for cited invoice IDs.",
                "Create approval-gated billing follow-up drafts for affected accounts.",
            ],
            is_specific=True,
        )

    if has_failed_invoices and has_context and _contains_any(
        evidence_text, ["expired card", "card expiration", "payment method"]
    ):
        return Diagnosis(
            root_cause="Expired payment methods were not refreshed before renewal.",
            next_actions=[
                "Draft billing contact reminders.",
                "Audit card-expiration notices.",
                "Create approval-gated billing follow-up drafts for affected accounts.",
            ],
            is_specific=True,
        )

    if has_context and _contains_all(evidence_text, ["procurement", "onboarding"]):
        return Diagnosis(
            root_cause="Enterprise sponsors canceled after unresolved onboarding risk.",
            next_actions=[
                "Prepare win-back outreach.",
                "Summarize unresolved onboarding blockers by account.",
                "Keep outreach approval-gated before contacting sponsors.",
            ],
            is_specific=True,
        )

    if has_context and _contains_all(evidence_text, ["csv", "import"]):
        return Diagnosis(
            root_cause="CSV import instability reduced recent active usage.",
            next_actions=[
                "Prioritize the import stability fix.",
                "Send an approval-gated status update draft to affected admins.",
                "Collect product-event evidence before making revenue recovery claims.",
            ],
            is_specific=True,
        )

    if has_context and _contains_all(evidence_text, ["report", "export"]):
        return Diagnosis(
            root_cause="Report export filter bug caused duplicate product tickets.",
            next_actions=[
                "Fix export filter handling.",
                "Deduplicate support backlog tickets.",
                "Publish an approval-gated workaround draft for affected admins.",
            ],
            is_specific=True,
        )

    return Diagnosis(
        root_cause=(
            "MRR dropped after failed renewals, but the available evidence does not "
            "prove a specific operational root cause."
        ),
        next_actions=[
            "Collect additional account, support, and product evidence before naming a root cause.",
            "Review failed invoice rows and support tickets for affected accounts.",
            "Keep any customer follow-up in approval-gated drafts.",
        ],
        is_specific=False,
    )


def _evidence_text(
    *,
    account_details: dict[str, Any],
    doc_results: dict[str, Any],
    support_tickets: dict[str, Any],
) -> str:
    """Build a lowercased text blob of actual evidence for deterministic diagnosis.

    Doc results are intentionally excluded. Runbook snippets describe what to
    look for (e.g., "check whether the failure reason says retry webhook"), so
    including them would create self-fulfilling diagnoses: any query about
    failed renewals returns docs that mention various failure modes, and the
    diagnosis would match whatever the docs mention rather than the actual
    invoice failure reasons and support ticket content.
    """
    parts: list[str] = []
    for account in account_details["accounts"]:
        parts.extend(
            str(value)
            for value in [
                account.get("account_name"),
                account.get("segment"),
                account.get("subscription_status"),
            ]
            if value
        )
        parts.extend(
            str(invoice.get("failure_reason"))
            for invoice in account.get("failed_invoices", [])
            if invoice.get("failure_reason")
        )
    for ticket in support_tickets["tickets"]:
        parts.extend(
            str(value)
            for value in [
                ticket.get("category"),
                ticket.get("subject"),
                ticket.get("description"),
            ]
            if value
        )
    return " ".join(parts).lower()


def _invoice_failure_text(account_details: dict[str, Any]) -> str:
    reasons: list[str] = []
    for account in account_details["accounts"]:
        reasons.extend(
            str(invoice.get("failure_reason"))
            for invoice in account.get("failed_invoices", [])
            if invoice.get("failure_reason")
        )
    return " ".join(reasons).lower()


def _contains_any(text: str, needles: list[str]) -> bool:
    return any(needle in text for needle in needles)


def _contains_all(text: str, needles: list[str]) -> bool:
    return all(needle in text for needle in needles)


def _parse_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
