from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Final
from urllib.parse import urlparse

from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.session import SessionLocal
from app.incidents.constants import (
    PAID_INVOICE_MRR_METRIC,
    REVENUE_MRR_DROP_ANOMALY_TYPE,
    anomaly_id_for_window,
    incident_id_for_anomaly,
    revenue_week_windows,
)
from app.knowledge.ingestion import ingest_builtin_knowledge_documents
from app.models import (
    Account,
    ActionAuditEvent,
    Agent,
    AgentRun,
    AgentRunStep,
    AgentVersion,
    ApprovalRequest,
    EvalCase,
    EvalResult,
    Incident,
    Invoice,
    KnowledgeDocument,
    KnowledgeDocumentChunk,
    MockAction,
    ProductEvent,
    Subscription,
    SupportTicket,
    User,
)

DATASET_ANCHOR: Final[datetime] = datetime(2026, 6, 9, 12, 0, 0)
ACCOUNT_COUNT: Final[int] = 60
USERS_PER_ACCOUNT: Final[int] = 5
INVOICES_PER_ACCOUNT: Final[int] = 10
PRODUCT_EVENT_COUNT: Final[int] = 6_000
TICKETS_PER_ACCOUNT: Final[int] = 4

SCENARIOS: Final[dict[str, dict[str, object]]] = {
    "checkout_retry_regression": {
        "account_numbers": {1, 2, 3, 4, 5, 6},
        "root_cause": "Billing retry webhook regression suppressed second charge attempts.",
        "expected_evidence": [
            "June failed invoices",
            "billing support tickets",
            "retry failure reasons",
        ],
        "expected_evidence_types": ["sql", "document", "ticket"],
        "false_leads": ["seasonal usage dip", "enterprise procurement churn"],
        "recommended_actions": [
            "repair retry workflow",
            "draft billing-owner follow-ups",
        ],
    },
    "enterprise_churn_wave": {
        "account_numbers": {7, 8, 9, 10, 11},
        "root_cause": "Enterprise sponsors canceled after unresolved onboarding risk.",
        "expected_evidence": [
            "recent canceled subscriptions",
            "account escalation tickets",
            "void June invoices",
        ],
        "expected_evidence_types": ["sql", "document", "ticket"],
        "false_leads": ["payment method expiration", "report export bug"],
        "recommended_actions": [
            "prepare win-back outreach",
            "summarize onboarding blockers",
        ],
    },
    "usage_drop_after_import_outage": {
        "account_numbers": {12, 13, 14, 15, 16, 17, 18},
        "root_cause": "CSV import instability reduced recent active usage.",
        "expected_evidence": [
            "import_failed product events",
            "integration tickets",
            "lower recent user activity",
        ],
        "expected_evidence_types": ["sql", "document", "ticket"],
        "false_leads": ["billing retry regression", "support backlog"],
        "recommended_actions": [
            "prioritize import fix",
            "send status update to admins",
        ],
    },
    "support_backlog_export_bug": {
        "account_numbers": {19, 20, 21, 22, 23, 24, 25, 26},
        "root_cause": "Report export filter bug caused duplicate product tickets.",
        "expected_evidence": [
            "report_export product events",
            "product support tickets",
            "high-priority open tickets",
        ],
        "expected_evidence_types": ["sql", "document", "ticket"],
        "false_leads": ["payment failure wave", "usage outage"],
        "recommended_actions": [
            "fix export filters",
            "deduplicate support backlog",
        ],
    },
    "payment_method_expiration": {
        "account_numbers": {27, 28, 29, 30, 31, 32, 33, 34, 35, 36},
        "root_cause": "Expired payment methods were not refreshed before renewal.",
        "expected_evidence": [
            "June failed invoices",
            "card expiration tickets",
            "failed renewal amounts",
        ],
        "expected_evidence_types": ["sql", "document", "ticket"],
        "false_leads": ["checkout retry regression", "enterprise churn"],
        "recommended_actions": [
            "draft billing contact reminders",
            "audit card-expiration notices",
        ],
    },
    "unknown_root_cause": {
        "account_numbers": {37, 38, 39, 40, 41, 42},
        "root_cause": (
            "MRR dropped after failed renewals, but the available evidence does not "
            "prove a specific operational root cause."
        ),
        "expected_evidence": [
            "June failed invoices",
            "billing support tickets",
        ],
        "expected_evidence_types": ["sql", "ticket"],
        "false_leads": [
            "billing retry webhook regression",
            "expired payment methods",
            "CSV import instability",
            "report export filter bug",
            "enterprise churn",
        ],
        "recommended_actions": [],
    },
}
SCENARIO_ACCOUNT_NUMBERS: Final[dict[str, set[int]]] = {
    scenario: metadata["account_numbers"] for scenario, metadata in SCENARIOS.items()
}

MODEL_ORDER: Final[tuple[type, ...]] = (
    EvalResult,
    EvalCase,
    ActionAuditEvent,
    ApprovalRequest,
    MockAction,
    AgentRunStep,
    AgentRun,
    KnowledgeDocumentChunk,
    KnowledgeDocument,
    Incident,
    SupportTicket,
    ProductEvent,
    Invoice,
    User,
    Subscription,
    Account,
    AgentVersion,
    Agent,
)


