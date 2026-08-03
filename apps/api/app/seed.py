from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any, Final
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
    EvalDataset,
    EvalDatasetCase,
    EvalResult,
    Incident,
    Invoice,
    KnowledgeDocument,
    KnowledgeDocumentChunk,
    MockAction,
    ProductEvent,
    Subscription,
    SupportTicket,
    Tool,
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
    EvalDatasetCase,
    EvalDataset,
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
    Tool,
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
        _seed_phase6_agent_version(session)
        _seed_eval_studio_assets(session)
        _seed_demo_audit_surfaces(session)
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
    from app.tools.service import register_builtin_tools

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
    _seed_phase6_agent_version(session)
    _seed_eval_studio_assets(session)
    register_builtin_tools(session, commit=False)
    _seed_demo_audit_surfaces(session)
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
                        "@example.ledger.test"
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
    from app.agents.service import DEFAULT_V1_ENABLED_TOOL_IDS
    from app.tools.scopes import DEFAULT_V1_ALLOWED_SCOPES

    agent_id = "ledger"
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
            name="Ledger",
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
            enabled_tool_ids=list(DEFAULT_V1_ENABLED_TOOL_IDS),
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
        # Never rewrite an existing version row. Published versions are immutable
        # execution snapshots; even a seed-owned id may already be referenced by
        # historical runs. Capability expansion belongs in a new version.
        return

    _backfill_agent_run_versions(session, agent_id, version_id)


def _seed_eval_studio_assets(session: Session) -> None:
    """Idempotently seed the default dataset and a reproducibly degraded version."""
    from app.agents.service import (
        PHASE6_AGENT_VERSION_ID,
        PHASE6_DEGRADED_AGENT_VERSION_ID,
        PHASE6_ENABLED_TOOL_IDS,
    )
    from app.tools.scopes import PHASE6_ALLOWED_SCOPES

    now = datetime.now(UTC).replace(tzinfo=None)
    dataset = session.get(EvalDataset, "mrr-drop-suite")
    if dataset is None:
        dataset = EvalDataset(
            id="mrr-drop-suite",
            name="mrr-drop-suite",
            description=(
                "Deterministic revenue-operations regression suite covering every "
                "seeded incident scenario."
            ),
            created_at=now,
            updated_at=now,
        )
        session.add(dataset)
        session.flush()

    linked_case_ids = {
        link.eval_case_id
        for link in session.scalars(
            select(EvalDatasetCase).where(
                EvalDatasetCase.dataset_id == dataset.id
            )
        )
    }
    eval_case_ids = session.scalars(select(EvalCase.id).order_by(EvalCase.id)).all()
    session.add_all(
        EvalDatasetCase(dataset_id=dataset.id, eval_case_id=case_id)
        for case_id in eval_case_ids
        if case_id not in linked_case_ids
    )

    degraded_id = PHASE6_DEGRADED_AGENT_VERSION_ID
    if session.get(AgentVersion, degraded_id) is None:
        source = session.get(AgentVersion, PHASE6_AGENT_VERSION_ID) or session.get(
            AgentVersion, "ledger_v1"
        )
        if source is None:
            return
        enabled_tool_ids = [
            tool_id for tool_id in PHASE6_ENABLED_TOOL_IDS if tool_id != "search_docs"
        ]
        minimum_version_number = int(
            session.scalar(
                select(func.coalesce(func.min(AgentVersion.version_number), 0)).where(
                    AgentVersion.agent_id == source.agent_id
                )
            )
            or 0
        )
        session.add(
            AgentVersion(
                id=degraded_id,
                agent_id=source.agent_id,
                # A negative version keeps the intentionally degraded candidate
                # out of default-version selection on fresh and upgraded data.
                version_number=min(minimum_version_number, 0) - 1,
                semantic_version="0.8.0-phase6-degraded",
                status="published",
                system_prompt=source.system_prompt,
                model=source.model,
                temperature=source.temperature,
                max_tokens=source.max_tokens,
                enabled_tool_ids=enabled_tool_ids,
                allowed_scopes=list(PHASE6_ALLOWED_SCOPES),
                published_at=now,
                published_by="bootstrap",
                forked_from_version_id=source.id,
                created_at=now,
                updated_at=now,
            )
        )


