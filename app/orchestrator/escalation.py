"""Self-contained escalation report builder (spec Section 6).

A human reading only this report must understand what happened without opening
the codebase: the triage record, the repro (or why it failed), the bisection
result (or why it was skipped), any candidate patches and why they failed, and
the specific gate(s) that triggered escalation.
"""

from __future__ import annotations

from app.agents.types import (
    BisectionOutcome,
    BugReport,
    Candidate,
    Repro,
    TriageRecord,
)
from app.orchestrator.confidence import GateDecision


def build_escalation_report(
    *,
    report: BugReport,
    reason: str,
    triage: TriageRecord | None = None,
    repro: Repro | None = None,
    repro_failure: str | None = None,
    bisection: BisectionOutcome | None = None,
    bisection_skipped_reason: str | None = None,
    candidates: list[Candidate] | None = None,
    gate: GateDecision | None = None,
) -> dict[str, object]:
    """Assemble the structured, self-contained human-handoff report."""
    return {
        "reason": reason,
        "bug_report": report.model_dump(),
        "triage": triage.model_dump() if triage else None,
        "repro": repro.model_dump() if repro else None,
        "repro_failure": repro_failure,
        "bisection": bisection.model_dump() if bisection else None,
        "bisection_skipped_reason": bisection_skipped_reason,
        "candidates": [c.model_dump() for c in (candidates or [])],
        "gate": gate.model_dump() if gate else None,
    }
