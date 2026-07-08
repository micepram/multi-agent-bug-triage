"""ASGI entrypoint for `make run` (uvicorn app.orchestrator.main:app).

Assembles the FastAPI surface from the default config and the Phase 2 pipeline.
Backends connect lazily, so importing this module does not require Docker or a
live database; a request is what triggers a real run.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config.settings import load_settings
from app.db.store import SqlStore
from app.orchestrator.api import create_app
from app.orchestrator.composition import build_orchestrator

_CONFIG_PATH = Path(__file__).resolve().parents[1] / "config" / "default.yaml"


def _build() -> FastAPI:
    settings = load_settings(_CONFIG_PATH)
    session_factory = sessionmaker(create_engine(settings.database_url))
    orchestrator = build_orchestrator(settings, session_factory=session_factory)
    reader = SqlStore(session_factory)
    return create_app(orchestrator.run, reader)


app = _build()