def _seed_demo_audit_surfaces(session: Session) -> None:
    """Seed public-safe synthetic audit surfaces for the anonymous demo.

    The public demo is read-only (every anonymous mutation returns 403), so
    completed runs, approvals, and eval results can never be created by an
    anonymous visitor. Seeding deterministic, evidence-carrying audit artifacts here is
    what makes the demo's ``/runs``, ``/approvals``, and ``/evals`` surfaces
    inspectable at all (docs/deployment.md section 0.4). The surfaces only
    exist in the ``demo`` environment and are idempotent: a demo database
    always contains the same fixed run/approval/eval rows after every boot.
    """
    if get_settings().app_env != "demo":
        return
    if session.get(AgentRun, DEMO_AUDIT_ANCHOR_RUN_ID) is not None:
        return

    from app.agents.service import PHASE6_AGENT_VERSION_ID, PHASE6_DEGRADED_AGENT_VERSION_ID

    anomaly_id = anomaly_id_for_window(revenue_week_windows(DATASET_ANCHOR).current_start)
    incident_ids = {
        scenario: incident_id_for_scenario(scenario, anomaly_id)
        for scenario in SCENARIOS
    }
    now = datetime.now(UTC).replace(tzinfo=None)
    anchor = DATASET_ANCHOR.replace(tzinfo=None)

    # Completed run on the headline checkout-retry incident: full step timeline,
    # cited report, local trace, tokens/cost, plus executed and pending actions.
    checkout_run = _demo_audit_run(
        session,
        run_id=DEMO_AUDIT_ANCHOR_RUN_ID,
        scenario="checkout_retry_regression",
        incident_id=incident_ids["checkout_retry_regression"],
        agent_version_id=PHASE6_AGENT_VERSION_ID,
        started_at=anchor + timedelta(hours=2, minutes=10),
        completed_at=anchor + timedelta(hours=2, minutes=24),
        created_at=anchor + timedelta(hours=2),
        updated_at=anchor + timedelta(hours=2, minutes=24),
        confidence="high",
        report_offset_hours=2,
        tokens=(1420, 380),
        cost=0.021,
        now=now,
    )
    _demo_audit_actions(
        session,
        run=checkout_run,
        created_at=anchor + timedelta(hours=2, minutes=20),
        mode="pending",
    )

    usage_run = _demo_audit_run(
        session,
        run_id="run_f70b3bda3bf443c5",
        scenario="usage_drop_after_import_outage",
        incident_id=incident_ids["usage_drop_after_import_outage"],
        agent_version_id=PHASE6_AGENT_VERSION_ID,
        started_at=anchor + timedelta(hours=3, minutes=5),
        completed_at=anchor + timedelta(hours=3, minutes=19),
        created_at=anchor + timedelta(hours=3),
        updated_at=anchor + timedelta(hours=3, minutes=19),
        confidence="medium",
        report_offset_hours=3,
        tokens=(1180, 310),
        cost=0.017,
        now=now,
    )
    _demo_audit_actions(
        session,
        run=usage_run,
        created_at=anchor + timedelta(hours=3, minutes=15),
        mode="decided",
    )

    ambiguous_run = _demo_audit_run(
        session,
        run_id="run_da3c0b5dac4140e7",
        scenario="unknown_root_cause",
        incident_id=incident_ids["unknown_root_cause"],
        agent_version_id=PHASE6_AGENT_VERSION_ID,
        started_at=anchor + timedelta(hours=4, minutes=0),
        completed_at=anchor + timedelta(hours=4, minutes=16),
        created_at=anchor + timedelta(hours=4),
        updated_at=anchor + timedelta(hours=4, minutes=16),
        confidence="low",
        report_offset_hours=4,
        tokens=(960, 240),
        cost=0.013,
        now=now,
    )
    _demo_audit_actions(
        session,
        run=ambiguous_run,
        created_at=anchor + timedelta(hours=4, minutes=12),
        mode="single_pending",
    )

    # Failed run: the tool step itself fails, so the run history surfaces the
    # failure instead of stopping at an empty state.
    _demo_audit_run(
        session,
        run_id="run_demo_failed_renewal_probe",
        scenario="payment_method_expiration",
        incident_id=incident_ids["payment_method_expiration"],
        agent_version_id=PHASE6_AGENT_VERSION_ID,
        started_at=anchor + timedelta(hours=5, minutes=2),
        completed_at=anchor + timedelta(hours=5, minutes=9),
        created_at=anchor + timedelta(hours=5),
        updated_at=anchor + timedelta(hours=5, minutes=9),
        confidence="low",
        report_offset_hours=5,
        tokens=(0, 0),
        cost=0.0,
        now=now,
        fail_sequence=3,
    )

    # Dedicated runs backing the eval-regression comparison below. The degraded
    # candidate has ``search_docs`` disabled, so its run timeline records a
    # visible ``blocked`` step (permission-scope enforcement, PRD FR-7).
    eval_good_run = _demo_audit_run(
        session,
        run_id="run_eval_demo_phase6",
        scenario="enterprise_churn_wave",
        incident_id=incident_ids["enterprise_churn_wave"],
        agent_version_id=PHASE6_AGENT_VERSION_ID,
        started_at=anchor + timedelta(hours=6, minutes=0),
        completed_at=anchor + timedelta(hours=6, minutes=15),
        created_at=anchor + timedelta(hours=6),
        updated_at=anchor + timedelta(hours=6, minutes=15),
        confidence="high",
        report_offset_hours=6,
        tokens=(1320, 340),
        cost=0.019,
        now=now,
    )
    eval_degraded_run = _demo_audit_run(
        session,
        run_id="run_eval_demo_degraded",
        scenario="support_backlog_export_bug",
        incident_id=incident_ids["support_backlog_export_bug"],
        agent_version_id=PHASE6_DEGRADED_AGENT_VERSION_ID,
        started_at=anchor + timedelta(hours=7, minutes=0),
        completed_at=anchor + timedelta(hours=7, minutes=18),
        created_at=anchor + timedelta(hours=7),
        updated_at=anchor + timedelta(hours=7, minutes=18),
        confidence="medium",
        report_offset_hours=7,
        tokens=(1080, 260),
        cost=0.015,
        now=now,
        blocked_sequences={5},
    )

    # The immutable v1 baseline also gets a complete eval run so any published
    # version pair selected on /evals compares cleanly (the studio defaults to
    # the first two published versions, and every one must have results).
    eval_phase1_run = _demo_audit_run(
        session,
        run_id="run_eval_demo_phase1",
        scenario="checkout_retry_regression",
        incident_id=incident_ids["checkout_retry_regression"],
        agent_version_id="ledger_v1",
        started_at=anchor + timedelta(hours=8, minutes=0),
        completed_at=anchor + timedelta(hours=8, minutes=14),
        created_at=anchor + timedelta(hours=8),
        updated_at=anchor + timedelta(hours=8, minutes=14),
        confidence="high",
        report_offset_hours=8,
        tokens=(1240, 300),
        cost=0.018,
        now=now,
    )

    session.add_all(_demo_audit_eval_results(
        session,
        eval_run_id="evalrun_demo_phase1",
        agent_run_id=eval_phase1_run.id,
        agent_version_id="ledger_v1",
        fail_scenarios=set(),
        anchor=anchor,
    ))
    session.add_all(_demo_audit_eval_results(
        session,
        eval_run_id="evalrun_demo_phase6",
        agent_run_id=eval_good_run.id,
        agent_version_id=PHASE6_AGENT_VERSION_ID,
        fail_scenarios=set(),
        anchor=anchor,
    ))
    session.add_all(_demo_audit_eval_results(
        session,
        eval_run_id="evalrun_demo_degraded",
        agent_run_id=eval_degraded_run.id,
        agent_version_id=PHASE6_DEGRADED_AGENT_VERSION_ID,
        fail_scenarios={"support_backlog_export_bug"},
        anchor=anchor,
    ))


