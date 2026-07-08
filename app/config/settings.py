"""Typed configuration surface (spec Section 9).

All model names, endpoints, sampling parameters, and pipeline tunables live here
and are loaded from a YAML file plus the environment. Nothing in the codebase
hard-codes a model, endpoint, credential, or the escalation threshold.

Secrets are never stored in config: a model config names an *environment
variable* (``api_key_env``) and resolves the value on demand.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field


class ModelConfig(BaseModel):
    """Provider + model + sampling for a single agent's LLM calls."""

    provider: str = "ollama"
    endpoint: str = "http://localhost:11434"
    api_key_env: str | None = None
    model: str
    temperature: float = 0.2
    max_tokens: int = 2048

    def resolve_api_key(self) -> str | None:
        """Read the credential from the environment; never persisted in config."""
        if self.api_key_env is None:
            return None
        return os.environ.get(self.api_key_env)


class AgentsConfig(BaseModel):
    """Per-agent model config; agents may use different models and providers."""

    triage: ModelConfig
    reproduction: ModelConfig
    fix: ModelConfig
    selection: ModelConfig
    reviewer: ModelConfig
    embedding: ModelConfig


class FixConfig(BaseModel):
    k: int = Field(default=4, ge=1)


class ReproductionConfig(BaseModel):
    n_runs: int = Field(default=5, ge=1)


class BisectionConfig(BaseModel):
    max_skip_ratio: float = Field(default=0.5, ge=0.0, le=1.0)


class ConfidenceConfig(BaseModel):
    # Calibrated by the threshold sweep and persisted as an artifact; the code
    # only ever reads it from here, never inlines a value.
    threshold: float = Field(default=0.7, ge=0.0, le=1.0)


class Settings(BaseModel):
    """Top-level application configuration."""

    database_url: str
    agents: AgentsConfig
    fix: FixConfig = FixConfig()
    reproduction: ReproductionConfig = ReproductionConfig()
    bisection: BisectionConfig = BisectionConfig()
    confidence: ConfidenceConfig = ConfidenceConfig()


def load_settings(path: str | Path) -> Settings:
    """Load :class:`Settings` from a YAML file.

    The database URL may be overridden by the ``DATABASE_URL`` environment
    variable so secrets and deploy-specific endpoints stay out of the file.
    """
    raw: dict[str, Any] = yaml.safe_load(Path(path).read_text()) or {}
    if env_db := os.environ.get("DATABASE_URL"):
        raw["database_url"] = env_db
    return Settings.model_validate(raw)
