from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.orm import Session

from app.agent.schemas import AgentInvestigationCreate, AgentRunDetail
from app.agent.service import get_run_detail, start_investigation_run
from app.core.access import require_demo_data_access
from app.db.session import get_db

router = APIRouter(
    prefix="/agent",
    tags=["agent"],
    dependencies=[Depends(require_demo_data_access)],
)


@router.post("/investigations")
def start_investigation(
    payload: AgentInvestigationCreate,
    response: Response,
    db: Session = Depends(get_db),
) -> AgentRunDetail:
    try:
        run, created = start_investigation_run(
            db, payload.incident_id, force=payload.force
        )
    except LookupError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc

    response.status_code = status.HTTP_201_CREATED if created else status.HTTP_200_OK
    return run


@router.get("/runs/{run_id}")
def agent_run_detail(run_id: str, db: Session = Depends(get_db)) -> AgentRunDetail:
    try:
        return get_run_detail(db, run_id)
    except LookupError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