DEMO_AUDIT_ANCHOR_RUN_ID: Final[str] = "run_f5af975d8f27487f"

# Deterministic knowledge document cited by each scenario's final report,
# matching the doc query the investigation workflow would retrieve.
_DEMO_REPORT_DOCS: Final[dict[str, str]] = {
    "checkout_retry_regression": "kb-runbook-billing-retry-regression",
    "enterprise_churn_wave": "kb-incident-response-enterprise-churn",
    "usage_drop_after_import_outage": "kb-troubleshooting-usage-activity-drop",
    "support_backlog_export_bug": "kb-incident-response-report-export",
    "payment_method_expiration": "kb-troubleshooting-payment-methods",
    "unknown_root_cause": "kb-runbook-mrr-drop-investigation",
}

# Ordered step shape for a completed investigation, mirroring the linear DAG
# in ``app.agent.workflow`` (``query metrics`` runs two tools, so 8 stages).
_DEMO_RUN_STAGE_TEMPLATE: Final[tuple[tuple[str, str | None], ...]] = (
    ("intake", None),
    ("plan", None),
    ("query metrics", "query_revenue_metrics"),
    ("query metrics", "fetch_account_details"),
    ("search docs", "search_docs"),
    ("fetch tickets", "fetch_support_tickets"),
    ("synthesize report", None),
    ("create mock actions", "create_mock_action"),
)


