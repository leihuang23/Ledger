from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.session import get_db

from .schemas import (
    ActiveUserMetrics,
    ChurnMetrics,
    DashboardMetrics,
    FailedInvoiceMetrics,
    MrrMetrics,
    TicketVolumeMetrics,
)
from .service import (
    get_active_user_metrics,
    get_churn_metrics,
    get_dashboard_metrics,
    get_failed_invoice_metrics,
    get_mrr_metrics,
    get_ticket_volume_metrics,
)

def require_demo_metrics_access() -> None:
    settings = get_settings()
    if settings.app_env not in {"local", "test", "development", "demo"}:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Metrics endpoints are only available in local, test, development, or demo environments.",
        )


router = APIRouter(
    prefix="/metrics",
    tags=["metrics"],
    dependencies=[Depends(require_demo_metrics_access)],
)


@router.get("/mrr")
def mrr(db: Session = Depends(get_db)) -> MrrMetrics:
    return get_mrr_metrics(db)


@router.get("/churn")
def churn(db: Session = Depends(get_db)) -> ChurnMetrics:
    return get_churn_metrics(db)


@router.get("/failed-invoices")
def failed_invoices(db: Session = Depends(get_db)) -> FailedInvoiceMetrics:
    return get_failed_invoice_metrics(db)


@router.get("/ticket-volume")
def ticket_volume(db: Session = Depends(get_db)) -> TicketVolumeMetrics:
    return get_ticket_volume_metrics(db)


@router.get("/active-users")
def active_users(db: Session = Depends(get_db)) -> ActiveUserMetrics:
    return get_active_user_metrics(db)


@router.get("/dashboard")
def dashboard(db: Session = Depends(get_db)) -> DashboardMetrics:
    return get_dashboard_metrics(db)
