"""Provider seams (spec Section 9).

Every model call goes through one of these adapters so a local Ollama backend and
any hosted API are interchangeable by config alone — provider name, endpoint,
model id, and sampling params — with no change to agent code. The Fix and
Selection models are independently swappable so a hosted model can be dropped in
there alone.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class Message:
    """A single chat message."""

    role: str  # 'system' | 'user' | 'assistant'
    content: str


@dataclass(frozen=True)
class Completion:
    """A provider-agnostic completion result."""

    text: str
    model: str
    prompt_tokens: int | None = None
    completion_tokens: int | None = None

    @property
    def total_tokens(self) -> int | None:
        if self.prompt_tokens is None and self.completion_tokens is None:
            return None
        return (self.prompt_tokens or 0) + (self.completion_tokens or 0)


@runtime_checkable
class LLMProvider(Protocol):
    def complete(
        self,
        messages: list[Message],
        *,
        model: str,
        temperature: float,
        max_tokens: int,
        **kw: object,
    ) -> Completion: ...


@runtime_checkable
class EmbeddingProvider(Protocol):
    def embed(self, texts: list[str], *, model: str) -> list[list[float]]: ...