def _demo_audit_run(
    session: Session,
    *,
    run_id: str,
    scenario: str,
    incident_id: str,
    agent_version_id: str,
    started_at: datetime,
    completed_at: datetime,
    created_at: datetime,
    updated_at: datetime,
    confidence: str,
    report_offset_hours: int,
    tokens: tuple[int, int],
    cost: float,
    now: datetime,
    fail_sequence: int | None = None,
    blocked_sequences: set[int] | None = None,
) -> AgentRun:
    """Create one deterministic completed demo run plus its ordered steps."""
    report = _demo_audit_report(
        session,
        incident_id=incident_id,
        scenario=scenario,
        confidence=confidence,
        generated_offset_hours=report_offset_hours,
    )
    run = AgentRun(
        id=run_id,
        incident_id=incident_id,
        agent_id="ledger",
        agent_version_id=agent_version_id,
        status="failed" if fail_sequence is not None else "succeeded",
        trace_id=f"local-trace-{run_id}",
        trace_url=f"local://agent-runs/{run_id}/traces/local-trace-{run_id}",
        trace_provider="local",
        trace_metadata={
            "llm_provider": "openai",
            "llm_model": "gpt-4o-mini",
            "llm_used": True,
            "agent_version_id": agent_version_id,
        },
        input_payload={"incident_id": incident_id},
        final_report=None if fail_sequence is not None else report,
        token_estimate=sum(tokens),
        prompt_tokens=tokens[0],
        completion_tokens=tokens[1],
        cost_estimate_usd=cost,
        error=(
            "Database query timed out while fetching revenue metrics."
            if fail_sequence is not None
            else None
        ),
        started_at=started_at,
        completed_at=completed_at,
        created_at=created_at,
        updated_at=updated_at,
    )
    session.add(run)
    session.flush()
    session.add_all(
        _demo_audit_steps(
            session,
            run_id=run_id,
            incident_id=incident_id,
            scenario=scenario,
            started_at=started_at,
            report=report,
            fail_sequence=fail_sequence,
            blocked_sequences=blocked_sequences,
            now=now,
        )
    )
    return run


def _demo_audit_steps(
    session: Session,
    *,
    run_id: str,
    incident_id: str,
    scenario: str,
    started_at: datetime,
    report: dict[str, Any],
    fail_sequence: int | None,
    blocked_sequences: set[int] | None,
    now: datetime,
) -> list[AgentRunStep]:
    incident = session.get(Incident, incident_id)
    evidence = incident.evidence if incident is not None else {}
    metric = evidence.get("metric_evidence", {})
    affected = evidence.get("affected_accounts", [])
    account_ids = [account["account_id"] for account in affected]
    invoice_ids = metric.get("invoice_ids") or []
    source_queries = evidence.get("source_queries") or []
    doc_id = _DEMO_REPORT_DOCS[scenario]
    doc_query = f"{incident.title if incident else scenario} {doc_id}"

    steps: list[AgentRunStep] = []
    for index, (stage, tool_name) in enumerate(_DEMO_RUN_STAGE_TEMPLATE, start=1):
        status = "succeeded"
        outputs: dict[str, Any] | None = {}
        error: str | None = None
        blocked_reason: str | None = None
        if fail_sequence == index:
            status = "failed"
            outputs = None
            error = "Database query timed out after 30s while fetching revenue metrics."
        elif blocked_sequences and index in blocked_sequences:
            status = "blocked"
            blocked_reason = "tool_not_enabled"
            outputs = {
                "query": doc_query,
                "results": [],
                "tool_disabled": True,
                "tool_disabled_reason": "search_docs was not enabled for this agent version.",
            }
        elif stage == "synthesize report":
            outputs = report
        else:
            outputs = _demo_step_outputs(stage, tool_name, incident, evidence)

        step_start = started_at + timedelta(seconds=index * 18)
        step_end = step_start + timedelta(seconds=11)
        steps.append(
            AgentRunStep(
                id=f"{run_id}_s{index:02d}",
                run_id=run_id,
                sequence=index,
                stage=stage,
                tool_name=tool_name,
                status=status,
                inputs=_demo_step_inputs(
                    stage=stage,
                    tool_name=tool_name,
                    incident_id=incident_id,
                    account_ids=account_ids,
                    invoice_ids=invoice_ids,
                    doc_query=doc_query,
                    source_queries=source_queries,
                ),
                outputs=outputs,
                error=error,
                blocked_reason=blocked_reason,
                started_at=step_start,
                completed_at=step_end if status in ("succeeded", "blocked") else step_start,
                created_at=now,
            )
        )
        # A failed tool step ends the investigation; later stages never run.
        if fail_sequence == index:
            break
    return steps


