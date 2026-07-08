"""A provider bound to one agent's model config.

Pairs an :class:`LLMProvider` with the :class:`ModelConfig` for a specific agent
so callers issue ``client.complete(messages)`` without repeating the model name
and sampling parameters. Agents depend on this (or the raw provider) and are
tested with a fake provider that returns fixtures.
"""

from __future__ import annotations

from app.config.settings import ModelConfig
from app.providers.base import Completion, LLMProvider, Message


class LLMClient:
    def __init__(self, provider: LLMProvider, config: ModelConfig) -> None:
        self._provider = provider
        self._config = config

    def complete(self, messages: list[Message]) -> Completion:
        return self._provider.complete(
            messages,
            model=self._config.model,
            temperature=self._config.temperature,
            max_tokens=self._config.max_tokens,
        )
