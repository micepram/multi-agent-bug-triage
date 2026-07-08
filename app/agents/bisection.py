"""Bisection agent (spec Phase 4). Phase 2 ships the stub only.

The real skip-aware, bounded, determinism-gated bisection lands in Phase 4. Until
then the node is present but always returns "inconclusive" so the SBFL/localize
fallback path is exercised end to end and contributes no false bisection prior.
"""

from __future__ import annotations

from app.agents.types import BisectionOutcome, BugReport, Repro
from app.sandbox.interface import Sandbox


class StubBisectionAgent:
    """Always inconclusive (Phase 2). Never reports a false introducing commit."""

    def bisect(self, report: BugReport, repro: Repro, sandbox: Sandbox) -> BisectionOutcome:
        return BisectionOutcome(
            good_ref=report.last_good_ref,
            bad_ref=report.base_ref,
            introducing_commit=None,
            skip_ratio=None,
            conclusive=False,
        )
