from __future__ import annotations

import json
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent.persistence import utcnow_naive
from app.agent.schemas import AgentRunDetail, AgentRunStepRead, InvestigationReport
from app.agent.workflow import run_investigation_workflow
from app.approvals.service import list_mock_actions_for_run, propose_actions_for_report
from app.models import AgentRun, AgentRunStep, Incident


def start_investigation_run(
    session: Session, incident_id: str, *, force: bool = False
) -> tuple[AgentRunDetail, bool]:
    incident = session.get(Incident, incident_id)
    if incident is None:
        raise LookupError(f"Unknown incident id: {incident_id}")

    if not force:
        existing_run_id = session.scalar(
            select(AgentRun.id)
            .where(
                AgentRun.incident_id == incident_id,
                AgentRun.status.in_(("running", "succeeded")),
            )
            .order_by(AgentRun.created_at.desc(), AgentRun.id.desc())
            .limit(1)
        )
        if existing_run_id is not None:
            backfill_report_actions(session, existing_run_id)
            return get_run_detail(session, existing_run_id), False

    now = utcnow_naive()
    run = AgentRun(
        id=f"run_{uuid4().hex[:16]}",
        incident_id=incident_id,
        status="running",
        trace_id=f"local-{uuid4().hex[:16]}",
        input_payload={"incident_id": incident_id},
        final_report=None,
        token_estimate=0,
        cost_estimate_usd=0.0,
        error=None,
        started_at=now,
        completed_at=None,
        created_at=now,
        updated_at=now,
    )
    session.add(run)
    session.commit()
    session.refresh(run)

    try:
        report = run_investigation_workflow(session, run)
    except Exception as exc:
        session.rollback()
        failed_run = session.get(AgentRun, run.id)
        if failed_run is None:
            raise
        completed_at = utcnow_naive()
        failed_run.status = "failed"
        failed_run.error = str(exc)
        failed_run.completed_at = completed_at
        failed_run.updated_at = completed_at
        session.commit()
        return get_run_detail(session, failed_run.id), True

    completed_at = utcnow_naive()
    finished_run = session.get(AgentRun, run.id)
    if finished_run is None:
        raise RuntimeError(f"Agent run disappeared: {run.id}")
    finished_run.status = "succeeded"
    finished_run.final_report = report.model_dump(mode="json")
    finished_run.token_estimate = estimate_token_count(finished_run.final_report)
    finished_run.cost_estimate_usd = 0.0
    finished_run.completed_at = completed_at
    finished_run.updated_at = completed_at
    propose_actions_for_report(session, run_id=finished_run.id, report=report)
    return get_run_detail(session, finished_run.id), True


def get_run_detail(session: Session, run_id: str) -> AgentRunDetail:
    run = session.get(AgentRun, run_id)
    if run is None:
        raise LookupError(f"Unknown agent run id: {run_id}")

    steps = session.scalars(
        select(AgentRunStep)
        .where(AgentRunStep.run_id == run.id)
        .order_by(AgentRunStep.sequence)
    ).all()

    return AgentRunDetail(
        id=run.id,
        incident_id=run.incident_id,
        status=run.status,
        trace_id=run.trace_id,
        token_estimate=run.token_estimate,
        cost_estimate_usd=run.cost_estimate_usd,
        input_payload=run.input_payload,
        final_report=InvestigationReport.model_validate(run.final_report)
        if run.final_report is not None
        else None,
        error=run.error,
        started_at=run.started_at,
        completed_at=run.completed_at,
        created_at=run.created_at,
        updated_at=run.updated_at,
        steps=[
            AgentRunStepRead(
                id=step.id,
                run_id=step.run_id,
                sequence=step.sequence,
                stage=step.stage,
                tool_name=step.tool_name,
                status=step.status,
                inputs=step.inputs,
                outputs=step.outputs,
                error=step.error,
                started_at=step.started_at,
                completed_at=step.completed_at,
            )
            for step in steps
        ],
        mock_actions=list_mock_actions_for_run(session, run.id),
    )


def backfill_report_actions(session: Session, run_id: str) -> None:
    run = session.get(AgentRun, run_id)
    if run is None or run.status != "succeeded" or run.final_report is None:
        return

    report = InvestigationReport.model_validate(run.final_report)
    propose_actions_for_report(session, run_id=run.id, report=report)


def estimate_token_count(payload: dict[str, object]) -> int:
    text = json.dumps(payload, sort_keys=True)
    return max(1, len(text) // 4)
