# CLAUDE.md

Guidance for Claude Code working in this repository. Read this fully before making changes.

## Project

Multi-agent system that triages an incoming bug report, reproduces it in a sandbox, optionally bisects to the introducing commit, drafts a fix, validates it, and either opens a draft pull request or escalates to a human. Targets arbitrary Python and Java GitHub repositories.

**The design spec is the source of truth: `spec.md`.** This file governs *how* we work (conventions, workflow, tooling). When they appear to conflict, follow the spec for design and ask before diverging.

## Non-negotiable safety rails

These are load-bearing. Violating any of them is a bug regardless of test status.

1. **Never run a repo's build, tests, or a generated reproduction outside the sandbox.** All such execution goes through the `Sandbox` interface, which launches Docker containers under the gVisor runtime (`--runtime=runsc`), network off by default, all capabilities dropped, read-only root, non-root user, resource-limited, ephemeral. The sandbox-escape test is a gating test for the whole project and must stay green.
2. **Draft pull requests only, human-gated.** No merges, no writes to a default branch, no force-push, no closing issues, no changing repo settings or permissions.
3. **Never report a bisection introducing commit when the skip ratio is high.** Mark it inconclusive and fall back. A false introducing commit is worse than none.
4. **Never commit secrets.** No tokens, API keys, or `.env` files. Read credentials from the environment only.

If a task seems to require breaking one of these, stop and surface it rather than working around it.

## Workflow: test-driven development

Work in strict red-green-refactor cycles. Do not write implementation code before there is a failing test for it.

1. **Red.** Write the smallest test that expresses the next behavior. Run it. Confirm it fails for the expected reason (assertion, not import error).
2. **Green.** Write the minimum code to make that test pass. Nothing more.
3. **Refactor.** Clean up with the test green. Re-run to confirm still green.
4. Commit (see commit rules below). Then repeat.

Rules that make TDD real here:

- **Each acceptance check in the spec is a test.** Phase 1's sandbox-escape check, Phase 2's end-to-end run, Phase 4's known-regression bisection: write these as executable tests, not manual checklists.
- **LLM calls are mocked in the fast suite.** Unit tests inject a fake `LLMProvider` that returns fixtures, so tests are deterministic and offline. Real-model behavior is exercised only in the eval suite (below), never in the fast loop.
- **Three test tiers, kept separate:**
  - `unit` (mocked providers, no Docker): fast, run on every cycle and before every commit.
  - `integration` (real sandbox, real Docker under `runsc`): slower, run before pushing and in CI.
  - `eval` (SWE-bench Verified for Python, Multi-SWE-bench for Java): slowest, run on demand and for metrics. Marked and excluded from the default run.
- **Do not weaken a test to make it pass.** If a test is wrong, fix the test deliberately and say so in the commit; do not loosen an assertion to get green.
- **Our own suite must be deterministic.** Flaky *repro* detection is a product feature; a flaky test in *our* suite is a defect. Fix the root cause, never add retries or sleeps to paper over it. `pytest -p randomly` is used to catch order dependence.
- **A bug fix starts with a failing regression test** that reproduces the bug, then the fix. No fix lands without a test that would have caught it.

**Definition of done for a task:** the new behavior has a test, the full `unit` suite is green, `lint` and `typecheck` pass, and the change is committed with a conventional-commit message.

## Workflow: frequent, small commits


- **NEVER ADD CLAUDE AS CO-AUTHOR IN COMMITS**. NEVER EVER OR I KILL YOU.
- **Commit after every green refactor**, at the granularity of one logical change. Prefer many small commits over few large ones. A commit should be reviewable in a minute.
- **Test and implementation land together** in the same commit (the test that drove the code ships with the code).
- **Conventional Commits format:**
  ```
  <type>(<scope>): <subject>

  <optional body: what and why, not how>
  ```
  Types: `feat`, `fix`, `test`, `refactor`, `chore`, `docs`, `perf`, `build`, `ci`.
  Scopes map to the layout: `sandbox`, `triage`, `reproduction`, `bisection`, `localize`, `fix`, `validation`, `orchestrator`, `providers`, `vcs`, `db`, `eval`, `config`.
  Example: `feat(sandbox): drop all caps and enforce read-only rootfs`.
