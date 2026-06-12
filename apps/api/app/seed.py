from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Final
from urllib.parse import urlparse

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.session import SessionLocal
from app.models import Account, Invoice, ProductEvent, Subscription, SupportTicket, User

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
        "false_leads": ["checkout retry regression", "enterprise churn"],
        "recommended_actions": [
            "draft billing contact reminders",
            "audit card-expiration notices",
        ],
    },
}
SCENARIO_ACCOUNT_NUMBERS: Final[dict[str, set[int]]] = {
    scenario: metadata["account_numbers"] for scenario, metadata in SCENARIOS.items()
}

MODEL_ORDER: Final[tuple[type, ...]] = (
    SupportTicket,
    ProductEvent,
    Invoice,
    User,
    Subscription,
    Account,
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
        return None
    return reseed_database(session)


def reseed_database(session: Session) -> SeedResult:
    try:
        clear_domain_data(session)
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

        counts = seed_counts(session)
        fingerprint = dataset_fingerprint(session)
        session.commit()
    except Exception:
        session.rollback()
        raise

    return SeedResult(counts=counts, fingerprint=fingerprint)


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
            }:
                status = "failed"
                failure_reason = (
                    "Retry webhook regression suppressed second charge attempt"
                    if scenario == "checkout_retry_regression"
                    else "Expired cards were not refreshed before renewal"
                )
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


def seed_counts(session: Session) -> dict[str, int]:
    models = {
        "accounts": Account,
        "users": User,
        "subscriptions": Subscription,
        "invoices": Invoice,
        "product_events": ProductEvent,
        "support_tickets": SupportTicket,
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


def validate_seed_target(database_url: str, _app_env: str) -> None:
    parsed_url = urlparse(database_url.replace("+psycopg", "", 1))
    safe_hosts = {"", "localhost", "127.0.0.1", "::1", "postgres"}
    database_name = parsed_url.path.rsplit("/", maxsplit=1)[-1]
    if parsed_url.hostname in safe_hosts and database_name in {"ops_agent", "ops_agent_test"}:
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
