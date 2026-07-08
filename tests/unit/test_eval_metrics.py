"""Unit tests for eval metrics and the calibration threshold sweep (Section 6).

The escalation threshold is derived from a labeled sweep, not hand-picked: for
each candidate threshold we measure precision and recall of the escalate-vs-
autofix decision, and pick an operating point that prioritizes autofix precision.
"""

from __future__ import annotations

from app.eval.metrics import (
    LabeledScore,
    pick_operating_point,
    resolve_rate,
    resolve_rate_by_bisection_prior,
    threshold_sweep,
)


def test_resolve_rate_counts_resolved_over_total() -> None:
    assert resolve_rate([True, False, True, False]) == 0.5
    assert resolve_rate([]) == 0.0


def test_resolve_rate_split_by_bisection_prior() -> None:
    # (resolved, had_prior)
    labeled = [
        (True, True),
        (True, True),
        (False, True),  # with-prior: 2/3
        (True, False),
        (False, False),  # without-prior: 1/2
    ]
    split = resolve_rate_by_bisection_prior(labeled)
    assert split.n_with_prior == 3
    assert split.n_without_prior == 2
    assert abs(split.with_prior - 2 / 3) < 1e-9
    assert split.without_prior == 0.5


def test_sweep_reports_precision_and_recall_per_threshold() -> None:
    labeled = [
        LabeledScore(composite=0.9, correct=True),
        LabeledScore(composite=0.8, correct=True),
        LabeledScore(composite=0.6, correct=False),  # wrong patch, mid score
        LabeledScore(composite=0.3, correct=False),
    ]
    sweep = threshold_sweep(labeled, thresholds=[0.5, 0.7, 0.95])
    by_t = {round(p.threshold, 2): p for p in sweep}

    # At 0.7: autofix the two correct (>=0.7), escalate the rest -> perfect precision.
    assert by_t[0.7].precision == 1.0
    assert by_t[0.7].recall == 1.0
    # At 0.5: also autofix the wrong 0.6 patch -> precision drops.
    assert by_t[0.5].precision < 1.0
    # At 0.95: autofix nothing -> recall 0, precision defined as 1.0 (no false autofix).
    assert by_t[0.95].recall == 0.0
    assert by_t[0.95].precision == 1.0


def test_operating_point_prioritizes_precision() -> None:
    labeled = [
        LabeledScore(composite=0.9, correct=True),
        LabeledScore(composite=0.8, correct=True),
        LabeledScore(composite=0.6, correct=False),
        LabeledScore(composite=0.3, correct=False),
    ]
    sweep = threshold_sweep(labeled, thresholds=[0.5, 0.7, 0.95])
    # Require precision >= 0.95: the lowest such threshold maximizes recall subject
    # to the precision floor -> 0.7 (precision 1.0, recall 1.0), not 0.5.
    chosen = pick_operating_point(sweep, min_precision=0.95)
    assert chosen == 0.7
