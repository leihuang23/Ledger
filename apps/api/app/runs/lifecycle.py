"""Run status lifecycle state machine (PRD FR-9).

A pure, I/O-free helper that validates run status transitions. The control-plane
worker drives the real transitions internally (``queued -> running -> succeeded |
failed``); ``app.runs.service.transition_run`` exposes operator/API-level
advancement through ``POST /runs/{id}/transitions`` and maps an
``IllegalTransition`` to HTTP 409 (FR-9, I-14).

Phase 3 ships only the table + validator. Workflow pausing on
``waiting_for_approval`` (PRD I-19) is Phase 5; the table permits the
``running <-> waiting_for_approval`` transitions now so it is forward-compatible.
"""

from __future__ import annotations

# PRD FR-9: the run status lifecycle.
RUN_STATUSES: tuple[str, ...] = (
    "queued",
    "running",
    "waiting_for_approval",
    "succeeded",
    "failed",
)

# Permitted transitions. Terminal states (``succeeded``/``failed``) have empty
# sets. ``queued`` may go to ``failed`` so a pre-flight failure (e.g. no agent
# version available) can be recorded without first claiming the run.
VALID_TRANSITIONS: dict[str, frozenset[str]] = {
    "queued": frozenset({"running", "failed"}),
    "running": frozenset({"waiting_for_approval", "succeeded", "failed"}),
    "waiting_for_approval": frozenset({"running", "failed"}),
    "succeeded": frozenset(),  # terminal
    "failed": frozenset(),  # terminal
}


class IllegalTransition(ValueError):
    """Raised when a run status transition is not permitted by the state machine."""


def validate_transition(current: str, target: str) -> None:
    """Validate that ``current -> target`` is a permitted run-status transition.

    Raises :class:`IllegalTransition` if ``target`` is not in the set of
    transitions allowed from ``current`` (including when ``current`` is terminal
    or unknown).
    """
    if current not in VALID_TRANSITIONS:
        raise IllegalTransition(f"Unknown current run status: {current!r}")
    if target not in VALID_TRANSITIONS[current]:
        raise IllegalTransition(
            f"Illegal run status transition: {current!r} -> {target!r}"
        )
