from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph
from sqlalchemy.orm import Session

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
from app.llm.schemas import LLMUsage
from app.agent.tools import (
    FetchAccountDetailsInput,
    FetchSupportTicketsInput,
    QueryRevenueMetricsInput,
    SearchDocsInput,
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
) -> InvestigationReport:
    recorder = AgentRunRecorder(session, run, trace)
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
            doc_query = _doc_query_for_incident(incident)
            account_ids = [
                account["account_id"] for account in incident["affected_accounts"]
            ]
            return {
                "objective": (
                    "Explain the paid MRR drop, identify affected accounts, "
                    "cite retrieved evidence, and propose approval-safe next actions."
                ),
                "hypotheses": _hypotheses_for_incident(incident),
                "tool_calls": [
                    {
                        "tool_name": "query_revenue_metrics",
                        "reason": "Confirm the metric movement and failed invoice evidence.",
                    },
                    {
                        "tool_name": "fetch_account_details",
                        "reason": "Attach account, subscription, and invoice context.",
                    },
                    {
                        "tool_name": "search_docs",
                        "reason": "Retrieve internal runbooks and policy citations.",
                        "query": doc_query,
                    },
                    {
                        "tool_name": "fetch_support_tickets",
                        "reason": "Connect account impact with recent customer signals.",
                    },
                ],
                "account_ids": account_ids,
                "doc_query": doc_query,
            }

        plan = recorder.record(
            stage="plan",
            inputs={
                "incident_id": incident["id"],
                "metric_name": incident["metric_evidence"]["metric_name"],
            },
            action=build_plan,
        )
        return {"plan": plan}

    def query_metrics_node(state: InvestigationState) -> dict[str, Any]:
        incident_id = state["incident_id"]
        metrics = recorder.record(
            stage="query metrics",
            tool_name="query_revenue_metrics",
            inputs={"incident_id": incident_id},
            action=lambda: query_revenue_metrics(
                session, QueryRevenueMetricsInput(incident_id=incident_id)
            ).model_dump(mode="json"),
        )
        account_details = recorder.record(
            stage="query metrics",
            tool_name="fetch_account_details",
            inputs={
                "account_ids": metrics["affected_account_ids"],
                "invoice_ids": metrics["invoice_ids"],
            },
            action=lambda: fetch_account_details(
                session,
                FetchAccountDetailsInput(
                    account_ids=metrics["affected_account_ids"],
                    invoice_ids=metrics["invoice_ids"],
                ),
            ).model_dump(mode="json"),
        )
        return {"revenue_metrics": metrics, "account_details": account_details}

    def search_docs_node(state: InvestigationState) -> dict[str, Any]:
        plan = state["plan"]
        doc_results = recorder.record(
            stage="search docs",
            tool_name="search_docs",
            inputs={"query": plan["doc_query"], "limit": 5},
            action=lambda: search_docs(
                session, SearchDocsInput(query=plan["doc_query"], limit=5)
            ).model_dump(mode="json"),
        )
        return {"doc_results": doc_results}

    def fetch_tickets_node(state: InvestigationState) -> dict[str, Any]:
        incident = state["incident"]
        plan = state["plan"]
        since = _parse_datetime(incident["detected_at"]) - timedelta(days=30)
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
        return {"support_tickets": support_tickets}

    def synthesize_report_node(state: InvestigationState) -> dict[str, Any]:
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
            },
            action=lambda: _synthesize_report(
                state,
                llm_client=llm_client or NoopLLMClient(),
                run=recorder.run,
            ).model_dump(mode="json"),
        )
        return {"final_report": report}

    builder.add_node("intake", intake_node)
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


def _synthesize_report(
    state: InvestigationState, *, llm_client: LLMClient, run: AgentRun
) -> InvestigationReport:
    incident = state["incident"]
    revenue_metrics = state["revenue_metrics"]
    account_details = state["account_details"]
    doc_results = state["doc_results"]
    support_tickets = state["support_tickets"]

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

    tickets_by_account: dict[str, list[dict[str, Any]]] = {}
    for ticket in support_tickets["tickets"]:
        tickets_by_account.setdefault(ticket["account_id"], []).append(ticket)

    affected_accounts = [
        ReportAffectedAccount(
            account_id=account["account_id"],
            account_name=account["account_name"],
            segment=account["segment"],
            health_score=account["health_score"],
            failed_invoice_cents=sum(
                invoice["amount_cents"] for invoice in account["failed_invoices"]
            ),
            failed_invoice_ids=[
                invoice["invoice_id"] for invoice in account["failed_invoices"]
            ],
            ticket_ids=[
                ticket["ticket_id"]
                for ticket in tickets_by_account.get(account["account_id"], [])
            ],
        )
        for account in account_details["accounts"]
    ]

    evidence = _report_evidence(
        revenue_metrics=revenue_metrics,
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
    account_context = (
        f"{affected_count} accounts have failed renewal evidence."
        if affected_count
        else "No affected accounts were confirmed by the available evidence."
    )

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
    impact_refs = _first_refs(refs_by_kind.get("sql", []), refs_by_kind.get("ticket", []))
    recommendation_refs = _first_refs(
        refs_by_kind.get("document", []),
        refs_by_kind.get("ticket", []),
        refs_by_kind.get("sql", []),
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
                citation_refs=all_refs[:2],
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
    doc_results: dict[str, Any],
    support_tickets: dict[str, Any],
) -> list[ReportEvidence]:
    evidence: list[ReportEvidence] = []
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


def _doc_query_for_incident(incident: dict[str, Any]) -> str:
    query_parts = [
        incident["title"],
        incident["summary"],
        incident["metric_evidence"]["metric_name"],
    ]
    query_parts.extend(str(query) for query in incident["evidence"].get("source_queries", []))
    query_parts.extend(
        f"{signal['category']} {signal['subject']}"
        for signal in incident.get("support_signals", [])[:4]
    )
    query_parts.extend(
        signal["event_name"] for signal in incident.get("product_signals", [])[:4]
    )
    return " ".join(query_parts)


def _hypotheses_for_incident(incident: dict[str, Any]) -> list[str]:
    hypotheses = [
        "Failed renewal invoices explain the paid MRR delta.",
        "Recent support tickets identify the operational cause.",
        "Retrieved runbooks match the observed invoice and ticket pattern.",
        "Product disruption or support backlog is a contributing factor rather than the primary revenue cause.",
    ]
    return hypotheses


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
    for result in doc_results["results"]:
        citation = result.get("citation", {})
        parts.extend(
            str(value)
            for value in [
                result.get("source_id"),
                result.get("title"),
                result.get("snippet"),
                citation.get("source_id"),
                citation.get("title"),
            ]
            if value
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
