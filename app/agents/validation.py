"""Validation agent (spec Section 5).

Runs against the patched workspace inside the sandbox and emits the hard-gate and
continuous confidence signals:

- Re-run the original repro: it must now pass (hard gate).
- Run the full suite with a regression gate: no new failures (hard gate).
- Run lint / static analysis: a continuous cleanliness signal.
- Reviewer step: an LLM judgment on whether the patch addresses the reported
  symptom versus merely making the repro pass. Injected as a seam; Phase 2 uses a
  simple reviewer, Phase 3 an LLM behind the same protocol.

All execution goes through the Sandbox; this agent never runs repo commands
directly.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from app.agents.types import BugReport, Candidate, Language, Repro, ValidationOutcome
from app.sandbox.interface import Sandbox

_REPRO_PATH = "repro_test.py"
_TIMEOUT = 600


@runtime_checkable
class Reviewer(Protocol):
    def review(self, report: BugReport, repro: Repro, candidate: Candidate) -> float:
        """Return a [0,1] verdict that the patch addresses the reported symptom."""
        ...


class ValidationAgent:
    """Re-run repro + suite + static analysis + reviewer against the patched tree."""

    def __init__(
        self,
        reviewer: Reviewer,
        *,
        test_cmd: list[str] | None = None,
        lint_cmd: list[str] | None = None,
        repro_path: str = _REPRO_PATH,
    ) -> None:
        self._reviewer = reviewer
        self._test_cmd = test_cmd or ["pytest", "-q"]
        self._lint_cmd = lint_cmd or ["ruff", "check", "."]
        self._repro_path = repro_path

    def validate(
        self,
        report: BugReport,
        repro: Repro,
        candidate: Candidate,
        sandbox: Sandbox,
    ) -> ValidationOutcome:
        repro_cmd = self._repro_cmd(repro.language)
        repro_after_patch_passes = sandbox.run(repro_cmd, timeout=_TIMEOUT).exit_code == 0

        # Regression gate: a single new failure fails validation. Phase 2 uses the
        # whole-suite exit status; Phase 4 tightens to previously-passing tests.
        regression_free = sandbox.run(self._test_cmd, timeout=_TIMEOUT).exit_code == 0

        static_analysis_clean = (
            1.0 if sandbox.run(self._lint_cmd, timeout=_TIMEOUT).exit_code == 0 else 0.0
        )

        reviewer_verdict = self._reviewer.review(report, repro, candidate)

        return ValidationOutcome(
            repro_after_patch_passes=repro_after_patch_passes,
            regression_free=regression_free,
            static_analysis_clean=static_analysis_clean,
            reviewer_verdict=reviewer_verdict,
        )

    def _repro_cmd(self, language: Language) -> list[str]:
        if language is Language.PYTHON:
            return ["python", self._repro_path]
        return ["sh", self._repro_path]
