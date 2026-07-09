"""Unit tests for the run status lifecycle state machine (testing-strategy U-6..U-12)."""

from __future__ import annotations

import pytest

from app.runs.lifecycle import (
    RUN_STATUSES,
    VALID_TRANSITIONS,
    IllegalTransition,
    validate_transition,
)


@pytest.mark.parametrize(
    "current,target",
    [
        ("queued", "running"),  # U-6
        ("running", "waiting_for_approval"),  # U-7
        ("waiting_for_approval", "running"),  # U-8 (resume on approval)
        ("running", "succeeded"),  # U-9
        ("running", "failed"),  # U-10
        ("queued", "failed"),  # pre-flight failure without claiming
        ("waiting_for_approval", "failed"),  # abandoned while waiting
    ],
)
def test_allowed_transition_returns_none(current: str, target: str) -> None:
    """Allowed transitions validate without raising."""
    validate_transition(current, target)  # no raise


@pytest.mark.parametrize(
    "current,target",
    [
        ("succeeded", "running"),  # U-11 terminal
        ("queued", "succeeded"),  # U-12 skips running
        ("failed", "running"),  # terminal
        ("succeeded", "failed"),  # terminal -> terminal
        ("waiting_for_approval", "succeeded"),  # must resume via running first
    ],
)
def test_illegal_transition_raises(current: str, target: str) -> None:
    """Illegal transitions raise IllegalTransition (mapped to 409 by the router)."""
    with pytest.raises(IllegalTransition):
        validate_transition(current, target)


def test_unknown_current_status_raises() -> None:
    with pytest.raises(IllegalTransition):
        validate_transition("pending", "running")


def test_terminal_states_have_no_outgoing_transitions() -> None:
    assert VALID_TRANSITIONS["succeeded"] == frozenset()
    assert VALID_TRANSITIONS["failed"] == frozenset()


def test_transition_table_covers_all_run_statuses() -> None:
    assert set(VALID_TRANSITIONS) == set(RUN_STATUSES)
