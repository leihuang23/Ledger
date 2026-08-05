"""Behavior tests for the bounded agentic investigation loop.

These tests read like product claims about the agentic mode:
- the LLM chooses which tools to call and in which order;
- tool policy, argument sanitization, and deduplication are enforced;
- malformed decisions, LLM errors, and exhausted budgets degrade honestly
  to the deterministic evidence sweep;
- the deterministic evidence classifier remains the adoption gate for any
  final LLM diagnosis.
"""

from __future__ import annotations

import json
from collections.abc import Generator
from typing import Callable

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

import app.models  # noqa: F401
from app.agent.workflow import run_investigation_workflow
from app.agents.service import DEFAULT_AGENT_ID, DEFAULT_AGENT_VERSION_ID
from app.db.base import Base
from app.llm.schemas import LLMUsage, parse_agent_decision
from app.models import AgentRun, AgentRunStep, Incident, Invoice
from app.seed import reseed_database

RETRY_WEBHOOK_ROOT_CAUSE = (
    "Billing retry webhook regression suppressed second charge attempts."
)


class FakeAgenticClient:
    """Scripted agentic client: one raw JSON decision per loop turn."""

    provider: str = "fake"
    model: str = "fake-agentic"
    agentic: bool = True

    def __init__(self, scripted: list[str]) -> None:
        self.scripted = scripted
        self.prompts: list[str] = []

    def complete_raw(self, prompt: str) -> tuple[str, LLMUsage]:
        self.prompts.append(prompt)
        raw = self.scripted[min(len(self.prompts) - 1, len(self.scripted) - 1)]
        usage = LLMUsage(
            provider=self.provider,
            model=self.model,
            prompt_tokens=12,
            completion_tokens=7,
            latency_ms=2,
            used_llm=True,
        )
        return raw, usage


def tool_call(tool: str, arguments: dict | None = None, reasoning: str = "") -> str:
    return json.dumps(
        {
            "decision": "tool_call",
            "tool": tool,
            "arguments": arguments or {},
            "reasoning": reasoning,
        }
    )


def final_decision(root_cause: str, confidence: str = "high") -> str:
    return json.dumps(
        {
            "decision": "final",
            "root_cause": root_cause,
            "confidence": confidence,
            "next_actions": ["Create approval-gated billing follow-up drafts."],
            "reasoning": "Evidence aligns.",
        }
    )


FULL_EVIDENCE_SCRIPT = [
    tool_call("query_revenue_metrics", reasoning="Confirm the MRR movement."),
    tool_call("fetch_account_details", reasoning="Get invoice failure reasons."),
    tool_call("search_docs", {"query": "retry webhook renewal failures"}),
    tool_call("fetch_support_tickets", reasoning="Connect customer signals."),
]


@pytest.fixture()
def session_factory(tmp_path) -> Generator[Callable[[], Session], None, None]:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'agent_loop_test.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    yield TestingSessionLocal

    Base.metadata.drop_all(engine)
    engine.dispose()


def _make_run(session: Session, *, run_id: str) -> AgentRun:
    incident = session.scalar(select(Incident))
    assert incident is not None
    run = AgentRun(
        id=run_id,
        incident_id=incident.id,
        agent_id=DEFAULT_AGENT_ID,
        agent_version_id=DEFAULT_AGENT_VERSION_ID,
        status="running",
        trace_id=None,
        trace_metadata={},
        input_payload={},
        token_estimate=0,
        cost_estimate_usd=0.0,
        created_at=incident.created_at,
        updated_at=incident.created_at,
    )
    session.add(run)
    session.commit()
    return run


def _steps(session: Session, run_id: str) -> list[AgentRunStep]:
    return list(
        session.scalars(
            select(AgentRunStep)
            .where(AgentRunStep.run_id == run_id)
            .order_by(AgentRunStep.sequence)
        )
    )


# --------------------------------------------------------------------- parse


