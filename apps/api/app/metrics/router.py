from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.access import require_demo_data_access
from app.db.session import get_db
from app.incidents.schemas import RevenueAnomaly
from app.incidents.service import detect_revenue_anomalies

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


require_demo_metrics_access = require_demo_data_access


router = APIRouter(
    prefix="/metrics",
    tags=["metrics"],
    dependencies=[Depends(require_demo_metrics_access)],
)


@router.get("/mrr")
def mrr(db: Session = Depends(get_db)) -> MrrMetrics:
    return get_mrr_metrics(db)


@router.get("/revenue")
def revenue(db: Session = Depends(get_db)) -> MrrMetrics:
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


@router.get("/anomalies")
def anomalies(db: Session = Depends(get_db)) -> list[RevenueAnomaly]:
    return detect_revenue_anomalies(db)
