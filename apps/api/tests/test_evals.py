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
from app.evals.runner import (
    list_latest_eval_results,
    run_eval_suite,
    score_action_safety,
    score_root_cause,
)
from app.main import app
from app.models import AgentRun, EvalCase, EvalResult
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

        assert len(cases) == 5
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

        assert summary.total_scenarios == 5
        assert summary.passed_scenarios >= 4
        assert len(summary.results) == 5
        assert all(result.status in {"passed", "failed"} for result in summary.results)
        assert all(0 <= result.root_cause_score <= 1 for result in summary.results)
        assert all(0 <= result.citation_quality_score <= 1 for result in summary.results)
        assert all(0 <= result.action_safety_score <= 1 for result in summary.results)
        assert all(result.action_safety_score == 1 for result in summary.results)
        assert all(result.latency_ms >= 0 for result in summary.results)

        persisted = session.scalars(
            select(EvalResult).order_by(EvalResult.created_at, EvalResult.id)
        ).all()
        assert len(persisted) == 5
        assert {result.eval_run_id for result in persisted} == {summary.eval_run_id}
        assert all(result.example_output["root_cause"] for result in persisted)
        assert all(result.example_output["action_statuses"] for result in persisted)
        assert all(result.failure_reasons is not None for result in persisted)

        run_ids = [result.agent_run_id for result in persisted]
        runs = session.scalars(select(AgentRun).where(AgentRun.id.in_(run_ids))).all()
        assert len(runs) == 5
        assert all(run.trace_id for run in runs)
        assert all(run.trace_url for run in runs)
        assert all(run.trace_provider in {"langfuse", "langsmith", "local"} for run in runs)


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
        assert len(latest.results) == 5


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
        assert summary.passed_scenarios == 5


def test_eval_api_runs_suite_and_lists_latest_results(
    session_factory: Callable[[], Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with session_factory() as session:
        reseed_database(session)

    def override_get_db() -> Generator[Session, None, None]:
        with session_factory() as db:
            yield db

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
        results_response = client.get("/evals/results")
    finally:
        app.dependency_overrides.clear()
        get_settings.cache_clear()

    assert disabled_response.status_code == 403
    assert missing_token_response.status_code == 403
    assert run_response.status_code == 201
    run_payload = run_response.json()
    assert run_payload["total_scenarios"] == 5
    assert run_payload["passed_scenarios"] >= 4
    assert len(run_payload["results"]) == 5

    assert results_response.status_code == 200
    results_payload = results_response.json()
    assert results_payload["latest_eval_run_id"] == run_payload["eval_run_id"]
    assert len(results_payload["results"]) == 5
    assert all("root_cause_score" in result for result in results_payload["results"])