def test_parse_agent_decision_accepts_tool_call_and_final() -> None:
    call = parse_agent_decision(
        tool_call("search_docs", {"query": "renewal failures"})
    )
    assert call.decision == "tool_call"
    assert call.tool == "search_docs"
    assert call.arguments == {"query": "renewal failures"}

    done = parse_agent_decision(final_decision("Some root cause."))
    assert done.decision == "final"
    assert done.root_cause == "Some root cause."


def test_parse_agent_decision_rejects_malformed_output() -> None:
    with pytest.raises(ValueError):
        parse_agent_decision("I will now investigate the metrics.")
    with pytest.raises(ValueError):
        parse_agent_decision(json.dumps({"decision": "explore"}))


# ---------------------------------------------------------------- happy path


def test_agentic_loop_drives_tool_selection_and_adopts_supported_diagnosis(
    session_factory: Callable[[], Session],
) -> None:
    with session_factory() as session:
        reseed_database(session)
        run = _make_run(session, run_id="run_agentic_happy")
        llm_root_cause = (
            "The billing retry webhook regression suppressed second charge "
            "attempts for the affected renewals."
        )
        client = FakeAgenticClient(
            FULL_EVIDENCE_SCRIPT + [final_decision(llm_root_cause)]
        )

        report = run_investigation_workflow(session, run, llm_client=client)

        # The LLM drove evidence gathering: one decision per tool plus final.
        assert len(client.prompts) == 5
        # Its diagnosis shares the deterministic signature, so it is adopted.
        assert report.root_cause == llm_root_cause
        assert run.trace_metadata.get("agent_mode") == "agentic"
        assert run.trace_metadata.get("llm_used") is True
        # Run-level tokens aggregate every loop decision.
        assert run.prompt_tokens == 12 * 5
        assert run.completion_tokens == 7 * 5

        steps = _steps(session, run.id)
        stages = [step.stage for step in steps]
        assert stages.count("agent decision") == 5
        tool_steps = [step for step in steps if step.stage == "agent tool call"]
        assert {step.tool_name for step in tool_steps} == {
            "query_revenue_metrics",
            "fetch_account_details",
            "search_docs",
            "fetch_support_tickets",
        }
        assert all(step.status == "succeeded" for step in tool_steps)
        # Citations still come from retrieved evidence only.
        kinds = {evidence.kind for evidence in report.cited_evidence}
        assert {"sql", "document", "ticket"}.issubset(kinds)


def test_agentic_loop_records_blocked_tool_and_still_succeeds(
    session_factory: Callable[[], Session],
) -> None:
    with session_factory() as session:
        reseed_database(session)
        run = _make_run(session, run_id="run_agentic_blocked")
        client = FakeAgenticClient(
            [
                tool_call("query_revenue_metrics"),
                tool_call("fetch_account_details"),
                tool_call("fetch_support_tickets"),
                tool_call("search_docs", {"query": "retry webhook"}),
                final_decision(
                    "A retry webhook regression blocked second charge attempts."
                ),
            ]
        )

        report = run_investigation_workflow(
            session,
            run,
            llm_client=client,
            enabled_tool_ids={
                "query_revenue_metrics",
                "fetch_account_details",
                "fetch_support_tickets",
            },
            blocked_reasons={"search_docs": "scope_not_allowed"},
        )

        steps = _steps(session, run.id)
        blocked = [
            step
            for step in steps
            if step.tool_name == "search_docs" and step.status == "blocked"
        ]
        assert len(blocked) == 1
        assert blocked[0].blocked_reason == "scope_not_allowed"
        # The diagnosis gate still matched the retry-webhook signature.
        assert "retry webhook" in report.root_cause.lower()


