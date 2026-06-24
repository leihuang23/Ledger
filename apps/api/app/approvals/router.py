from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.approvals.schemas import (
    ApprovalDecisionCreate,
    ApprovalRequestRead,
    ApprovalStatus,
    MockActionCreate,
    MockActionRead,
)
from app.approvals.service import (
    approve_request,
    create_mock_action,
    list_approval_requests,
    reject_request,
)
from app.core.access import require_demo_data_access
from app.db.session import get_db

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


@mock_actions_router.post("", status_code=status.HTTP_201_CREATED)
def propose_mock_action(
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
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail=str(exc),
        ) from exc


@approvals_router.get("")
def approval_queue(
    status: ApprovalStatus | None = None,
    db: Session = Depends(get_db),
) -> list[ApprovalRequestRead]:
    return list_approval_requests(db, status=status)


@approvals_router.post("/{approval_id}/approve")
def approve(
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


@approvals_router.post("/{approval_id}/reject")
def reject(
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
