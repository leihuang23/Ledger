"""Pure comparison/regression logic for eval version comparison (PRD FR-17).

These functions are deliberately I/O-free so the regression/improvement/unchanged
classification and pass-rate math can be unit-tested in isolation
(testing-strategy §4.3, U-13..U-17) without a database session. The DB-bound
orchestration lives in ``app.evals.service.compare_eval_versions`` and delegates
the pure classification to ``classify_change``.
"""

from __future__ import annotations

CHANGE_REGRESSION = "regression"
CHANGE_IMPROVEMENT = "improvement"
CHANGE_UNCHANGED = "unchanged"


def classify_change(*, passed_a: bool, passed_b: bool) -> str:
    """Classify how a single eval case changed between version A and version B.

    * A passed, B failed  -> ``"regression"``   (U-13)
    * A failed, B passed  -> ``"improvement"``  (U-14)
    * both pass           -> ``"unchanged"``    (U-15)
    * both fail           -> ``"unchanged"``    (U-16)
    """
    if passed_a and not passed_b:
        return CHANGE_REGRESSION
    if not passed_a and passed_b:
        return CHANGE_IMPROVEMENT
    return CHANGE_UNCHANGED


def compute_pass_rate(*, passed_count: int, total_cases: int) -> float:
    """Return the pass rate as a float in [0.0, 1.0].

    Zero total cases yields 0.0 (avoids a ZeroDivisionError while remaining an
    honest "no cases to evaluate" sentinel).
    """
    if total_cases <= 0:
        return 0.0
    return passed_count / total_cases