def _demo_step_inputs(
    *,
    stage: str,
    tool_name: str | None,
    incident_id: str,
    account_ids: list[str],
    invoice_ids: list[str],
    doc_query: str,
    source_queries: list[str],
) -> dict[str, Any]:
    if stage == "intake":
        return {"incident_id": incident_id}
    if stage == "plan":
        return {
            "incident_id": incident_id,
            "enabled_tool_ids": [
                "query_revenue_metrics",
                "fetch_account_details",
                "search_docs",
                "fetch_support_tickets",
            ],
        }
    if tool_name == "query_revenue_metrics":
        return {"incident_id": incident_id}
    if tool_name == "fetch_account_details":
        return {"account_ids": account_ids, "invoice_ids": invoice_ids, "include_invoices": True}
    if tool_name == "search_docs":
        return {"query": doc_query, "limit": 5}
    if tool_name == "fetch_support_tickets":
        return {"account_ids": account_ids, "since": "2026-05-10T00:00:00", "limit": 24}
    if stage == "synthesize report":
        return {
            "incident_id": incident_id,
            "evidence_sets": ["revenue_metrics", "account_details", "doc_results", "support_tickets"],
        }
    if tool_name == "create_mock_action":
        return {"run_id": "", "action_types": ["draft_slack_message", "create_task"]}
    return {"incident_id": incident_id}


def _demo_step_outputs(
    stage: str,
    tool_name: str | None,
    incident: Incident | None,
    evidence: dict[str, Any],
) -> dict[str, Any]:
    if stage == "intake":
        return {
            "incident_id": incident.id if incident else "",
            "title": incident.title if incident else "",
            "severity": incident.severity if incident else "",
        }
    if stage == "plan":
        return {
            "objective": "Explain the paid MRR drop, identify affected accounts, and cite evidence.",
            "hypotheses": [str(item) for item in (SCENARIOS.get(incident.source_scenario, {}) if incident else {}).get("false_leads", [])][:2],
            "tool_calls": ["query_revenue_metrics", "fetch_account_details", "search_docs", "fetch_support_tickets"],
            "disabled_tool_ids": [],
        }
    if tool_name == "query_revenue_metrics":
        metric = evidence.get("metric_evidence", {})
        return {
            "incident_id": incident.id if incident else "",
            "metric_evidence": metric,
            "affected_account_ids": [account["account_id"] for account in (evidence.get("affected_accounts") or [])],
            "invoice_ids": metric.get("invoice_ids") or [],
            "sql_evidence": [{"query": query} for query in (evidence.get("source_queries") or [])],
        }
    if tool_name == "fetch_account_details":
        return {
            "accounts": [
                {
                    "account_id": account["account_id"],
                    "account_name": account["account_name"],
                    "segment": account["segment"],
                    "health_score": account["health_score"],
                    "failed_invoice_ids": account.get("failed_invoice_ids", [])[:2],
                }
                for account in (evidence.get("affected_accounts") or [])[:3]
            ]
        }
    if tool_name == "search_docs":
        return {
            "query": (evidence.get("source_queries") or [""])[0],
            "results": [
                {"document_id": _DEMO_REPORT_DOCS.get(incident.source_scenario, "") if incident else "", "score": 0.92}
            ],
        }
    if tool_name == "fetch_support_tickets":
        return {
            "tickets": [
                {"ticket_id": ticket_id, "account_id": "", "category": "billing"}
                for ticket_id in (evidence.get("support_ticket_ids") or [])[:3]
            ]
        }
    if tool_name == "create_mock_action":
        return {"action_count": 2, "action_types": ["draft_slack_message", "create_task"], "pending_approval_count": 0}
    return {}


