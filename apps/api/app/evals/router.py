from __future__ import annotations

import secrets
from uuid import uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status
from sqlalchemy.orm import Session

from app.agent.persistence import utcnow_naive
from app.agents.service import DEFAULT_AGENT_ID, DEFAULT_AGENT_VERSION_ID
from app.core.access import require_demo_data_access
from app.core.config import get_settings
from app.core.limiter import limiter
from app.db.session import get_db
from app.evals.runner import build_eval_run_summary, list_latest_eval_results
from app.evals.schemas import EvalResultsReport, EvalRunSummary
from app.evals.tasks import run_eval_suite_task
from app.models import AgentVersion
from app.runs.service import record_blocked_control_plane_tool_attempt
from app.tools.policy import can_call_tool

_settings = get_settings()

router = APIRouter(
    prefix="/evals",
    tags=["evals"],
    dependencies=[Depends(require_demo_data_access)],
)


def require_eval_run_access(
    eval_run_token: str | None = Header(default=None, alias="X-Eval-Run-Token"),
) -> None:
    settings = get_settings()
    if settings.eval_run_token is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "Eval run API is disabled. Use the CLI runner or set "
                "EVAL_RUN_TOKEN for explicit operator-triggered suites."
            ),
        )
    if eval_run_token is None or not secrets.compare_digest(
        eval_run_token, settings.eval_run_token
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid eval run token.",
        )


def _enqueue_eval_suite(eval_run_id: str) -> None:
    """Enqueue the eval suite as a Celery task.

    In the ``test`` env (``task_always_eager=True``) ``.run()`` executes the
    task body synchronously so tests can assert on persisted rows without a
    live broker. In all other envs ``.delay()`` hands the work to a worker
    and returns immediately, keeping the HTTP request off the suite path.
    """
    settings = get_settings()
    if settings.app_env == "test":
        run_eval_suite_task.run(eval_run_id)
    else:
        run_eval_suite_task.delay(eval_run_id)


@router.post("/run", dependencies=[Depends(require_eval_run_access)])
@limiter.limit(f"{_settings.rate_limit_mutations_per_minute}/minute")
def run_evals(
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
) -> EvalRunSummary:
    version = db.get(AgentVersion, DEFAULT_AGENT_VERSION_ID)
    if version is None or version.status != "published":
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="The Project1 v1 eval baseline is unavailable.",
        )
    allowed, blocked_reason = can_call_tool(version, "run_eval")
    # This token-gated endpoint is the Project1 compatibility surface and
    # predates the governed run_eval capability. The exact immutable v1 may
    # therefore run here despite lacking that tool ID/scope. Phase 5 dataset
    # runs and every non-v1 candidate continue through strict can_call_tool.
    is_project1_v1 = (
        version.id == DEFAULT_AGENT_VERSION_ID
        and version.agent_id == DEFAULT_AGENT_ID
    )
    if not allowed and not is_project1_v1:
        reason = blocked_reason or "tool_not_enabled"
        blocked_run_id = record_blocked_control_plane_tool_attempt(
            db,
            agent_version_id=version.id,
            tool_name="run_eval",
            blocked_reason=reason,
            input_payload={"operation": "run_eval", "dataset_id": None},
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"run_eval blocked: {reason}",
            headers={"X-Agent-Run-Id": blocked_run_id},
        )
    eval_run_id = f"evalrun_{uuid4().hex[:16]}"
    started_at = utcnow_naive()
    _enqueue_eval_suite(eval_run_id)
    response.status_code = status.HTTP_202_ACCEPTED
    return EvalRunSummary(
        eval_run_id=eval_run_id,
        status="running",
        total_scenarios=0,
        passed_scenarios=0,
        failed_scenarios=0,
        started_at=started_at,
        completed_at=None,
        results=[],
    )


@router.get("/runs/{eval_run_id}")
def eval_run_status(
    eval_run_id: str,
    db: Session = Depends(get_db),
) -> EvalRunSummary:
    summary = build_eval_run_summary(db, eval_run_id)
    if summary is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Eval run {eval_run_id} not found.",
        )
    return summary


@router.get("/results")
def eval_results(db: Session = Depends(get_db)) -> EvalResultsReport:
    return list_latest_eval_results(db)