def test_agentic_loop_rejects_unknown_tool_request(
    session_factory: Callable[[], Session],
) -> None:
    with session_factory() as session:
        reseed_database(session)
        run = _make_run(session, run_id="run_agentic_unknown_tool")
        client = FakeAgenticClient(
            [
                tool_call("query_revenue_metrics"),
                tool_call("exfiltrate_database", {"target": "all"}),
                final_decision(
                    "The retry webhook regression suppressed charge attempts."
                ),
            ]
        )

        run_investigation_workflow(session, run, llm_client=client)

        steps = _steps(session, run.id)
        blocked = [
            step
            for step in steps
            if step.tool_name == "exfiltrate_database"
        ]
        assert len(blocked) == 1
        assert blocked[0].status == "blocked"
        assert blocked[0].blocked_reason == "unknown_tool_requested"


# ------------------------------------------------------------------ safety


def test_agentic_loop_sanitizes_unknown_account_ids(
    session_factory: Callable[[], Session],
) -> None:
    with session_factory() as session:
        reseed_database(session)
        incident = session.scalar(select(Incident))
        known_ids = set(incident.affected_account_ids)
        run = _make_run(session, run_id="run_agentic_sanitize")
        client = FakeAgenticClient(
            [
                tool_call("query_revenue_metrics"),
                tool_call(
                    "fetch_account_details",
                    {"account_ids": ["acct_never_existed", *sorted(known_ids)[:1]]},
                ),
                final_decision(
                    "The retry webhook regression suppressed charge attempts.",
                    confidence="low",
                ),
            ]
        )

        run_investigation_workflow(session, run, llm_client=client)

        steps = _steps(session, run.id)
        account_step = next(
            step
            for step in steps
            if step.tool_name == "fetch_account_details"
            and step.status == "succeeded"
        )
        dispatched_ids = set(account_step.inputs["account_ids"])
        assert "acct_never_existed" not in dispatched_ids
        assert dispatched_ids.issubset(known_ids)
        # Invoice evidence is restricted to genuinely failed invoice ids; an
        # empty argument would return every invoice including paid ones.
        failed_invoice_ids = set(
            session.scalars(
                select(Invoice.id).where(Invoice.status == "failed")
            )
        )
        dispatched_invoices = set(account_step.inputs["invoice_ids"])
        assert dispatched_invoices
        assert dispatched_invoices.issubset(failed_invoice_ids)


def test_duplicate_tool_call_is_short_circuited(
    session_factory: Callable[[], Session],
) -> None:
    with session_factory() as session:
        reseed_database(session)
        run = _make_run(session, run_id="run_agentic_duplicate")
        client = FakeAgenticClient(
            [
                tool_call("query_revenue_metrics"),
                tool_call("query_revenue_metrics"),
                final_decision(
                    "The retry webhook regression suppressed charge attempts.",
                    confidence="low",
                ),
            ]
        )

        run_investigation_workflow(session, run, llm_client=client)

        steps = _steps(session, run.id)
        metrics_steps = [
            step
            for step in steps
            if step.tool_name == "query_revenue_metrics"
        ]
        assert len(metrics_steps) == 1
        assert metrics_steps[0].status == "succeeded"


# ------------------------------------------------------------- degradation


def test_malformed_decision_degrades_to_deterministic_sweep(
    session_factory: Callable[[], Session],
) -> None:
    with session_factory() as session:
        reseed_database(session)
        run = _make_run(session, run_id="run_agentic_malformed")
        client = FakeAgenticClient(
            ["Let me think about this anomaly step by step..."]
        )

        report = run_investigation_workflow(session, run, llm_client=client)

        assert report.root_cause == RETRY_WEBHOOK_ROOT_CAUSE
        assert run.trace_metadata.get("llm_fallback_reason", "").startswith(
            "agent_loop_degraded: parse_error_fallback"
        )
        # The deterministic sweep still executed every enabled tool.
        steps = _steps(session, run.id)
        tool_names = {
            step.tool_name
            for step in steps
            if step.stage == "agent tool call" and step.status == "succeeded"
        }
        assert tool_names == {
            "query_revenue_metrics",
            "fetch_account_details",
            "search_docs",
            "fetch_support_tickets",
        }


