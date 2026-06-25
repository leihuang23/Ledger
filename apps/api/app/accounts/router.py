from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.accounts.schemas import AccountDetailRead
from app.accounts.service import get_account_detail
from app.core.access import require_demo_data_access
from app.db.session import get_db

router = APIRouter(
    prefix="/accounts",
    tags=["accounts"],
    dependencies=[Depends(require_demo_data_access)],
)


@router.get("/{account_id}")
def account_detail(account_id: str, db: Session = Depends(get_db)) -> AccountDetailRead:
    account = get_account_detail(db, account_id)
    if account is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Unknown account id: {account_id}",
        )
    return account