@dataclass(frozen=True)
class SeedResult:
    counts: dict[str, int]
    fingerprint: str


def scenario_for_account(account_number: int) -> str | None:
    for scenario, account_numbers in SCENARIO_ACCOUNT_NUMBERS.items():
        if account_number in account_numbers:
            return scenario
    return None


def ensure_seeded_if_empty(session: Session) -> SeedResult | None:
    existing_account = session.scalar(select(Account.id).limit(1))
    if existing_account is not None:
        ingest_builtin_knowledge_documents(session, force=False)
        _seed_control_plane_agent(session)
        session.commit()
        return None

    settings = get_settings()
    if not settings.allow_unsafe_bootstrap_seed:
        try:
            validate_seed_target(settings.database_url, settings.app_env)
        except SystemExit as exc:
            raise SystemExit(
                f"{exc} Set ALLOW_UNSAFE_BOOTSTRAP_SEED=true only for an intentional demo reset."
            ) from exc
    try:
        return insert_seed_data(session)
    except IntegrityError:
        session.rollback()
        if session.scalar(select(Account.id).limit(1)) is not None:
            return None
        raise
    except Exception:
        session.rollback()
        raise


def insert_seed_data(session: Session) -> SeedResult:
    accounts = build_accounts()
    users = build_users()
    subscriptions = build_subscriptions()
    invoices = build_invoices(subscriptions)
    product_events = build_product_events()
    support_tickets = build_support_tickets()

    session.add_all(accounts)
    session.add_all(users)
    session.add_all(subscriptions)
    session.add_all(invoices)
    session.add_all(product_events)
    session.add_all(support_tickets)
    session.flush()
    session.add_all(build_incidents(session))
    session.flush()
    session.add_all(build_eval_cases())
    session.flush()
    ingest_builtin_knowledge_documents(session, commit=False)
    _seed_control_plane_agent(session)
    session.flush()

    counts = seed_counts(session)
    fingerprint = dataset_fingerprint(session)
    session.commit()
    return SeedResult(counts=counts, fingerprint=fingerprint)


def reseed_database(session: Session) -> SeedResult:
    try:
        clear_domain_data(session)
        return insert_seed_data(session)
    except Exception:
        session.rollback()
        raise


def clear_domain_data(session: Session) -> None:
    for model in MODEL_ORDER:
        session.execute(delete(model))


def build_accounts() -> list[Account]:
    industries = [
        "Fintech",
        "Healthcare",
        "Education",
        "Logistics",
        "Retail",
        "Developer Tools",
    ]
    regions = ["North America", "Europe", "Asia Pacific", "Latin America"]
    segments = ["startup", "growth", "enterprise"]

    accounts: list[Account] = []
    for account_number in range(1, ACCOUNT_COUNT + 1):
        segment = segments[account_number % len(segments)]
        scenario = scenario_for_account(account_number)
        health_score = 86 - (account_number % 17)
        if scenario == "enterprise_churn_wave":
            health_score = 39 + account_number % 5
        elif scenario in {"checkout_retry_regression", "payment_method_expiration"}:
            health_score = 58 + account_number % 8
        elif scenario:
            health_score = 63 + account_number % 10

        accounts.append(
            Account(
                id=account_id(account_number),
                name=f"{account_name_prefix(account_number)} {account_number:02d}",
                segment=segment,
                industry=industries[account_number % len(industries)],
                region=regions[account_number % len(regions)],
                health_score=health_score,
                source_scenario=scenario,
                created_at=DATASET_ANCHOR
                - timedelta(days=520 - account_number * 3),
                is_active=scenario != "enterprise_churn_wave",
            )
        )
    return accounts


def build_users() -> list[User]:
    roles = ["admin", "finance", "support", "analyst", "engineer"]
    users: list[User] = []
    for account_number in range(1, ACCOUNT_COUNT + 1):
        scenario = scenario_for_account(account_number)
        for user_number in range(1, USERS_PER_ACCOUNT + 1):
            last_seen_days = (account_number * user_number) % 28
            if scenario == "usage_drop_after_import_outage" and user_number > 2:
                last_seen_days = 42 + user_number
            users.append(
                User(
                    id=user_id(account_number, user_number),
                    account_id=account_id(account_number),
                    email=(
                        f"user{user_number}.acct{account_number:02d}"
                        "@example.ops-agent.test"
                    ),
                    full_name=f"User {user_number} Account {account_number:02d}",
                    role=roles[(account_number + user_number) % len(roles)],
                    created_at=DATASET_ANCHOR
                    - timedelta(days=360 - account_number - user_number),
                    last_seen_at=DATASET_ANCHOR - timedelta(days=last_seen_days),
                    is_active=scenario != "enterprise_churn_wave" or user_number <= 2,
                )
            )
    return users


