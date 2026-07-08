"""initial schema (spec Section 7)

Creates runs, agent_events (append-only audit trail), patches, escalations,
bisections, and issue_embeddings (pgvector) with an HNSW cosine index.

Revision ID: 0001
Revises:
Create Date: 2026-07-08
"""

from __future__ import annotations

import pgvector.sqlalchemy
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

EMBEDDING_DIM = 768


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("repo", sa.Text(), nullable=False),
        sa.Column("base_ref", sa.Text(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("source_ref", sa.Text()),
        sa.Column("severity", sa.Text()),
        sa.Column("component", sa.Text()),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Numeric()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "agent_events",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("runs.id"), nullable=False),
        sa.Column("agent", sa.Text(), nullable=False),
        sa.Column("phase", sa.Text(), nullable=False),
        sa.Column("inputs", postgresql.JSONB()),
        sa.Column("outputs", postgresql.JSONB()),
        sa.Column("signals", postgresql.JSONB()),
        sa.Column("model", sa.Text()),
        sa.Column("tokens", sa.Integer()),
        sa.Column("duration_ms", sa.Integer()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_agent_events_run_id", "agent_events", ["run_id"])

    op.create_table(
        "patches",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("runs.id"), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("diff", sa.Text(), nullable=False),
        sa.Column("files_touched", sa.Integer()),
        sa.Column("lines_touched", sa.Integer()),
        sa.Column("suite_passed", sa.Boolean()),
        sa.Column("repro_passed", sa.Boolean()),
        sa.Column("regression_free", sa.Boolean()),
        sa.Column("selected", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "escalations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("runs.id"), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("report", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "bisections",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("runs.id"), nullable=False),
        sa.Column("good_ref", sa.Text()),
        sa.Column("bad_ref", sa.Text()),
        sa.Column("introducing_commit", sa.Text()),
        sa.Column("skip_ratio", sa.Numeric()),
        sa.Column("conclusive", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "issue_embeddings",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("repo", sa.Text(), nullable=False),
        sa.Column("issue_ref", sa.Text(), nullable=False),
        sa.Column("title", sa.Text()),
        sa.Column("embedding", pgvector.sqlalchemy.Vector(EMBEDDING_DIM)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index(
        "ix_issue_embeddings_hnsw",
        "issue_embeddings",
        ["embedding"],
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )


def downgrade() -> None:
    op.drop_index("ix_issue_embeddings_hnsw", table_name="issue_embeddings")
    op.drop_table("issue_embeddings")
    op.drop_table("bisections")
    op.drop_table("escalations")
    op.drop_table("patches")
    op.drop_index("ix_agent_events_run_id", table_name="agent_events")
    op.drop_table("agent_events")
    op.drop_table("runs")
