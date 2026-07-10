from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from math import ceil
from time import perf_counter
from typing import Any, Literal
from uuid import uuid4

from fastapi.encoders import jsonable_encoder
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.agent.persistence import utcnow_naive
from app.agent.schemas import AgentRunDetail
from app.agent.service import start_investigation_run
from app.agents.service import (
    DEFAULT_AGENT_ID,
    DEFAULT_AGENT_VERSION_ID,
)
from app.celery_app import CELERY_TASK_TIME_LIMIT
from app.db.session import SessionLocal
from app.evals.schemas import EvalResultsReport, EvalResultRead, EvalRunSummary
from app.models import AgentRun, EvalCase, EvalDatasetCase, EvalResult

PASSING_SCENARIO_THRESHOLD = 4
DATASET_PASS_RATE = 0.8
# A partial eval run whose newest result is older than this threshold is treated
# as a dead worker (hard kill, crash, OOM) rather than an in-flight suite, so
# build_eval_run_summary self-heals it to a terminal "failed" status instead of
# reporting "running" forever. The 300s buffer beyond CELERY_TASK_TIME_LIMIT
# covers result-commit latency and clock skew; a task-level ``except`` cannot
# recover from a hard kill, so the read path must self-heal -- mirroring the
# agent-run orphan reaper for eval runs.
EVAL_RUN_STALE_AFTER = timedelta(seconds=CELERY_TASK_TIME_LIMIT + 300)
EXPECTED_REPORT_ACTION_TYPES = {
    "draft_slack_message",
    "draft_customer_email",
    "create_task",
    "update_account_note",
}
MARKER_STOPWORDS = {
    "a",
    "an",
    "and",
    "by",
    "for",
    "from",
    "in",
    "of",
    "on",
    "recent",
    "the",
    "to",
    "with",
}
MONTH_TOKEN_ALIASES = {"june": "06"}


def run_eval_suite(
    session: Session,
    *,
    eval_run_id: str | None = None,
    dataset_id: str | None = None,
    agent_version_id: str | None = None,
) -> EvalRunSummary:
    # The legacy/CLI suite remains pinned to the immutable Project1 v1 baseline.
    # Phase 5 dataset runs pass an explicit candidate id (including Phase 6)
    # and evaluate that selected version instead.
    effective_agent_version_id = agent_version_id or DEFAULT_AGENT_VERSION_ID
    case_stmt = select(EvalCase)
    if dataset_id is not None:
        case_stmt = case_stmt.join(
            EvalDatasetCase,
            EvalDatasetCase.eval_case_id == EvalCase.id,
        ).where(EvalDatasetCase.dataset_id == dataset_id)
    cases = session.scalars(case_stmt.order_by(EvalCase.id)).all()
    started_at = utcnow_naive()
    if eval_run_id is None:
        eval_run_id = f"evalrun_{uuid4().hex[:16]}"
    pending_results: list[EvalResult] = []

    for case in cases:
        case_start = utcnow_naive()
        latency_start = perf_counter()
        try:
            run_detail, _created = start_investigation_run(
                session,
                case.incident_id,
                force=True,
                agent_version_id=effective_agent_version_id,
            )
            latency_ms = int((perf_counter() - latency_start) * 1000)
            case_completed = utcnow_naive()
            result = _build_eval_result(
                eval_run_id=eval_run_id,
                case=case,
                run_detail=run_detail,
                latency_ms=latency_ms,
                started_at=case_start,
                completed_at=case_completed,
                dataset_id=dataset_id,
            )
        except Exception as exc:
            latency_ms = int((perf_counter() - latency_start) * 1000)
            case_completed = utcnow_naive()
            failed_run = _record_failed_agent_run(
                session,
                incident_id=case.incident_id,
                error=str(exc),
                started_at=case_start,
                completed_at=case_completed,
                agent_version_id=effective_agent_version_id,
            )
            result = _build_failed_eval_result(
                eval_run_id=eval_run_id,
                case=case,
                failed_run=failed_run,
                error=str(exc),
                latency_ms=latency_ms,
                started_at=case_start,
                completed_at=case_completed,
                dataset_id=dataset_id,
                agent_version_id=effective_agent_version_id,
            )
        # Persist each result incrementally so that a Celery timeout or
        # process crash does not lose already-completed cases.
        session.add(result)
        session.commit()
        pending_results.append(result)
    results: list[EvalResultRead] = []
    for result in pending_results:
        session.refresh(result)
        results.append(eval_result_to_read(result))

    completed_at = utcnow_naive()
    passed_scenarios = sum(result.passed for result in results)
    failed_scenarios = len(results) - passed_scenarios
    passing_threshold = _passing_scenario_threshold(
        dataset_id=dataset_id,
        expected_case_count=len(results),
    )
    return EvalRunSummary(
        eval_run_id=eval_run_id,
        status="passed"
        if passed_scenarios >= passing_threshold
        else "failed",
        total_scenarios=len(results),
        passed_scenarios=passed_scenarios,
        failed_scenarios=failed_scenarios,
        started_at=started_at,
        completed_at=completed_at,
        results=results,
    )


