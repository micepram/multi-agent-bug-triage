"""Unit tests for the Phase 2 heuristic Localize agent.

No LLM/SBFL yet: extract candidate fault files from the repro stack trace and the
report body, ranked by prominence. A conclusive bisection introducing commit,
when present, is folded in as a strong prior (boosts its files to the top).
"""

from __future__ import annotations

from app.agents.localize import HeuristicLocalizeAgent
from app.agents.types import (
    BisectionOutcome,
    BugReport,
    Language,
    Repro,
)

REPORT = BugReport(repo="octo/repo", base_ref="v1", source="manual", title="crash")
NO_BISECTION = BisectionOutcome(conclusive=False)


def _repro(trace: str) -> Repro:
    return Repro(
        script="s",
        language=Language.PYTHON,
        reproduced=True,
        reproduce_rate=1.0,
        n_runs=5,
        stack_trace=trace,
    )


class _NoSandbox:
    def prepare(self, repo: str, ref: str):  # type: ignore[no-untyped-def]
        return None

    def run(self, cmd, timeout, *, network=False):  # type: ignore[no-untyped-def]
        from app.sandbox.interface import RunResult

        return RunResult(stdout="", stderr="", exit_code=0, duration=0.0, timed_out=False)

    def read_file(self, path: str) -> bytes:
        return b""

    def write_file(self, path: str, data: bytes) -> None:
        return None

    def destroy(self) -> None:
        return None


def test_extracts_file_from_python_traceback() -> None:
    agent = HeuristicLocalizeAgent()
    trace = 'Traceback:\n  File "src/pkg/mod.py", line 12, in f\n    x[0]\nIndexError'
    loc = agent.localize(REPORT, _repro(trace), NO_BISECTION, _NoSandbox())
    files = [location.file for location in loc.locations]
    assert "src/pkg/mod.py" in files


def test_no_paths_yields_empty_localization() -> None:
    agent = HeuristicLocalizeAgent()
    loc = agent.localize(REPORT, _repro("something went wrong"), NO_BISECTION, _NoSandbox())
    assert loc.locations == []


def test_bisection_diff_files_are_boosted_to_top() -> None:
    agent = HeuristicLocalizeAgent()
    trace = 'File "src/other.py", line 3, in g'
    bisection = BisectionOutcome(
        introducing_commit="abc",
        conclusive=True,
        # files carried on the outcome by Phase 4; Phase 2 reads them if present.
    )
    bisection_with_files = bisection.model_copy(update={})
    loc = agent.localize(
        REPORT,
        _repro(trace),
        bisection_with_files,
        _NoSandbox(),
        introducing_files=["src/culprit.py"],
    )
    assert loc.locations[0].file == "src/culprit.py"
    assert loc.locations[0].score >= max(
        (location.score for location in loc.locations[1:]), default=0.0
    )
