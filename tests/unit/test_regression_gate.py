"""Unit tests for the Phase 4 regression gate.

After a stable repro, decide whether the bug is a regression: a cited last-good
ref, or a probe that finds an older tag where the repro passes. If neither, it is
not a regression and bisection is skipped in favour of SBFL/localize.
"""

from __future__ import annotations

from app.agents.regression import (
    SandboxRegressionProbe,
    regression_gate,
)
from app.agents.types import BugReport, Language, Repro

REPRO = Repro(
    script="assert f([]) is not None",
    language=Language.PYTHON,
    reproduced=True,
    reproduce_rate=1.0,
    n_runs=5,
)


def _report(**kw: object) -> BugReport:
    return BugReport(repo="octo/repo", base_ref="HEAD", source="manual", title="bug", **kw)


class _NoSandbox:
    def prepare(self, repo, ref):  # type: ignore[no-untyped-def]
        return None

    def run(self, cmd, timeout, *, network=False):  # type: ignore[no-untyped-def]
        from app.sandbox.interface import RunResult

        return RunResult(stdout="", stderr="", exit_code=0, duration=0.0, timed_out=False)

    def read_file(self, path):  # type: ignore[no-untyped-def]
        return b""

    def write_file(self, path, data):  # type: ignore[no-untyped-def]
        return None

    def destroy(self):  # type: ignore[no-untyped-def]
        return None


def test_cited_last_good_ref_is_a_regression() -> None:
    decision = regression_gate(_report(last_good_ref="v1.0"), REPRO, _NoSandbox())
    assert decision.is_regression is True
    assert decision.good_ref == "v1.0"
    assert decision.bad_ref == "HEAD"


def test_no_ref_and_no_probe_is_not_a_regression() -> None:
    decision = regression_gate(_report(), REPRO, _NoSandbox())
    assert decision.is_regression is False
    assert decision.good_ref is None


class _ProbeSandbox:
    """Repro passes (exit 0) at the older tag, fails (nonzero) at HEAD."""

    def __init__(self) -> None:
        self.checked_out: list[str] = []

    def prepare(self, repo, ref):  # type: ignore[no-untyped-def]
        return None

    def run(self, cmd, timeout, *, network=False):  # type: ignore[no-untyped-def]
        from app.sandbox.interface import RunResult

        if "tag" in cmd:
            return RunResult(
                stdout="v0.9\nv0.8\n", stderr="", exit_code=0, duration=0.0, timed_out=False
            )
        if "checkout" in cmd:
            self.checked_out.append(cmd[-1])
            return RunResult(stdout="", stderr="", exit_code=0, duration=0.0, timed_out=False)
        # repro run: passes at the older tag
        return RunResult(stdout="", stderr="", exit_code=0, duration=0.0, timed_out=False)

    def read_file(self, path):  # type: ignore[no-untyped-def]
        return b""

    def write_file(self, path, data):  # type: ignore[no-untyped-def]
        return None

    def destroy(self):  # type: ignore[no-untyped-def]
        return None


def test_probe_finds_older_good_tag() -> None:
    sandbox = _ProbeSandbox()
    probe = SandboxRegressionProbe()
    decision = regression_gate(_report(), REPRO, sandbox, probe=probe)
    assert decision.is_regression is True
    assert decision.good_ref == "v0.9"  # most recent tag where the repro passes
    assert "v0.9" in sandbox.checked_out


def test_cited_ref_takes_precedence_over_probe() -> None:
    # A cited ref short-circuits; the probe is never consulted.
    decision = regression_gate(
        _report(last_good_ref="v2.0"), REPRO, _NoSandbox(), probe=SandboxRegressionProbe()
    )
    assert decision.good_ref == "v2.0"
