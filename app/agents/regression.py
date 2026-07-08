"""The regression gate (spec Phase 4).

Decides whether a stably-reproduced bug is a regression, and if so between which
refs to bisect. A cited last-good ref is authoritative. Otherwise a probe may
look for the most recent older tag where the repro passes (bug absent) while it
fails at HEAD. If neither holds, it is not a regression and the pipeline skips
bisection in favour of SBFL/localize.

The probe runs the repro at older refs; it only ever executes repo code through
the Sandbox.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from pydantic import BaseModel

from app.agents.types import BugReport, Language, Repro
from app.sandbox.interface import Sandbox

_PROBE_TIMEOUT = 300
_MAX_TAGS_PROBED = 5


class RegressionDecision(BaseModel):
    is_regression: bool
    good_ref: str | None
    bad_ref: str
    reason: str


@runtime_checkable
class RegressionProbe(Protocol):
    def find_last_good_ref(self, report: BugReport, repro: Repro, sandbox: Sandbox) -> str | None:
        """Return an older ref where the repro passes, or None."""
        ...


class SandboxRegressionProbe:
    """Probe recent tags for the newest one where the repro no longer fires."""

    def __init__(self, *, max_tags: int = _MAX_TAGS_PROBED) -> None:
        self._max_tags = max_tags

    def find_last_good_ref(self, report: BugReport, repro: Repro, sandbox: Sandbox) -> str | None:
        tags = self._recent_tags(sandbox)
        repro_cmd = _repro_cmd(repro.language)
        for tag in tags[: self._max_tags]:
            if sandbox.run(["git", "checkout", tag], timeout=_PROBE_TIMEOUT).exit_code != 0:
                continue
            # Repro passes (exit 0) here => the bug is absent at this older tag.
            if sandbox.run(repro_cmd, timeout=_PROBE_TIMEOUT).exit_code == 0:
                return tag
        return None

    def _recent_tags(self, sandbox: Sandbox) -> list[str]:
        result = sandbox.run(
            ["git", "tag", "--sort=-creatordate", "--merged", "HEAD"], timeout=_PROBE_TIMEOUT
        )
        return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def regression_gate(
    report: BugReport,
    repro: Repro,
    sandbox: Sandbox,
    *,
    probe: RegressionProbe | None = None,
) -> RegressionDecision:
    """Decide regression-ness and the good/bad refs to bisect between."""
    bad_ref = report.base_ref
    if report.last_good_ref:
        return RegressionDecision(
            is_regression=True,
            good_ref=report.last_good_ref,
            bad_ref=bad_ref,
            reason="report cites a last-good ref",
        )
    if probe is not None:
        good = probe.find_last_good_ref(report, repro, sandbox)
        if good is not None:
            return RegressionDecision(
                is_regression=True,
                good_ref=good,
                bad_ref=bad_ref,
                reason=f"repro passes at older ref {good}",
            )
    return RegressionDecision(
        is_regression=False,
        good_ref=None,
        bad_ref=bad_ref,
        reason="no last-good ref found; treating as non-regression",
    )


def _repro_cmd(language: Language) -> list[str]:
    if language is Language.PYTHON:
        return ["python", "repro_test.py"]
    return ["sh", "repro_test.py"]
