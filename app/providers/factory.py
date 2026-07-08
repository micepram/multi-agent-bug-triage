"""Build providers from config (spec Section 9).

Selects the backend by ``config.provider``. Local Ollama is the default; hosted
adapters register here without any change to agent code. Different agents can use
different providers because each is built from its own :class:`ModelConfig`.
"""

from __future__ import annotations

from app.config.settings import ModelConfig
from app.providers.base import LLMProvider
from app.providers.client import LLMClient
from app.providers.ollama import OllamaProvider


def build_provider(config: ModelConfig) -> LLMProvider:
    if config.provider == "ollama":
        return OllamaProvider(endpoint=config.endpoint)
    raise ValueError(
        f"unsupported LLM provider: {config.provider!r} "
        "(only 'ollama' is wired; add an adapter here to support others)"
    )


def build_llm_client(config: ModelConfig) -> LLMClient:
    return LLMClient(build_provider(config), config)
