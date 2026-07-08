"""Eval metrics and the calibration threshold sweep (spec Sections 6, 8).

The escalation threshold is not hand-picked. Given a labeled set (each instance
has a ground-truth reference patch, so "did the produced patch behave like the
reference" is a label), we sweep the threshold and measure the precision and
recall of the escalate-vs-autofix decision, then pick an operating point that
prioritizes precision of the autofix path.
"""

from __future__ import annotations

from collections.abc import Iterable

from pydantic import BaseModel


class LabeledScore(BaseModel):
    """A produced run's composite confidence and whether its patch was correct."""

    composite: float
    correct: bool


class SweepPoint(BaseModel):
    threshold: float
    precision: float  # of the autofix decision
    recall: float  # of the autofix decision
    autofixed: int
    escalated: int


def resolve_rate(resolved_flags: Iterable[bool]) -> float:
    """Fraction of instances whose produced patch resolved the bug."""
    flags = list(resolved_flags)
    if not flags:
        return 0.0
    return sum(1 for f in flags if f) / len(flags)


class SplitResolveRate(BaseModel):
    """Resolve rate split by whether a bisection prior was available."""

    with_prior: float
    without_prior: float
    n_with_prior: int
    n_without_prior: int


def resolve_rate_by_bisection_prior(labeled: list[tuple[bool, bool]]) -> SplitResolveRate:
    """Split resolve rate by prior availability. Input: (resolved, had_prior).

    The spec expects the resolve rate to be higher when a bisection prior was
    available; reporting the split makes that measurable.
    """
    with_prior = [resolved for resolved, had_prior in labeled if had_prior]
    without_prior = [resolved for resolved, had_prior in labeled if not had_prior]
    return SplitResolveRate(
        with_prior=resolve_rate(with_prior),
        without_prior=resolve_rate(without_prior),
        n_with_prior=len(with_prior),
        n_without_prior=len(without_prior),
    )


def threshold_sweep(labeled: list[LabeledScore], thresholds: list[float]) -> list[SweepPoint]:
    """Precision/recall of autofix (composite >= threshold) at each threshold."""
    total_correct = sum(1 for s in labeled if s.correct)
    points: list[SweepPoint] = []
    for t in thresholds:
        autofixed = [s for s in labeled if s.composite >= t]
        true_pos = sum(1 for s in autofixed if s.correct)
        # No autofix -> no false autofix, so precision is defined as 1.0.
        precision = (true_pos / len(autofixed)) if autofixed else 1.0
        recall = (true_pos / total_correct) if total_correct else 0.0
        points.append(
            SweepPoint(
                threshold=t,
                precision=precision,
                recall=recall,
                autofixed=len(autofixed),
                escalated=len(labeled) - len(autofixed),
            )
        )
    return points


def pick_operating_point(sweep: list[SweepPoint], *, min_precision: float) -> float:
    """Lowest threshold meeting the precision floor, maximizing recall.

    A wrong autofix reaching a human as a draft PR is worse than an unnecessary
    escalation, so precision is the constraint and recall the objective.
    """
    eligible = [p for p in sweep if p.precision >= min_precision]
    if not eligible:
        # Nothing meets the floor: fall back to the highest-precision point.
        return max(sweep, key=lambda p: (p.precision, p.recall)).threshold
    best = max(eligible, key=lambda p: p.recall)
    # Prefer the lowest threshold among those tying on the best recall.
    best_recall_points = [p for p in eligible if p.recall == best.recall]
    return min(p.threshold for p in best_recall_points)