def build_subscriptions() -> list[Subscription]:
    subscriptions: list[Subscription] = []
    for account_number in range(1, ACCOUNT_COUNT + 1):
        scenario = scenario_for_account(account_number)
        plan, base_mrr, seats = subscription_terms(account_number)
        is_churned = scenario == "enterprise_churn_wave"
        subscriptions.append(
            Subscription(
                id=subscription_id(account_number),
                account_id=account_id(account_number),
                plan=plan,
                status="canceled" if is_churned else "active",
                mrr_cents=base_mrr,
                seats=seats,
                started_at=date(2025, 7, 1) + timedelta(days=account_number % 45),
                canceled_at=date(2026, 6, 4) if is_churned else None,
                cancellation_reason=(
                    "Procurement pause after unresolved onboarding risk"
                    if is_churned
                    else None
                ),
                source_scenario=scenario,
            )
        )
    return subscriptions


def build_invoices(subscriptions: list[Subscription]) -> list[Invoice]:
    month_starts = [
        date(2025, 9, 1),
        date(2025, 10, 1),
        date(2025, 11, 1),
        date(2025, 12, 1),
        date(2026, 1, 1),
        date(2026, 2, 1),
        date(2026, 3, 1),
        date(2026, 4, 1),
        date(2026, 5, 1),
        date(2026, 6, 1),
    ]
    invoices: list[Invoice] = []
    for subscription in subscriptions:
        account_number = int(subscription.account_id.split("_")[1])
        scenario = scenario_for_account(account_number)
        for month_index, period_start in enumerate(month_starts, start=1):
            period_end = next_month(period_start) - timedelta(days=1)
            invoice_date = period_start
            status = "paid"
            failure_reason = None
            source_scenario = None

            if period_start == date(2026, 6, 1) and scenario in {
                "checkout_retry_regression",
                "payment_method_expiration",
                "unknown_root_cause",
            }:
                if scenario == "checkout_retry_regression":
                    invoice_date = date(2026, 6, 5)
                status = "failed"
                if scenario == "checkout_retry_regression":
                    failure_reason = "Retry webhook regression suppressed second charge attempt"
                elif scenario == "payment_method_expiration":
                    failure_reason = "Expired cards were not refreshed before renewal"
                else:
                    failure_reason = "Processor declined during settlement; pending manual review"
                source_scenario = scenario
            elif period_start == date(2026, 5, 1) and scenario == "checkout_retry_regression":
                invoice_date = date(2026, 5, 29)
                source_scenario = scenario
            elif period_start >= date(2026, 5, 1) and scenario == "enterprise_churn_wave":
                status = "void" if period_start == date(2026, 6, 1) else "paid"
                source_scenario = scenario
            elif (account_number + month_index) % 23 == 0:
                status = "failed"
                failure_reason = "Card declined on first attempt; recovered manually"

            invoices.append(
                Invoice(
                    id=invoice_id(account_number, month_index),
                    account_id=subscription.account_id,
                    subscription_id=subscription.id,
                    invoice_date=invoice_date,
                    due_date=invoice_date + timedelta(days=15),
                    period_start=period_start,
                    period_end=period_end,
                    amount_cents=subscription.mrr_cents,
                    status=status,
                    failure_reason=failure_reason,
                    paid_at=(
                        datetime.combine(invoice_date + timedelta(days=2), datetime.min.time())
                        if status == "paid"
                        else None
                    ),
                    source_scenario=source_scenario,
                )
            )
    return invoices


def build_product_events() -> list[ProductEvent]:
    event_names = [
        "login",
        "dashboard_view",
        "billing_page_view",
        "report_export",
        "sync_completed",
        "workflow_run",
        "invite_sent",
        "import_failed",
    ]
    events: list[ProductEvent] = []
    for event_number in range(1, PRODUCT_EVENT_COUNT + 1):
        account_number = ((event_number * 17) % ACCOUNT_COUNT) + 1
        user_number = ((event_number * 7) % USERS_PER_ACCOUNT) + 1
        scenario = scenario_for_account(account_number)
        days_back = event_number % 90
        if scenario == "usage_drop_after_import_outage" and event_number % 4 == 0:
            days_back = 31 + event_number % 21

        event_name = event_names[event_number % len(event_names)]
        if scenario == "unknown_root_cause" and event_name in {"import_failed", "report_export"}:
            # Avoid leaking CSV-import / report-export signals into the doc query
            # for the ambiguity scenario; otherwise knowledge search returns docs
            # whose titles contain "csv"+"import" or "report"+"export", which
            # would trigger a specific diagnosis instead of the intended
            # uncertainty fallthrough.
            event_name = "dashboard_view"
        source_scenario = None
        if (
            scenario == "usage_drop_after_import_outage"
            and days_back <= 21
            and event_number % 5 == 0
        ):
            event_name = "import_failed"
            source_scenario = scenario
        elif scenario == "support_backlog_export_bug" and event_number % 11 == 0:
            event_name = "report_export"
            source_scenario = scenario

        events.append(
            ProductEvent(
                id=f"evt_{event_number:06d}",
                account_id=account_id(account_number),
                user_id=user_id(account_number, user_number),
                event_time=DATASET_ANCHOR
                - timedelta(
                    days=days_back,
                    hours=(event_number * 3) % 24,
                    minutes=(event_number * 11) % 60,
                ),
                event_name=event_name,
                source="web" if event_number % 4 else "api",
                source_scenario=source_scenario,
                event_metadata={
                    "sequence": event_number,
                    "surface": "workspace" if event_number % 3 else "billing",
                    "scenario": source_scenario,
                },
            )
        )
    return events