def _demo_audit_report(
    session: Session,
    *,
    incident_id: str,
    scenario: str,
    confidence: str,
    generated_offset_hours: int,
) -> dict[str, Any]:
    """Build a deterministic, evidence-citing final report for a seeded run."""
    from app.agent.schemas import (
        InvestigationReport,
        ReportAffectedAccount,
        ReportClaim,
        ReportEvidence,
    )

    incident = session.get(Incident, incident_id)
    if incident is None:
        raise LookupError(f"Missing incident {incident_id} for demo audit seed")
    evidence = incident.evidence or {}
    source_queries = evidence.get("source_queries") or []
    doc_id = _DEMO_REPORT_DOCS[scenario]

    affected_accounts = [
        ReportAffectedAccount(
            account_id=account["account_id"],
            account_name=account["account_name"],
            segment=account["segment"],
            health_score=account["health_score"],
            failed_invoice_cents=account.get("failed_invoice_cents", 0),
            failed_invoice_ids=account.get("failed_invoice_ids", [])[:3],
            ticket_ids=(evidence.get("support_ticket_ids") or [])[:2],
        )
        for account in (evidence.get("affected_accounts") or [])[:3]
    ]
    cited_evidence: list[ReportEvidence] = [
        ReportEvidence(
            kind="sql",
            title="Revenue metrics query",
            summary=query,
            reference_id=f"sql:{query[:32]}",
            source_query=query,
            citation={
                "window": evidence.get("metric_evidence", {}).get("current_window_start")
            },
        )
        for query in source_queries[:2]
    ]
    cited_evidence.append(
        ReportEvidence(
            kind="document",
            title="Incident runbook",
            summary=f"Retrieved internal runbook {doc_id}.",
            reference_id=doc_id,
            citation={"document_id": doc_id},
        )
    )
    for ticket_id in (evidence.get("support_ticket_ids") or [])[:2]:
        cited_evidence.append(
            ReportEvidence(
                kind="ticket",
                title="Support ticket",
                summary=f"Support signal {ticket_id} attached to an affected account.",
                reference_id=ticket_id,
                citation={"ticket_id": ticket_id},
            )
        )

    refs = [item.reference_id for item in cited_evidence]
    recommended = list(SCENARIOS[scenario].get("recommended_actions") or [])
    if not recommended:
        recommended = ["Repair the failing renewal flow"]
    report = InvestigationReport(
        root_cause=str(SCENARIOS[scenario]["root_cause"]),
        summary=f"{incident.title}: {SCENARIOS[scenario]['root_cause']}",
        affected_accounts=affected_accounts,
        cited_evidence=cited_evidence,
        claims=[
            ReportClaim(
                category="root_cause",
                text=str(SCENARIOS[scenario]["root_cause"]),
                citation_refs=refs[:2],
            ),
            ReportClaim(
                category="impact",
                text=f"{len(affected_accounts)} affected accounts with failed renewal evidence.",
                citation_refs=refs,
            ),
            ReportClaim(
                category="recommendation",
                text="Proposed approval-gated follow-up actions.",
                citation_refs=[],
            ),
        ],
        confidence=confidence,  # type: ignore[arg-type]
        next_actions=recommended,
        generated_at=incident.detected_at + timedelta(hours=generated_offset_hours),
    )
    return report.model_dump(mode="json")


