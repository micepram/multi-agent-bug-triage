"""Unit tests for the GitBisectionAgent policy (spec Phase 4).

Determinism-gated and skip-aware: flaky repros never bisect; a high skip ratio is
reported as inconclusive rather than a false introducing commit; a clean bisect
yields the introducing commit, its files, and full certainty.
"""

from __future__ import annotations

from app.agents.bisection import GitBisectionAgent
from app.agents.types import BugReport, Language, Repro

SHA = "abcdef1234567890abcdef1234567890abcdef12"


def _repro(*, deterministic: bool = True) -> Repro:
    return Repro(
        script="assert False",
        language=Language.PYTHON,
        reproduced=True,
        reproduce_rate=1.0 if deterministic else 0.5,
        n_runs=5,
    )


def _report(**kw: object) -> BugReport:
    return BugReport(repo="octo/repo", base_ref="HEAD", source="manual", title="bug", **kw)


class FakeSandbox:
    def __init__(
        self, *, bisect_output: str, log: str, show_files: str = "src/culprit.py\n"
    ) -> None:
        self._bisect_output = bisect_output
        self._log = log
        self._show_files = show_files
        self.ran_bisect = False
        self.writes: dict[str, bytes] = {}

    def prepare(self, repo, ref):  # type: ignore[no-untyped-def]
        return None

    def run(self, cmd, timeout, *, network=False):  # type: ignore[no-untyped-def]
        from app.sandbox.interface import RunResult

        joined = " ".join(cmd)
        if "bisect" in joined:
            self.ran_bisect = True
            return RunResult(
                stdout=self._bisect_output, stderr="", exit_code=0, duration=0.1, timed_out=False
            )
        if "show" in cmd:
            return RunResult(
                stdout=self._show_files, stderr="", exit_code=0, duration=0.1, timed_out=False
            )
        return RunResult(stdout="", stderr="", exit_code=0, duration=0.1, timed_out=False)

    def read_file(self, path):  # type: ignore[no-untyped-def]
        return self._log.encode()

    def write_file(self, path, data):  # type: ignore[no-untyped-def]
        self.writes[path] = data

    def destroy(self):  # type: ignore[no-untyped-def]
        return None


def test_flaky_repro_never_bisects() -> None:
    sbx = FakeSandbox(bisect_output="", log="")
    outcome = GitBisectionAgent().bisect(
        _report(last_good_ref="v1"), _repro(deterministic=False), sbx
    )
    assert outcome.conclusive is False
    assert outcome.introducing_commit is None
    assert sbx.ran_bisect is False


def test_non_regression_is_inconclusive() -> None:
    sbx = FakeSandbox(bisect_output="", log="")
    outcome = GitBisectionAgent().bisect(_report(), _repro(), sbx)  # no last_good_ref, no probe
    assert outcome.conclusive is False
    assert sbx.ran_bisect is False


def test_clean_bisect_reports_introducing_commit_and_files() -> None:
    output = f"Bisecting: 2 revisions left\n{SHA} is the first bad commit\n"
    sbx = FakeSandbox(bisect_output=output, log="good\nbad\ngood\n")
    outcome = GitBisectionAgent().bisect(_report(last_good_ref="v1.0"), _repro(), sbx)
    assert outcome.conclusive is True
    assert outcome.introducing_commit == SHA
    assert outcome.skip_ratio == 0.0
    assert outcome.certainty == 1.0
    assert outcome.introducing_files == ["src/culprit.py"]
    assert outcome.good_ref == "v1.0"


def test_high_skip_ratio_is_inconclusive_even_with_a_named_commit() -> None:
    output = f"{SHA} is the first bad commit\n"
    sbx = FakeSandbox(bisect_output=output, log="skip\nskip\nskip\ngood\n")  # 0.75 skip
    outcome = GitBisectionAgent(max_skip_ratio=0.5).bisect(
        _report(last_good_ref="v1.0"), _repro(), sbx
    )
    assert outcome.conclusive is False
    assert outcome.introducing_commit is None  # never a false introducing commit
    assert outcome.skip_ratio == 0.75
    assert outcome.certainty is None


def test_no_first_bad_commit_is_inconclusive() -> None:
    sbx = FakeSandbox(bisect_output="only skipped commits left to test\n", log="skip\nskip\n")
    outcome = GitBisectionAgent().bisect(_report(last_good_ref="v1.0"), _repro(), sbx)
    assert outcome.conclusive is False
    assert outcome.introducing_commit is None
