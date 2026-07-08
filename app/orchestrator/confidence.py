"""The confidence gate (spec Section 6).

Confidence is composed from *measurable* signals, never from an LLM's
self-reported confidence. Three signals are hard gates: a reproduce rate below a
floor, a repro that does not pass after the patch, and any regression. Any hard
gate escalates regardless of the composite. The remaining continuous signals are
combined into a weighted composite; the run escalates when the composite falls
below the calibrated, config-supplied threshold.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class Signals(BaseModel):
    """The measurable confidence signals emitted across the pipeline."""

    # Reproduction Agent: fraction of N runs that reproduced. Hard-gated by floor.
    reproduce_rate: float = Field(ge=0.0, le=1.0)
    # Bisection Agent: 1 - skip_ratio. None for non-regression bugs.
    bisection_certainty: float | None = Field(default=None, ge=0.0, le=1.0)
    # Validation Agent hard gates.
    repro_after_patch_passes: bool
    regression_free: bool
    # Validation Agent continuous signals.
    static_analysis_clean: float = Field(ge=0.0, le=1.0)
    reviewer_verdict: float = Field(ge=0.0, le=1.0)
    # Fix Agent blast radius (smaller is safer).
    files_touched: int = Field(ge=0)
    lines_touched: int = Field(ge=0)


class GateConfig(BaseModel):
    """Tunable gate parameters. Weights and caps are config, not magic numbers."""

    reproduce_rate_floor: float = Field(default=0.6, ge=0.0, le=1.0)
    # Blast-radius soft cap: at this many lines the blast score reaches ~0.
    blast_radius_lines_cap: int = Field(default=400, gt=0)
    # Composite weights over the continuous signals.
    w_reproduce: float = 0.15
    w_bisection: float = 0.15
    w_static: float = 0.20
    w_reviewer: float = 0.30
    w_blast: float = 0.20


class GateDecision(BaseModel):
    escalate: bool
    composite: float
    hard_gate_failures: list[str]
    reason: str


def _blast_score(files_touched: int, lines_touched: int, cap: int) -> float:
    """Map blast radius to [0, 1]; smaller diffs score higher."""
    return max(0.0, 1.0 - lines_touched / cap)


def evaluate(signals: Signals, *, threshold: float, config: GateConfig) -> GateDecision:
    """Decide autofix vs. escalate from measurable signals only."""
    hard_failures: list[str] = []
    if signals.reproduce_rate < config.reproduce_rate_floor:
        hard_failures.append("reproduce_rate")
    if not signals.repro_after_patch_passes:
        hard_failures.append("repro_after_patch")
    if not signals.regression_free:
        hard_failures.append("regression")

    blast = _blast_score(
        signals.files_touched, signals.lines_touched, config.blast_radius_lines_cap
    )

    # Weight the continuous signals. Bisection certainty is absent for
    # non-regression bugs; when absent, redistribute its weight proportionally so
    # the composite stays on [0, 1] rather than being penalised for a missing prior.
    weighted: list[tuple[float, float]] = [
        (config.w_reproduce, signals.reproduce_rate),
        (config.w_static, signals.static_analysis_clean),
        (config.w_reviewer, signals.reviewer_verdict),
        (config.w_blast, blast),
    ]
    if signals.bisection_certainty is not None:
        weighted.append((config.w_bisection, signals.bisection_certainty))

    total_weight = sum(w for w, _ in weighted)
    composite = sum(w * v for w, v in weighted) / total_weight if total_weight > 0 else 0.0

    if hard_failures:
        return GateDecision(
            escalate=True,
            composite=composite,
            hard_gate_failures=hard_failures,
            reason=f"hard gate(s) failed: {', '.join(hard_failures)}",
        )
    if composite < threshold:
        return GateDecision(
            escalate=True,
            composite=composite,
            hard_gate_failures=[],
            reason=f"composite {composite:.3f} below threshold {threshold:.3f}",
        )
    return GateDecision(
        escalate=False,
        composite=composite,
        hard_gate_failures=[],
        reason=f"composite {composite:.3f} at or above threshold {threshold:.3f}",
    )