def _demo_audit_actions(
    session: Session,
    *,
    run: AgentRun,
    created_at: datetime,
    mode: str,
) -> None:
    """Seed deterministic mock actions and approval states for a demo run.

    ``mode`` selects the approval-state mix visible on /approvals:
    - "pending"     -> two executed low-risk actions plus two pending approvals
    - "decided"     -> one approved and one rejected decision (history view)
    - "single_pending" -> a single pending approval
    """
    base = run.id
    updated = created_at + timedelta(minutes=4)

    def action(
        action_id: str,
        action_type: str,
        risk_level: str,
        status: str,
        title: str,
        description: str,
        target: str,
        payload: dict[str, Any],
        *,
        executed_at: datetime | None = None,
    ) -> MockAction:
        return MockAction(
            id=action_id,
            run_id=run.id,
            action_type=action_type,
            risk_level=risk_level,
            status=status,
            title=title,
            description=description,
            target=target,
            payload=payload,
            created_by="ledger-agent",
            created_at=created_at,
            updated_at=updated,
            executed_at=executed_at,
        )

    def approval(
        approval_id: str,
        action_id: str,
        status: str,
        risk_level: str,
        reason: str,
        *,
        decided_by: str | None = None,
        decision_notes: str | None = None,
        decided_at: datetime | None = None,
    ) -> ApprovalRequest:
        return ApprovalRequest(
            id=approval_id,
            run_id=run.id,
            action_id=action_id,
            status=status,
            risk_level=risk_level,
            reason=reason,
            requested_by="ledger-agent",
            decided_by=decided_by,
            decision_notes=decision_notes,
            created_at=created_at,
            decided_at=decided_at,
        )

    def audit(
        event_id: str,
        action_id: str,
        approval_id: str | None,
        event_type: str,
        actor: str,
        notes: str | None,
        event_time: datetime,
    ) -> ActionAuditEvent:
        return ActionAuditEvent(
            id=event_id,
            run_id=run.id,
            action_id=action_id,
            approval_request_id=approval_id,
            event_type=event_type,
            actor=actor,
            notes=notes,
            event_metadata={},
            created_at=event_time,
        )

    if mode == "pending":
        low_slack = action(
            f"{base}_act_slack",
            "draft_slack_message",
            "low",
            "executed",
            "Notify billing on-call about the retry regression",
            "Draft a Slack message to billing on-call summarizing affected accounts.",
            "slack:#billing-oncall",
            {"channel": "#billing-oncall"},
            executed_at=updated,
        )
        low_task = action(
            f"{base}_act_task",
            "create_task",
            "low",
            "executed",
            "Create a task to monitor renewal retries",
            "Track the retry webhook repair in the operations queue.",
            "ops:ledger-queue",
            {"assignee": "billing-ops"},
            executed_at=updated,
        )
        email_action = action(
            f"{base}_act_email",
            "draft_customer_email",
            "high",
            "pending_approval",
            "Draft renewal outreach to affected billing owners",
            "Explain the retry repair and next billing date to affected customers.",
            "email:customers",
            {"template": "renewal-retry"},
        )
        note_action = action(
            f"{base}_act_note",
            "update_account_note",
            "high",
            "pending_approval",
            "Attach incident note to affected accounts",
            "Record the MRR-drop incident link on each affected account.",
            "accounts:affected",
            {"note": "incident-linked"},
        )
        email_approval = approval(
            f"{base}_apr_email",
            email_action.id,
            "pending",
            "high",
            "High-risk customer outreach requires an operator decision.",
        )
        note_approval = approval(
            f"{base}_apr_note",
            note_action.id,
            "pending",
            "high",
            "Writing account notes requires an operator decision.",
        )
        session.add_all(
            [
                low_slack,
                low_task,
                email_action,
                note_action,
                email_approval,
                note_approval,
                audit(f"{base}_aud_1", low_slack.id, None, "executed", "ledger-agent", None, updated),
                audit(f"{base}_aud_2", low_task.id, None, "executed", "ledger-agent", None, updated),
                audit(f"{base}_aud_3", email_action.id, email_approval.id, "proposed", "ledger-agent", None, created_at),
                audit(f"{base}_aud_4", note_action.id, note_approval.id, "proposed", "ledger-agent", None, created_at),
            ]
        )
        return

    if mode == "decided":
        approved_action = action(
            f"{base}_act_email",
            "draft_customer_email",
            "high",
            "executed",
            "Send renewal outreach after approval",
            "Approved customer email drafted for the import-outage incident.",
            "email:customers",
            {"template": "import-outage"},
            executed_at=updated + timedelta(minutes=1),
        )
        rejected_action = action(
            f"{base}_act_note",
            "update_account_note",
            "high",
            "rejected",
            "Attach account note about import instability",
            "Proposed account-note update rejected by the operator.",
            "accounts:affected",
            {"note": "import-outage"},
        )
        approved_approval = approval(
            f"{base}_apr_email",
            approved_action.id,
            "approved",
            "high",
            "Customer outreach for affected accounts.",
            decided_by="demo-operator",
            decision_notes="Approved with default template.",
            decided_at=updated,
        )
        rejected_approval = approval(
            f"{base}_apr_note",
            rejected_action.id,
            "rejected",
            "high",
            "Account-note update proposed by the agent.",
            decided_by="demo-operator",
            decision_notes="Rejected; note content needs review.",
            decided_at=updated,
        )
        session.add_all(
            [
                approved_action,
                rejected_action,
                approved_approval,
                rejected_approval,
                audit(f"{base}_aud_1", approved_action.id, approved_approval.id, "proposed", "ledger-agent", None, created_at),
                audit(f"{base}_aud_2", approved_action.id, approved_approval.id, "approved", "demo-operator", "Approved with default template.", updated),
                audit(f"{base}_aud_3", approved_action.id, None, "executed", "ledger-agent", None, updated + timedelta(minutes=1)),
                audit(f"{base}_aud_4", rejected_action.id, rejected_approval.id, "proposed", "ledger-agent", None, created_at),
                audit(f"{base}_aud_5", rejected_action.id, rejected_approval.id, "rejected", "demo-operator", "Rejected; note content needs review.", updated),
            ]
        )
        return

    # mode == "single_pending"
    pending_action = action(
        f"{base}_act_email",
        "draft_customer_email",
        "high",
        "pending_approval",
        "Draft uncertainty follow-up to affected accounts",
        "Propose a follow-up acknowledging the mixed renewal signals.",
        "email:customers",
        {"template": "uncertainty-followup"},
    )
    pending_approval = approval(
        f"{base}_apr_email",
        pending_action.id,
        "pending",
        "high",
        "Customer follow-up for the ambiguous root cause case.",
    )
    session.add_all(
        [
            pending_action,
            pending_approval,
            audit(f"{base}_aud_1", pending_action.id, pending_approval.id, "proposed", "ledger-agent", None, created_at),
        ]
    )