def test_budget_exhaustion_degrades_to_deterministic_sweep(
    session_factory: Callable[[], Session],
) -> None:
    with session_factory() as session:
        reseed_database(session)
        run = _make_run(session, run_id="run_agentic_budget")
        client = FakeAgenticClient(
            [
                tool_call("search_docs", {"query": "first query"}),
                tool_call("search_docs", {"query": "second query"}),
                # The script never finalizes; the last entry repeats forever.
            ]
        )

        report = run_investigation_workflow(
            session, run, llm_client=client, max_iterations=2
        )

        assert report.root_cause == RETRY_WEBHOOK_ROOT_CAUSE
        assert run.trace_metadata.get("llm_fallback_reason") == (
            "agent_loop_degraded: iteration_budget_exhausted"
        )
        # Only two decisions were billed despite the repeating script.
        assert len(client.prompts) == 2
        assert run.prompt_tokens == 12 * 2


class ExplodingAgenticClient:
    """Agentic client whose transport fails on the very first decision."""

    provider: str = "fake"
    model: str = "fake-agentic"
    agentic: bool = True

    def complete_raw(self, prompt: str) -> tuple[str, LLMUsage]:
        raise ConnectionError("provider down")


def test_llm_transport_error_degrades_to_deterministic_sweep(
    session_factory: Callable[[], Session],
) -> None:
    """An LLM provider failure must degrade honestly, not fail the run."""
    with session_factory() as session:
        reseed_database(session)
        run = _make_run(session, run_id="run_agentic_llm_error")

        report = run_investigation_workflow(
            session, run, llm_client=ExplodingAgenticClient()
        )

        assert report.root_cause == RETRY_WEBHOOK_ROOT_CAUSE
        assert run.trace_metadata.get("llm_fallback_reason", "").startswith(
            "agent_loop_degraded: llm_error_fallback"
        )
        # The deterministic sweep still executed every enabled tool.
        steps = _steps(session, run.id)
        tool_names = {
            step.tool_name
            for step in steps
            if step.stage == "agent tool call" and step.status == "succeeded"
        }
        assert tool_names == {
            "query_revenue_metrics",
            "fetch_account_details",
            "search_docs",
            "fetch_support_tickets",
        }
        # The degraded LLM turn stays visible in the timeline.
        degraded = [
            step
            for step in steps
            if step.stage == "agent decision" and step.outputs.get("degraded")
        ]
        assert len(degraded) == 1


def test_unsupported_final_diagnosis_falls_back_to_classifier(
    session_factory: Callable[[], Session],
) -> None:
    with session_factory() as session:
        reseed_database(session)
        run = _make_run(session, run_id="run_agentic_unsupported")
        client = FakeAgenticClient(
            FULL_EVIDENCE_SCRIPT
            + [final_decision("Pricing discounts were misconfigured at renewal.")]
        )

        report = run_investigation_workflow(session, run, llm_client=client)

        assert report.root_cause == RETRY_WEBHOOK_ROOT_CAUSE
        assert run.trace_metadata.get("llm_fallback_reason") == (
            "unsupported_llm_diagnosis: deterministic_fallback"
        )


def test_legacy_non_agentic_client_stays_on_deterministic_pipeline(
    session_factory: Callable[[], Session],
) -> None:
    """Clients without agentic capability keep the fixed pipeline behavior."""
    from app.llm import NoopLLMClient

    with session_factory() as session:
        reseed_database(session)
        run = _make_run(session, run_id="run_deterministic_mode")

        report = run_investigation_workflow(session, run, llm_client=NoopLLMClient())

        assert report.root_cause == RETRY_WEBHOOK_ROOT_CAUSE
        assert run.trace_metadata.get("agent_mode") == "deterministic"
        stages = [step.stage for step in _steps(session, run.id)]
        assert "agent decision" not in stages
        assert "plan" in stages


