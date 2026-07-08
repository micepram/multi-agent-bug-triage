"""Eval slice runner (spec Section 8).

Drives the pipeline over a slice of instances, guaranteeing every instance
produces a result and none crash the orchestrator. Collects the metrics the spec
asks for: resolve rate, draft-PR vs. escalation counts, and median wall-clock
time from ingestion to draft-PR-or-escalation.
"""

from __future__ import annotations

import statistics
import time
from collections.abc import Callable

from pydantic import BaseModel

from app.eval.swebench import Instance
from app.orchestrator.dag import RunOutcome

# run_one drives one instance to an outcome; scorer labels resolution against the
# instance's ground truth (e.g. the fail-to-pass test on the produced patch).
RunOne = Callable[[Instance], RunOutcome]
Scorer = Callable[[Instance, RunOutcome], bool]


class EvalResult(BaseModel):
    instance_id: str
    status: str  # 'draft_pr' | 'escalated' | 'crashed'
    confidence: float | None
    resolved: bool
    duration_s: float
    error: str | None = None


class EvalReport(BaseModel):
    results: list[EvalResult]
    n: int
    resolve_rate: float
    draft_pr: int
    escalated: int
    crashed: int
    median_time_s: float


def run_slice(instances: list[Instance], run_one: RunOne, scorer: Scorer) -> EvalReport:
    """Run every instance; catch crashes so one failure never aborts the slice."""
    results: list[EvalResult] = []
    for instance in instances:
        started = time.monotonic()
        try:
            outcome = run_one(instance)
            duration = time.monotonic() - started
            resolved = outcome.status == "draft_pr" and scorer(instance, outcome)
            results.append(
                EvalResult(
                    instance_id=instance.instance_id,
                    status=outcome.status,
                    confidence=outcome.confidence,
                    resolved=resolved,
                    duration_s=duration,
                )
            )
        except Exception as exc:
            results.append(
                EvalResult(
                    instance_id=instance.instance_id,
                    status="crashed",
                    confidence=None,
                    resolved=False,
                    duration_s=time.monotonic() - started,
                    error=repr(exc),
                )
            )
    return _summarize(results)


def _summarize(results: list[EvalResult]) -> EvalReport:
    n = len(results)
    resolved = sum(1 for r in results if r.resolved)
    return EvalReport(
        results=results,
        n=n,
        resolve_rate=(resolved / n) if n else 0.0,
        draft_pr=sum(1 for r in results if r.status == "draft_pr"),
        escalated=sum(1 for r in results if r.status == "escalated"),
        crashed=sum(1 for r in results if r.status == "crashed"),
        median_time_s=statistics.median([r.duration_s for r in results]) if results else 0.0,
    )