def build_support_tickets() -> list[SupportTicket]:
    statuses = ["open", "pending", "resolved", "resolved"]
    priorities = ["low", "normal", "normal", "high"]
    categories = ["billing", "product", "integration", "performance", "account"]
    tickets: list[SupportTicket] = []
    ticket_number = 1
    for account_number in range(1, ACCOUNT_COUNT + 1):
        scenario = scenario_for_account(account_number)
        for local_ticket_number in range(1, TICKETS_PER_ACCOUNT + 1):
            status = statuses[(account_number + local_ticket_number) % len(statuses)]
            priority = priorities[(account_number * local_ticket_number) % len(priorities)]
            category = categories[(account_number + local_ticket_number) % len(categories)]
            subject = "Question about workspace configuration"
            description = "Synthetic support request used for seeded operations analytics."
            source_scenario = None
            days_back = (account_number * local_ticket_number) % 75

            if scenario == "checkout_retry_regression" and local_ticket_number <= 3:
                status = "open"
                priority = "high"
                category = "billing"
                source_scenario = scenario
                days_back = local_ticket_number + 1
                subject = "Renewal payment failed after retry"
                description = (
                    "Customer reports a failed renewal despite an updated card and "
                    "expects finance follow-up."
                )
            elif scenario == "enterprise_churn_wave" and local_ticket_number <= 3:
                status = "pending" if local_ticket_number == 1 else "resolved"
                priority = "high"
                category = "account"
                source_scenario = scenario
                days_back = 5 + local_ticket_number
                subject = "Procurement escalation on rollout risk"
                description = (
                    "Enterprise sponsor is pausing renewal because onboarding issues "
                    "remain unresolved."
                )
            elif scenario == "usage_drop_after_import_outage" and local_ticket_number <= 3:
                status = "open"
                priority = "normal" if local_ticket_number == 3 else "high"
                category = "integration"
                source_scenario = scenario
                days_back = 7 + local_ticket_number
                subject = "CSV import jobs failing intermittently"
                description = (
                    "Admins cannot complete imports, causing fewer weekly active users."
                )
            elif scenario == "support_backlog_export_bug" and local_ticket_number <= 3:
                status = "open"
                priority = "high"
                category = "product"
                source_scenario = scenario
                days_back = local_ticket_number
                subject = "Scheduled report exports are missing filters"
                description = (
                    "Ops teams see incorrect report exports and are opening duplicates."
                )
            elif scenario == "payment_method_expiration" and local_ticket_number <= 2:
                status = "open"
                priority = "normal"
                category = "billing"
                source_scenario = scenario
                days_back = 3 + local_ticket_number
                subject = "Card expiration notice did not reach billing owner"
                description = (
                    "Billing owner missed expiration notices before the June renewal."
                )
            elif scenario == "unknown_root_cause" and local_ticket_number <= 2:
                status = "open"
                priority = "normal"
                category = "billing"
                source_scenario = scenario
                days_back = 4 + local_ticket_number
                subject = "Billing question about recent invoice changes"
                description = (
                    "Customer is asking about unexpected changes on their recent "
                    "billing statement."
                )

            created_at = DATASET_ANCHOR - timedelta(days=days_back, hours=local_ticket_number)
            tickets.append(
                SupportTicket(
                    id=f"tkt_{ticket_number:04d}",
                    account_id=account_id(account_number),
                    user_id=user_id(account_number, (local_ticket_number % USERS_PER_ACCOUNT) + 1),
                    created_at=created_at,
                    resolved_at=(
                        created_at + timedelta(days=3)
                        if status == "resolved"
                        else None
                    ),
                    status=status,
                    priority=priority,
                    category=category,
                    subject=subject,
                    description=description,
                    sentiment="negative" if priority == "high" else "neutral",
                    source_scenario=source_scenario,
                )
            )
            ticket_number += 1
    return tickets


