"""Unit tests for the Phase 3 LLM Fix agent.

Generates k candidate patches conditioned on the failure context, runs the suite
on each in the sandbox, then a Selection step ranks survivors against the issue
description (not only test pass/fail). Fake LLM providers return fixtures; a fake
sandbox answers git-apply / suite / reset commands.
"""

from __future__ import annotations

from app.agents.fix import LLMFixAgent
from app.agents.types import (
    BisectionOutcome,
    BugReport,
    Candidate,
    FaultLocation,
    Language,
    Localization,
    Repro,
)
from app.config.settings import ModelConfig
from app.providers.base import Completion, Message
from app.providers.client import LLMClient

REPORT = BugReport(
    repo="octo/repo", base_ref="v1", source="manual", title="IndexError", body="boom"
)
REPRO = Repro(script="s", language=Language.PYTHON, reproduced=True, reproduce_rate=1.0, n_runs=5)
LOCALIZATION = Localization(locations=[FaultLocation(file="src/mod.py", score=1.0)])
NO_BISECTION = BisectionOutcome(conclusive=False)

DIFF = (
    "```diff\n"
    "diff --git a/src/mod.py b/src/mod.py\n"
    "--- a/src/mod.py\n"
    "+++ b/src/mod.py\n"
    "@@ -1,2 +1,2 @@\n"
    "-    return items[0]\n"
    "+    return items[0] if items else None\n"
    "```\n"
)


class QueueProvider:
    """Returns queued texts in order (one per complete call)."""

    def __init__(self, texts: list[str]) -> None:
        self._texts = list(texts)

    def complete(
        self,
        messages: list[Message],
        *,
        model: str,
        temperature: float,
        max_tokens: int,
        **kw: object,
    ) -> Completion:
        text = self._texts.pop(0) if self._texts else ""
        return Completion(text=text, model=model)


def _client(texts: list[str]) -> LLMClient:
    return LLMClient(QueueProvider(texts), ModelConfig(provider="ollama", model="m"))


class KeyedSandbox:
    def __init__(self, *, apply_code: int = 0, suite_code: int = 0) -> None:
        self._apply_code = apply_code
        self._suite_code = suite_code
        self.commands: list[list[str]] = []

    def prepare(self, repo, ref):  # type: ignore[no-untyped-def]
        return None

    def run(self, cmd, timeout, *, network=False):  # type: ignore[no-untyped-def]
        from app.sandbox.interface import RunResult

        self.commands.append(cmd)
        if "apply" in cmd:
            code = self._apply_code
        elif "checkout" in cmd:
            code = 0
        else:
            code = self._suite_code
        return RunResult(stdout="", stderr="", exit_code=code, duration=0.1, timed_out=False)

    def read_file(self, path):  # type: ignore[no-untyped-def]
        return b""

    def write_file(self, path, data):  # type: ignore[no-untyped-def]
        return None

    def destroy(self):  # type: ignore[no-untyped-def]
        return None


def _agent(gen_texts: list[str], *, selection_text: str = "0", k: int = 3) -> LLMFixAgent:
    return LLMFixAgent(_client(gen_texts), _client([selection_text]), k=k)


def test_generates_k_candidates_and_runs_suite_each() -> None:
    agent = _agent([DIFF, DIFF, DIFF], k=3)
    sbx = KeyedSandbox(apply_code=0, suite_code=0)
    candidates = agent.generate_candidates(REPORT, REPRO, LOCALIZATION, NO_BISECTION, sbx)
    assert len(candidates) == 3
    assert all(c.kind == "llm_candidate" for c in candidates)
    assert all(c.suite_passed is True for c in candidates)
    # the diff fence is stripped
    assert "```" not in candidates[0].diff
    assert candidates[0].files_touched == 1
    # suite was actually run in the sandbox
    assert any("pytest" in c or "apply" in c for c in sbx.commands)


def test_candidate_that_fails_to_apply_is_marked_not_passing() -> None:
    agent = _agent([DIFF], k=1)
    sbx = KeyedSandbox(apply_code=1)  # git apply fails
    candidates = agent.generate_candidates(REPORT, REPRO, LOCALIZATION, NO_BISECTION, sbx)
    assert len(candidates) == 1
    assert candidates[0].suite_passed is False


def test_empty_llm_output_yields_no_candidate() -> None:
    agent = _agent(["not a diff at all"], k=1)
    sbx = KeyedSandbox()
    candidates = agent.generate_candidates(REPORT, REPRO, LOCALIZATION, NO_BISECTION, sbx)
    assert candidates == []


def test_selection_prefers_suite_passing_and_uses_llm_choice() -> None:
    passing_a = Candidate(
        kind="llm_candidate", diff="A", files_touched=1, lines_touched=2, suite_passed=True
    )
    passing_b = Candidate(
        kind="llm_candidate", diff="B", files_touched=1, lines_touched=2, suite_passed=True
    )
    failing = Candidate(
        kind="llm_candidate", diff="C", files_touched=1, lines_touched=2, suite_passed=False
    )
    # selection LLM picks index 1 among the survivors [passing_a, passing_b]
    agent = _agent([], selection_text="1")
    chosen = agent.select([passing_a, failing, passing_b], REPORT)
    assert chosen is passing_b
    assert chosen.selected is True
    assert chosen.kind == "selected"


def test_select_returns_none_when_empty() -> None:
    agent = _agent([], selection_text="0")
    assert agent.select([], REPORT) is None
