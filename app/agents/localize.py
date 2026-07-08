"""Localize agents (spec Sections 3, 5; Phases 2-4).

- ``HeuristicLocalizeAgent`` (Phase 2): extract candidate fault files from the
  stack trace and report, ranked by prominence.
- ``LLMLocalizeAgent`` (Phase 3): SBFL (Ochiai) suspiciousness blended with LLM
  code-search over the repo.

Both fold a conclusive bisection introducing diff (its files) in as a strong
prior that floats to the top of the ranking.
"""

from __future__ import annotations

import json
import re
from typing import Protocol, runtime_checkable

from app.agents.sbfl import Spectrum, rank_suspicious
from app.agents.types import (
    BisectionOutcome,
    BugReport,
    FaultLocation,
    Localization,
    Repro,
)
from app.providers.base import Message
from app.providers.client import LLMClient
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


@runtime_checkable
class CoverageCollector(Protocol):
    def collect(self, sandbox: Sandbox) -> Spectrum | None:
        """Gather a failing/passing coverage spectrum, or None if unavailable.

        Runs the suite with per-test coverage in the sandbox; only ever executes
        repo code via the Sandbox.
        """
        ...


class NullCoverageCollector:
    """No spectrum (SBFL disabled). Localization falls back to LLM + prior.

    A real per-test coverage collector (Python ``coverage`` contexts / Java
    JaCoCo, sandboxed) implements the same seam; the Ochiai math is already in
    ``app.agents.sbfl`` and ranks the spectrum once one is wired in.
    """

    def collect(self, sandbox: Sandbox) -> Spectrum | None:
        return None


_CODE_SEARCH_PROMPT = (
    "You are a fault-localization assistant. Given a bug report and a stack "
    "trace, list the repository source files most likely to contain the fault. "
    'Respond ONLY with JSON: {"files": ["path/one.py", ...]}, most likely first.'
)

# Weights blending the three localization signals. The bisection prior strictly
# dominates: its weight exceeds the largest possible SBFL + LLM sum (0.6 + 0.4).
_W_PRIOR = 2.0
_W_SBFL = 0.6
_W_LLM = 0.4


class LLMLocalizeAgent:
    """SBFL suspiciousness + LLM code-search, with a bisection prior on top."""

    def __init__(
        self,
        llm: LLMClient,
        coverage_collector: CoverageCollector,
        *,
        max_locations: int = 10,
    ) -> None:
        self._llm = llm
        self._coverage = coverage_collector
        self._max_locations = max_locations

    def localize(
        self,
        report: BugReport,
        repro: Repro,
        bisection: BisectionOutcome,
        sandbox: Sandbox,
        *,
        introducing_files: list[str] | None = None,
    ) -> Localization:
        scores: dict[str, float] = {}

        # 1. Spectrum-based fault localization, when a suite/coverage exists.
        spectrum = self._coverage.collect(sandbox)
        if spectrum is not None:
            for element, score in rank_suspicious(spectrum):
                path = element.split(":", 1)[0]
                scores[path] = scores.get(path, 0.0) + _W_SBFL * score

        # 2. LLM code-search over the repo, ranked, decaying by position.
        llm_files = self._llm_code_search(report, repro)
        n = max(len(llm_files), 1)
        for i, path in enumerate(llm_files):
            scores[path] = scores.get(path, 0.0) + _W_LLM * (1.0 - i / n)

        # 3. Bisection introducing diff as a strong prior (floats to the top).
        # Defaults to the files carried on a conclusive bisection outcome; an
        # explicit argument overrides (used in tests).
        prior_files = (
            introducing_files if introducing_files is not None else bisection.introducing_files
        )
        prior = prior_files if (bisection.conclusive and prior_files) else []
        for path in _ordered_unique(prior):
            scores[path] = scores.get(path, 0.0) + _W_PRIOR

        ranked = sorted(scores.items(), key=lambda item: (-item[1], item[0]))
        locations = [
            FaultLocation(file=path, symbol=None, score=score)
            for path, score in ranked[: self._max_locations]
        ]
        return Localization(locations=locations)

    def _llm_code_search(self, report: BugReport, repro: Repro) -> list[str]:
        messages = [
            Message(role="system", content=_CODE_SEARCH_PROMPT),
            Message(
                role="user",
                content=(
                    f"Repo: {report.repo}\nTitle: {report.title}\n"
                    f"Report:\n{report.body}\n\nStack trace:\n{repro.stack_trace}"
                ),
            ),
        ]
        text = self._llm.complete(messages).text
        return _parse_files(text)


def _parse_files(text: str) -> list[str]:
    """Parse a file list from LLM output; tolerate non-JSON by regex fallback."""
    try:
        data = json.loads(text)
        if isinstance(data, dict) and isinstance(data.get("files"), list):
            return _ordered_unique([str(f) for f in data["files"]])
        if isinstance(data, list):
            return _ordered_unique([str(f) for f in data])
    except (json.JSONDecodeError, TypeError):
        pass
    return _ordered_unique(_PATH_RE.findall(text))
