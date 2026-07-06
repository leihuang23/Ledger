from __future__ import annotations

import secrets

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.access import require_demo_data_access
from app.core.config import get_settings
from app.core.errors import error_response
from app.core.limiter import limiter
from app.db.session import get_db
from app.evals.runner import list_latest_eval_results, run_eval_suite
from app.evals.schemas import EvalResultsReport, EvalRunSummary

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


@router.post("/run", dependencies=[Depends(require_eval_run_access)])
@limiter.limit(f"{_settings.rate_limit_mutations_per_minute}/minute")
def run_evals(
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
) -> EvalRunSummary:
    try:
        summary = run_eval_suite(db)
    except LookupError as exc:
        return error_response("not_found", str(exc), 404)
    except IntegrityError as exc:
        return error_response("conflict", str(exc), 409)
    response.status_code = status.HTTP_201_CREATED
    return summary


@router.get("/results")
def eval_results(db: Session = Depends(get_db)) -> EvalResultsReport:
    return list_latest_eval_results(db)
