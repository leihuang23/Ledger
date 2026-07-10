from __future__ import annotations

from collections.abc import Callable, Generator
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

import app.models  # noqa: F401
from app.core.config import get_settings
from app.db.base import Base
from app.db.session import get_db
from app.agent.service import (
    _invalidate_run_detail_cache,
    create_investigation_run,
    start_investigation_run,
)
from app.evals.runner import (
    list_latest_eval_results,
    run_eval_suite,
    score_action_safety,
    score_root_cause,
)
from app.main import app
from app.agents.service import (
    DEFAULT_AGENT_ID,
    DEFAULT_AGENT_VERSION_ID,
    PHASE6_AGENT_VERSION_ID,
)
from app.models import AgentRun, AgentVersion, EvalCase, EvalResult
from app.seed import SCENARIOS, reseed_database


@pytest.fixture()
def session_factory(tmp_path) -> Generator[Callable[[], Session], None, None]:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'evals_test.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    yield TestingSessionLocal

    Base.metadata.drop_all(engine)
    engine.dispose()


def test_seeded_eval_cases_cover_every_incident_scenario(
    session_factory: Callable[[], Session],
) -> None:
    with session_factory() as session:
        reseed_database(session)

        cases = session.scalars(select(EvalCase).order_by(EvalCase.id)).all()

        assert len(cases) == 6
        assert {case.scenario for case in cases} == set(SCENARIOS)
        assert all(case.incident_id for case in cases)
        assert all(case.expected_root_cause for case in cases)
        assert all(case.expected_evidence_types for case in cases)
        assert {case.scenario for case in cases if "sql" in case.expected_evidence_types} >= {
            "checkout_retry_regression",
            "payment_method_expiration",
        }


def test_eval_suite_persists_scoring_shape_and_trace_links(
    session_factory: Callable[[], Session],
) -> None:
    with session_factory() as session:
        reseed_database(session)

        summary = run_eval_suite(session)

        assert summary.total_scenarios == 6
        assert summary.passed_scenarios >= 4
        assert {result.agent_version_id for result in summary.results} == {
            DEFAULT_AGENT_VERSION_ID
        }
        assert len(summary.results) == 6
        assert all(result.status in {"passed", "failed"} for result in summary.results)
        assert all(0 <= result.root_cause_score <= 1 for result in summary.results)
        assert all(0 <= result.citation_quality_score <= 1 for result in summary.results)
        assert all(0 <= result.action_safety_score <= 1 for result in summary.results)
        assert all(result.action_safety_score == 1 for result in summary.results)
        assert all(result.latency_ms >= 0 for result in summary.results)

        persisted = session.scalars(
            select(EvalResult).order_by(EvalResult.created_at, EvalResult.id)
        ).all()
        assert len(persisted) == 6
        assert {result.eval_run_id for result in persisted} == {summary.eval_run_id}
        assert all(result.example_output["root_cause"] for result in persisted)
        assert all(result.example_output["action_statuses"] for result in persisted)
        assert all(result.failure_reasons is not None for result in persisted)

        run_ids = [result.agent_run_id for result in persisted]
        runs = session.scalars(select(AgentRun).where(AgentRun.id.in_(run_ids))).all()
        assert len(runs) == 6
        assert all(run.trace_id for run in runs)
        assert all(run.trace_url for run in runs)
        assert all(run.trace_provider in {"langfuse", "langsmith", "local"} for run in runs)


def test_legacy_eval_suite_is_pinned_to_project1_v1(
    session_factory: Callable[[], Session],
) -> None:
    with session_factory() as session:
        reseed_database(session)
        phase6 = session.get(AgentVersion, PHASE6_AGENT_VERSION_ID)
        assert phase6 is not None
        session.add(
            AgentVersion(
                id="newer-review-candidate",
                agent_id=DEFAULT_AGENT_ID,
                version_number=99,
                semantic_version="99.0.0",
                status="published",
                system_prompt=phase6.system_prompt,
                model=phase6.model,
                temperature=phase6.temperature,
                max_tokens=phase6.max_tokens,
                enabled_tool_ids=[],
                allowed_scopes=[],
                published_at=phase6.published_at,
                published_by="test",
                forked_from_version_id=phase6.id,
                created_at=phase6.created_at,
                updated_at=phase6.updated_at,
            )
        )
        session.commit()

        summary = run_eval_suite(session)

    assert {result.agent_version_id for result in summary.results} == {
        DEFAULT_AGENT_VERSION_ID
    }


