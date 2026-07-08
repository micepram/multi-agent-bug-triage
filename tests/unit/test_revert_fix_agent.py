"""Unit tests for the Phase 2 revert Fix strategy (SapFix full-revert).

The agent reverts a candidate commit in the sandbox and captures the diff. The
target commit comes from a conclusive bisection when present, else the last
commit touching the top localized file. With neither, there is no candidate and
the run escalates. A fake sandbox returns canned git output.
"""

from __future__ import annotations

from app.agents.fix import RevertFixAgent
from app.agents.types import (
    BisectionOutcome,
    BugReport,
    FaultLocation,
    Language,
    Localization,
    Repro,
)
from app.sandbox.interface import RunResult

REPORT = BugReport(repo="octo/repo", base_ref="v1", source="manual", title="bug")
REPRO = Repro(script="s", language=Language.PYTHON, reproduced=True, reproduce_rate=1.0, n_runs=5)

SAMPLE_DIFF = (
    "diff --git a/src/mod.py b/src/mod.py\n"
    "--- a/src/mod.py\n"
    "+++ b/src/mod.py\n"
    "@@ -1,3 +1,3 @@\n"
    "-bad_line_one\n"
    "-bad_line_two\n"
    "+good_line\n"
)


class GitSandbox:
    """Fake sandbox that answers git subcommands used by the revert strategy."""

    def __init__(self, *, log_sha: str = "dead000", diff: str = SAMPLE_DIFF) -> None:
        self._log_sha = log_sha
        self._diff = diff

    def prepare(self, repo: str, ref: str):  # type: ignore[no-untyped-def]
        return None

    def run(self, cmd, timeout, *, network=False) -> RunResult:  # type: ignore[no-untyped-def]
        if "log" in cmd:
            out = self._log_sha
        elif "revert" in cmd:
            out = ""
        elif "diff" in cmd:
            out = self._diff
        else:
            out = ""
        code = 0 if self._log_sha or "revert" not in cmd else 1
        return RunResult(stdout=out, stderr="", exit_code=code, duration=0.1, timed_out=False)

    def read_file(self, path: str) -> bytes:
        return b""

    def write_file(self, path: str, data: bytes) -> None:
        return None

    def destroy(self) -> None:
        return None


def _localization(*files: str) -> Localization:
    return Localization(locations=[FaultLocation(file=f, score=1.0) for f in files])


def test_reverts_bisection_introducing_commit_when_present() -> None:
    agent = RevertFixAgent()
    sbx = GitSandbox()
    bisection = BisectionOutcome(introducing_commit="abc123", conclusive=True)
    candidates = agent.generate_candidates(REPORT, REPRO, _localization(), bisection, sbx)
    assert len(candidates) == 1
    assert candidates[0].kind == "revert"
    assert candidates[0].diff == SAMPLE_DIFF


def test_falls_back_to_last_commit_touching_top_file() -> None:
    agent = RevertFixAgent()
    sbx = GitSandbox(log_sha="feed456")
    candidates = agent.generate_candidates(
        REPORT, REPRO, _localization("src/mod.py"), BisectionOutcome(conclusive=False), sbx
    )
    assert len(candidates) == 1
    assert candidates[0].kind == "revert"


def test_no_target_yields_no_candidate() -> None:
    agent = RevertFixAgent()
    sbx = GitSandbox(log_sha="")  # git log finds nothing
    candidates = agent.generate_candidates(
        REPORT, REPRO, _localization(), BisectionOutcome(conclusive=False), sbx
    )
    assert candidates == []


def test_blast_radius_counted_from_diff() -> None:
    agent = RevertFixAgent()
    sbx = GitSandbox()
    bisection = BisectionOutcome(introducing_commit="abc123", conclusive=True)
    [cand] = agent.generate_candidates(REPORT, REPRO, _localization(), bisection, sbx)
    assert cand.files_touched == 1
    assert cand.lines_touched == 3  # two '-' lines + one '+' line


def test_select_returns_single_candidate() -> None:
    agent = RevertFixAgent()
    sbx = GitSandbox()
    bisection = BisectionOutcome(introducing_commit="abc123", conclusive=True)
    candidates = agent.generate_candidates(REPORT, REPRO, _localization(), bisection, sbx)
    assert agent.select(candidates, REPORT) is candidates[0]
    assert agent.select([], REPORT) is None
