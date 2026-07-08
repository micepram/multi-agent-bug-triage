"""Reproduction agent (spec Section 5).

Synthesizes a minimal repro, writes it into the sandbox, runs it N times, and
records the reproduce rate. A repro that fires on every run is deterministic; an
intermittent one is flaky and is barred from bisection downstream. The
synthesizer is a seam: Phase 2 injects a deterministic one (e.g. the SWE-bench
fail-to-pass test); Phase 3 swaps in an LLM synthesizer behind the same protocol.

Runs untrusted generated code; execution only ever goes through the Sandbox.
"""

from __future__ import annotations

import re
from typing import Protocol, runtime_checkable

from app.agents.types import BugReport, Language, Repro
from app.providers.base import Message
from app.providers.client import LLMClient
from app.sandbox.interface import Sandbox
from app.sandbox.profiles import LanguageDetector, profile_for

_REPRO_TIMEOUT = 120


@runtime_checkable
class ReproSynthesizer(Protocol):
    def synthesize(self, report: BugReport, attempt: int) -> str | None:
        """Produce a self-contained repro script, or None if synthesis failed."""
        ...


class NullSynthesizer:
    """Phase 2 placeholder: no LLM synthesis yet, so no repro is produced.

    Used on the live ingestion path until the LLM synthesizer lands in Phase 3.
    A run using it reproduces nothing and routes to a could-not-reproduce
    escalation — the honest Phase 2 behaviour for a free-text report.
    """

    def synthesize(self, report: BugReport, attempt: int) -> str | None:
        return None


_SYNTH_PROMPT = (
    "You are a bug-reproduction assistant. From the bug report, write a MINIMAL, "
    "self-contained script that reproduces the bug and exits non-zero when the bug "
    "is present. Respond ONLY with the script, in a code fence."
)


class LLMReproSynthesizer:
    """Phase 3 synthesizer: an LLM writes a self-contained repro from the report."""

    def __init__(self, llm: LLMClient) -> None:
        self._llm = llm

    def synthesize(self, report: BugReport, attempt: int) -> str | None:
        messages = [
            Message(role="system", content=_SYNTH_PROMPT),
            Message(
                role="user",
                content=f"Repo: {report.repo}\nTitle: {report.title}\n\nReport:\n{report.body}",
            ),
        ]
        return _extract_script(self._llm.complete(messages).text)


def _extract_script(text: str) -> str | None:
    """Strip a code fence if present; return None when there is no script."""
    fence = re.search(r"```(?:\w+)?\n(.*?)```", text, re.DOTALL)
    script = fence.group(1) if fence else text
    return script if script.strip() else None


class ReproductionAgent:
    """Determinism-checked reproduction over N sandboxed runs."""

    def __init__(
        self,
        synthesizer: ReproSynthesizer,
        n_runs: int,
        *,
        language: Language = Language.PYTHON,
        detector: LanguageDetector | None = None,
    ) -> None:
        self._synthesizer = synthesizer
        self._n_runs = n_runs
        self._language = language
        self._detector = detector

    def reproduce(self, report: BugReport, sandbox: Sandbox) -> Repro:
        # Detect the repo's language (falling back to the configured default) and
        # drive the repro through that execution profile.
        language = (self._detector.detect(sandbox) if self._detector else None) or self._language
        profile = profile_for(language)

        # The orchestrator retries synthesis; each call is one synthesis attempt.
        script = self._synthesizer.synthesize(report, attempt=0)
        if script is None:
            return Repro(
                script="",
                language=language,
                reproduced=False,
                reproduce_rate=0.0,
                n_runs=0,
            )

        sandbox.write_file(profile.repro_filename, script.encode("utf-8"))
        cmd = profile.repro_cmd(profile.repro_filename)

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
            language=language,
            reproduced=reproduced_count > 0,
            reproduce_rate=rate,
            n_runs=self._n_runs,
            stack_trace=last_trace,
        )
