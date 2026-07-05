from __future__ import annotations

import json
from typing import Any

INVESTIGATION_SYSTEM_PROMPT = """You are a senior revenue operations analyst investigating a SaaS metric anomaly.

Your task is to read the evidence below and produce a structured JSON diagnosis.

Return ONLY a JSON object matching this schema:
{
  "root_cause": "One sentence explaining the operational root cause. Do not restate the symptom.",
  "confidence": "low | medium | high",
  "next_actions": ["3-5 concrete, approval-safe next actions"],
  "reasoning": "One paragraph explaining how the evidence supports your conclusion."
}

Rules:
- Root cause must be a single specific operational reason, not a symptom.
- Confidence is high only when SQL evidence, a relevant document, and support tickets all align.
- Next actions must be safe to draft; never claim a message was already sent.
- If the evidence is insufficient to prove a specific cause, set confidence to low and say so in root_cause.

Examples:

Example 1 (strong evidence):
{
  "root_cause": "Billing retry webhook regression suppressed second charge attempts.",
  "confidence": "high",
  "next_actions": [
    "Repair the retry webhook handler and replay failed retry jobs.",
    "Draft an approval-gated customer email explaining the delayed retry.",
    "Create a task to audit retry webhook logs for the affected window."
  ],
  "reasoning": "SQL evidence shows 6 failed invoices tied to retry webhook failures, the billing retry runbook matches the observed pattern, and support tickets cite missing retry attempts."
}

Example 2 (insufficient evidence):
{
  "root_cause": "MRR dropped after failed renewals, but the available evidence does not prove a specific operational root cause.",
  "confidence": "low",
  "next_actions": [
    "Collect additional account, support, and product evidence before naming a root cause.",
    "Review failed invoice rows and support tickets for affected accounts.",
    "Keep any customer follow-up in approval-gated drafts."
  ],
  "reasoning": "Failed invoices are present but no matching runbook, support tickets, or product signals point to a single operational cause."
}
"""


def build_investigation_prompt(
    *,
    incident: dict[str, Any],
    revenue_metrics: dict[str, Any],
    account_details: dict[str, Any],
    doc_results: dict[str, Any],
    support_tickets: dict[str, Any],
) -> str:
    sections: list[str] = [f"# Incident: {incident.get('title', '')}"]
    summary = incident.get("summary")
    if summary:
        sections.append(f"Summary: {summary}")

    metric_evidence = revenue_metrics.get("metric_evidence", {})
    sections.append("## Revenue metrics")
    sections.append(json.dumps(metric_evidence, indent=2, default=str))

    sections.append("## Affected accounts")
    for account in account_details.get("accounts", [])[:8]:
        failed_invoices = account.get("failed_invoices", [])
        invoice_summary = [
            {
                "id": invoice.get("invoice_id"),
                "amount_cents": invoice.get("amount_cents"),
                "failure_reason": invoice.get("failure_reason"),
            }
            for invoice in failed_invoices
        ]
        sections.append(
            json.dumps(
                {
                    "account_id": account.get("account_id"),
                    "account_name": account.get("account_name"),
                    "segment": account.get("segment"),
                    "subscription_status": account.get("subscription_status"),
                    "failed_invoice_count": len(failed_invoices),
                    "failed_invoices": invoice_summary,
                },
                indent=2,
                default=str,
            )
        )

    sections.append("## Relevant documents")
    for result in doc_results.get("results", [])[:5]:
        sections.append(
            f"Source: {result.get('source_id')} - {result.get('title')}\n"
            f"{result.get('snippet', '')}"
        )

    sections.append("## Support tickets")
    for ticket in support_tickets.get("tickets", [])[:8]:
        sections.append(
            f"Ticket: {ticket.get('ticket_id')} | Account: {ticket.get('account_id')} | "
            f"Category: {ticket.get('category')} | Priority: {ticket.get('priority')}\n"
            f"Subject: {ticket.get('subject')}\n"
            f"{ticket.get('description', '')}"
        )

    return "\n\n".join(sections)