def test_mrr_drop_suite_explicit_project1_v1_meets_release_threshold(
    session_factory: Callable[[], Session],
) -> None:
    with session_factory() as session:
        reseed_database(session)

        summary = run_eval_suite(
            session,
            dataset_id="mrr-drop-suite",
            agent_version_id=DEFAULT_AGENT_VERSION_ID,
        )

    assert summary.total_scenarios == 6
    assert summary.passed_scenarios >= 4
    assert {result.agent_version_id for result in summary.results} == {
        DEFAULT_AGENT_VERSION_ID
    }


def test_latest_eval_results_ignore_incomplete_runs(
    session_factory: Callable[[], Session],
) -> None:
    with session_factory() as session:
        reseed_database(session)
        summary = run_eval_suite(session)
        completed_result = session.scalar(select(EvalResult).limit(1))
        assert completed_result is not None

        partial_result = EvalResult(
            id="evalres_partial_latest",
            eval_run_id="evalrun_partial_latest",
            eval_case_id=completed_result.eval_case_id,
            agent_run_id=completed_result.agent_run_id,
            scenario=completed_result.scenario,
            status=completed_result.status,
            passed=completed_result.passed,
            root_cause_score=completed_result.root_cause_score,
            citation_quality_score=completed_result.citation_quality_score,
            action_safety_score=completed_result.action_safety_score,
            latency_ms=completed_result.latency_ms,
            expected_root_cause=completed_result.expected_root_cause,
            actual_root_cause=completed_result.actual_root_cause,
            expected_evidence_types=completed_result.expected_evidence_types,
            observed_evidence_types=completed_result.observed_evidence_types,
            failure_reasons=completed_result.failure_reasons,
            example_output=completed_result.example_output,
            started_at=completed_result.started_at,
            completed_at=completed_result.completed_at,
            created_at=completed_result.created_at + timedelta(seconds=10),
        )
        session.add(partial_result)
        session.commit()

        latest = list_latest_eval_results(session)

        assert latest.latest_eval_run_id == summary.eval_run_id
        assert len(latest.results) == 6


def test_latest_legacy_eval_results_ignore_phase5_dataset_runs(
    session_factory: Callable[[], Session],
) -> None:
    with session_factory() as session:
        reseed_database(session)
        legacy = run_eval_suite(session, eval_run_id="evalrun_legacy_contract")
        run_eval_suite(
            session,
            eval_run_id="evalrun_phase5_newer",
            dataset_id="mrr-drop-suite",
            agent_version_id="revenue-ops-agent_phase6_degraded",
        )

        latest = list_latest_eval_results(session)

    assert latest.latest_eval_run_id == legacy.eval_run_id
    assert len(latest.results) == 6
    assert all(result.dataset_id is None for result in latest.results)


def test_force_launch_reuses_waiting_run_blocking_same_incident(
    session_factory: Callable[[], Session],
) -> None:
    with session_factory() as session:
        reseed_database(session)
        case = session.scalar(select(EvalCase).order_by(EvalCase.id))
        assert case is not None
        original, created = create_investigation_run(
            session,
            case.incident_id,
            force=True,
        )
        assert created is True
        run = session.get(AgentRun, original.id)
        assert run is not None
        run.status = "waiting_for_approval"
        session.commit()
        _invalidate_run_detail_cache(run.id)

        reused, created = create_investigation_run(
            session,
            case.incident_id,
            force=True,
        )

    assert created is False
    assert reused.id == original.id
    assert reused.status == "waiting_for_approval"


def test_action_safety_fails_when_expected_actions_are_missing() -> None:
    assert score_action_safety([], expected_actions_required=True) == 0.0


def test_root_cause_score_rejects_non_exact_or_contradictory_wording() -> None:
    assert (
        score_root_cause(
            "Expired payment methods were not refreshed before renewal.",
            "Payment methods were refreshed before renewal.",
        )
        == 0.0
    )
    assert (
        score_root_cause(
            "Billing retry webhook regression suppressed second charge attempts.",
            "Billing retry webhook regression suppressed second charge attempts.",
        )
        == 1.0
    )


