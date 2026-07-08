"""Eval-tier smoke test for the live Ollama provider path (spec Section 9).

Requires a reachable Ollama server (OLLAMA_HOST) and a pulled model
(OLLAMA_MODEL). Skips cleanly otherwise. This exercises the real provider path
that the Fix/Localize/Reviewer agents depend on, without asserting model quality.
"""

from __future__ import annotations

import os

import pytest
from app.providers.base import Message
from app.providers.ollama import OllamaProvider

pytestmark = pytest.mark.eval

OLLAMA_HOST = os.environ.get("OLLAMA_HOST")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "llama3.1:8b")


@pytest.mark.skipif(OLLAMA_HOST is None, reason="OLLAMA_HOST not set; skipping live provider test.")
def test_live_completion_returns_text() -> None:
    provider = OllamaProvider(endpoint=OLLAMA_HOST or "")
    completion = provider.complete(
        [Message(role="user", content="Reply with the single word: ok")],
        model=OLLAMA_MODEL,
        temperature=0.0,
        max_tokens=16,
    )
    assert completion.text.strip() != ""
    assert completion.model
