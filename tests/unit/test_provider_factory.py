"""Unit tests for provider selection by config and the bound LLM client.

The provider, endpoint, model, and sampling params all come from a ModelConfig
(spec Section 9). The bound client forwards the configured model and sampling to
the underlying provider so agents never pass model names themselves.
"""

from __future__ import annotations

import pytest
from app.config.settings import ModelConfig
from app.providers.base import Completion, Message
from app.providers.client import LLMClient
from app.providers.factory import build_llm_client, build_provider
from app.providers.ollama import OllamaProvider


class RecordingProvider:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def complete(self, messages, *, model, temperature, max_tokens, **kw) -> Completion:  # type: ignore[no-untyped-def]
        self.calls.append({"model": model, "temperature": temperature, "max_tokens": max_tokens})
        return Completion(text="ok", model=model)


def test_factory_builds_ollama_by_default() -> None:
    cfg = ModelConfig(provider="ollama", endpoint="http://x:11434", model="m")
    assert isinstance(build_provider(cfg), OllamaProvider)


def test_unknown_provider_raises() -> None:
    cfg = ModelConfig(provider="does-not-exist", model="m")
    with pytest.raises(ValueError, match="does-not-exist"):
        build_provider(cfg)


def test_bound_client_forwards_configured_model_and_sampling() -> None:
    provider = RecordingProvider()
    cfg = ModelConfig(
        provider="ollama", model="qwen2.5-coder:32b", temperature=0.15, max_tokens=512
    )
    client = LLMClient(provider, cfg)

    client.complete([Message(role="user", content="hi")])

    assert provider.calls == [
        {"model": "qwen2.5-coder:32b", "temperature": 0.15, "max_tokens": 512}
    ]


def test_build_llm_client_pairs_provider_and_config() -> None:
    cfg = ModelConfig(provider="ollama", model="m", temperature=0.2, max_tokens=64)
    client = build_llm_client(cfg)
    assert isinstance(client, LLMClient)
