"""Unit tests for the confidence gate (spec Section 6).

The gate composes *measurable* signals only. Any hard-gate failure escalates
regardless of the composite; otherwise the continuous signals form a composite
and escalation happens below a calibrated, config-supplied threshold. LLM
self-reported confidence is never an input.
"""

from __future__ import annotations

from app.orchestrator.confidence import GateConfig, Signals, evaluate

# A "clean" signal set that should comfortably pass the gate.
CLEAN = Signals(
    reproduce_rate=1.0,
    bisection_certainty=1.0,
    repro_after_patch_passes=True,
    regression_free=True,
    static_analysis_clean=1.0,
    reviewer_verdict=1.0,
    files_touched=1,
    lines_touched=5,
)

THRESHOLD = 0.7
CFG = GateConfig()


def test_clean_signals_pass_the_gate() -> None:
    decision = evaluate(CLEAN, threshold=THRESHOLD, config=CFG)
    assert decision.escalate is False
    assert decision.composite >= THRESHOLD
    assert decision.hard_gate_failures == []


def test_repro_after_patch_failure_is_a_hard_gate() -> None:
    signals = CLEAN.model_copy(update={"repro_after_patch_passes": False})
    decision = evaluate(signals, threshold=THRESHOLD, config=CFG)
    assert decision.escalate is True
    assert "repro_after_patch" in decision.hard_gate_failures


def test_regression_is_a_hard_gate() -> None:
    signals = CLEAN.model_copy(update={"regression_free": False})
    decision = evaluate(signals, threshold=THRESHOLD, config=CFG)
    assert decision.escalate is True
    assert "regression" in decision.hard_gate_failures


def test_reproduce_rate_below_floor_is_a_hard_gate() -> None:
    signals = CLEAN.model_copy(update={"reproduce_rate": 0.2})
    decision = evaluate(signals, threshold=THRESHOLD, config=CFG)
    assert decision.escalate is True
    assert "reproduce_rate" in decision.hard_gate_failures


def test_hard_gate_overrides_high_composite() -> None:
    # Even with otherwise perfect signals, a single hard-gate failure escalates.
    signals = CLEAN.model_copy(update={"regression_free": False})
    decision = evaluate(signals, threshold=0.0, config=CFG)
    assert decision.escalate is True


def test_low_composite_escalates_without_hard_gate_failure() -> None:
    signals = CLEAN.model_copy(
        update={"static_analysis_clean": 0.0, "reviewer_verdict": 0.0, "lines_touched": 4000}
    )
    decision = evaluate(signals, threshold=0.9, config=CFG)
    assert decision.hard_gate_failures == []
    assert decision.composite < 0.9
    assert decision.escalate is True


def test_smaller_blast_radius_yields_higher_composite() -> None:
    small = CLEAN.model_copy(update={"files_touched": 1, "lines_touched": 3})
    large = CLEAN.model_copy(update={"files_touched": 20, "lines_touched": 2000})
    d_small = evaluate(small, threshold=THRESHOLD, config=CFG)
    d_large = evaluate(large, threshold=THRESHOLD, config=CFG)
    assert d_small.composite > d_large.composite


def test_absent_bisection_certainty_is_handled() -> None:
    # Non-regression bugs have no bisection signal; the composite still computes.
    signals = CLEAN.model_copy(update={"bisection_certainty": None})
    decision = evaluate(signals, threshold=THRESHOLD, config=CFG)
    assert 0.0 <= decision.composite <= 1.0
    assert decision.escalate is False


def test_composite_is_bounded_unit_interval() -> None:
    decision = evaluate(CLEAN, threshold=THRESHOLD, config=CFG)
    assert 0.0 <= decision.composite <= 1.0
