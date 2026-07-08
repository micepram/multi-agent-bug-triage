"""Unit tests for the FastAPI orchestrator surface.

Uses TestClient with an injected fake pipeline and the in-memory store, so the
HTTP contract is exercised without Docker, a database, or a real pipeline.
"""

from __future__ import annotations

from app.agents.types import BugReport
from app.orchestrator.api import create_app
from app.orchestrator.dag import RunOutcome
from app.orchestrator.store import InMemoryStore
from fastapi.testclient import TestClient


def _report_payload() -> dict[str, object]:
    return {
        "repo": "octo/repo",
        "base_ref": "v1",
        "source": "manual",
        "title": "Crash on empty input",
        "body": "boom",
    }


def _draft_pr_pipeline(store: InMemoryStore):  # type: ignore[no-untyped-def]
    def run(report: BugReport) -> RunOutcome:
        run_id = store.create_run(report)
        store.finish_run(run_id, "draft_pr", 0.85)
        return RunOutcome(run_id=run_id, status="draft_pr", confidence=0.85)

    return run


def _escalating_pipeline(store: InMemoryStore):  # type: ignore[no-untyped-def]
    def run(report: BugReport) -> RunOutcome:
        run_id = store.create_run(report)
        store.save_escalation(run_id, "no_repro", {"reason": "no_repro", "triage": {}})
        store.finish_run(run_id, "escalated", None)
        return RunOutcome(run_id=run_id, status="escalated", escalation_reason="no_repro")

    return run


def test_healthz() -> None:
    store = InMemoryStore()
    client = TestClient(create_app(_draft_pr_pipeline(store), store))
    assert client.get("/healthz").json() == {"status": "ok"}


def test_ingest_returns_draft_pr_outcome() -> None:
    store = InMemoryStore()
    client = TestClient(create_app(_draft_pr_pipeline(store), store))
    resp = client.post("/runs", json=_report_payload())
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "draft_pr"
    assert body["confidence"] == 0.85
    assert body["run_id"]


def test_get_run_status() -> None:
    store = InMemoryStore()
    client = TestClient(create_app(_draft_pr_pipeline(store), store))
    run_id = client.post("/runs", json=_report_payload()).json()["run_id"]
    resp = client.get(f"/runs/{run_id}")
    assert resp.status_code == 200
    assert resp.json()["status"] == "draft_pr"


def test_get_run_404_for_unknown() -> None:
    store = InMemoryStore()
    client = TestClient(create_app(_draft_pr_pipeline(store), store))
    assert client.get("/runs/does-not-exist").status_code == 404


def test_escalation_retrieval_is_self_contained() -> None:
    store = InMemoryStore()
    client = TestClient(create_app(_escalating_pipeline(store), store))
    run_id = client.post("/runs", json=_report_payload()).json()["run_id"]
    resp = client.get(f"/runs/{run_id}/escalation")
    assert resp.status_code == 200
    body = resp.json()
    assert body["reason"] == "no_repro"
    assert "triage" in body["report"]


def test_escalation_404_when_none() -> None:
    store = InMemoryStore()
    client = TestClient(create_app(_draft_pr_pipeline(store), store))
    run_id = client.post("/runs", json=_report_payload()).json()["run_id"]
    assert client.get(f"/runs/{run_id}/escalation").status_code == 404
