"""Unit tests for the Phase 3 LLM Reviewer.

The reviewer judges whether a patch addresses the reported symptom (not merely
makes a test pass) and returns a [0,1] verdict for the confidence gate. A fake
provider returns fixtures; parsing tolerates a bare number or a yes/no verdict.
"""

from __future__ import annotations

from app.agents.types import BugReport, Candidate, Language, Repro
from app.agents.validation import LLMReviewer
from app.config.settings import ModelConfig
from app.providers.base import Completion, Message
from app.providers.client import LLMClient

REPORT = BugReport(repo="octo/repo", base_ref="v1", source="manual", title="bug")
REPRO = Repro(script="s", language=Language.PYTHON, reproduced=True, reproduce_rate=1.0, n_runs=5)
CANDIDATE = Candidate(kind="selected", diff="d", files_touched=1, lines_touched=3)


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


def _reviewer(text: str) -> LLMReviewer:
    return LLMReviewer(LLMClient(FakeProvider(text), ModelConfig(provider="ollama", model="m")))


def test_parses_numeric_verdict() -> None:
    assert _reviewer("0.85").review(REPORT, REPRO, CANDIDATE) == 0.85


def test_parses_yes_as_high_confidence() -> None:
    assert _reviewer("YES, it addresses the root cause.").review(REPORT, REPRO, CANDIDATE) >= 0.8


def test_parses_no_as_low_confidence() -> None:
    assert _reviewer("No, this only masks the symptom.").review(REPORT, REPRO, CANDIDATE) <= 0.2


def test_verdict_is_clamped_to_unit_interval() -> None:
    assert _reviewer("1.7").review(REPORT, REPRO, CANDIDATE) == 1.0
    assert _reviewer("-3").review(REPORT, REPRO, CANDIDATE) == 0.0


def test_unparseable_output_is_neutral() -> None:
    assert _reviewer("hmm, unclear").review(REPORT, REPRO, CANDIDATE) == 0.5
