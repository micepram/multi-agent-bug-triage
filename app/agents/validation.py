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

import re
from typing import Protocol, runtime_checkable

from app.agents.types import BugReport, Candidate, Repro, ValidationOutcome
from app.providers.base import Message
from app.providers.client import LLMClient
from app.sandbox.interface import Sandbox
from app.sandbox.profiles import profile_for

_TIMEOUT = 600


@runtime_checkable
class Reviewer(Protocol):
    def review(self, report: BugReport, repro: Repro, candidate: Candidate) -> float:
        """Return a [0,1] verdict that the patch addresses the reported symptom."""
        ...


_REVIEW_PROMPT = (
    "You are a patch reviewer. Judge whether the patch addresses the reported "
    "symptom's root cause, versus merely making a test pass. Respond with a single "
    "confidence number between 0 and 1 (or YES/NO)."
)


class LLMReviewer:
    """LLM-backed Reviewer (Phase 3) behind the provider interface."""

    def __init__(self, llm: LLMClient) -> None:
        self._llm = llm

    def review(self, report: BugReport, repro: Repro, candidate: Candidate) -> float:
        messages = [
            Message(role="system", content=_REVIEW_PROMPT),
            Message(
                role="user",
                content=(
                    f"Issue: {report.title}\n{report.body}\n\n"
                    f"Stack trace:\n{repro.stack_trace}\n\nPatch:\n{candidate.diff}"
                ),
            ),
        ]
        return _parse_verdict(self._llm.complete(messages).text)


def _parse_verdict(text: str) -> float:
    """Parse a [0,1] verdict from a number or a yes/no; neutral 0.5 if unclear."""
    match = re.search(r"-?\d+(?:\.\d+)?", text)
    if match is not None:
        return max(0.0, min(1.0, float(match.group())))
    lowered = text.lower()
    if "yes" in lowered:
        return 0.9
    if "no" in lowered:
        return 0.1
    return 0.5


class ValidationAgent:
    """Re-run repro + suite + static analysis + reviewer against the patched tree."""

    def __init__(
        self,
        reviewer: Reviewer,
        *,
        test_cmd: list[str] | None = None,
        lint_cmd: list[str] | None = None,
    ) -> None:
        self._reviewer = reviewer
        self._test_cmd_override = test_cmd
        self._lint_cmd_override = lint_cmd

    def validate(
        self,
        report: BugReport,
        repro: Repro,
        candidate: Candidate,
        sandbox: Sandbox,
    ) -> ValidationOutcome:
        # Language-specific commands come from the profile so validation works
        # for both the Python and JVM execution profiles.
        profile = profile_for(repro.language)
        repro_cmd = profile.repro_cmd(profile.repro_filename)
        test_cmd = self._test_cmd_override or profile.test_cmd
        lint_cmd = self._lint_cmd_override or profile.lint_cmd

        repro_after_patch_passes = sandbox.run(repro_cmd, timeout=_TIMEOUT).exit_code == 0

        # Regression gate: a single new failure fails validation. Phase 2 uses the
        # whole-suite exit status; Phase 4 tightens to previously-passing tests.
        regression_free = sandbox.run(test_cmd, timeout=_TIMEOUT).exit_code == 0

        static_analysis_clean = (
            1.0 if sandbox.run(lint_cmd, timeout=_TIMEOUT).exit_code == 0 else 0.0
        )

        reviewer_verdict = self._reviewer.review(report, repro, candidate)

        return ValidationOutcome(
            repro_after_patch_passes=repro_after_patch_passes,
            regression_free=regression_free,
            static_analysis_clean=static_analysis_clean,
            reviewer_verdict=reviewer_verdict,
        )
