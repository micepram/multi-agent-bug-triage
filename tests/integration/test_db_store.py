"""Integration round-trip for the Postgres-backed Store (spec Section 7).

Requires a real Postgres with pgvector (DATABASE_URL). Skips cleanly otherwise.
Asserts persistence works and that agent_events is append-only in practice.
"""

from __future__ import annotations

import os

import pytest
from app.agents.types import BugReport, Candidate
from app.orchestrator.store import AgentEvent

pytestmark = pytest.mark.integration

DATABASE_URL = os.environ.get("DATABASE_URL")


@pytest.fixture
def session_factory():  # type: ignore[no-untyped-def]
    if not DATABASE_URL:
        pytest.skip("DATABASE_URL not set; skipping Postgres integration test.")
    from app.db.models import Base
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker

    engine = create_engine(DATABASE_URL)
    with engine.begin() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    return sessionmaker(engine)


def test_run_lifecycle_and_append_only_events(session_factory) -> None:  # type: ignore[no-untyped-def]
    from app.db.models import AgentEventRow, Patch, Run
    from app.db.store import SqlStore
    from sqlalchemy import func, select

    store = SqlStore(session_factory)
    report = BugReport(repo="octo/repo", base_ref="v1", source="manual", title="bug")

    run_id = store.create_run(report)
    store.set_triage(run_id, "high", "runtime")
    store.record_event(AgentEvent(run_id=run_id, agent="triage", phase="enter"))
    store.record_event(AgentEvent(run_id=run_id, agent="triage", phase="exit"))
    store.save_patch(run_id, Candidate(kind="revert", diff="d", files_touched=1, lines_touched=3))
    store.finish_run(run_id, "draft_pr", 0.82)

    with session_factory() as s:
        run = s.get(Run, __import__("uuid").UUID(run_id))
        assert run is not None
        assert run.status == "draft_pr"
        assert run.severity == "high"
        n_events = s.scalar(
            select(func.count()).select_from(AgentEventRow).where(AgentEventRow.run_id == run.id)
        )
        assert n_events == 2  # both inserts retained; nothing overwritten
        n_patches = s.scalar(select(func.count()).select_from(Patch).where(Patch.run_id == run.id))
        assert n_patches == 1
