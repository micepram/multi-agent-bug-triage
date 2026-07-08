"""Local Ollama adapter (spec Section 9), the default backend.

Implements both :class:`LLMProvider` and :class:`EmbeddingProvider` against the
Ollama HTTP API. The httpx client is injectable so the adapter is unit-tested
offline with a mock transport. Swapping to a hosted API is a config change that
selects a different adapter, not an edit here.
"""

from __future__ import annotations

import httpx

from app.providers.base import Completion, Message

_DEFAULT_TIMEOUT = 300.0


class OllamaProvider:
    """LLM + embedding provider backed by a local Ollama server."""

    def __init__(
        self,
        endpoint: str = "http://localhost:11434",
        *,
        client: httpx.Client | None = None,
        timeout: float = _DEFAULT_TIMEOUT,
    ) -> None:
        self._endpoint = endpoint.rstrip("/")
        self._client = client or httpx.Client(timeout=timeout)

    def complete(
        self,
        messages: list[Message],
        *,
        model: str,
        temperature: float,
        max_tokens: int,
        **kw: object,
    ) -> Completion:
        payload = {
            "model": model,
            "stream": False,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "options": {"temperature": temperature, "num_predict": max_tokens},
        }
        response = self._client.post(f"{self._endpoint}/api/chat", json=payload)
        response.raise_for_status()
        data = response.json()
        return Completion(
            text=data["message"]["content"],
            model=data.get("model", model),
            prompt_tokens=data.get("prompt_eval_count"),
            completion_tokens=data.get("eval_count"),
        )

    def embed(self, texts: list[str], *, model: str) -> list[list[float]]:
        payload = {"model": model, "input": texts}
        response = self._client.post(f"{self._endpoint}/api/embed", json=payload)
        response.raise_for_status()
        data = response.json()
        return [list(vec) for vec in data["embeddings"]]
