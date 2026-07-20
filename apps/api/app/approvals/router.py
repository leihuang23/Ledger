from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.approvals.schemas import (
    ApprovalDecisionCreate,
    ApprovalRequestRead,
    ApprovalStatus,
    MockActionCreate,
    MockActionRead,
    RiskLevel,
)
from app.approvals.service import (
    RunStateConflictError,
    approve_request,
    create_mock_action,
    list_approval_requests,
    reject_request,
)
from app.core.access import require_demo_data_access, require_demo_operator_access
from app.core.config import get_settings
from app.core.limiter import limiter
from app.db.session import get_db

_settings = get_settings()

mock_actions_router = APIRouter(
    prefix="/mock-actions",
    tags=["mock-actions"],
    dependencies=[Depends(require_demo_data_access)],
)
approvals_router = APIRouter(
    prefix="/approvals",
    tags=["approvals"],
    dependencies=[Depends(require_demo_data_access)],
)


@mock_actions_router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_demo_operator_access)],
)
@limiter.limit(f"{_settings.rate_limit_mutations_per_minute}/minute")
def propose_mock_action(
    request: Request,
    payload: MockActionCreate,
    db: Session = Depends(get_db),
) -> MockActionRead:
    try:
        return create_mock_action(db, payload)
    except LookupError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    except RunStateConflictError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail=str(exc),
        ) from exc


@approvals_router.get("")
def approval_queue(
    status: ApprovalStatus | None = None,
    agent_version_id: str | None = None,
    risk_level: RiskLevel | None = None,
    include_decided: bool = False,
    db: Session = Depends(get_db),
) -> list[ApprovalRequestRead]:
    return list_approval_requests(
        db,
        status=status,
        agent_version_id=agent_version_id,
        risk_level=risk_level,
        include_decided=include_decided,
    )


@approvals_router.post(
    "/{approval_id}/approve",
    dependencies=[Depends(require_demo_operator_access)],
)
@limiter.limit(f"{_settings.rate_limit_mutations_per_minute}/minute")
def approve(
    request: Request,
    approval_id: str,
    payload: ApprovalDecisionCreate,
    db: Session = Depends(get_db),
) -> ApprovalRequestRead:
    try:
        return approve_request(db, approval_id, payload)
    except LookupError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@approvals_router.post(
    "/{approval_id}/reject",
    dependencies=[Depends(require_demo_operator_access)],
)
@limiter.limit(f"{_settings.rate_limit_mutations_per_minute}/minute")
def reject(
    request: Request,
    approval_id: str,
    payload: ApprovalDecisionCreate,
    db: Session = Depends(get_db),
) -> ApprovalRequestRead:
    try:
        return reject_request(db, approval_id, payload)
    except LookupError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
