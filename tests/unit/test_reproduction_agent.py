"""Unit tests for the Reproduction agent's determinism check (spec Section 5).

The agent writes a synthesized repro into the sandbox, runs it N times, and
records the reproduce rate. A repro that fires on every run is deterministic; an
intermittent one is flaky and must not later enter bisection. A synthesizer that
returns nothing yields a non-reproduced result so the orchestrator can retry.
"""

from __future__ import annotations

from app.agents.reproduction import ReproductionAgent
from app.agents.types import BugReport, Language
from app.sandbox.interface import RunResult

REPORT = BugReport(repo="octo/repo", base_ref="v1", source="manual", title="IndexError on []")


class SequencedSandbox:
    """Fake sandbox returning preset exit codes for successive runs."""

    def __init__(self, exit_codes: list[int]) -> None:
        self._exit_codes = list(exit_codes)
        self.written: dict[str, bytes] = {}
        self.run_count = 0

    def prepare(self, repo: str, ref: str):  # type: ignore[no-untyped-def]
        return None

    def run(self, cmd, timeout, *, network=False) -> RunResult:  # type: ignore[no-untyped-def]
        code = self._exit_codes[self.run_count]
        self.run_count += 1
        return RunResult(
            stdout="",
            stderr="IndexError: list index out of range",
            exit_code=code,
            duration=0.1,
            timed_out=False,
        )

    def read_file(self, path: str) -> bytes:
        return b""

    def write_file(self, path: str, data: bytes) -> None:
        self.written[path] = data

    def destroy(self) -> None:
        return None


class FixedSynth:
    def __init__(self, script: str | None) -> None:
        self._script = script

    def synthesize(self, report: BugReport, attempt: int) -> str | None:
        return self._script


def test_consistent_failure_is_deterministic_repro() -> None:
    agent = ReproductionAgent(FixedSynth("assert False"), n_runs=5)
    sbx = SequencedSandbox([1, 1, 1, 1, 1])  # bug fires every run
    repro = agent.reproduce(REPORT, sbx)
    assert repro.reproduced is True
    assert repro.reproduce_rate == 1.0
    assert repro.deterministic is True
    assert repro.n_runs == 5


def test_intermittent_failure_is_flaky() -> None:
    agent = ReproductionAgent(FixedSynth("assert maybe()"), n_runs=4)
    sbx = SequencedSandbox([1, 0, 1, 0])  # fires half the time
    repro = agent.reproduce(REPORT, sbx)
    assert repro.reproduced is True
    assert repro.reproduce_rate == 0.5
    assert repro.deterministic is False  # must not enter bisection


def test_never_failing_repro_is_not_reproduced() -> None:
    agent = ReproductionAgent(FixedSynth("assert True"), n_runs=3)
    sbx = SequencedSandbox([0, 0, 0])
    repro = agent.reproduce(REPORT, sbx)
    assert repro.reproduced is False
    assert repro.reproduce_rate == 0.0


def test_failed_synthesis_yields_non_reproduced() -> None:
    agent = ReproductionAgent(FixedSynth(None), n_runs=5)
    sbx = SequencedSandbox([])
    repro = agent.reproduce(REPORT, sbx)
    assert repro.reproduced is False
    assert sbx.run_count == 0  # nothing run when there is no script


def test_repro_script_is_written_into_sandbox() -> None:
    agent = ReproductionAgent(FixedSynth("assert False"), n_runs=1)
    sbx = SequencedSandbox([1])
    repro = agent.reproduce(REPORT, sbx)
    assert repro.script == "assert False"
    assert any(data == b"assert False" for data in sbx.written.values())
    assert repro.stack_trace  # captured from the failing run
    assert repro.language == Language.PYTHON
