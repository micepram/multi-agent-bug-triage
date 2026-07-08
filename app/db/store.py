"""Postgres-backed :class:`Store` (spec Section 7).

Implements the orchestrator's persistence contract against SQLAlchemy. Writes to
``agent_events`` are inserts only — there is deliberately no update/delete path
for the audit trail.
"""

from __future__ import annotations

import uuid

from sqlalchemy import update
from sqlalchemy.orm import Session, sessionmaker

from app.agents.types import BisectionOutcome, BugReport, Candidate
from app.db.models import AgentEventRow, Bisection, Escalation, Patch, Run
from app.orchestrator.store import AgentEvent


class SqlStore:
    """A ``Store`` backed by a SQLAlchemy session factory."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def create_run(self, report: BugReport) -> str:
        run_id = uuid.uuid4()
        with self._session_factory() as session, session.begin():
            session.add(
                Run(
                    id=run_id,
                    repo=report.repo,
                    base_ref=report.base_ref,
                    source=report.source,
                    source_ref=report.source_ref,
                    status="running",
                )
            )
        return str(run_id)

    def record_event(self, event: AgentEvent) -> None:
        # INSERT only. Never UPDATE/DELETE: agent_events is the audit trail.
        with self._session_factory() as session, session.begin():
            session.add(
                AgentEventRow(
                    run_id=uuid.UUID(event.run_id),
                    agent=event.agent,
                    phase=event.phase,
                    inputs=event.inputs,
                    outputs=event.outputs,
                    signals=event.signals,
                    model=event.model,
                    tokens=event.tokens,
                    duration_ms=event.duration_ms,
                )
            )

    def set_triage(self, run_id: str, severity: str, component: str) -> None:
        self._update_run(run_id, severity=severity, component=component)

    def save_bisection(self, run_id: str, outcome: BisectionOutcome) -> None:
        with self._session_factory() as session, session.begin():
            session.add(
                Bisection(
                    run_id=uuid.UUID(run_id),
                    good_ref=outcome.good_ref,
                    bad_ref=outcome.bad_ref,
                    introducing_commit=outcome.introducing_commit,
                    skip_ratio=outcome.skip_ratio,
                    conclusive=outcome.conclusive,
                )
            )

    def save_patch(self, run_id: str, candidate: Candidate) -> None:
        with self._session_factory() as session, session.begin():
            session.add(
                Patch(
                    run_id=uuid.UUID(run_id),
                    kind=candidate.kind,
                    diff=candidate.diff,
                    files_touched=candidate.files_touched,
                    lines_touched=candidate.lines_touched,
                    suite_passed=candidate.suite_passed,
                    repro_passed=candidate.repro_passed,
                    regression_free=candidate.regression_free,
                    selected=candidate.selected,
                )
            )

    def save_escalation(self, run_id: str, reason: str, report: dict[str, object]) -> None:
        with self._session_factory() as session, session.begin():
            session.add(Escalation(run_id=uuid.UUID(run_id), reason=reason, report=report))

    def finish_run(self, run_id: str, status: str, confidence: float | None) -> None:
        self._update_run(run_id, status=status, confidence=confidence)

    def _update_run(self, run_id: str, **values: object) -> None:
        with self._session_factory() as session, session.begin():
            session.execute(update(Run).where(Run.id == uuid.UUID(run_id)).values(**values))

    # -- read accessors (used by the HTTP surface) --------------------------

    def get_run(self, run_id: str) -> dict[str, object] | None:
        with self._session_factory() as session:
            run = session.get(Run, uuid.UUID(run_id))
            if run is None:
                return None
            return {
                "repo": run.repo,
                "base_ref": run.base_ref,
                "source": run.source,
                "source_ref": run.source_ref,
                "severity": run.severity,
                "component": run.component,
                "status": run.status,
                "confidence": float(run.confidence) if run.confidence is not None else None,
            }

    def get_escalation(self, run_id: str) -> tuple[str, dict[str, object]] | None:
        with self._session_factory() as session:
            row = (
                session.query(Escalation)
                .filter(Escalation.run_id == uuid.UUID(run_id))
                .order_by(Escalation.created_at.desc())
                .first()
            )
            if row is None:
                return None
            return row.reason, row.report
