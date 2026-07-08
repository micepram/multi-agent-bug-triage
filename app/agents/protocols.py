"""Agent seams (spec Section 5).

Each agent is a Protocol so the orchestrator depends on behaviour, not concrete
classes. Phase 2 ships trivial deterministic implementations; Phase 3 swaps in
LLM-backed ones behind the same contracts.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from app.agents.types import (
    BisectionOutcome,
    BugReport,
    Candidate,
    Localization,
    Repro,
    TriageRecord,
    ValidationOutcome,
)
from app.sandbox.interface import Sandbox


@runtime_checkable
class TriageAgent(Protocol):
    def triage(self, report: BugReport) -> TriageRecord: ...


@runtime_checkable
class ReproductionAgent(Protocol):
    def reproduce(self, report: BugReport, sandbox: Sandbox) -> Repro:
        """Synthesize and run a repro; runs untrusted code, only via Sandbox."""
        ...


@runtime_checkable
class BisectionAgent(Protocol):
    def bisect(self, report: BugReport, repro: Repro, sandbox: Sandbox) -> BisectionOutcome: ...


@runtime_checkable
class LocalizeAgent(Protocol):
    def localize(
        self,
        report: BugReport,
        repro: Repro,
        bisection: BisectionOutcome,
        sandbox: Sandbox,
    ) -> Localization: ...


@runtime_checkable
class FixAgent(Protocol):
    def generate_candidates(
        self,
        report: BugReport,
        repro: Repro,
        localization: Localization,
        bisection: BisectionOutcome,
        sandbox: Sandbox,
    ) -> list[Candidate]:
        """Produce candidate patches, each executed against the suite in-sandbox."""
        ...

    def select(self, candidates: list[Candidate], report: BugReport) -> Candidate | None:
        """Rank survivors against the issue description, not only test results."""
        ...


@runtime_checkable
class ValidationAgent(Protocol):
    def validate(
        self,
        report: BugReport,
        repro: Repro,
        candidate: Candidate,
        sandbox: Sandbox,
    ) -> ValidationOutcome: ...