def test_payment_method_expiration_eval_identifies_card_expiration(
    session_factory: Callable[[], Session],
) -> None:
    with session_factory() as session:
        reseed_database(session)

        summary = run_eval_suite(session)

        payment_result = next(
            result
            for result in summary.results
            if result.scenario == "payment_method_expiration"
        )
        assert payment_result.passed is True
        assert payment_result.actual_root_cause == (
            "Expired payment methods were not refreshed before renewal."
        )
        persisted_payment_result = session.scalar(
            select(EvalResult).where(EvalResult.scenario == "payment_method_expiration")
        )
        assert persisted_payment_result is not None
        payment_run = session.get(AgentRun, persisted_payment_result.agent_run_id)
        assert payment_run is not None
        assert payment_run.final_report is not None
        failed_invoice_citation = next(
            evidence
            for evidence in payment_run.final_report["cited_evidence"]
            if evidence["reference_id"].endswith(":failed-renewals")
        )
        failed_rows = failed_invoice_citation["citation"]["rows"]
        assert failed_rows
        assert all(
            any("Expired cards" in reason for reason in row["failure_reasons"])
            for row in failed_rows
        )
        assert not any(
            any("Retry webhook" in reason for reason in row["failure_reasons"])
            for row in failed_rows
        )
        assert summary.passed_scenarios >= 5


def test_unknown_root_cause_eval_identifies_ambiguity(
    session_factory: Callable[[], Session],
) -> None:
    """The 6th scenario exercises the agent's ambiguity path: the evidence
    should not match any specific root cause, so the agent must report
    uncertainty rather than hallucinate a diagnosis (audit §2 caveat 2)."""
    with session_factory() as session:
        reseed_database(session)

        summary = run_eval_suite(session)

        unknown_result = next(
            result
            for result in summary.results
            if result.scenario == "unknown_root_cause"
        )
        assert unknown_result.passed is True
        assert unknown_result.actual_root_cause is not None
        assert "does not prove a specific operational root cause" in (
            unknown_result.actual_root_cause
        )
        assert unknown_result.root_cause_score == 1.0


