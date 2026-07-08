"""Composition root: wire Phase 2 agents, sandbox, store, and orchestrator.

This is the one place concrete implementations are assembled from config. It is
constructed lazily (no Docker ping or DB connection at import) so the module
imports even where the real backends are absent; the connections are only made
when a run actually executes.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.agents.bisection import StubBisectionAgent
from app.agents.fix import RevertFixAgent
from app.agents.localize import HeuristicLocalizeAgent
from app.agents.reproduction import NullSynthesizer, ReproductionAgent
from app.agents.triage import RuleBasedTriageAgent, Taxonomy
from app.agents.types import BugReport, Candidate, Repro
from app.config.settings import Settings
from app.db.store import SqlStore
from app.orchestrator.confidence import GateConfig
from app.orchestrator.dag import Agents, Orchestrator, RunOutcome
from app.sandbox.config import SandboxConfig
from app.sandbox.gvisor import GvisorSandbox
from app.sandbox.interface import Sandbox

# Coarse default taxonomy (spec Section 5) when a repo has none configured.
_DEFAULT_TAXONOMY = Taxonomy(
    components={
        "build": ["build", "compile", "setup.py", "pyproject", "maven", "gradle"],
        "api": ["endpoint", "http", "request", "route", "api"],
        "tests": ["test", "pytest", "junit", "assert"],
        "docs": ["docs", "documentation", "readme"],
    },
    default="runtime",
)


class _ConstantReviewer:
    """Phase 2 reviewer stub: neutral verdict until the LLM reviewer (Phase 3)."""

    def review(self, report: BugReport, repro: Repro, candidate: Candidate) -> float:
        return 0.5


def _build_agents(settings: Settings) -> Agents:
    from app.agents.validation import ValidationAgent

    return Agents(
        triage=RuleBasedTriageAgent(_DEFAULT_TAXONOMY),
        reproduction=ReproductionAgent(NullSynthesizer(), settings.reproduction.n_runs),
        bisection=StubBisectionAgent(),
        localize=HeuristicLocalizeAgent(),
        fix=RevertFixAgent(),
        validation=ValidationAgent(_ConstantReviewer()),
    )


def build_orchestrator(
    settings: Settings,
    *,
    docker_client: Any | None = None,
    session_factory: sessionmaker[Any] | None = None,
) -> Orchestrator:
    """Assemble the Phase 2 orchestrator from config and (lazy) backends."""
    if session_factory is None:
        session_factory = sessionmaker(create_engine(settings.database_url))
    store = SqlStore(session_factory)

    def sandbox_factory() -> Sandbox:
        import docker  # local import so the module loads without Docker present

        client = docker_client or docker.from_env()
        return GvisorSandbox(SandboxConfig(), client)

    return Orchestrator(
        agents=_build_agents(settings),
        sandbox_factory=sandbox_factory,
        store=store,
        publisher=_publisher(),
        threshold=settings.confidence.threshold,
        gate_config=GateConfig(),
    )


def _publisher() -> Any:
    from app.vcs.publisher import LocalDraftPRPublisher

    return LocalDraftPRPublisher()


def build_run_pipeline(orchestrator: Orchestrator) -> Callable[[BugReport], RunOutcome]:
    return orchestrator.run
