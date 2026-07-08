"""Unit tests for the Phase 3 LLM repro synthesizer.

Synthesizes a self-contained repro script from the report. Strips code fences and
returns None when the model produces nothing usable, so the orchestrator's
retry/escalate path still works. A fake provider returns fixtures.
"""

from __future__ import annotations

from app.agents.reproduction import LLMReproSynthesizer
from app.agents.types import BugReport
from app.config.settings import ModelConfig
from app.providers.base import Completion, Message
from app.providers.client import LLMClient

REPORT = BugReport(repo="octo/repo", base_ref="v1", source="manual", title="IndexError on []")


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


def _synth(text: str) -> LLMReproSynthesizer:
    return LLMReproSynthesizer(
        LLMClient(FakeProvider(text), ModelConfig(provider="ollama", model="m"))
    )


def test_extracts_fenced_python_script() -> None:
    text = "Here is a repro:\n```python\ndef test_bug():\n    assert f([]) is None\n```\n"
    script = _synth(text).synthesize(REPORT, attempt=0)
    assert script is not None
    assert "def test_bug" in script
    assert "```" not in script


def test_bare_script_is_returned_as_is() -> None:
    text = "def test_bug():\n    assert f([]) is None\n"
    script = _synth(text).synthesize(REPORT, attempt=0)
    assert script == text


def test_empty_output_returns_none() -> None:
    assert _synth("   ").synthesize(REPORT, attempt=0) is None
