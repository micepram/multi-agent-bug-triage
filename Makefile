.PHONY: setup test test-int eval lint typecheck migrate run fmt

# Create venv, install deps, install pre-commit hooks.
setup:
	uv sync --extra dev
	uv run pre-commit install || true

# Unit tier only (mocked providers, no Docker); the fast loop.
test:
	uv run pytest -m "unit"

# Integration tier (requires Docker + runsc installed).
test-int:
	uv run pytest -m "integration"

# Eval tier (SWE-bench / Multi-SWE-bench); on demand.
eval:
	uv run pytest -m "eval"

# ruff check + ruff format --check.
lint:
	uv run ruff check .
	uv run ruff format --check .

# Auto-format.
fmt:
	uv run ruff format .
	uv run ruff check --fix .

# mypy strict.
typecheck:
	uv run mypy app tests

# alembic upgrade head.
migrate:
	uv run alembic upgrade head

# Start the FastAPI orchestrator locally.
run:
	uv run uvicorn app.orchestrator.api:app --reload