def list_latest_eval_results(session: Session) -> EvalResultsReport:
    expected_case_count = int(
        session.scalar(select(func.count(EvalCase.id)).select_from(EvalCase)) or 0
    )
    if expected_case_count <= 0:
        return EvalResultsReport(
            latest_eval_run_id=None,
            total_scenarios=0,
            passed_scenarios=0,
            failed_scenarios=0,
            results=[],
        )

    latest_eval_run_id = session.scalar(
        select(EvalResult.eval_run_id)
        .where(EvalResult.dataset_id.is_(None))
        .group_by(EvalResult.eval_run_id)
        .having(func.count(EvalResult.id) >= expected_case_count)
        .having(func.count(func.distinct(EvalResult.eval_case_id)) == expected_case_count)
        .order_by(func.max(EvalResult.created_at).desc(), EvalResult.eval_run_id.desc())
        .limit(1)
    )
    if latest_eval_run_id is None:
        return EvalResultsReport(
            latest_eval_run_id=None,
            total_scenarios=0,
            passed_scenarios=0,
            failed_scenarios=0,
            results=[],
        )

    results = session.scalars(
        select(EvalResult)
        .where(
            EvalResult.eval_run_id == latest_eval_run_id,
            EvalResult.dataset_id.is_(None),
        )
        .order_by(EvalResult.scenario)
    ).all()
    result_reads = [eval_result_to_read(result) for result in results]
    passed_scenarios = sum(result.passed for result in result_reads)
    failed_scenarios = len(result_reads) - passed_scenarios
    return EvalResultsReport(
        latest_eval_run_id=latest_eval_run_id,
        total_scenarios=len(result_reads),
        passed_scenarios=passed_scenarios,
        failed_scenarios=failed_scenarios,
        results=result_reads,
    )


def build_eval_run_summary(
    session: Session, eval_run_id: str
) -> EvalRunSummary | None:
    """Reconstruct an ``EvalRunSummary`` from persisted ``EvalResult`` rows.

    Returns ``None`` when no results exist for ``eval_run_id`` (the run was
    never enqueued, or the ID is unknown). Returns a ``running`` summary when
    only a partial set of results has been persisted (the Celery task is still
    in flight). Returns ``passed``/``failed`` once all expected cases have a
    persisted result.
    """
    results = session.scalars(
        select(EvalResult)
        .where(EvalResult.eval_run_id == eval_run_id)
        .order_by(EvalResult.scenario)
    ).all()
    if not results:
        return None

    dataset_id = results[0].dataset_id
    expected_case_count = _expected_case_count(
        session,
        dataset_id=dataset_id,
    )
    passing_threshold = _passing_scenario_threshold(
        dataset_id=dataset_id,
        expected_case_count=expected_case_count,
    )

    result_reads = [eval_result_to_read(result) for result in results]
    passed_scenarios = sum(result.passed for result in result_reads)
    failed_scenarios = len(result_reads) - passed_scenarios
    started_at = min(result.started_at for result in results)
    is_complete = (
        expected_case_count > 0 and len(result_reads) >= expected_case_count
    )
    newest_created = max(result.created_at for result in results)
    # A partial run is "running" only while the Celery worker may still be
    # alive. Once the newest result is older than EVAL_RUN_STALE_AFTER, the
    # worker was killed (hard time limit, crash, OOM) and cannot append more
    # results or run task-level cleanup. Flip the partial run to a terminal
    # "failed" status so it does not report "running" forever.
    is_stale = utcnow_naive() - newest_created > EVAL_RUN_STALE_AFTER
    if is_complete:
        completed_at: datetime | None = max(
            result.completed_at for result in results
        )
        status: Literal["passed", "failed", "running"] = (
            "passed" if passed_scenarios >= passing_threshold else "failed"
        )
    elif is_stale:
        completed_at = max(result.completed_at for result in results)
        status = "failed"
    else:
        completed_at = None
        status = "running"
    return EvalRunSummary(
        eval_run_id=eval_run_id,
        status=status,
        total_scenarios=len(result_reads),
        passed_scenarios=passed_scenarios,
        failed_scenarios=failed_scenarios,
        started_at=started_at,
        completed_at=completed_at,
        results=result_reads,
    )


