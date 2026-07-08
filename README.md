# Automated Bug Triage & Reproduction Agent

A multi-agent system that ingests a bug report, triages it, reproduces it in a
hardened sandbox, optionally bisects to the introducing commit, drafts a fix,
validates it, and then either opens a **draft** pull request or **escalates to a
human** with a structured, self-contained report. It targets arbitrary Python
and Java GitHub repositories.

The headline is **the safe autonomous triage-to-patch loop with calibrated human
escalation** — not git bisect. The two genuinely hard, load-bearing parts are
(a) executing untrusted third-party and generated code safely, and (b) knowing
when the system must *not* act and should hand off to a human.

> Design spec: [`docs/spec.md`](docs/spec.md). Working conventions for
> contributors (and Claude Code): [`CLAUDE.md`](CLAUDE.md).

## Safety rails (non-negotiable)

1. **All untrusted execution is sandboxed.** Every repo build, repro run, test
   suite, and static-analysis pass runs in an ephemeral Docker container under
   the **gVisor `runsc` runtime** — network off by default, all capabilities
   dropped, read-only root, non-root user, resource-limited. Never `runc`.
2. **Draft PRs only, human-gated.** No merges, no default-branch writes, no
   force-push, no closing issues, no repo settings/permission changes.
3. **No false bisection.** A high skip ratio is reported as *inconclusive* and
   the pipeline falls back to in-tree localization. A wrong introducing commit is
   worse than none.
4. **No secrets in the repo.** Credentials are read from the environment only.

## Architecture

Five agents behind an orchestrator that runs them as an audited DAG state
machine. Every node writes an append-only `agent_events` row on entry and exit.

```
bug report → Triage → Reproduction → ⟨regression?⟩ → Bisection ┐
                                            └→ SBFL / code-search┴→ Localize
                                                                      → Fix (k candidates → Selection)
                                                                      → Validation (repro + suite + lint + Reviewer)
                                                                      → ⟨confidence gate⟩ → draft PR  |  ESCALATE
```

Any hard-gate failure or a composite confidence below the calibrated threshold
routes to a self-contained escalation report.

| Layer | Location |
|---|---|
| Sandbox interface + gVisor/Docker backend | `app/sandbox/` |
| Agents (triage, reproduction, bisection, localize, fix, validation) | `app/agents/` |
| Orchestrator: DAG, confidence gate, escalation, HTTP surface | `app/orchestrator/` |
| Provider adapters (LLM / embeddings) | `app/providers/` |
| GitHub client (read + draft PR only) | `app/vcs/` |
| Postgres schema, migrations, repositories | `app/db/` |
| SWE-bench / Multi-SWE-bench eval + metrics | `app/eval/` |
| Per-agent model + threshold config | `app/config/` |

## Requirements

- **Python 3.12+**, managed with [`uv`](https://docs.astral.sh/uv/).
- **Docker** with the **gVisor `runsc` runtime** registered (Linux host) for the
  sandbox — integration and eval tiers. gVisor is Linux-only; on macOS/Windows
  those tiers skip cleanly rather than falling back to `runc`.
- **Postgres** with the **`pgvector`** extension (run state, audit log, dedup).
- An **Ollama** endpoint (or a hosted API by config) for the LLM-backed agents
  (Phase 3+). The fast unit suite mocks all providers and needs none of this.

## Getting started

```bash
make setup      # create venv, install deps, install pre-commit hooks
make test       # unit tier: fast, mocked providers, no Docker — the dev loop
make lint       # ruff check + format --check
make typecheck  # mypy --strict
make migrate    # alembic upgrade head   (needs DATABASE_URL)
make run        # start the FastAPI orchestrator (uvicorn)
```

Configuration lives in [`app/config/default.yaml`](app/config/default.yaml):
per-agent provider/model/sampling, `fix.k`, `reproduction.n_runs`,
`bisection.max_skip_ratio`, and `confidence.threshold`. Secrets are referenced by
environment-variable name (`api_key_env`), never stored. `DATABASE_URL` overrides
the configured database.

### HTTP surface

```
POST /runs                     submit a bug report → run outcome
GET  /runs/{id}                run status and confidence
GET  /runs/{id}/escalation     the self-contained human-handoff report
GET  /healthz
```

## Testing

Three tiers, kept separate (markers in `pyproject.toml`):

- **`unit`** — mocked providers, no Docker. Deterministic and offline; the fast
  loop and the pre-commit gate. `make test`.
- **`integration`** — real Docker under `runsc`, real Postgres. Guarded to skip
  with a clear reason when the runtime/DB is unavailable. `make test-int`.
- **`eval`** — SWE-bench Verified (Python) and Multi-SWE-bench (Java); on demand,
  excluded from the default run. `make eval`.

Development is TDD (red → green → refactor), small conventional commits, one
logical change each. The sandbox-escape acceptance test (`tests/integration/`) is
the gating security test for the whole project.

## Build status

Built phase by phase per the spec; earlier acceptance checks gate later phases.

- [x] **Phase 1 — Sandbox & gVisor hardening.** `Sandbox` interface, ephemeral
      hardened gVisor/Docker backend, hostile-payload escape acceptance test.
- [x] **Phase 2 — End-to-end skeleton.** Full audited DAG, revert-only fix
      (SapFix full-revert), confidence gate, escalation, draft-PR handoff,
      Postgres schema + append-only audit log, SWE-bench harness + calibration
      threshold sweep, FastAPI surface.
- [x] **Phase 3 — Real LLM Fix & Localize** behind the provider interface.
      Config-driven provider factory (Ollama default), SBFL (Ochiai) + LLM
      code-search Localize with a bisection prior, k-candidate Fix with
      suite-per-candidate and Selection, LLM Reviewer and repro synthesizer.
- [ ] **Phase 4 — Bisection accelerator** (bounded, skip-aware).
- [ ] **Phase 5 — Java execution profile** + Multi-SWE-bench eval.

> **gVisor is Linux-only.** The escape test and other integration/eval tiers are
> written as real executable tests but skip on macOS/Windows; run them on a
> Linux + gVisor host (CI) to turn the gating checks green.

## References

SapFix (Meta), Getafix (Meta), AutoCodeRover / SpecRover, SWE-agent, SWE-bench
Verified, Multi-SWE-bench, and gVisor. See `docs/spec.md` for full citations.