def _demo_audit_eval_results(
    session: Session,
    *,
    eval_run_id: str,
    agent_run_id: str,
    agent_version_id: str,
    fail_scenarios: set[str],
    anchor: datetime,
) -> list[EvalResult]:
    """Seed one complete eval-regression run for a candidate version."""
    case_ids = session.scalars(select(EvalCase.id).order_by(EvalCase.id)).all()
    results: list[EvalResult] = []
    for case_id in case_ids:
        case = session.get(EvalCase, case_id)
        passed = case.scenario not in fail_scenarios
        started_at = anchor + timedelta(hours=8)
        completed_at = started_at + timedelta(seconds=2)
        results.append(
            EvalResult(
                id=f"{eval_run_id}_{case_id}",
                eval_run_id=eval_run_id,
                eval_case_id=case_id,
                agent_run_id=agent_run_id,
                agent_version_id=agent_version_id,
                dataset_id="mrr-drop-suite",
                scenario=case.scenario,
                status="passed" if passed else "failed",
                passed=passed,
                root_cause_score=0.92 if passed else 0.41,
                citation_quality_score=0.88 if passed else 0.30,
                action_safety_score=1.0,
                latency_ms=1800 if passed else 2400,
                cost_estimate_usd=0.012 if passed else 0.008,
                expected_root_cause=case.expected_root_cause,
                actual_root_cause=(
                    case.expected_root_cause
                    if passed
                    else "Missing document evidence; root cause unconfirmed."
                ),
                expected_evidence_types=list(case.expected_evidence_types),
                observed_evidence_types=(
                    list(case.expected_evidence_types)
                    if passed
                    else ["sql", "ticket"]
                ),
                failure_reasons=(
                    []
                    if passed
                    else ["expected_document_evidence_missing"]
                ),
                example_output={
                    "run_id": agent_run_id,
                    "root_cause": case.expected_root_cause if passed else None,
                    "confidence": "high" if passed else "low",
                },
                started_at=started_at,
                completed_at=completed_at,
                created_at=started_at,
            )
        )
    return results


def _seed_phase6_agent_version(session: Session) -> None:
    """Ensure fresh or explicit reset seeds reproduce migration 0015's snapshot."""
    from app.agents.service import PHASE6_AGENT_VERSION_ID, PHASE6_ENABLED_TOOL_IDS
    from app.tools.scopes import PHASE6_ALLOWED_SCOPES

    session.flush()
    version_id = PHASE6_AGENT_VERSION_ID
    if session.get(AgentVersion, version_id) is not None:
        return
    source = session.get(AgentVersion, "ledger_v1")
    if source is None:
        return

    now = datetime.now(UTC).replace(tzinfo=None)
    next_version_number = int(
        session.scalar(
            select(func.coalesce(func.max(AgentVersion.version_number), 0)).where(
                AgentVersion.agent_id == source.agent_id
            )
        )
        or 0
    ) + 1
    session.add(
        AgentVersion(
            id=version_id,
            agent_id=source.agent_id,
            version_number=next_version_number,
            semantic_version=f"{next_version_number}.0.0",
            status="published",
            system_prompt=source.system_prompt,
            model=source.model,
            temperature=source.temperature,
            max_tokens=source.max_tokens,
            enabled_tool_ids=list(PHASE6_ENABLED_TOOL_IDS),
            allowed_scopes=list(PHASE6_ALLOWED_SCOPES),
            published_at=now,
            published_by="bootstrap:phase6",
            forked_from_version_id=source.id,
            created_at=now,
            updated_at=now,
        )
    )
    session.flush()


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
        "eval_datasets": EvalDataset,
        "eval_dataset_cases": EvalDatasetCase,
        "eval_results": EvalResult,
        "agent_runs": AgentRun,
        "agent_run_steps": AgentRunStep,
        "mock_actions": MockAction,
        "approval_requests": ApprovalRequest,
        "action_audit_events": ActionAuditEvent,
        "knowledge_documents": KnowledgeDocument,
        "knowledge_document_chunks": KnowledgeDocumentChunk,
        "agents": Agent,
        "agent_versions": AgentVersion,
        "tools": Tool,
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
    safe_database_names = {"ledger", "ledger_test", "test_ledger"}
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