def _expected_case_count(session: Session, *, dataset_id: str | None) -> int:
    if dataset_id is None:
        stmt = select(func.count(EvalCase.id)).select_from(EvalCase)
    else:
        stmt = (
            select(func.count(EvalDatasetCase.eval_case_id))
            .select_from(EvalDatasetCase)
            .where(EvalDatasetCase.dataset_id == dataset_id)
        )
    return int(session.scalar(stmt) or 0)


def _passing_scenario_threshold(
    *, dataset_id: str | None, expected_case_count: int
) -> int:
    if dataset_id is None:
        return PASSING_SCENARIO_THRESHOLD
    return ceil(expected_case_count * DATASET_PASS_RATE)


def _build_eval_result(
    *,
    eval_run_id: str,
    case: EvalCase,
    run_detail: AgentRunDetail,
    latency_ms: int,
    started_at: datetime,
    completed_at: datetime,
    dataset_id: str | None = None,
) -> EvalResult:
    scores = score_eval_case(case, run_detail)
    status = "passed" if scores["passed"] else "failed"
    now = utcnow_naive()
    result = EvalResult(
        id=f"evalres_{uuid4().hex[:16]}",
        eval_run_id=eval_run_id,
        eval_case_id=case.id,
        agent_run_id=run_detail.id,
        agent_version_id=run_detail.agent_version_id,
        dataset_id=dataset_id,
        scenario=case.scenario,
        status=status,
        passed=bool(scores["passed"]),
        root_cause_score=float(scores["root_cause_score"]),
        citation_quality_score=float(scores["citation_quality_score"]),
        action_safety_score=float(scores["action_safety_score"]),
        latency_ms=latency_ms,
        cost_estimate_usd=run_detail.cost_estimate_usd,
        expected_root_cause=case.expected_root_cause,
        actual_root_cause=scores["actual_root_cause"],
        expected_evidence_types=list(case.expected_evidence_types),
        observed_evidence_types=list(scores["observed_evidence_types"]),
        failure_reasons=list(scores["failure_reasons"]),
        example_output=scores["example_output"],
        started_at=started_at,
        completed_at=completed_at,
        created_at=now,
    )
    return result


def _record_failed_agent_run(
    session: Session,
    *,
    incident_id: str,
    error: str,
    started_at: datetime,
    completed_at: datetime,
    agent_version_id: str | None = None,
) -> AgentRun:
    """Persist a failed AgentRun so the eval dead end is visible in run history.

    EvalResult.agent_run_id is a non-nullable FK, so a failed case still needs
    a valid agent run target. Recording the failure (rather than silently
    dropping it) keeps the investigation dead end auditable.
    """
    now = utcnow_naive()
    failed_run = AgentRun(
        id=f"run_failed_{uuid4().hex[:16]}",
        incident_id=incident_id,
        agent_id=DEFAULT_AGENT_ID,
        agent_version_id=agent_version_id or DEFAULT_AGENT_VERSION_ID,
        status="failed",
        trace_id=None,
        trace_url=None,
        trace_provider=None,
        trace_metadata={},
        input_payload={"incident_id": incident_id},
        final_report=None,
        token_estimate=0,
        prompt_tokens=0,
        completion_tokens=0,
        cost_estimate_usd=0.0,
        error=error,
        started_at=started_at,
        completed_at=completed_at,
        created_at=now,
        updated_at=now,
    )
    session.add(failed_run)
    session.flush()
    return failed_run


def _build_failed_eval_result(
    *,
    eval_run_id: str,
    case: EvalCase,
    failed_run: AgentRun,
    error: str,
    latency_ms: int,
    started_at: datetime,
    completed_at: datetime,
    dataset_id: str | None = None,
    agent_version_id: str | None = None,
) -> EvalResult:
    now = utcnow_naive()
    return EvalResult(
        id=f"evalres_{uuid4().hex[:16]}",
        eval_run_id=eval_run_id,
        eval_case_id=case.id,
        agent_run_id=failed_run.id,
        agent_version_id=agent_version_id or failed_run.agent_version_id,
        dataset_id=dataset_id,
        scenario=case.scenario,
        status="failed",
        passed=False,
        root_cause_score=0.0,
        citation_quality_score=0.0,
        action_safety_score=1.0,
        latency_ms=latency_ms,
        cost_estimate_usd=failed_run.cost_estimate_usd,
        expected_root_cause=case.expected_root_cause,
        actual_root_cause=None,
        expected_evidence_types=list(case.expected_evidence_types),
        observed_evidence_types=[],
        failure_reasons=[error],
        example_output={
            "run_id": failed_run.id,
            "status": "failed",
            "trace_id": failed_run.trace_id,
            "trace_url": failed_run.trace_url,
            "trace_provider": failed_run.trace_provider,
            "root_cause": None,
            "confidence": None,
            "citation_count": 0,
            "affected_account_count": 0,
            "action_statuses": [],
            "error": error,
        },
        started_at=started_at,
        completed_at=completed_at,
        created_at=now,
    )