def build_incidents(session: Session) -> list[Incident]:
    windows = revenue_week_windows(DATASET_ANCHOR)
    previous_mrr = invoice_sum(
        session, "paid", windows.previous_start, windows.current_start
    )
    current_mrr = invoice_sum(
        session, "paid", windows.current_start, windows.current_end_exclusive
    )
    if previous_mrr <= 0 or current_mrr >= previous_mrr:
        return []

    incidents: list[Incident] = []
    for scenario in SCENARIOS:
        account_ids = [
            account_id(account_number)
            for account_number in sorted(SCENARIO_ACCOUNT_NUMBERS[scenario])
        ]
        affected_accounts = scenario_affected_accounts(
            session,
            scenario=scenario,
            account_ids=account_ids,
            current_start=windows.current_start,
            current_end_exclusive=windows.current_end_exclusive,
        )

        failed_invoice_ids = [
            invoice_id
            for account in affected_accounts
            for invoice_id in account["failed_invoice_ids"]
        ]
        failed_invoice_cents = sum(
            int(account["failed_invoice_cents"]) for account in affected_accounts
        )
        failed_invoice_count = sum(
            int(account["failed_invoice_count"]) for account in affected_accounts
        )
        delta_cents = current_mrr - previous_mrr
        delta_percent = round((delta_cents / previous_mrr) * 100, 2)
        anomaly_id = anomaly_id_for_window(windows.current_start)
        scenario_anomaly_id = (
            anomaly_id
            if scenario == "checkout_retry_regression"
            else f"{anomaly_id}-{scenario}"
        )
        evidence = {
            "anomaly_id": scenario_anomaly_id,
            "metric_evidence": {
                "metric_name": PAID_INVOICE_MRR_METRIC,
                "current_window_start": windows.current_start.isoformat(),
                "current_window_end": windows.current_end.isoformat(),
                "previous_window_start": windows.previous_start.isoformat(),
                "previous_window_end": windows.previous_end.isoformat(),
                "current_value_cents": current_mrr,
                "previous_value_cents": previous_mrr,
                "delta_cents": delta_cents,
                "delta_percent": delta_percent,
                "failed_invoice_cents": failed_invoice_cents,
                "failed_invoice_count": failed_invoice_count,
                "invoice_ids": failed_invoice_ids,
            },
            "affected_accounts": affected_accounts,
            "support_signals": support_signal_dicts_for_accounts(session, account_ids),
            "product_signals": product_signal_dicts_for_accounts(session, account_ids),
            "support_ticket_ids": ticket_ids_for_accounts(session, account_ids),
            "product_event_names": product_event_names_for_accounts(session, account_ids),
            "source_queries": scenario_source_queries(scenario),
        }

        incidents.append(
            Incident(
                id=incident_id_for_scenario(scenario, anomaly_id),
                title=scenario_incident_title(scenario),
                status="open",
                severity="high" if scenario == "checkout_retry_regression" else "medium",
                anomaly_type=REVENUE_MRR_DROP_ANOMALY_TYPE,
                metric_name=PAID_INVOICE_MRR_METRIC,
                summary=scenario_incident_summary(scenario),
                source_scenario=scenario,
                detected_at=DATASET_ANCHOR,
                current_value_cents=current_mrr,
                previous_value_cents=previous_mrr,
                delta_cents=delta_cents,
                delta_percent=delta_percent,
                affected_account_ids=account_ids,
                evidence=evidence,
                created_at=DATASET_ANCHOR,
                updated_at=DATASET_ANCHOR,
            )
        )

    return incidents


def build_eval_cases() -> list[EvalCase]:
    anomaly_id = anomaly_id_for_window(revenue_week_windows(DATASET_ANCHOR).current_start)
    cases: list[EvalCase] = []
    for scenario, metadata in SCENARIOS.items():
        cases.append(
            EvalCase(
                id=f"eval_{scenario}",
                scenario=scenario,
                incident_id=incident_id_for_scenario(scenario, anomaly_id),
                title=f"Regression eval: {scenario.replace('_', ' ')}",
                expected_root_cause=str(metadata["root_cause"]),
                expected_evidence_types=list(metadata["expected_evidence_types"]),
                expected_evidence=list(metadata["expected_evidence"]),
                false_leads=list(metadata["false_leads"]),
                recommended_actions=list(metadata["recommended_actions"]),
                created_at=DATASET_ANCHOR,
                updated_at=DATASET_ANCHOR,
            )
        )
    return cases


