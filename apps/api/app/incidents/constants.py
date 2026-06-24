from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

REVENUE_MRR_DROP_ANOMALY_TYPE = "revenue_mrr_week_over_week_drop"
PAID_INVOICE_MRR_METRIC = "paid_invoice_mrr"


@dataclass(frozen=True)
class WeekWindows:
    current_start: date
    current_end: date
    current_end_exclusive: date
    previous_start: date
    previous_end: date


def revenue_week_windows(anchor: datetime) -> WeekWindows:
    current_end_exclusive = anchor.date() + timedelta(days=1)
    current_start = current_end_exclusive - timedelta(days=7)
    previous_start = current_start - timedelta(days=7)
    return WeekWindows(
        current_start=current_start,
        current_end=current_end_exclusive - timedelta(days=1),
        current_end_exclusive=current_end_exclusive,
        previous_start=previous_start,
        previous_end=current_start - timedelta(days=1),
    )


def anomaly_id_for_window(current_start: date) -> str:
    return f"rev_mrr_wow_drop_{current_start:%Y%m%d}"


def incident_id_for_anomaly(anomaly_id: str) -> str:
    return f"inc_{anomaly_id}"
