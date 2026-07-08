"""Unit tests for the Validation agent (spec Section 5).

Against the patched workspace: the repro must now pass, the full suite must have
no new failures (regression gate), static analysis runs, and an LLM Reviewer
judges whether the patch addresses the reported symptom. Command outcomes come
from a fake sandbox keyed by command; the reviewer is injected.
"""

from __future__ import annotations

from app.agents.types import BugReport, Candidate, Language, Repro
from app.agents.validation import ValidationAgent
from app.sandbox.interface import RunResult

REPORT = BugReport(repo="octo/repo", base_ref="v1", source="manual", title="bug")
REPRO = Repro(
    script="assert f([]) is not None",
    language=Language.PYTHON,
    reproduced=True,
    reproduce_rate=1.0,
    n_runs=5,
)
CANDIDATE = Candidate(kind="revert", diff="d", files_touched=1, lines_touched=3)


class KeyedSandbox:
    """Fake sandbox returning an exit code per command class."""

    def __init__(self, *, repro_code: int, suite_code: int, lint_code: int) -> None:
        self._codes = {"repro": repro_code, "suite": suite_code, "lint": lint_code}

    def prepare(self, repo: str, ref: str):  # type: ignore[no-untyped-def]
        return None

    def run(self, cmd, timeout, *, network=False) -> RunResult:  # type: ignore[no-untyped-def]
        if "repro_test.py" in cmd:
            code = self._codes["repro"]
        elif "ruff" in cmd or "mypy" in cmd:
            code = self._codes["lint"]
        else:
            code = self._codes["suite"]
        return RunResult(stdout="", stderr="", exit_code=code, duration=0.1, timed_out=False)

    def read_file(self, path: str) -> bytes:
        return b""

    def write_file(self, path: str, data: bytes) -> None:
        return None

    def destroy(self) -> None:
        return None


class FixedReviewer:
    def __init__(self, verdict: float) -> None:
        self._verdict = verdict

    def review(self, report: BugReport, repro: Repro, candidate: Candidate) -> float:
        return self._verdict


def test_clean_patch_passes_validation() -> None:
    agent = ValidationAgent(FixedReviewer(0.9))
    sbx = KeyedSandbox(repro_code=0, suite_code=0, lint_code=0)
    outcome = agent.validate(REPORT, REPRO, CANDIDATE, sbx)
    assert outcome.repro_after_patch_passes is True
    assert outcome.regression_free is True
    assert outcome.static_analysis_clean == 1.0
    assert outcome.reviewer_verdict == 0.9


def test_repro_still_failing_after_patch() -> None:
    agent = ValidationAgent(FixedReviewer(0.9))
    sbx = KeyedSandbox(repro_code=1, suite_code=0, lint_code=0)  # bug still fires
    outcome = agent.validate(REPORT, REPRO, CANDIDATE, sbx)
    assert outcome.repro_after_patch_passes is False


def test_suite_failure_flags_regression() -> None:
    agent = ValidationAgent(FixedReviewer(0.9))
    sbx = KeyedSandbox(repro_code=0, suite_code=1, lint_code=0)
    outcome = agent.validate(REPORT, REPRO, CANDIDATE, sbx)
    assert outcome.regression_free is False


def test_lint_failure_lowers_static_signal() -> None:
    agent = ValidationAgent(FixedReviewer(0.9))
    sbx = KeyedSandbox(repro_code=0, suite_code=0, lint_code=1)
    outcome = agent.validate(REPORT, REPRO, CANDIDATE, sbx)
    assert outcome.static_analysis_clean < 1.0
