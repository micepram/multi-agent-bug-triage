"""Phase 2 heuristic Localize agent (spec Sections 3, 5).

The Phase 2 stand-in for SBFL + LLM code-search: extract candidate fault files
from the repro stack trace and the report body, ranked by prominence. When a
conclusive bisection introducing commit is available, its touched files are
folded in as a strong prior and float to the top of the ranking. Phase 3 replaces
the heuristic body with real spectrum-based localization and LLM code-search
behind this same interface.
"""

from __future__ import annotations

import re

from app.agents.types import (
    BisectionOutcome,
    BugReport,
    FaultLocation,
    Localization,
    Repro,
)
from app.sandbox.interface import Sandbox

# Matches path-like tokens ending in a source extension, e.g. src/pkg/mod.py.
_PATH_RE = re.compile(r"[\w./-]+\.(?:py|java)")


class HeuristicLocalizeAgent:
    """Rank fault files from stack-trace/report text, with a bisection prior."""

    def localize(
        self,
        report: BugReport,
        repro: Repro,
        bisection: BisectionOutcome,
        sandbox: Sandbox,
        *,
        introducing_files: list[str] | None = None,
    ) -> Localization:
        # Order matters: stack trace first (most specific), then report body.
        ranked = _ordered_unique(
            _PATH_RE.findall(repro.stack_trace) + _PATH_RE.findall(report.body)
        )

        prior = introducing_files if (bisection.conclusive and introducing_files) else []
        prior = _ordered_unique(prior)

        locations: list[FaultLocation] = []
        # Bisection-implicated files are the strongest prior: score them highest.
        for i, path in enumerate(prior):
            locations.append(FaultLocation(file=path, symbol=None, score=1.0 - i * 0.01))
        # Then stack-trace/report files, below any prior, decaying by rank.
        base = 0.9 if prior else 1.0
        for i, path in enumerate(p for p in ranked if p not in prior):
            locations.append(FaultLocation(file=path, symbol=None, score=base - i * 0.05))

        return Localization(locations=locations)


def _ordered_unique(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out