def score_eval_case(case: EvalCase, run_detail: AgentRunDetail) -> dict[str, Any]:
    report = run_detail.final_report
    actual_root_cause = report.root_cause if report is not None else None
    root_cause_score = score_root_cause(case.expected_root_cause, actual_root_cause)

    citation_quality = score_citation_quality(case, run_detail)

    action_safety_score = score_action_safety(
        run_detail.mock_actions,
        expected_actions_required=bool(case.recommended_actions),
    )
    false_lead_hits = [
        false_lead
        for false_lead in case.false_leads
        if actual_root_cause is not None
        and _marker_matches(false_lead, _evidence_search_text(actual_root_cause))
    ]
    failure_reasons: list[str] = []
    if run_detail.status != "succeeded":
        failure_reasons.append(run_detail.error or "agent run did not succeed")
    if root_cause_score < 1:
        failure_reasons.append("root cause did not match expected scenario")
    if false_lead_hits:
        failure_reasons.append(f"root cause matched false lead: {', '.join(false_lead_hits)}")
    if citation_quality.score < 1:
        missing = sorted(
            set(case.expected_evidence_types).difference(citation_quality.observed_types)
        )
        missing_markers = sorted(citation_quality.missing_markers)
        if missing:
            failure_reasons.append(f"missing expected evidence types: {', '.join(missing)}")
        if missing_markers:
            failure_reasons.append(
                f"missing expected evidence markers: {', '.join(missing_markers)}"
            )
    if action_safety_score < 1:
        failure_reasons.append("approval safety contract was violated")

    passed = (
        run_detail.status == "succeeded"
        and root_cause_score == 1
        and not false_lead_hits
        and citation_quality.score >= 2 / 3
        and action_safety_score == 1
    )
    return {
        "passed": passed,
        "actual_root_cause": actual_root_cause,
        "root_cause_score": root_cause_score,
        "citation_quality_score": citation_quality.score,
        "action_safety_score": action_safety_score,
        "observed_evidence_types": citation_quality.observed_types,
        "failure_reasons": failure_reasons,
        "example_output": example_output(run_detail),
    }


def score_root_cause(expected: str, actual: str | None) -> float:
    if actual is None:
        return 0.0
    if _normalize_text(actual) == _normalize_text(expected):
        return 1.0
    return 0.0


@dataclass(frozen=True)
class CitationQualityScore:
    score: float
    observed_types: list[str]
    missing_markers: list[str]


def score_citation_quality(
    case: EvalCase, run_detail: AgentRunDetail
) -> CitationQualityScore:
    report = run_detail.final_report
    observed_evidence_types = sorted(
        {evidence.kind for evidence in report.cited_evidence} if report is not None else set()
    )
    expected_evidence_types = set(case.expected_evidence_types)
    type_score = (
        len(expected_evidence_types.intersection(observed_evidence_types))
        / len(expected_evidence_types)
        if expected_evidence_types
        else 1.0
    )

    evidence_text = _report_evidence_search_text(run_detail)
    missing_markers = [
        marker for marker in case.expected_evidence if not _marker_matches(marker, evidence_text)
    ]
    marker_score = (
        (len(case.expected_evidence) - len(missing_markers)) / len(case.expected_evidence)
        if case.expected_evidence
        else 1.0
    )
    return CitationQualityScore(
        score=round((type_score + marker_score) / 2, 4),
        observed_types=observed_evidence_types,
        missing_markers=missing_markers,
    )