- **Never commit a failing test to a shared branch.** If you must checkpoint mid-cycle, do it on a feature branch and mark the message `wip:`; squash before the branch merges so history stays green.
- **Reference the phase** in the body when relevant (for example, `Phase 1 acceptance: sandbox escape blocked`).
- **Branch per phase or per agent.** Keep `main` always green and always deployable.
- Do not bundle unrelated changes. Formatting-only churn goes in its own `chore` or `refactor` commit.

## Build order

Follow the five phases in `bug-triage-agent-spec.md` in order. Do not start a later phase before the earlier phase's acceptance test is green. In particular, **Phase 1 (sandbox) comes before any agent logic**, because it cannot be retrofitted safely.

## Tooling and commands

Python 3.12+. Package management with `uv`. Lint and format with `ruff`, type-check with `mypy` (strict), test with `pytest`, migrations with `alembic`. These are the intended interfaces; wire them into a `Makefile` so the commands below are stable.

```
make setup          # create venv, install deps, install pre-commit hooks
make test           # unit tier only (mocked providers, no Docker); the fast loop
make test-int       # integration tier (requires Docker + runsc installed)
make eval           # eval tier (SWE-bench / Multi-SWE-bench); on demand
make lint           # ruff check + ruff format --check
make typecheck      # mypy strict
make migrate        # alembic upgrade head
make run            # start the FastAPI orchestrator locally
```

- `runsc` must be installed and registered as a Docker runtime for `make test-int`. Guard integration tests with a skip-if-unavailable check that reports clearly, and never fall back to `runc` silently.
- Pre-commit runs `ruff`, `mypy`, and the `unit` suite. A commit that fails these should not be created.

## Code conventions

- **Type hints everywhere.** `mypy` runs in strict mode. No untyped public functions.
- **The interfaces in the spec are contracts.** `Sandbox`, `LLMProvider`, and `EmbeddingProvider` are the seams. Program to them; keep the gVisor/Docker backend, the Ollama backend, and any hosted-API backend behind them so each is swappable by config alone.
- **Per-agent model config.** Never hard-code a model name, endpoint, or sampling parameter. Everything comes from config (see the config surface in the spec). Different agents may use different models.
- Small, single-purpose functions. Prefer pure functions and explicit dependencies (constructor injection) so units are testable without patching globals.
- **`agent_events` is append-only.** Never write code that updates or deletes rows there. It is the audit trail.
- Structured logging with the `run_id` on every log line so a run is traceable end to end. Do not log secrets or full untrusted stdout at info level.
- Docstrings on public functions state intent and any safety relevance (for example, "runs untrusted code; must be called only via Sandbox").
- Keep diffs minimal and focused. The system we are building prefers small patches with a bounded blast radius; hold ourselves to the same standard.

## Testing specifics for this project

- **Sandbox tests** assert isolation, not just function: outbound network blocked, host paths outside the workspace unreadable, writes outside the scratch mount denied, and the gVisor kernel signature present (the sandboxed `/proc/version` reports the Sentry version, not the host kernel). These are integration tier.
- **Agent tests** mock the provider and assert on the structured output and the emitted confidence signals, not on model prose.
- **Confidence and escalation** have their own tests: hard-gate failures must always escalate; the composite must be computed only from the measurable signals defined in the spec; the calibration threshold is loaded from config, never inlined.
- **Eval runs** produce metrics (resolve rate, escalation precision and recall across the threshold sweep, median time to draft-PR-or-escalation). Treat metric regressions as review-worthy, but eval is not part of the pass/fail gate for a normal commit.

## What not to do

- Do not run untrusted code outside the sandbox, even "just to check something."
- Do not add sleeps or retries to stabilize a flaky test in our own suite.
- Do not hard-code model names, endpoints, credentials, or the escalation threshold.
- Do not make the pipeline take autonomous, irreversible actions on a repo. Draft PR or escalate, nothing else.
- Do not skip build phases or land a later phase before the earlier acceptance test is green.
- Do not expand a patch's scope to force a test green; fix the cause.

## When unsure

Ask, or leave a `# TODO(question):` with a clear description and surface it, rather than guessing at a design decision that the spec does not cover. Do not invent behavior for the safety rails.