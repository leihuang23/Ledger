from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import datetime
from time import perf_counter
from typing import Any
from uuid import uuid4

from fastapi.encoders import jsonable_encoder
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.agent.persistence import utcnow_naive
from app.agent.schemas import AgentRunDetail
from app.agent.service import start_investigation_run
from app.db.session import SessionLocal
from app.evals.schemas import EvalResultsReport, EvalResultRead, EvalRunSummary
from app.models import EvalCase, EvalResult

PASSING_SCENARIO_THRESHOLD = 4
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


def run_eval_suite(session: Session) -> EvalRunSummary:
    cases = session.scalars(select(EvalCase).order_by(EvalCase.id)).all()
    started_at = utcnow_naive()
    eval_run_id = f"evalrun_{uuid4().hex[:16]}"
    pending_results: list[EvalResult] = []

    for case in cases:
        case_start = utcnow_naive()
        latency_start = perf_counter()
        run_detail, _created = start_investigation_run(
            session,
            case.incident_id,
            force=True,
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
        )
        pending_results.append(result)

    session.add_all(pending_results)
    session.commit()
    results: list[EvalResultRead] = []
    for result in pending_results:
        session.refresh(result)
        results.append(eval_result_to_read(result))

    completed_at = utcnow_naive()
    passed_scenarios = sum(result.passed for result in results)
    failed_scenarios = len(results) - passed_scenarios
    return EvalRunSummary(
        eval_run_id=eval_run_id,
        status="passed"
        if passed_scenarios >= PASSING_SCENARIO_THRESHOLD
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
        .where(EvalResult.eval_run_id == latest_eval_run_id)
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


def _build_eval_result(
    *,
    eval_run_id: str,
    case: EvalCase,
    run_detail: AgentRunDetail,
    latency_ms: int,
    started_at: datetime,
    completed_at: datetime,
) -> EvalResult:
    scores = score_eval_case(case, run_detail)
    status = "passed" if scores["passed"] else "failed"
    now = utcnow_naive()
    result = EvalResult(
        id=f"evalres_{uuid4().hex[:16]}",
        eval_run_id=eval_run_id,
        eval_case_id=case.id,
        agent_run_id=run_detail.id,
        scenario=case.scenario,
        status=status,
        passed=bool(scores["passed"]),
        root_cause_score=float(scores["root_cause_score"]),
        citation_quality_score=float(scores["citation_quality_score"]),
        action_safety_score=float(scores["action_safety_score"]),
        latency_ms=latency_ms,
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
        scenario=result.scenario,
        status=result.status,
        passed=result.passed,
        root_cause_score=result.root_cause_score,
        citation_quality_score=result.citation_quality_score,
        action_safety_score=result.action_safety_score,
        latency_ms=result.latency_ms,
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