def test_agent_loop_gate_pins_provider_back_to_deterministic_pipeline(
    session_factory: Callable[[], Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    """AGENT_LOOP_ENABLED=false opts an agentic client out of the loop."""
    from app.core.config import get_settings

    monkeypatch.setenv("AGENT_LOOP_ENABLED", "false")
    get_settings.cache_clear()
    try:
        with session_factory() as session:
            reseed_database(session)
            run = _make_run(session, run_id="run_agentic_gate_off")
            client = FakeAgenticClient(
                FULL_EVIDENCE_SCRIPT + [final_decision("Anything.")]
            )

            report = run_investigation_workflow(session, run, llm_client=client)

            assert report.root_cause == RETRY_WEBHOOK_ROOT_CAUSE
            assert run.trace_metadata.get("agent_mode") == "deterministic"
            # The loop never ran: no decision steps, no billed LLM calls.
            assert client.prompts == []
            stages = [step.stage for step in _steps(session, run.id)]
            assert "agent decision" not in stages
            assert "plan" in stages
    finally:
        get_settings.cache_clear()


# ------------------------------------------------- policy-blocked evidence


def test_disabled_metrics_tool_keeps_uncertainty_guard_in_agentic_mode(
    session_factory: Callable[[], Session],
) -> None:
    """A policy-blocked metrics tool must behave like the deterministic
    pipeline: disabled fallback evidence and the explicit uncertainty path,
    never a confident root cause from the incident snapshot."""
    with session_factory() as session:
        reseed_database(session)
        run = _make_run(session, run_id="run_agentic_metrics_blocked")
        client = FakeAgenticClient(
            [
                tool_call("query_revenue_metrics"),
                tool_call("fetch_account_details"),
                tool_call("fetch_support_tickets"),
                final_decision(
                    "The billing retry webhook regression suppressed second "
                    "charge attempts for the affected renewals."
                ),
            ]
        )

        report = run_investigation_workflow(
            session,
            run,
            llm_client=client,
            enabled_tool_ids={
                "fetch_account_details",
                "search_docs",
                "fetch_support_tickets",
            },
            blocked_reasons={"query_revenue_metrics": "scope_not_allowed"},
        )

        # Without revenue metric evidence no specific root cause is provable,
        # regardless of what the LLM claims.
        assert "does not prove a specific operational root cause" in (
            report.root_cause
        )
        # The blocked tool stays visible and carries the disabled fallback.
        steps = _steps(session, run.id)
        blocked = [
            step
            for step in steps
            if step.tool_name == "query_revenue_metrics"
            and step.status == "blocked"
        ]
        assert len(blocked) == 1
        assert blocked[0].blocked_reason == "scope_not_allowed"
        assert blocked[0].outputs.get("tool_disabled") is True
        # The report cites the tool-disabled evidence row.
        kinds = {evidence.kind for evidence in report.cited_evidence}
        assert "tool" in kinds


def test_unrequested_enabled_tool_is_recorded_as_neutral_skip(
    session_factory: Callable[[], Session],
) -> None:
    """Tools the agent chose not to call stay visible but do not pollute the
    blocked-step contract (blocked means a policy violation)."""
    with session_factory() as session:
        reseed_database(session)
        run = _make_run(session, run_id="run_agentic_skip")
        client = FakeAgenticClient(
            [
                tool_call("query_revenue_metrics"),
                final_decision(
                    "The retry webhook regression suppressed charge attempts.",
                    confidence="low",
                ),
            ]
        )

        run_investigation_workflow(session, run, llm_client=client)

        steps = _steps(session, run.id)
        skipped = [
            step
            for step in steps
            if step.inputs.get("source") == "agent_finalized_without_request"
        ]
        assert {step.tool_name for step in skipped} == {
            "fetch_account_details",
            "search_docs",
            "fetch_support_tickets",
        }
        # Neutral steps are not blocked: the blocked-step count stays zero.
        assert all(step.status != "blocked" for step in skipped)
        assert all(step.outputs.get("skipped_by_agent") for step in skipped)
        assert not [
            step for step in steps if step.status == "blocked"
        ]
