"""Unit tests for the Ollama provider adapter (spec Section 9).

Uses httpx.MockTransport so the adapter is exercised offline. Asserts the request
carries the configured model and sampling params, and that responses are mapped
onto the provider-agnostic Completion / embedding types.
"""

from __future__ import annotations

import json

import httpx
from app.providers.base import Completion, Message
from app.providers.ollama import OllamaProvider

ENDPOINT = "http://ollama.test:11434"


def _provider(handler: httpx.MockTransport) -> OllamaProvider:
    return OllamaProvider(endpoint=ENDPOINT, client=httpx.Client(transport=handler))


def test_complete_sends_model_and_sampling_and_maps_response() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "model": "qwen2.5-coder:32b",
                "message": {"role": "assistant", "content": "patch here"},
                "prompt_eval_count": 12,
                "eval_count": 34,
            },
        )

    provider = _provider(httpx.MockTransport(handler))
    completion = provider.complete(
        [Message(role="user", content="fix it")],
        model="qwen2.5-coder:32b",
        temperature=0.1,
        max_tokens=256,
    )

    assert isinstance(completion, Completion)
    assert completion.text == "patch here"
    assert completion.model == "qwen2.5-coder:32b"
    assert completion.prompt_tokens == 12
    assert completion.completion_tokens == 34

    body = seen["body"]
    assert isinstance(body, dict)
    assert body["model"] == "qwen2.5-coder:32b"
    assert body["stream"] is False
    assert body["messages"] == [{"role": "user", "content": "fix it"}]
    assert body["options"]["temperature"] == 0.1
    assert body["options"]["num_predict"] == 256
    assert str(seen["url"]).endswith("/api/chat")


def test_embed_returns_one_vector_per_text() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        n = len(body["input"])
        return httpx.Response(200, json={"embeddings": [[0.1, 0.2, 0.3]] * n})

    provider = _provider(httpx.MockTransport(handler))
    vectors = provider.embed(["a", "b"], model="nomic-embed-text")

    assert len(vectors) == 2
    assert vectors[0] == [0.1, 0.2, 0.3]


def test_http_error_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "boom"})

    provider = _provider(httpx.MockTransport(handler))
    try:
        provider.complete(
            [Message(role="user", content="x")], model="m", temperature=0.0, max_tokens=1
        )
    except httpx.HTTPStatusError:
        pass
    else:  # pragma: no cover - the call must raise
        raise AssertionError("expected an HTTP error to propagate")
