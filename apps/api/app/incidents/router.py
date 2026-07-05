from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.orm import Session

from app.core.access import require_demo_data_access, require_demo_operator_access
from app.db.session import get_db

from .schemas import IncidentCreate, IncidentDetail, IncidentSummary
from .service import (
    create_or_get_incident_from_anomaly,
    get_incident_detail,
    list_incidents,
)

router = APIRouter(
    prefix="/incidents",
    tags=["incidents"],
    dependencies=[Depends(require_demo_data_access)],
)


@router.get("")
def incidents(db: Session = Depends(get_db)) -> list[IncidentSummary]:
    return list_incidents(db)


@router.post("", dependencies=[Depends(require_demo_operator_access)])
def create_incident(
    payload: IncidentCreate,
    response: Response,
    db: Session = Depends(get_db),
) -> IncidentDetail:
    try:
        incident, created = create_or_get_incident_from_anomaly(db, payload.anomaly_id)
    except LookupError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc

    response.status_code = (
        status.HTTP_201_CREATED if created else status.HTTP_200_OK
    )
    return incident


@router.get("/{incident_id}")
def incident_detail(
    incident_id: str, db: Session = Depends(get_db)
) -> IncidentDetail:
    incident = get_incident_detail(db, incident_id)
    if incident is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Unknown incident id: {incident_id}",
        )
    return incident
