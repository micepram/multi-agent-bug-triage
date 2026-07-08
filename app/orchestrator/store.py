"""Persistence seam for the orchestrator (spec Section 7).

The orchestrator depends on the ``Store`` protocol, not on Postgres directly, so
the DAG is unit-testable with :class:`InMemoryStore`. A SQLAlchemy-backed store
implements the same contract for production.

``agent_events`` is append-only: :meth:`Store.record_event` only ever inserts.
There is deliberately no update/delete method for the audit trail.
"""

from __future__ import annotations

import uuid
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, Field

from app.agents.types import BisectionOutcome, BugReport, Candidate


class AgentEvent(BaseModel):
    """One append-only audit row for an agent node entry or exit."""

    run_id: str
    agent: str
    phase: str  # 'enter' | 'exit'
    inputs: dict[str, object] = Field(default_factory=dict)
    outputs: dict[str, object] = Field(default_factory=dict)
    signals: dict[str, object] = Field(default_factory=dict)
    model: str | None = None
    tokens: int | None = None
    duration_ms: int | None = None


@runtime_checkable
class Store(Protocol):
    def create_run(self, report: BugReport) -> str:
        """Create a run row (status 'running') and return its id."""
        ...

    def record_event(self, event: AgentEvent) -> None:
        """Append one audit event. Never updates or deletes."""
        ...

    def set_triage(self, run_id: str, severity: str, component: str) -> None: ...

    def save_bisection(self, run_id: str, outcome: BisectionOutcome) -> None: ...

    def save_patch(self, run_id: str, candidate: Candidate) -> None: ...

    def save_escalation(self, run_id: str, reason: str, report: dict[str, object]) -> None: ...

    def finish_run(self, run_id: str, status: str, confidence: float | None) -> None: ...


class InMemoryStore:
    """In-process ``Store`` for unit tests and local development."""

    def __init__(self) -> None:
        self.runs: dict[str, dict[str, object]] = {}
        self.events: list[AgentEvent] = []
        self.patches: list[tuple[str, Candidate]] = []
        self.bisections: list[tuple[str, BisectionOutcome]] = []
        self.escalations: list[tuple[str, str, dict[str, object]]] = []

    def create_run(self, report: BugReport) -> str:
        run_id = str(uuid.uuid4())
        self.runs[run_id] = {
            "repo": report.repo,
            "base_ref": report.base_ref,
            "source": report.source,
            "source_ref": report.source_ref,
            "status": "running",
            "confidence": None,
        }
        return run_id

    def record_event(self, event: AgentEvent) -> None:
        self.events.append(event)

    def set_triage(self, run_id: str, severity: str, component: str) -> None:
        self.runs[run_id]["severity"] = severity
        self.runs[run_id]["component"] = component

    def save_bisection(self, run_id: str, outcome: BisectionOutcome) -> None:
        self.bisections.append((run_id, outcome))

    def save_patch(self, run_id: str, candidate: Candidate) -> None:
        self.patches.append((run_id, candidate))

    def save_escalation(self, run_id: str, reason: str, report: dict[str, object]) -> None:
        self.escalations.append((run_id, reason, report))

    def finish_run(self, run_id: str, status: str, confidence: float | None) -> None:
        self.runs[run_id]["status"] = status
        self.runs[run_id]["confidence"] = confidence

    def events_for(self, run_id: str) -> list[AgentEvent]:
        return [e for e in self.events if e.run_id == run_id]
