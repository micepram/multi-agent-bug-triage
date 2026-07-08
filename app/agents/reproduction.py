"""Reproduction agent (spec Section 5).

Synthesizes a minimal repro, writes it into the sandbox, runs it N times, and
records the reproduce rate. A repro that fires on every run is deterministic; an
intermittent one is flaky and is barred from bisection downstream. The
synthesizer is a seam: Phase 2 injects a deterministic one (e.g. the SWE-bench
fail-to-pass test); Phase 3 swaps in an LLM synthesizer behind the same protocol.

Runs untrusted generated code; execution only ever goes through the Sandbox.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from app.agents.types import BugReport, Language, Repro
from app.sandbox.interface import Sandbox

_REPRO_PATH = "repro_test.py"
_REPRO_TIMEOUT = 120


@runtime_checkable
class ReproSynthesizer(Protocol):
    def synthesize(self, report: BugReport, attempt: int) -> str | None:
        """Produce a self-contained repro script, or None if synthesis failed."""
        ...


class ReproductionAgent:
    """Determinism-checked reproduction over N sandboxed runs."""

    def __init__(
        self,
        synthesizer: ReproSynthesizer,
        n_runs: int,
        *,
        language: Language = Language.PYTHON,
        repro_path: str = _REPRO_PATH,
    ) -> None:
        self._synthesizer = synthesizer
        self._n_runs = n_runs
        self._language = language
        self._repro_path = repro_path

    def reproduce(self, report: BugReport, sandbox: Sandbox) -> Repro:
        # The orchestrator retries synthesis; each call is one synthesis attempt.
        script = self._synthesizer.synthesize(report, attempt=0)
        if script is None:
            return Repro(
                script="",
                language=self._language,
                reproduced=False,
                reproduce_rate=0.0,
                n_runs=0,
            )

        sandbox.write_file(self._repro_path, script.encode("utf-8"))
        cmd = self._run_cmd()

        reproduced_count = 0
        last_trace = ""
        for _ in range(self._n_runs):
            result = sandbox.run(cmd, timeout=_REPRO_TIMEOUT)
            # A non-zero exit means the bug manifested (failing test / assertion).
            if result.exit_code != 0:
                reproduced_count += 1
                last_trace = result.stderr or result.stdout

        rate = reproduced_count / self._n_runs if self._n_runs else 0.0
        return Repro(
            script=script,
            language=self._language,
            reproduced=reproduced_count > 0,
            reproduce_rate=rate,
            n_runs=self._n_runs,
            stack_trace=last_trace,
        )

    def _run_cmd(self) -> list[str]:
        if self._language is Language.PYTHON:
            return ["python", self._repro_path]
        # Java and others are wired in Phase 5.
        return ["sh", self._repro_path]
