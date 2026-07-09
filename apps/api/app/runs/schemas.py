"""Pydantic schemas for the control-plane runs API (PRD FR-8, FR-9)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class RunCreate(BaseModel):
    """Body for ``POST /runs`` (FR-8).

    ``run_inline`` mirrors ``app.agent.schemas.AgentInvestigationCreate``: when
    true the run executes synchronously on the request session (used by tests and
    inline operators); otherwise it is dispatched to Celery and the endpoint
    returns 202 with the run still queued.
    """

    agent_version_id: str = Field(min_length=1, max_length=128)
    input_payload: dict[str, Any] = Field(default_factory=dict)
    incident_id: str | None = Field(default=None, max_length=64)
    run_inline: bool = False


class RunTransitionRequest(BaseModel):
    """Body for ``POST /runs/{id}/transitions`` (FR-9).

    Only the Phase 3 operator-reachable targets are accepted.
    ``waiting_for_approval`` is deliberately omitted from the API surface: the
    state machine (``app.runs.lifecycle.VALID_TRANSITIONS``) permits it for
    forward-compatibility with Phase 5, but no worker resumes a paused run in
    Phase 3, so allowing an operator to strand a run there would leave it
    unrecoverable until a further manual transition.
    """

    status: Literal["queued", "running", "succeeded", "failed"]
