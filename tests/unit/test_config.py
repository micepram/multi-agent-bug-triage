"""Unit tests for the per-agent config surface (spec Section 9).

Model names, endpoints, sampling params, the candidate count, repro run count,
the max skip ratio, and the escalation threshold all come from config — never
hard-coded. These tests pin that contract.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

from app.config.settings import Settings, load_settings


def test_default_config_loads(tmp_path: Path) -> None:
    settings = load_settings(_write_default(tmp_path))
    assert isinstance(settings, Settings)


def test_per_agent_models_are_independently_configurable(tmp_path: Path) -> None:
    settings = load_settings(_write_default(tmp_path))
    # Fix and Selection must be swappable independently (spec Section 9).
    assert settings.agents.fix.model == "qwen2.5-coder:32b"
    assert settings.agents.triage.model == "llama3.1:8b"
    assert settings.agents.fix.model != settings.agents.triage.model


def test_tunables_present(tmp_path: Path) -> None:
    settings = load_settings(_write_default(tmp_path))
    assert settings.fix.k >= 1
    assert settings.reproduction.n_runs >= 1
    assert 0.0 <= settings.bisection.max_skip_ratio <= 1.0
    # The escalation threshold is loaded from config, never inlined in code.
    assert 0.0 <= settings.confidence.threshold <= 1.0


def test_api_key_is_read_from_env_not_config(tmp_path: Path, monkeypatch: object) -> None:
    import os

    os.environ["MY_FIX_KEY"] = "secret-token"
    try:
        settings = load_settings(_write_hosted(tmp_path))
        assert settings.agents.fix.api_key_env == "MY_FIX_KEY"
        assert settings.agents.fix.resolve_api_key() == "secret-token"
        # The secret itself is never stored in the config object.
        assert "secret-token" not in settings.model_dump_json()
    finally:
        del os.environ["MY_FIX_KEY"]


def _write_default(tmp_path: Path) -> Path:
    path = tmp_path / "config.yaml"
    path.write_text(
        textwrap.dedent(
            """
            database_url: postgresql+psycopg://localhost/triage
            agents:
              triage:      {provider: ollama, model: "llama3.1:8b"}
              reproduction: {provider: ollama, model: "qwen2.5-coder:14b"}
              fix:         {provider: ollama, model: "qwen2.5-coder:32b"}
              selection:   {provider: ollama, model: "qwen2.5-coder:32b"}
              reviewer:    {provider: ollama, model: "llama3.1:8b"}
              embedding:   {provider: ollama, model: "nomic-embed-text"}
            fix: {k: 4}
            reproduction: {n_runs: 5}
            bisection: {max_skip_ratio: 0.5}
            confidence: {threshold: 0.7}
            """
        )
    )
    return path


def _write_hosted(tmp_path: Path) -> Path:
    path = tmp_path / "hosted.yaml"
    path.write_text(
        textwrap.dedent(
            """
            database_url: postgresql+psycopg://localhost/triage
            agents:
              triage:      {provider: ollama, model: "llama3.1:8b"}
              reproduction: {provider: ollama, model: "qwen2.5-coder:14b"}
              fix:         {provider: anthropic, model: "claude-opus-4-8", api_key_env: MY_FIX_KEY}
              selection:   {provider: ollama, model: "qwen2.5-coder:32b"}
              reviewer:    {provider: ollama, model: "llama3.1:8b"}
              embedding:   {provider: ollama, model: "nomic-embed-text"}
            fix: {k: 4}
            reproduction: {n_runs: 5}
            bisection: {max_skip_ratio: 0.5}
            confidence: {threshold: 0.7}
            """
        )
    )
    return path
