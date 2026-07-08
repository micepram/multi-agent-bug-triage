"""Schema-fidelity unit tests for the ORM (spec Section 7).

Offline metadata assertions that the tables and the audit-trail columns match the
spec. A real round-trip against Postgres/pgvector is exercised in the integration
tier.
"""

from __future__ import annotations

from app.db.models import EMBEDDING_DIM, AgentEventRow, Base


def test_all_spec_tables_are_defined() -> None:
    tables = set(Base.metadata.tables)
    assert tables == {
        "runs",
        "agent_events",
        "patches",
        "escalations",
        "bisections",
        "issue_embeddings",
    }


def test_agent_events_has_audit_columns() -> None:
    cols = set(AgentEventRow.__table__.columns.keys())
    assert {
        "run_id",
        "agent",
        "phase",
        "inputs",
        "outputs",
        "signals",
        "model",
        "tokens",
        "duration_ms",
        "created_at",
    } <= cols


def test_embedding_dimension_matches_configured_model() -> None:
    embedding_col = Base.metadata.tables["issue_embeddings"].columns["embedding"]
    assert embedding_col.type.dim == EMBEDDING_DIM  # type: ignore[attr-defined]
