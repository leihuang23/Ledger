"""Run status lifecycle state machine (PRD FR-9).

A pure, I/O-free helper that validates run status transitions. The control-plane
worker drives the real transitions internally (``queued -> running -> succeeded |
failed``); ``app.runs.service.transition_run`` exposes operator/API-level
advancement through ``POST /runs/{id}/transitions`` and maps an
``IllegalTransition`` to HTTP 409 (FR-9, I-14).

Phase 5 uses ``running <-> waiting_for_approval`` for the system-managed
approval checkpoint. The operator transition API still cannot place a run into
that state directly; only the action proposal and approval decision paths own it.
Operators likewise cannot advance ``running -> succeeded``: run completion is
reserved for workflow finalization and the approval resume path so a succeeded
run always carries a generated report (audit hardening).
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


def validate_operator_transition(current: str, target: str) -> None:
    """Validate an API/operator transition without bypassing system checkpoints."""
    validate_transition(current, target)
    if current == "waiting_for_approval" and target == "running":
        raise IllegalTransition(
            "Approval checkpoint resume is system-managed; decide every pending "
            "approval or transition the run to 'failed'."
        )
    if current == "running" and target == "succeeded":
        raise IllegalTransition(
            "Run completion is system-managed: only workflow finalization or "
            "the approval resume path advances a run to 'succeeded'. "
            "Operators may force-fail a run instead."
        )
