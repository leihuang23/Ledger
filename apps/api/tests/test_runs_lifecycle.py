"""Unit tests for the run status lifecycle state machine (testing-strategy U-6..U-12)."""

from __future__ import annotations

import pytest

from app.runs.lifecycle import (
    RUN_STATUSES,
    VALID_TRANSITIONS,
    IllegalTransition,
    validate_operator_transition,
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


def test_operator_cannot_force_succeed_a_running_run() -> None:
    """Operator guard: ``running -> succeeded`` is system-managed (workflow
    finalization or the approval resume path), so the operator transition API
    must reject it even though the underlying state machine allows it. The
    internal validator must stay permissive so the executor and the approval
    resume path keep working."""
    with pytest.raises(IllegalTransition, match="system-managed"):
        validate_operator_transition("running", "succeeded")
    # The internal state machine is unchanged: system paths may still advance
    # running -> succeeded.
    validate_transition("running", "succeeded")  # no raise


def test_operator_force_fail_and_checkpoint_guards_unchanged() -> None:
    """The operator may still force-fail a running run, and the pre-existing
    waiting_for_approval -> running resume guard is unaffected."""
    validate_operator_transition("running", "failed")  # no raise
    with pytest.raises(IllegalTransition):
        validate_operator_transition("waiting_for_approval", "running")