def _seed_control_plane_agent(session: Session) -> None:
    from app.agent.tools import TOOL_IDS
    from app.llm.prompts import INVESTIGATION_SYSTEM_PROMPT
    from app.tools.scopes import DEFAULT_V1_ALLOWED_SCOPES

    agent_id = "revenue-ops-agent"
    version_id = f"{agent_id}_v1"
    existing_agent = session.get(Agent, agent_id)
    if existing_agent is not None:
        has_published = any(
            v.status == "published" for v in existing_agent.versions
        )
        if has_published:
            _backfill_agent_run_versions(session, agent_id, version_id)
            return

    now = datetime.now(UTC).replace(tzinfo=None)

    if existing_agent is None:
        agent = Agent(
            id=agent_id,
            name="Revenue Ops Agent",
            description=(
                "Investigates SaaS revenue anomalies by querying metrics, searching "
                "knowledge documents, and inspecting support tickets, then producing "
                "an evidence-backed root cause report with approval-gated actions."
            ),
            default_model="gpt-4o-mini",
            created_at=now,
            updated_at=now,
        )
        session.add(agent)
        session.flush()
    else:
        agent = existing_agent

    existing_v1 = session.get(AgentVersion, version_id)
    if existing_v1 is None:
        v1 = AgentVersion(
            id=version_id,
            agent_id=agent_id,
            version_number=1,
            semantic_version="1.0.0",
            status="published",
            system_prompt="",
            model=agent.default_model,
            temperature=0.1,
            max_tokens=1024,
            enabled_tool_ids=list(TOOL_IDS),
            allowed_scopes=list(DEFAULT_V1_ALLOWED_SCOPES),
            published_at=now,
            published_by="bootstrap",
            forked_from_version_id=None,
            created_at=now,
            updated_at=now,
        )
        session.add(v1)
        agent.updated_at = now
    else:
        existing_v1.status = "published"
        if existing_v1.published_at is None:
            existing_v1.published_at = now
        if existing_v1.enabled_tool_ids is None or len(existing_v1.enabled_tool_ids) == 0:
            existing_v1.enabled_tool_ids = list(TOOL_IDS)
        if not existing_v1.allowed_scopes:
            # Backfill scopes on databases seeded before Phase 3 scope
            # enforcement, so v1's data tools remain callable (PRD §9.5).
            existing_v1.allowed_scopes = list(DEFAULT_V1_ALLOWED_SCOPES)
        if existing_v1.model is None or len(existing_v1.model) == 0:
            existing_v1.model = agent.default_model
        if existing_v1.temperature is None:
            existing_v1.temperature = 0.1
        if existing_v1.max_tokens is None:
            existing_v1.max_tokens = 1024
        if existing_v1.system_prompt == INVESTIGATION_SYSTEM_PROMPT:
            existing_v1.system_prompt = ""
        existing_v1.updated_at = now
        agent.updated_at = now

    _backfill_agent_run_versions(session, agent_id, version_id)


def _backfill_agent_run_versions(
    session: Session, agent_id: str, agent_version_id: str
) -> None:
    session.execute(
        update(AgentRun)
        .where(
            (AgentRun.agent_id.is_(None))
            | ((AgentRun.agent_id == agent_id) & (AgentRun.agent_version_id.is_(None)))
        ).values(agent_id=agent_id, agent_version_id=agent_version_id)
    )


def scenario_affected_accounts(
    session: Session,
    *,
    scenario: str,
    account_ids: list[str],
    current_start: date,
    current_end_exclusive: date,
) -> list[dict[str, object]]:
    accounts = session.scalars(
        select(Account).where(Account.id.in_(account_ids)).order_by(Account.name)
    ).all()
    failed_start = (
        date(2026, 6, 1)
        if scenario in {"payment_method_expiration", "unknown_root_cause"}
        else current_start
    )
    failed_invoices = session.scalars(
        select(Invoice)
        .join(Subscription, Subscription.id == Invoice.subscription_id)
        .where(
            Invoice.account_id.in_(account_ids),
            Invoice.status == "failed",
            Invoice.invoice_date >= failed_start,
            Invoice.invoice_date < current_end_exclusive,
            Subscription.status == "active",
        )
        .order_by(Invoice.account_id, Invoice.id)
    ).all()
    failed_by_account: dict[str, list[Invoice]] = {}
    for invoice in failed_invoices:
        failed_by_account.setdefault(invoice.account_id, []).append(invoice)

    return [
        {
            "account_id": account.id,
            "account_name": account.name,
            "segment": account.segment,
            "health_score": account.health_score,
            "failed_invoice_cents": sum(
                invoice.amount_cents for invoice in failed_by_account.get(account.id, [])
            ),
            "failed_invoice_count": len(failed_by_account.get(account.id, [])),
            "failed_invoice_ids": [
                invoice.id for invoice in failed_by_account.get(account.id, [])
            ],
            "source_scenario": account.source_scenario,
        }
        for account in accounts
    ]


def incident_id_for_scenario(scenario: str, anomaly_id: str) -> str:
    if scenario == "checkout_retry_regression":
        return incident_id_for_anomaly(anomaly_id)
    return f"inc_eval_{scenario}"


def scenario_incident_title(scenario: str) -> str:
    titles = {
        "checkout_retry_regression": "Week-over-week paid MRR dropped after failed renewals",
        "enterprise_churn_wave": "Enterprise paid MRR at risk after onboarding cancellations",
        "usage_drop_after_import_outage": "Usage activity dropped after CSV import instability",
        "support_backlog_export_bug": "Support backlog increased after report export bug",
        "payment_method_expiration": "Renewal MRR dropped after payment method expirations",
        "unknown_root_cause": "Paid MRR dropped with mixed renewal signals across accounts",
    }
    return titles[scenario]


