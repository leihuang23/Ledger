from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.access import require_demo_data_access
from app.db.session import get_db

from .schemas import AgentVersionObservability
from .service import get_agent_version_dashboard


# Read-only: the dashboard surfaces aggregates only (PRD FR-19). Gated by the
# same demo-data access check as the metrics endpoints.
require_demo_dashboard_access = require_demo_data_access


router = APIRouter(
    prefix="/dashboard",
    tags=["dashboard"],
    dependencies=[Depends(require_demo_dashboard_access)],
)


@router.get("/agents")
def list_agents_dashboard(
    db: Session = Depends(get_db),
) -> list[AgentVersionObservability]:
    """Per-version observability aggregates across every agent version that
    has at least one run."""
    return get_agent_version_dashboard(db)


@router.get("/agents/{agent_id}")
def agent_dashboard(
    agent_id: str,
    db: Session = Depends(get_db),
) -> list[AgentVersionObservability]:
    """Per-version observability aggregates scoped to one agent. Returns an
    empty list when the agent has no runs (run-driven aggregate)."""
    return get_agent_version_dashboard(db, agent_id=agent_id)
