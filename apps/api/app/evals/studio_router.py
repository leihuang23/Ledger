from __future__ import annotations

from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.core.access import require_demo_data_access, require_demo_operator_access
from app.core.config import get_settings
from app.core.limiter import limiter
from app.db.session import get_db
from app.evals.router import require_eval_run_access
from app.evals.schemas import (
    EvalDatasetCreate,
    EvalDatasetDetail,
    EvalDatasetList,
    EvalDatasetRunAccepted,
    EvalDatasetRunRequest,
    EvalResultList,
    EvalVersionComparison,
)
from app.evals.service import (
    DuplicateDatasetNameError,
    InvalidDatasetCasesError,
    create_eval_dataset,
    compare_eval_versions,
    get_eval_dataset,
    list_eval_datasets,
    list_eval_results,
)
from app.evals.tasks import run_eval_suite_task
from app.models import AgentVersion, EvalDataset
from app.runs.service import record_blocked_control_plane_tool_attempt
from app.tools.policy import can_call_tool

_settings = get_settings()

router = APIRouter(
    tags=["eval-studio"],
    dependencies=[Depends(require_demo_data_access)],
)


def _enqueue_eval_dataset(
    eval_run_id: str, dataset_id: str, agent_version_id: str
) -> None:
    settings = get_settings()
    if settings.app_env == "test":
        run_eval_suite_task.run(eval_run_id, dataset_id, agent_version_id)
    else:
        run_eval_suite_task.delay(eval_run_id, dataset_id, agent_version_id)


@router.post(
    "/eval-datasets",
    response_model=EvalDatasetDetail,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_demo_operator_access)],
)
@limiter.limit(f"{_settings.rate_limit_mutations_per_minute}/minute")
def create_dataset(
    request: Request,
    payload: EvalDatasetCreate,
    db: Session = Depends(get_db),
) -> EvalDatasetDetail:
    try:
        return create_eval_dataset(db, payload)
    except InvalidDatasetCasesError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except DuplicateDatasetNameError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/eval-datasets", response_model=EvalDatasetList)
def list_datasets(db: Session = Depends(get_db)) -> EvalDatasetList:
    return list_eval_datasets(db)


@router.get("/eval-datasets/{dataset_id}", response_model=EvalDatasetDetail)
def get_dataset(
    dataset_id: str, db: Session = Depends(get_db)
) -> EvalDatasetDetail:
    dataset = get_eval_dataset(db, dataset_id)
    if dataset is None:
        raise HTTPException(status_code=404, detail=f"Eval dataset {dataset_id} not found.")
    return dataset


@router.post(
    "/eval-datasets/{dataset_id}/run",
    response_model=EvalDatasetRunAccepted,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[
        Depends(require_demo_operator_access),
        Depends(require_eval_run_access),
    ],
)
@limiter.limit(f"{_settings.rate_limit_mutations_per_minute}/minute")
def run_dataset(
    request: Request,
    dataset_id: str,
    payload: EvalDatasetRunRequest,
    db: Session = Depends(get_db),
) -> EvalDatasetRunAccepted:
    if db.get(EvalDataset, dataset_id) is None:
        raise HTTPException(status_code=404, detail=f"Eval dataset {dataset_id} not found.")
    version = db.get(AgentVersion, payload.agent_version_id)
    if version is None or version.status != "published":
        raise HTTPException(
            status_code=404,
            detail=f"Unknown or unpublished agent version id: {payload.agent_version_id}",
        )
    allowed, blocked_reason = can_call_tool(version, "run_eval")
    if not allowed:
        reason = blocked_reason or "tool_not_enabled"
        blocked_run_id = record_blocked_control_plane_tool_attempt(
            db,
            agent_version_id=version.id,
            tool_name="run_eval",
            blocked_reason=reason,
            input_payload={
                "operation": "run_eval",
                "dataset_id": dataset_id,
            },
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"run_eval blocked: {reason}",
            headers={"X-Agent-Run-Id": blocked_run_id},
        )
    eval_run_id = f"evalrun_{uuid4().hex[:16]}"
    _enqueue_eval_dataset(eval_run_id, dataset_id, version.id)
    return EvalDatasetRunAccepted(
        eval_run_id=eval_run_id,
        dataset_id=dataset_id,
        agent_version_id=version.id,
    )


@router.get("/eval-results", response_model=EvalResultList)
def get_results(
    agent_version_id: str | None = None,
    dataset_id: str | None = None,
    db: Session = Depends(get_db),
) -> EvalResultList:
    return list_eval_results(
        db,
        agent_version_id=agent_version_id,
        dataset_id=dataset_id,
    )


@router.get("/eval-results/compare", response_model=EvalVersionComparison)
def compare_results(
    version_a: str,
    version_b: str,
    dataset_id: str = "mrr-drop-suite",
    db: Session = Depends(get_db),
) -> EvalVersionComparison:
    try:
        return compare_eval_versions(
            db,
            version_a=version_a,
            version_b=version_b,
            dataset_id=dataset_id,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