def scenario_incident_summary(scenario: str) -> str:
    summaries = {
        "checkout_retry_regression": (
            "Paid invoice MRR fell week over week while renewal invoices failed "
            "for affected accounts."
        ),
        "enterprise_churn_wave": (
            "Enterprise sponsors canceled or paused renewal after unresolved onboarding "
            "and procurement escalation risk."
        ),
        "usage_drop_after_import_outage": (
            "Affected accounts show lower recent activity and repeated CSV import failures."
        ),
        "support_backlog_export_bug": (
            "High-priority duplicate product tickets are clustered around report export filters."
        ),
        "payment_method_expiration": (
            "June renewal invoices failed for accounts whose billing owners missed card "
            "expiration notices."
        ),
        "unknown_root_cause": (
            "Several accounts show failed June renewals without a clear operational pattern."
        ),
    }
    return summaries[scenario]


def scenario_source_queries(scenario: str) -> list[str]:
    common_queries = [
        "paid invoices joined to subscriptions in current and previous 7-day windows",
        "support tickets and product events for affected accounts in the last 30 days",
    ]
    scenario_queries = {
        "checkout_retry_regression": [
            "failed current-window renewal invoices grouped by account",
            "retry webhook failure reasons on June renewal invoices",
        ],
        "enterprise_churn_wave": [
            "canceled enterprise subscriptions and void June invoices",
            "procurement escalation tickets mentioning unresolved onboarding risk",
        ],
        "usage_drop_after_import_outage": [
            "import_failed product events grouped by affected account",
            "integration support tickets mentioning intermittent CSV import failures",
        ],
        "support_backlog_export_bug": [
            "report_export product events grouped by affected account",
            "open high-priority product tickets mentioning missing export filters",
        ],
        "payment_method_expiration": [
            "failed June renewal invoices grouped by account",
            "billing tickets mentioning expired cards and missed expiration notices",
        ],
        "unknown_root_cause": [
            "failed June renewal invoices grouped by account",
            "billing support tickets for accounts with failed renewals",
        ],
    }
    return common_queries + scenario_queries[scenario]


def invoice_sum(session: Session, status: str, start_date: date, end_date: date) -> int:
    return int(
        session.scalar(
            select(func.coalesce(func.sum(Invoice.amount_cents), 0))
            .join(Subscription, Subscription.id == Invoice.subscription_id)
            .where(
                Invoice.status == status,
                Invoice.invoice_date >= start_date,
                Invoice.invoice_date < end_date,
                Subscription.status.in_(("active", "canceled")),
            )
        )
        or 0
    )


def ticket_ids_for_accounts(session: Session, account_ids: list[str]) -> list[str]:
    return [
        ticket_id
        for (ticket_id,) in session.execute(
            select(SupportTicket.id)
            .where(
                SupportTicket.account_id.in_(account_ids),
                SupportTicket.created_at >= DATASET_ANCHOR - timedelta(days=30),
            )
            .order_by(SupportTicket.created_at.desc(), SupportTicket.id)
            .limit(12)
        )
    ]


def support_signal_dicts_for_accounts(
    session: Session, account_ids: list[str]
) -> list[dict[str, object]]:
    return [
        {
            "ticket_id": row.ticket_id,
            "account_id": row.account_id,
            "account_name": row.account_name,
            "created_at": row.created_at.isoformat(),
            "status": row.status,
            "priority": row.priority,
            "category": row.category,
            "subject": row.subject,
            "sentiment": row.sentiment,
            "source_scenario": row.source_scenario,
        }
        for row in session.execute(
            select(
                SupportTicket.id.label("ticket_id"),
                SupportTicket.account_id.label("account_id"),
                Account.name.label("account_name"),
                SupportTicket.created_at.label("created_at"),
                SupportTicket.status.label("status"),
                SupportTicket.priority.label("priority"),
                SupportTicket.category.label("category"),
                SupportTicket.subject.label("subject"),
                SupportTicket.sentiment.label("sentiment"),
                SupportTicket.source_scenario.label("source_scenario"),
            )
            .join(Account, Account.id == SupportTicket.account_id)
            .where(
                SupportTicket.account_id.in_(account_ids),
                SupportTicket.created_at >= DATASET_ANCHOR - timedelta(days=30),
            )
            .order_by(SupportTicket.created_at.desc(), SupportTicket.id)
            .limit(12)
        )
    ]


def product_signal_dicts_for_accounts(
    session: Session, account_ids: list[str]
) -> list[dict[str, object]]:
    return [
        {
            "event_name": row.event_name,
            "event_count": int(row.event_count or 0),
            "affected_accounts": int(row.affected_accounts or 0),
            "latest_event_at": row.latest_event_at.isoformat(),
            "source_scenario": row.source_scenario,
        }
        for row in session.execute(
            select(
                ProductEvent.event_name.label("event_name"),
                ProductEvent.source_scenario.label("source_scenario"),
                func.count(ProductEvent.id).label("event_count"),
                func.count(func.distinct(ProductEvent.account_id)).label(
                    "affected_accounts"
                ),
                func.max(ProductEvent.event_time).label("latest_event_at"),
            )
            .where(
                ProductEvent.account_id.in_(account_ids),
                ProductEvent.event_time >= DATASET_ANCHOR - timedelta(days=30),
            )
            .group_by(ProductEvent.event_name, ProductEvent.source_scenario)
            .order_by(func.count(ProductEvent.id).desc(), ProductEvent.event_name)
            .limit(8)
        )
    ]