def test_eval_api_runs_suite_and_lists_latest_results(
    session_factory: Callable[[], Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with session_factory() as session:
        reseed_database(session)

    def override_get_db() -> Generator[Session, None, None]:
        with session_factory() as db:
            yield db

    def enqueue_with_test_session(eval_run_id: str) -> None:
        with session_factory() as db:
            run_eval_suite(db, eval_run_id=eval_run_id)

    monkeypatch.setattr(
        "app.evals.router._enqueue_eval_suite",
        enqueue_with_test_session,
    )

    app.dependency_overrides[get_db] = override_get_db
    get_settings.cache_clear()
    client = TestClient(app)
    try:
        disabled_response = client.post("/evals/run")
        monkeypatch.setenv("EVAL_RUN_TOKEN", "eval-token")
        get_settings.cache_clear()
        missing_token_response = client.post("/evals/run")
        run_response = client.post(
            "/evals/run",
            headers={"X-Eval-Run-Token": "eval-token"},
        )
        run_payload = run_response.json()
        status_response = client.get(f"/evals/runs/{run_payload['eval_run_id']}")
        unknown_response = client.get("/evals/runs/evalrun_unknown")
        results_response = client.get("/evals/results")
    finally:
        app.dependency_overrides.clear()
        get_settings.cache_clear()

    assert disabled_response.status_code == 403
    assert missing_token_response.status_code == 403
    # The run endpoint enqueues a Celery task and returns 202 immediately with
    # a "running" stub; the actual results are polled via the status endpoint.
    assert run_response.status_code == 202
    assert run_payload["status"] == "running"
    assert run_payload["completed_at"] is None
    assert run_payload["eval_run_id"]

    # With task_always_eager (test env) the monkeypatched enqueue runs the
    # suite synchronously, so the status endpoint already sees completed rows.
    assert status_response.status_code == 200
    status_payload = status_response.json()
    assert status_payload["status"] in {"passed", "failed"}
    assert status_payload["total_scenarios"] == 6
    assert status_payload["passed_scenarios"] >= 4
    assert len(status_payload["results"]) == 6
    assert status_payload["completed_at"] is not None

    # Unknown eval_run_id returns 404, not a "running" stub.
    assert unknown_response.status_code == 404

    assert results_response.status_code == 200
    results_payload = results_response.json()
    assert results_payload["latest_eval_run_id"] == run_payload["eval_run_id"]
    assert len(results_payload["results"]) == 6
    assert all("root_cause_score" in result for result in results_payload["results"])


def test_legacy_eval_api_uses_project1_v1_compatibility_baseline(
    session_factory: Callable[[], Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with session_factory() as session:
        reseed_database(session)
        phase6 = session.get(AgentVersion, PHASE6_AGENT_VERSION_ID)
        assert phase6 is not None
        phase6.allowed_scopes = [
            scope for scope in phase6.allowed_scopes if scope != "run_eval"
        ]
        session.commit()

    def override_get_db() -> Generator[Session, None, None]:
        with session_factory() as db:
            yield db

    monkeypatch.setenv("EVAL_RUN_TOKEN", "eval-token")
    monkeypatch.setattr("app.evals.router._enqueue_eval_suite", lambda _run_id: None)
    get_settings.cache_clear()
    app.dependency_overrides[get_db] = override_get_db
    try:
        response = TestClient(app).post(
            "/evals/run",
            headers={"X-Eval-Run-Token": "eval-token"},
        )
    finally:
        app.dependency_overrides.clear()
        get_settings.cache_clear()

    assert response.status_code == 202
    with session_factory() as session:
        assert session.scalar(select(AgentRun)) is None


def test_eval_suite_persists_failed_result_when_one_case_raises(
    session_factory: Callable[[], Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with session_factory() as session:
        reseed_database(session)
        cases = session.scalars(select(EvalCase).order_by(EvalCase.id)).all()
        failing_incident_id = cases[0].incident_id
        failing_scenario = cases[0].scenario
        total_cases = len(cases)

    original_start = start_investigation_run

    def flaky_start(
        session: Session,
        incident_id: str,
        *,
        force: bool = False,
        agent_version_id: str | None = None,
    ):
        if incident_id == failing_incident_id:
            raise RuntimeError("simulated investigation failure")
        return original_start(
            session,
            incident_id,
            force=force,
            agent_version_id=agent_version_id,
        )

    monkeypatch.setattr("app.evals.runner.start_investigation_run", flaky_start)

    with session_factory() as session:
        summary = run_eval_suite(session)

    # The suite must complete (not raise 500) and persist a result for every case.
    assert summary.total_scenarios == total_cases
    failed_results = [result for result in summary.results if not result.passed]
    assert len(failed_results) == 1
    failed = failed_results[0]
    assert failed.scenario == failing_scenario
    assert failed.actual_root_cause is None
    assert failed.root_cause_score == 0.0
    assert failed.failure_reasons
    assert any(
        "simulated investigation failure" in reason for reason in failed.failure_reasons
    )

    # The other cases must still be scored normally.
    assert len([result for result in summary.results if result.passed]) == total_cases - 1

    # Every result -- including the failed one -- must be persisted.
    with session_factory() as session:
        persisted = session.scalars(
            select(EvalResult).where(EvalResult.eval_run_id == summary.eval_run_id)
        ).all()
        assert len(persisted) == total_cases
        failed_persisted = next(
            result for result in persisted if result.scenario == failing_scenario
        )
        assert failed_persisted.passed is False
        assert failed_persisted.failure_reasons
        # The failed case must reference a recorded failed agent run so the dead
        # end is visible in run history, not silently dropped.
        failed_run = session.get(AgentRun, failed_persisted.agent_run_id)
        assert failed_run is not None
        assert failed_run.status == "failed"
        assert "simulated investigation failure" in (failed_run.error or "")


def test_build_eval_run_summary_marks_stale_partial_run_as_failed(
    session_factory: Callable[[], Session],
) -> None:
    """A partial eval run whose newest result is older than the staleness
    threshold must report 'failed', not 'running' forever.

    Covers the case where the Celery worker is hard-killed mid-suite (hard time
    limit, crash, OOM) and cannot append more results or run task-level cleanup.
    A task-level ``except`` cannot recover from a hard kill, so the summary must
    self-heal on read -- mirroring the agent-run orphan reaper for eval runs.
    """
    from app.agent.persistence import utcnow_naive
    from app.evals.runner import (
        EVAL_RUN_STALE_AFTER,
        _build_failed_eval_result,
        _record_failed_agent_run,
        build_eval_run_summary,
    )

    stale_at = utcnow_naive() - EVAL_RUN_STALE_AFTER - timedelta(seconds=60)
    eval_run_id = "evalrun_stale_partial"
    with session_factory() as session:
        reseed_database(session)
        cases = session.scalars(select(EvalCase).order_by(EvalCase.id)).all()
        # Persist results for only the first 3 of 6 cases, backdated past the
        # staleness threshold, simulating a worker that died mid-suite.
        for case in cases[:3]:
            failed_run = _record_failed_agent_run(
                session,
                incident_id=case.incident_id,
                error="simulated worker hard kill",
                started_at=stale_at,
                completed_at=stale_at,
            )
            session.flush()
            result = _build_failed_eval_result(
                eval_run_id=eval_run_id,
                case=case,
                failed_run=failed_run,
                error="simulated worker hard kill",
                latency_ms=0,
                started_at=stale_at,
                completed_at=stale_at,
            )
            result.created_at = stale_at
            session.add(result)
        session.commit()

        summary = build_eval_run_summary(session, eval_run_id)

    assert summary is not None
    assert summary.status == "failed"
    assert summary.total_scenarios == 3
    assert summary.completed_at is not None


def test_build_eval_run_summary_reports_running_for_fresh_partial_run(
    session_factory: Callable[[], Session],
) -> None:
    """A partial eval run with recent results is still in flight and must
    report 'running'. Regression guard so the staleness self-heal does not
    prematurely terminate a live suite."""
    from app.agent.persistence import utcnow_naive
    from app.evals.runner import (
        _build_failed_eval_result,
        _record_failed_agent_run,
        build_eval_run_summary,
    )

    fresh_at = utcnow_naive()
    eval_run_id = "evalrun_fresh_partial"
    with session_factory() as session:
        reseed_database(session)
        cases = session.scalars(select(EvalCase).order_by(EvalCase.id)).all()
        for case in cases[:3]:
            failed_run = _record_failed_agent_run(
                session,
                incident_id=case.incident_id,
                error="still in flight",
                started_at=fresh_at,
                completed_at=fresh_at,
            )
            session.flush()
            result = _build_failed_eval_result(
                eval_run_id=eval_run_id,
                case=case,
                failed_run=failed_run,
                error="still in flight",
                latency_ms=0,
                started_at=fresh_at,
                completed_at=fresh_at,
            )
            session.add(result)
        session.commit()

        summary = build_eval_run_summary(session, eval_run_id)

    assert summary is not None
    assert summary.status == "running"
    assert summary.completed_at is None


def test_build_eval_run_summary_complete_run_takes_precedence_over_stale(
    session_factory: Callable[[], Session],
) -> None:
    """A complete run (all expected results) whose newest result is older than
    the staleness threshold must still report its real passed/failed status --
    not be flipped to stale-failed. Pins the load-bearing branch ordering so a
    future refactor cannot silently corrupt historical eval results."""
    from app.agent.persistence import utcnow_naive
    from app.evals.runner import (
        EVAL_RUN_STALE_AFTER,
        build_eval_run_summary,
    )

    with session_factory() as session:
        reseed_database(session)
        summary = run_eval_suite(session)
        eval_run_id = summary.eval_run_id

        # Backdate every result past the staleness threshold. A complete run
        # must ignore staleness and report its real terminal status.
        stale_at = utcnow_naive() - EVAL_RUN_STALE_AFTER - timedelta(seconds=60)
        results = session.scalars(
            select(EvalResult).where(EvalResult.eval_run_id == eval_run_id)
        ).all()
        assert len(results) == 6
        for result in results:
            result.created_at = stale_at
        session.commit()

        reloaded = build_eval_run_summary(session, eval_run_id)

    assert reloaded is not None
    # A complete run reports its real status regardless of age.
    assert reloaded.status in {"passed", "failed"}
    assert reloaded.completed_at is not None
    assert reloaded.total_scenarios == 6
