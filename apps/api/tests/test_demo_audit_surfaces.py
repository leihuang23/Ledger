from __future__ import annotations

from collections.abc import Callable, Generator

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

import app.models  # noqa: F401
from app.core.config import get_settings
from app.db.base import Base
from app.models import (
    AgentRun,
    AgentRunStep,
    ApprovalRequest,
    EvalResult,
    MockAction,
)
from app.seed import reseed_database


@pytest.fixture()
def session_factory(tmp_path) -> Generator[Callable[[], Session], None, None]:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'demo_audit.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    yield TestingSessionLocal

    Base.metadata.drop_all(engine)
    engine.dispose()


def test_demo_seed_materializes_runs_approvals_and_eval_regressions(
    session_factory: Callable[[], Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A demo-environment seed makes /runs, /approvals, and /evals inspectable.

    The public demo is read-only (anonymous mutations return 403), so seeded
    audit surfaces are the only way a recruiter can inspect runs with ordered
    steps/citations/traces/costs, approval states, and eval regressions."""
    monkeypatch.setenv("APP_ENV", "demo")
    get_settings.cache_clear()
    try:
        with session_factory() as session:
            result = reseed_database(session)
            assert result.counts["agent_runs"] >= 7
            assert result.counts["agent_run_steps"] >= 7 * 7
            assert result.counts["eval_results"] >= 18
            assert result.counts["mock_actions"] >= 6
            assert result.counts["approval_requests"] >= 4
            assert result.counts["action_audit_events"] >= 6

            # Completed runs carry ordered steps, a local trace, token/cost
            # estimates, and a cited final report.
            anchor_run = session.get(AgentRun, "run_f5af975d8f27487f")
            assert anchor_run is not None
            assert anchor_run.status == "succeeded"
            assert anchor_run.trace_provider == "local"
            assert anchor_run.trace_url.startswith("local://agent-runs/")
            assert anchor_run.token_estimate > 0
            assert anchor_run.cost_estimate_usd > 0
            assert anchor_run.final_report is not None
            assert len(anchor_run.final_report["cited_evidence"]) >= 3
            assert anchor_run.final_report["claims"]
            steps = session.scalars(
                select(AgentRunStep)
                .where(AgentRunStep.run_id == anchor_run.id)
                .order_by(AgentRunStep.sequence)
            ).all()
            assert len(steps) == 8
            assert [step.sequence for step in steps] == list(range(1, 9))
            assert [step.status for step in steps] == ["succeeded"] * 8

            # A visible failure: one run fails at a tool step with an error.
            failed_run = session.get(AgentRun, "run_demo_failed_renewal_probe")
            assert failed_run is not None
            assert failed_run.status == "failed"
            assert failed_run.error
            failed_steps = session.scalars(
                select(AgentRunStep).where(AgentRunStep.run_id == failed_run.id)
            ).all()
            assert any(step.status == "failed" and step.error for step in failed_steps)
            # The failed tool step ends the investigation; later stages never run.
            assert len(failed_steps) == 3
            assert failed_steps[-1].status == "failed"

            # A visible blocked step on the degraded candidate (permission scope).
            degraded_run = session.get(AgentRun, "run_eval_demo_degraded")
            assert degraded_run is not None
            blocked_steps = session.scalars(
                select(AgentRunStep).where(
                    AgentRunStep.run_id == degraded_run.id,
                    AgentRunStep.status == "blocked",
                )
            ).all()
            assert blocked_steps
            assert blocked_steps[0].blocked_reason == "tool_not_enabled"

            # Approval states cover pending (default queue), approved, rejected.
            pending = session.scalars(
                select(ApprovalRequest).where(ApprovalRequest.status == "pending")
            ).all()
            approved = session.scalars(
                select(ApprovalRequest).where(ApprovalRequest.status == "approved")
            ).all()
            rejected = session.scalars(
                select(ApprovalRequest).where(ApprovalRequest.status == "rejected")
            ).all()
            assert pending
            assert approved
            assert rejected

            # Eval regression results: the good version passes every case and
            # the degraded version regresses on the document-dependent case.
            good = session.scalars(
                select(EvalResult).where(
                    EvalResult.eval_run_id == "evalrun_demo_phase6"
                )
            ).all()
            phase1 = session.scalars(
                select(EvalResult).where(
                    EvalResult.eval_run_id == "evalrun_demo_phase1"
                )
            ).all()
            degraded = session.scalars(
                select(EvalResult).where(
                    EvalResult.eval_run_id == "evalrun_demo_degraded"
                )
            ).all()
            assert len(good) == len(degraded) == len(phase1)
            assert all(result.passed for result in good)
            assert all(result.passed for result in phase1)
            assert sum(result.passed for result in degraded) == len(degraded) - 1
    finally:
        monkeypatch.delenv("APP_ENV", raising=False)
        get_settings.cache_clear()


def test_local_seed_keeps_audit_surfaces_empty(
    session_factory: Callable[[], Session],
) -> None:
    """Non-demo environments keep the current behavior: no seeded runs,
    approvals, or eval results, so existing tests relying on a clean seed
    remain green."""
    get_settings.cache_clear()
    with session_factory() as session:
        result = reseed_database(session)
        assert result.counts["agent_runs"] == 0
        assert result.counts["agent_run_steps"] == 0
        assert result.counts["eval_results"] == 0
        assert result.counts["approval_requests"] == 0
        assert result.counts["mock_actions"] == 0
        assert result.counts["action_audit_events"] == 0


def test_demo_audit_seed_is_idempotent(
    session_factory: Callable[[], Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Re-seeding a demo database keeps the audit surfaces byte-for-byte
    identical (no duplicate runs/approvals/eval results)."""
    monkeypatch.setenv("APP_ENV", "demo")
    get_settings.cache_clear()
    try:
        with session_factory() as session:
            first = reseed_database(session)
            second = reseed_database(session)
            assert second.counts == first.counts
            assert second.fingerprint == first.fingerprint
            assert (
                session.scalar(select(AgentRun.id).where(AgentRun.id == "run_f5af975d8f27487f"))
                == "run_f5af975d8f27487f"
            )
    finally:
        monkeypatch.delenv("APP_ENV", raising=False)
        get_settings.cache_clear()
