"""Unit tests for the Phase 3 LLM + SBFL Localize agent.

Combines spectrum-based suspiciousness, LLM code-search over the repo, and — when
available — a conclusive bisection introducing diff folded in as a strong prior.
A fake LLM provider returns fixtures; the coverage collector is injected.
"""

from __future__ import annotations

from app.agents.localize import LLMLocalizeAgent
from app.agents.sbfl import Spectrum
from app.agents.types import BisectionOutcome, BugReport, Language, Repro
from app.config.settings import ModelConfig
from app.providers.base import Completion, Message
from app.providers.client import LLMClient

REPORT = BugReport(repo="octo/repo", base_ref="v1", source="manual", title="crash", body="boom")
REPRO = Repro(
    script="s",
    language=Language.PYTHON,
    reproduced=True,
    reproduce_rate=1.0,
    n_runs=5,
    stack_trace='File "src/handler.py", line 3',
)
NO_BISECTION = BisectionOutcome(conclusive=False)


class FakeProvider:
    def __init__(self, text: str) -> None:
        self._text = text

    def complete(
        self,
        messages: list[Message],
        *,
        model: str,
        temperature: float,
        max_tokens: int,
        **kw: object,
    ) -> Completion:
        return Completion(text=self._text, model=model)


def _llm(text: str) -> LLMClient:
    return LLMClient(FakeProvider(text), ModelConfig(provider="ollama", model="m"))


class FakeCoverage:
    def __init__(self, spectrum: Spectrum | None) -> None:
        self._spectrum = spectrum

    def collect(self, sandbox: object) -> Spectrum | None:
        return self._spectrum


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


def test_sbfl_scores_drive_ranking_when_no_prior() -> None:
    spectrum = Spectrum(
        total_failed=2,
        total_passed=2,
        coverage={"src/a.py": (2, 0), "src/b.py": (0, 2)},
    )
    agent = LLMLocalizeAgent(_llm('{"files": []}'), FakeCoverage(spectrum))
    loc = agent.localize(REPORT, REPRO, NO_BISECTION, _NoSandbox())
    files = [location.file for location in loc.locations]
    assert files[0] == "src/a.py"  # most suspicious first


def test_llm_code_search_files_are_included() -> None:
    agent = LLMLocalizeAgent(
        _llm('{"files": ["src/handler.py", "src/util.py"]}'), FakeCoverage(None)
    )
    loc = agent.localize(REPORT, REPRO, NO_BISECTION, _NoSandbox())
    files = [location.file for location in loc.locations]
    assert "src/handler.py" in files
    assert "src/util.py" in files


def test_bisection_prior_floats_to_top() -> None:
    spectrum = Spectrum(total_failed=1, total_passed=1, coverage={"src/a.py": (1, 0)})
    agent = LLMLocalizeAgent(_llm('{"files": ["src/a.py"]}'), FakeCoverage(spectrum))
    bisection = BisectionOutcome(introducing_commit="abc", conclusive=True)
    loc = agent.localize(
        REPORT, REPRO, bisection, _NoSandbox(), introducing_files=["src/culprit.py"]
    )
    assert loc.locations[0].file == "src/culprit.py"


def test_malformed_llm_output_is_tolerated() -> None:
    # Not JSON: fall back to extracting path-like tokens from the text.
    agent = LLMLocalizeAgent(
        _llm("I think the bug is in src/handler.py near the top."), FakeCoverage(None)
    )
    loc = agent.localize(REPORT, REPRO, NO_BISECTION, _NoSandbox())
    assert any(location.file == "src/handler.py" for location in loc.locations)
