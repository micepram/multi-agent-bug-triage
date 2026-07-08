"""FastAPI HTTP surface for the orchestrator (spec Section 5).

Exposes ingestion (submit a bug report), run status, and escalation retrieval.
The pipeline runner and the read store are injected so the surface is testable
without a database or a real pipeline. This module takes no autonomous action on
any repo beyond what the pipeline itself does (draft PR or escalate).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol, runtime_checkable

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from app.agents.types import BugReport
from app.orchestrator.dag import RunOutcome

RunPipeline = Callable[[BugReport], RunOutcome]


@runtime_checkable
class RunReader(Protocol):
    def get_run(self, run_id: str) -> dict[str, object] | None: ...
    def get_escalation(self, run_id: str) -> tuple[str, dict[str, object]] | None: ...


class IngestResponse(BaseModel):
    run_id: str
    status: str
    confidence: float | None = None
    escalation_reason: str | None = None


def create_app(run_pipeline: RunPipeline, reader: RunReader) -> FastAPI:
    app = FastAPI(title="Bug Triage Orchestrator")

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/runs", response_model=IngestResponse)
    def ingest(report: BugReport) -> IngestResponse:
        outcome = run_pipeline(report)
        return IngestResponse(
            run_id=outcome.run_id,
            status=outcome.status,
            confidence=outcome.confidence,
            escalation_reason=outcome.escalation_reason,
        )

    @app.get("/runs/{run_id}")
    def get_run(run_id: str) -> dict[str, object]:
        run = reader.get_run(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="run not found")
        return {"run_id": run_id, **run}

    @app.get("/runs/{run_id}/escalation")
    def get_escalation(run_id: str) -> dict[str, object]:
        escalation = reader.get_escalation(run_id)
        if escalation is None:
            raise HTTPException(status_code=404, detail="no escalation for this run")
        reason, report = escalation
        return {"run_id": run_id, "reason": reason, "report": report}

    return app