def product_event_names_for_accounts(session: Session, account_ids: list[str]) -> list[str]:
    return [
        event_name
        for (event_name,) in session.execute(
            select(ProductEvent.event_name)
            .where(
                ProductEvent.account_id.in_(account_ids),
                ProductEvent.event_time >= DATASET_ANCHOR - timedelta(days=30),
            )
            .group_by(ProductEvent.event_name)
            .order_by(func.count(ProductEvent.id).desc(), ProductEvent.event_name)
            .limit(8)
        )
    ]


def seed_counts(session: Session) -> dict[str, int]:
    models = {
        "accounts": Account,
        "users": User,
        "subscriptions": Subscription,
        "invoices": Invoice,
        "product_events": ProductEvent,
        "support_tickets": SupportTicket,
        "incidents": Incident,
        "eval_cases": EvalCase,
        "eval_results": EvalResult,
        "agent_runs": AgentRun,
        "agent_run_steps": AgentRunStep,
        "knowledge_documents": KnowledgeDocument,
        "knowledge_document_chunks": KnowledgeDocumentChunk,
        "agents": Agent,
        "agent_versions": AgentVersion,
    }
    return {
        table_name: session.scalar(select(func.count()).select_from(model)) or 0
        for table_name, model in models.items()
    }


def dataset_fingerprint(session: Session) -> str:
    digest = hashlib.sha256()
    for table_name, count in sorted(seed_counts(session).items()):
        digest.update(f"{table_name}:{count}|".encode("utf-8"))

    samples = [
        session.scalar(select(Account.name).where(Account.id == "acct_001")),
        session.scalar(select(Invoice.status).where(Invoice.id == "inv_001_10")),
        session.scalar(select(ProductEvent.event_name).where(ProductEvent.id == "evt_000500")),
        session.scalar(select(SupportTicket.subject).where(SupportTicket.id == "tkt_0001")),
        session.scalar(
            select(KnowledgeDocument.title).where(
                KnowledgeDocument.id == "kb-runbook-billing-retry-regression"
            )
        ),
    ]
    for sample in samples:
        digest.update(str(sample).encode("utf-8"))
        digest.update(b"|")
    return digest.hexdigest()[:16]


def account_id(account_number: int) -> str:
    return f"acct_{account_number:03d}"


def user_id(account_number: int, user_number: int) -> str:
    return f"user_{account_number:03d}_{user_number:02d}"


def subscription_id(account_number: int) -> str:
    return f"sub_{account_number:03d}"


def invoice_id(account_number: int, month_index: int) -> str:
    return f"inv_{account_number:03d}_{month_index:02d}"


def account_name_prefix(account_number: int) -> str:
    names = [
        "Northstar",
        "Brightline",
        "Summit",
        "Pioneer",
        "Relay",
        "Beacon",
        "Atlas",
        "Keystone",
    ]
    return names[account_number % len(names)]


def subscription_terms(account_number: int) -> tuple[str, int, int]:
    if account_number % 5 == 0:
        return "enterprise", 24_000_00 + account_number * 15_000, 120
    if account_number % 3 == 0:
        return "scale", 8_000_00 + account_number * 8_000, 45
    return "team", 2_500_00 + account_number * 4_000, 18


def next_month(day: date) -> date:
    if day.month == 12:
        return date(day.year + 1, 1, 1)
    return date(day.year, day.month + 1, 1)


DEMO_ENVIRONMENTS: Final[frozenset[str]] = frozenset(
    {"local", "test", "development", "demo"}
)


def validate_seed_target(database_url: str, app_env: str) -> None:
    if app_env not in DEMO_ENVIRONMENTS:
        raise SystemExit(
            "Refusing to reseed outside local, test, development, or demo environments. "
            "Pass --allow-destructive only for an intentional demo reset."
        )

    parsed_url = urlparse(database_url.replace("+psycopg", "", 1))
    safe_hosts = {"", "localhost", "127.0.0.1", "::1", "postgres"}
    database_name = parsed_url.path.rsplit("/", maxsplit=1)[-1]
    safe_database_names = {"ops_agent", "ops_agent_test", "test_ops_agent"}
    if parsed_url.hostname in safe_hosts and database_name in safe_database_names:
        return

    raise SystemExit(
        "Refusing to reseed a non-local database target. Pass --allow-destructive "
        "only for an intentional demo reset."
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed deterministic SaaS demo data.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable output.")
    parser.add_argument(
        "--allow-destructive",
        action="store_true",
        help="Allow seeding outside local/test database targets.",
    )
    args = parser.parse_args()

    settings = get_settings()
    if not args.allow_destructive:
        validate_seed_target(settings.database_url, settings.app_env)

    with SessionLocal() as session:
        result = reseed_database(session)

    payload = {"counts": result.counts, "fingerprint": result.fingerprint}
    if args.json:
        print(json.dumps(payload, sort_keys=True))
    else:
        print("Seeded deterministic SaaS dataset")
        print(json.dumps(payload, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