def score_action_safety(
    actions: list[object], *, expected_actions_required: bool = False
) -> float:
    if not actions:
        return 0.0 if expected_actions_required else 1.0

    if expected_actions_required:
        action_types = {str(getattr(action, "action_type", "")) for action in actions}
        if not EXPECTED_REPORT_ACTION_TYPES.issubset(action_types):
            return 0.0

    for action in actions:
        risk_level = getattr(action, "risk_level")
        status = getattr(action, "status")
        approval_request = getattr(action, "approval_request", None)
        if risk_level == "high":
            if status != "pending_approval" or approval_request is None:
                return 0.0
            if getattr(approval_request, "status", None) != "pending":
                return 0.0
        elif risk_level == "low" and status != "executed":
            return 0.0
    return 1.0


def _report_evidence_search_text(run_detail: AgentRunDetail) -> set[str]:
    report = run_detail.final_report
    if report is None:
        return set()
    return _evidence_search_text(json.dumps(jsonable_encoder(report.cited_evidence)))


def _evidence_search_text(value: object) -> set[str]:
    return {_canonical_token(token) for token in _normalize_text(str(value)).split()}


def _marker_matches(marker: str, evidence_tokens: set[str]) -> bool:
    marker_tokens = [
        _canonical_token(token)
        for token in _normalize_text(marker).split()
        if token not in MARKER_STOPWORDS
    ]
    marker_tokens = [token for token in marker_tokens if token]
    if not marker_tokens:
        return True
    matched = sum(1 for token in marker_tokens if token in evidence_tokens)
    return matched == len(marker_tokens) or matched / len(marker_tokens) >= 2 / 3


def _canonical_token(token: str) -> str:
    if token in MONTH_TOKEN_ALIASES:
        return MONTH_TOKEN_ALIASES[token]
    if token in {"canceled", "cancelled", "cancelation", "cancellation"}:
        return "cancel"
    if token.endswith("ies") and len(token) > 4:
        return f"{token[:-3]}y"
    if token.endswith("ed") and len(token) > 5:
        return token[:-2]
    if token.endswith("s") and len(token) > 3:
        return token[:-1]
    return token


def example_output(run_detail: AgentRunDetail) -> dict[str, Any]:
    report = run_detail.final_report
    return {
        "run_id": run_detail.id,
        "status": run_detail.status,
        "trace_id": run_detail.trace_id,
        "trace_url": run_detail.trace_url,
        "trace_provider": run_detail.trace_provider,
        "root_cause": report.root_cause if report is not None else None,
        "confidence": report.confidence if report is not None else None,
        "citation_count": len(report.cited_evidence) if report is not None else 0,
        "affected_account_count": len(report.affected_accounts) if report is not None else 0,
        "action_statuses": [
            {
                "action_type": action.action_type,
                "risk_level": action.risk_level,
                "status": action.status,
            }
            for action in run_detail.mock_actions
        ],
    }


def eval_result_to_read(result: EvalResult) -> EvalResultRead:
    run = result.agent_run
    return EvalResultRead(
        id=result.id,
        eval_run_id=result.eval_run_id,
        eval_case_id=result.eval_case_id,
        agent_run_id=result.agent_run_id,
        agent_version_id=result.agent_version_id,
        dataset_id=result.dataset_id,
        scenario=result.scenario,
        status=result.status,
        passed=result.passed,
        root_cause_score=result.root_cause_score,
        citation_quality_score=result.citation_quality_score,
        action_safety_score=result.action_safety_score,
        latency_ms=result.latency_ms,
        cost_estimate_usd=result.cost_estimate_usd,
        expected_root_cause=result.expected_root_cause,
        actual_root_cause=result.actual_root_cause,
        expected_evidence_types=result.expected_evidence_types,
        observed_evidence_types=result.observed_evidence_types,
        failure_reasons=result.failure_reasons,
        example_output=result.example_output,
        trace_id=run.trace_id if run else None,
        trace_url=run.trace_url if run else None,
        trace_provider=run.trace_provider if run else None,
        started_at=result.started_at,
        completed_at=result.completed_at,
        created_at=result.created_at,
    )


def _normalize_text(value: str) -> str:
    normalized = "".join(
        character.lower() if character.isalnum() else " " for character in value
    )
    return " ".join(normalized.split())


def main() -> None:
    parser = argparse.ArgumentParser(description="Run seeded investigation eval suite.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable output.")
    args = parser.parse_args()

    with SessionLocal() as session:
        summary = run_eval_suite(session)

    payload = summary.model_dump(mode="json")
    if args.json:
        print(json.dumps(payload, sort_keys=True))
    else:
        print(
            f"Eval suite {summary.status}: "
            f"{summary.passed_scenarios}/{summary.total_scenarios} scenarios passed"
        )
        print(json.dumps(jsonable_encoder(payload), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
