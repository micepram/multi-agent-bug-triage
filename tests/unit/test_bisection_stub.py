"""Unit test for the Phase 2 bisection stub.

Phase 2 keeps the bisection node present but always inconclusive so the
SBFL/localize fallback path is exercised. It must contribute no bisection
certainty to the confidence gate.
"""

from __future__ import annotations

from app.agents.bisection import StubBisectionAgent
from app.agents.types import BugReport, Language, Repro


def test_stub_is_always_inconclusive() -> None:
    report = BugReport(
        repo="octo/repo", base_ref="HEAD", source="manual", title="x", last_good_ref="v1"
    )
    repro = Repro(
        script="s", language=Language.PYTHON, reproduced=True, reproduce_rate=1.0, n_runs=5
    )
    outcome = StubBisectionAgent().bisect(report, repro, sandbox=None)  # type: ignore[arg-type]
    assert outcome.conclusive is False
    assert outcome.introducing_commit is None
    assert outcome.certainty is None
