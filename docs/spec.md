# Automated Bug Triage & Reproduction Agent — Build Handoff Spec

**Audience:** Claude Code (implementation), with the project owner reviewing.
**Status:** Design locked. Build in the five phases below, in order. Do not skip ahead: each phase produces a demonstrable artifact and later phases depend on earlier ones.

---

## 1. What this is, and what the headline is

A multi-agent system that ingests a bug report, triages it, reproduces it in a sandbox, attempts to identify the introducing commit when the bug is a regression, drafts a fix, validates it, and either opens a **draft** pull request or escalates to a human with a structured report. It targets **arbitrary Python and Java GitHub repositories** and runs against **live repos** with real issue trackers.

**The headline of this project is the safe autonomous triage-to-patch loop with calibrated human escalation, not git bisect.** This framing is deliberate. Because the system targets arbitrary repos whose builds we do not control, historical bisection will be inconclusive on a large fraction of them, so bisection is built as an **opportunistic accelerator** that fires only when the bug is a clean regression on a build-reproducible repo, and the system degrades gracefully to in-tree fault localization otherwise. The two genuinely hard, defensible parts are (a) executing untrusted generated and third-party code safely, and (b) knowing when the system should not act and must hand off to a human.

### Non-goals
- Not Meta/Facebook scale. Single-tenant, one host plus a sandbox pool. Design for correctness and safety, document the horizontal-scaling path, do not build it.
- No auto-merge. Every patch that clears the confidence gate becomes a **draft PR** for human review. Human approval is mandatory, matching SapFix's deployment model.
- No full-history bisection. Bisection is bounded and skip-aware (see Phase 4).
- No writing to a repo's default branch, no force-push, no closing issues, no modifying repo settings or permissions.

---

## 2. Prior art this design borrows from (for context, not reimplementation)

- **SapFix** (Marginean et al., Meta): generate-and-validate repair with template, mutation, full-revert, and partial-revert strategies; runs tests on patched builds; sends a single candidate to a human reviewer. Pilot produced 165 patches for 57 crashes over 90 days. We borrow the **revert-as-a-first-class-strategy** idea (used as the trivial fix in Phase 2) and the **mandatory human approval** gate.
- **Getafix** (Bader et al., Meta): learns fix patterns from past human fixes via hierarchical clustering. We do not reimplement this, but the Triage Agent's duplicate search is the same "have we seen this before" instinct.
- **CodeR** and **SpecRover / AutoCodeRover** (SWE-bench repair systems): five-agent decompositions with a Reproducer, a Fault Localizer, an Editor, and a Verifier. **SpecRover's Selection and Reviewer agents** are borrowed directly: generate multiple candidate patches, then select against the natural-language issue description (not only test pass/fail), and review whether the patch addresses the reported symptom rather than merely making a test green.
- **AutoCodeRover / SWE-agent**: spectrum-based fault localization (SBFL) and LLM code-search over the current tree. This is our **fallback localization** path when bisection is inapplicable.

---

## 3. Architecture and control flow

Six components: five agents plus an orchestrator. The orchestrator runs them as a DAG with persisted per-node state, retries, and an escalation exit.

### Agents
1. **Triage Agent** — classify severity, map to a fixed component taxonomy, dedup against existing issues via semantic search.
2. **Reproduction Agent** — synthesize a minimal repro, run it in the sandbox, capture stack trace and exit signal, verify determinism across N runs.
3. **Bisection Agent** (conditional) — for regressions only, bounded and skip-aware, produces the introducing diff.
4. **Fix Agent** — generate multiple candidate patches from the localized fault, run the existing test suite, then a Selection step.
5. **Validation Agent** — re-run the original repro against the patched build, run the full suite with a regression gate, run lint and static analysis, then a Reviewer step.
6. **Orchestrator** — DAG state machine, audit logging, confidence gate, escalation, GitHub draft-PR handoff.

### Revised DAG

```
                         +-----------+
  bug report  ---------> |  Triage   |
                         +-----------+
                               | (not a duplicate)
                               v
                        +--------------+
                        | Reproduction |  run N times; require deterministic repro
                        +--------------+
                          | fail x3 -> ESCALATE (could-not-reproduce report)
                          v
                    < regression? >
             yes /                  \ no or inconclusive
                v                      v
        +--------------+        +-------------------+
        |  Bisection   |        | SBFL / code-search |
        | (bounded,    |        |  localization      |
        |  skip-aware) |        +-------------------+
        +--------------+                |
              | introducing diff        |
              +-----------+  +-----------+
                          v  v
                     +-----------+
                     | Localize  |  merged fault location + priors
                     +-----------+
                          v
                     +-----------+
                     |    Fix    |  k candidates -> run suite -> Selection
                     +-----------+
                          v
                     +-----------+
                     | Validation|  repro + full suite + regression gate + lint + Reviewer
                     +-----------+
                          v
                   < confidence gate >
             pass /                    \ fail
                 v                        v
          +-------------+          +-------------+
          | draft PR    |          |  ESCALATE   |
          | (human      |          | (structured |
          |  review)    |          |  report)    |
          +-------------+          +-------------+
```

Every node writes an `agent_events` row on entry and exit with inputs, outputs, and any confidence signals. Any hard-gate failure or a composite confidence below threshold routes to ESCALATE.

---

## 4. Build phases (the spine of this handoff)

Build in this order. Each phase ends with an acceptance check that must pass before moving on.

### Phase 1 — Sandbox contract and gVisor hardening (security-critical, do this first)

The sandbox is load-bearing and cannot be retrofitted, so it is built and proven before any agent logic. Because the system runs arbitrary third-party repos, the threat model is untrusted code execution: cloning and building an arbitrary repo runs attacker-controllable code before any patch is written (Python `setup.py` and `pip install` execute code at install time, `pytest` executes `conftest.py` at collection, Maven and Gradle run arbitrary plugin and build-script code). Shared-kernel Docker (`runc`) is not an adequate boundary for this class of workload.

**Requirements**
- All repo builds, repro runs, test-suite runs, and static analysis execute inside containers launched under the **gVisor runtime (`runsc`)**, not the default `runc`. gVisor is a drop-in OCI runtime (`docker run --runtime=runsc ...`) that interposes a userspace application kernel (Sentry) so a container exploit must first break out of Sentry before reaching the host kernel. Expected overhead is roughly 5 to 20 percent depending on syscall frequency; acceptable here.
- Additional hardening on every sandbox container:
  - `--network=none` by default. Network is opt-in per run and, when enabled for dependency fetch, is restricted (see below).
  - `--cap-drop=ALL`, `--security-opt=no-new-privileges`.
  - Read-only root filesystem; a single writable ephemeral scratch mount for the workspace.
  - Non-root user inside the container.
  - CPU, memory, and PID limits, plus a hard wall-clock timeout per run.
  - Ephemeral: the container is destroyed after each run. No reuse across bug reports.
  - The Docker socket is never mounted into a sandbox container.
- **Dependency fetch isolation.** Installs need network, but arbitrary install scripts must not get open network. Run dependency resolution in a separate, short-lived, network-enabled container that produces a populated dependency cache or built image, then run the actual build/test/repro in a `--network=none` container against that cache. Prefer an allowlisted proxy (package registries only) for the fetch step.
- **Sandbox interface.** Expose one internal interface, `Sandbox`, with methods roughly: `prepare(repo, ref) -> workspace`, `run(cmd, timeout, network=False) -> RunResult{stdout, stderr, exit_code, duration, timed_out}`, `read_file(path)`, `write_file(path, bytes)`, `destroy()`. The gVisor/Docker implementation sits behind this interface so an alternative backend (for example a Firecracker-based microVM service) can be swapped in later without touching agent code.

**Known compatibility caveat to design around:** gVisor restricts `ptrace`, so native `gdb`-style tooling will not work inside the sandbox. This does not affect the planned localization path (Python `coverage` uses `sys.monitoring`, Java JaCoCo uses bytecode instrumentation, both fine). Any repro that requires a native ptrace-based debugger should be treated as unrunnable and routed to escalation rather than silently failing.

**Acceptance check:** a deliberately hostile test payload that attempts (1) an outbound network connection, (2) a read of a host path outside the workspace, and (3) a write outside the scratch mount, all fail inside the sandbox, and the run is captured cleanly with a nonzero exit. Log the gVisor kernel signature (`cat /proc/version` returns the Sentry version, not the host kernel) as evidence the runtime is active.

### Phase 2 — End-to-end skeleton with a trivial fix strategy

Prove the whole pipeline runs before any LLM writes a real patch. The failure mode for a five-agent system is building all agents to 80 percent with nothing that runs end to end.

**Requirements**
- Full DAG wired: Triage -> Reproduction -> Localize -> Fix -> Validation -> confidence gate -> draft PR or escalate. Bisection node present but stubbed to always return "inconclusive" so the SBFL/localize fallback path is exercised.
- **Trivial fix strategy only:** the Fix Agent's sole strategy in this phase is a **revert** of a candidate commit (SapFix's full-revert strategy). No LLM patch generation yet. This lets the entire orchestration, audit log, confidence gate, escalation path, and GitHub draft-PR handoff be built and tested against a real signal.
- Postgres schema live (Section 7). Every node writes `agent_events`.
- Confidence gate implemented as a composite of measurable signals with a configurable threshold (Section 6), even though signal quality is limited in this phase.
- **Eval harness wired in from day one** (Section 8): the input stream for development is SWE-bench Verified (500 human-validated Python instances). Each instance provides an issue, a repo at a base commit, a fail-to-pass test, and a known-good reference patch, so the pipeline can be scored automatically rather than eyeballed.

**Acceptance check:** on a small SWE-bench Verified slice, the pipeline runs end to end for every instance, produces either a draft PR object (not pushed in dev) or a structured escalation, and writes a complete audit trail. Report resolve rate (expected low with revert-only) and, more importantly, that zero instances crash the orchestrator or leak out of the sandbox.

### Phase 3 — Real LLM Fix and Localize agents behind the provider interface

Swap the trivial strategy for real generation.

**Requirements**
- **Provider interface** (Section 9): every LLM call goes through one adapter so local Ollama models and hosted APIs are interchangeable by config. Default to local; make the model, endpoint, and sampling params config-driven.
- **Localize Agent** (SBFL + LLM code-search): compute spectrum-based suspiciousness from passing/failing test executions where a test suite exists, and combine with LLM code-search over the repo (file and symbol retrieval) to produce a ranked fault location set. When a bisection introducing diff exists (Phase 4), fold it in as a strong prior.
- **Fix Agent**: generate `k` candidate patches (k configurable, default 3 to 5) conditioned on the stack trace, the localized region, the relevant source, the failing test, and, when present, the introducing diff. Run the existing suite on each candidate in the sandbox. Then a **Selection** step ranks survivors against the natural-language issue description, not only test results, to avoid discarding a correct patch that an incomplete suite fails to reward.
- Keep patches small: prefer minimal diffs, cap blast radius (files and lines touched) and feed that cap into the confidence signal.

**Acceptance check:** on the same SWE-bench Verified slice, resolve rate rises meaningfully over the revert-only baseline. Record resolve rate broken down by whether a bisection prior was available (should be higher when it is).

### Phase 4 — Bisection accelerator

Add the conditional regression-finding path.

**Requirements**
- **Regression gate.** After a stable repro, decide whether the bug is a regression: does the report cite a last-good version or tag, or does the repro pass on an older tag and fail on HEAD? If neither, skip bisection and go straight to SBFL/localize.
- **Bounded window.** When it is a regression, bisect between the last-good ref and HEAD, never full history.
- **Containerized, skip-aware bisect.** Run `git bisect run` where the test script builds and runs the repro for each candidate commit inside the sandbox using the repo's own lockfile or CI config. If a candidate fails to build for environment reasons (dependency or toolchain drift), the script must exit with the `git bisect skip` code (125), never a "bad" code. Track the skip ratio: if too many commits skip, mark the bisection **inconclusive** and fall back.
- **Determinism prerequisite.** Bisection only runs when the repro is deterministic (Reproduction Agent verified it across N runs). Flaky repros never enter bisection.
- Output: the introducing commit and its diff, plus a bisection-certainty signal (clean bisect vs. high skip ratio) that feeds the confidence gate.

**Acceptance check:** construct or select a handful of known regressions on build-pinned repos, confirm the correct introducing commit is found, and confirm that a repo with floating deps triggers skips and falls back cleanly rather than reporting a false introducing commit.

### Phase 5 — Java execution profile and multilingual eval

Generalize the language-specific machinery.

**Requirements**
- Two sandbox execution profiles selected by repo detection: a **Python image** (pip/poetry/uv, pytest, coverage) and a **JVM image** (Maven/Gradle, JUnit, JaCoCo). Reproduction and Localize call per-language adapters behind a common interface; the orchestrator and everything else stay language-agnostic.
- Historical reproducibility notes to encode: Python with floating deps is the worst case for bisection; Python with a committed lockfile is workable; Java via Maven/Gradle is somewhat better when versions are pinned, though plugin and BOM resolution can still drift. The skip-aware bisect from Phase 4 already handles this; just make sure both profiles surface build-vs-genuine failure distinctly.
- **Java evaluation:** add **Multi-SWE-bench** (ByteDance Seed, NeurIPS 2025 Datasets and Benchmarks) as the Java harness. It provides human-validated Java GitHub issue instances with per-instance Docker environments and evaluates by running each project's built-in test suite against post-PR behavior, which matches this system's validation model directly.

**Acceptance check:** the pipeline resolves a nontrivial fraction of a Java Multi-SWE-bench slice end to end, with the same audit and escalation guarantees as the Python path.

---

## 5. Per-agent functional requirements (reference)

### Triage Agent
- Input: a bug report (structured fields or free text; when sourced from GitHub, the issue title, body, labels, and linked references).
- Classify severity into a fixed enum (for example: critical, high, medium, low).
- Map to a **fixed component taxonomy per target repo.** The taxonomy is configured per repo, not free-form, or downstream routing is unusable. If the repo has no configured taxonomy, use a coarse default (for example: build, runtime, api, tests, docs, unknown).
- Deduplicate against existing issues using semantic search over issue-embedding vectors (pgvector, Section 7). Emit a duplicate-confidence score and the top matches. High-confidence duplicates short-circuit to an escalation note ("likely duplicate of #NNN") rather than proceeding.
- Output: a structured triage record persisted to `runs`.

### Reproduction Agent
- Synthesize a minimal reproduction (a script or a test) from the report. LLMs can reproduce bugs by synthesizing test programs from bug reports; keep the repro minimal and self-contained.
- Execute in the sandbox; capture stack trace, exit signal, and any assertion output.
- **Determinism check:** run the repro N times (N configurable, default 5). A repro that does not reproduce consistently is flagged flaky and must not proceed to bisection. Record the reproduce rate.
- Retry synthesis up to 3 attempts. After 3 failed attempts, escalate with a "could-not-reproduce" structured report.

### Bisection Agent
- See Phase 4. Conditional, bounded, skip-aware, determinism-gated. Emits introducing diff plus a certainty signal, or "inconclusive."

### Fix Agent
- See Phase 3. Multi-candidate generation, suite execution per candidate, Selection against the issue description. Minimal-diff preference with a blast-radius cap.

### Validation Agent
- Re-run the original repro against the patched build; it must now pass.
- Run the full existing test suite with a **regression gate:** no test that previously passed may now fail. A single new failure fails validation.
- Run lint and static analysis (per-language: for example ruff/mypy for Python, a static analyzer for Java) inside the sandbox.
- **Reviewer step:** an LLM judgment on whether the patch actually addresses the reported symptom versus merely making the repro test pass. This guards against the plausible-but-wrong patch failure mode common in automated program repair. The Reviewer's verdict feeds the confidence gate.

### Orchestrator
- FastAPI as the HTTP surface (ingestion endpoint, run status, escalation retrieval).
- The agent DAG runs behind it as an explicit state machine with a persisted run record so a run can be inspected and, where safe, resumed. Implement as a hand-rolled state machine or with a graph/state library; keep node transitions and retries explicit and logged.
- Owns the confidence gate, the escalation exit, and the GitHub draft-PR handoff.

---

## 6. Confidence and escalation (a first-class artifact, not a hand-tuned number)

**Do not use LLM self-reported confidence.** It is poorly calibrated. Compose the confidence score from measurable signals:

| Signal | Source | Nature |
|---|---|---|
| Reproduce rate | Reproduction Agent (N runs) | hard gate below a floor; else continuous |
| Bisection certainty | Bisection Agent (skip ratio) | continuous; absent when non-regression |
| Repro-after-patch passes | Validation Agent | hard gate |
| Full-suite regression delta | Validation Agent | hard gate (zero new failures) |
| Static analysis clean | Validation Agent | continuous |
| Reviewer verdict | Validation Agent | continuous |
| Patch blast radius | Fix Agent (files/lines) | continuous; smaller is safer |

- Any **hard gate** failure routes to ESCALATE regardless of the composite.
- Otherwise compute a composite from the continuous signals and escalate when it falls below a **calibrated threshold.**
- **Calibration methodology (build this, do not hand-pick the threshold):** assemble a labeled set (SWE-bench and Multi-SWE-bench instances have ground-truth patches, so "did the produced patch match/behave like the reference" is a label). Sweep the threshold and measure the **precision and recall of the escalate-vs-autofix decision**. Report the full curve and pick an operating point that prioritizes precision of the autofix path (a wrong autofix that reaches a human as a draft PR is worse than an unnecessary escalation). Persist the chosen threshold and the curve as an artifact.

**Escalation report contents:** the triage record, the repro (or the reason reproduction failed), the bisection result or the reason it was skipped, any candidate patches and why they failed the gate, and the specific gate(s) that triggered escalation. This report is the human handoff and must be self-contained.

---

## 7. Data model (Postgres)

Append-only audit semantics for `agent_events`. Use `pgvector` for dedup so no second datastore is introduced.

```sql
-- A single bug report processed through the pipeline.
CREATE TABLE runs (
    id              UUID PRIMARY KEY,
    repo            TEXT NOT NULL,          -- owner/name
    base_ref        TEXT NOT NULL,          -- commit or tag processed
    source          TEXT NOT NULL,          -- 'github_issue' | 'manual' | 'swebench'
    source_ref      TEXT,                   -- issue number or instance id
    severity        TEXT,                   -- triage enum
    component       TEXT,                   -- from repo taxonomy
    status          TEXT NOT NULL,          -- 'running' | 'draft_pr' | 'escalated' | 'failed'
    confidence      NUMERIC,                -- final composite, nullable until computed
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Append-only event per agent node entry/exit. The audit trail.
CREATE TABLE agent_events (
    id              BIGSERIAL PRIMARY KEY,
    run_id          UUID NOT NULL REFERENCES runs(id),
    agent           TEXT NOT NULL,          -- 'triage' | 'reproduction' | 'bisection' | ...
    phase           TEXT NOT NULL,          -- 'enter' | 'exit'
    inputs          JSONB,                  -- redacted as needed
    outputs         JSONB,
    signals         JSONB,                  -- confidence signals emitted here
    model           TEXT,                   -- provider/model used, if any
    tokens          INTEGER,
    duration_ms     INTEGER,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Candidate and final patches.
CREATE TABLE patches (
    id              UUID PRIMARY KEY,
    run_id          UUID NOT NULL REFERENCES runs(id),
    kind            TEXT NOT NULL,          -- 'revert' | 'llm_candidate' | 'selected'
    diff            TEXT NOT NULL,          -- unified diff
    files_touched   INTEGER,
    lines_touched   INTEGER,
    suite_passed    BOOLEAN,
    repro_passed    BOOLEAN,
    regression_free BOOLEAN,
    selected        BOOLEAN NOT NULL DEFAULT false,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Human handoffs.
CREATE TABLE escalations (
    id              UUID PRIMARY KEY,
    run_id          UUID NOT NULL REFERENCES runs(id),
    reason          TEXT NOT NULL,          -- 'no_repro' | 'gate_failed:<gate>' | 'likely_duplicate' | ...
    report          JSONB NOT NULL,         -- self-contained structured report
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Bisection results (nullable per run).
CREATE TABLE bisections (
    id                  UUID PRIMARY KEY,
    run_id              UUID NOT NULL REFERENCES runs(id),
    good_ref            TEXT,
    bad_ref             TEXT,
    introducing_commit  TEXT,               -- null when inconclusive
    skip_ratio          NUMERIC,
    conclusive          BOOLEAN NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Existing-issue embeddings for dedup (pgvector).
CREATE EXTENSION IF NOT EXISTS vector;
CREATE TABLE issue_embeddings (
    id          UUID PRIMARY KEY,
    repo        TEXT NOT NULL,
    issue_ref   TEXT NOT NULL,
    title       TEXT,
    embedding   vector(768),                -- dim per chosen embedding model
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ON issue_embeddings USING hnsw (embedding vector_cosine_ops);
```

Adjust the embedding dimension to the chosen model. Keep `agent_events` insert-only; never update or delete rows there.

---

## 8. Evaluation harness (wired in from Phase 2, not bolted on)

- **Python:** SWE-bench Verified, 500 human-validated instances. Each instance is an input (issue + repo@base_commit + fail-to-pass test) and a ground truth (reference patch), so the pipeline is scored automatically.
- **Java:** Multi-SWE-bench (NeurIPS 2025 Datasets and Benchmarks), human-validated Java instances with per-instance Docker environments, evaluated via each project's built-in test suite against post-PR behavior.
- **Metrics to report:**
  - Resolve rate (fraction where the produced patch passes the instance's fail-to-pass test without regressions), overall and split by whether a bisection prior was available.
  - Escalation precision and recall vs. the calibrated threshold, plus the full threshold sweep curve.
  - Median wall-clock time from ingestion to draft-PR-or-escalation (SapFix's headline metric was a 69-minute median; report the analog).
  - Sandbox integrity: zero escapes on the hostile-payload check across the full run.

---

## 9. Provider interface and config surface

Every model call goes through one adapter. Local-first, configurable to hosted APIs by config only.

```python
class LLMProvider(Protocol):
    def complete(self, messages: list[Message], *, model: str,
                 temperature: float, max_tokens: int, **kw) -> Completion: ...

class EmbeddingProvider(Protocol):
    def embed(self, texts: list[str], *, model: str) -> list[list[float]]: ...
```

- Default backend: local Ollama (reachable over the existing Tailscale-accessed host if applicable). Swapping to a hosted API is a config change: provider name, endpoint, model id, and sampling params, no code change.
- Config keys (per agent, so different agents can use different models):
  - `triage.model`, `reproduction.model`, `fix.model`, `selection.model`, `reviewer.model`, `embedding.model`
  - `provider` (`ollama` | `openai` | `anthropic` | ...), `endpoint`, `api_key_env`
  - `fix.k` (candidate count), `reproduction.n_runs`, `bisection.max_skip_ratio`, `confidence.threshold`
- **Expectation to encode:** a local ~30B-class model will have a lower patch-acceptance rate than a frontier API on the Fix step. This is the one place the local ceiling bites; keep the Fix and Selection models independently swappable so a hosted model can be dropped in there alone if desired.

---

## 10. Stack summary

- **Orchestrator/API:** Python, FastAPI. Agent DAG as an explicit persisted state machine behind it.
- **Sandbox:** Docker under the **gVisor `runsc` runtime**, hardened and ephemeral, behind a `Sandbox` interface (Firecracker/microVM backend swappable later).
- **Models:** local Ollama by default, hosted APIs by config, behind the provider interface.
- **Persistence:** Postgres with `pgvector` (audit log, run state, patches, escalations, dedup embeddings).
- **VCS integration:** GitHub API for issues, commits, diffs, and **draft** PR creation. Scoped token; no default-branch writes, no force-push, no settings or permission changes.
- **Eval:** SWE-bench Verified (Python), Multi-SWE-bench (Java).

### Suggested repo layout
```
/app
  /orchestrator      FastAPI app, DAG state machine, confidence gate, escalation
  /agents
    triage.py  reproduction.py  bisection.py  localize.py  fix.py  validation.py
  /sandbox           Sandbox interface + gVisor/Docker backend + hardening
  /providers         LLMProvider / EmbeddingProvider adapters
  /vcs               GitHub client (read + draft PR only)
  /db                schema, migrations, repositories
  /eval              SWE-bench + Multi-SWE-bench runners, metrics, threshold sweep
  /config            per-agent model + threshold config
/tests
  test_sandbox_escape.py   the hostile-payload acceptance check (Phase 1)
```

---

## 11. Assumptions and risks Claude Code must respect

- **Untrusted code is the default assumption.** Never run a repo's build, tests, or a generated repro outside the gVisor sandbox. The sandbox-escape test (Phase 1) is a gating test for the whole project.
- **Bisection is best-effort.** Never report an introducing commit from a bisection with a high skip ratio; mark it inconclusive and fall back. A false introducing commit is worse than none.
- **No autonomous side effects on the repo.** Draft PRs only, human-gated. No merges, no branch-default writes, no issue closing, no permission or settings changes.
- **Escalation must be self-contained.** A human reading only the escalation report must understand what happened without opening the codebase.
- **Determinism precedes bisection.** Flaky repros never enter bisection and should lower confidence.
- **Calibrate, do not guess, the escalation threshold.** The threshold is derived from a labeled sweep and persisted as an artifact.

---

## References (for traceability)

- Marginean et al., *SapFix: Automated End-to-End Repair at Scale* (Meta). Strategy taxonomy and human-approval model.
- Bader, Scott, Pradel, Chandra, *Getafix: Learning to Fix Bugs Automatically* (arXiv:1902.06111).
- Zhang et al., *AutoCodeRover: Autonomous Program Improvement* (ISSTA 2024); SpecRover (AutoCodeRover v2); CodeR. Reproducer/Selection/Reviewer and SBFL.
- Yang et al., *SWE-agent: Agent-Computer Interfaces Enable Automated Software Engineering* (NeurIPS 2024).
- Jimenez et al., *SWE-bench* and the SWE-bench Verified 500-instance subset.
- Zan et al., *Multi-SWE-bench: A Multilingual Benchmark for Issue Resolving* (arXiv:2504.02605; NeurIPS 2025 Datasets and Benchmarks).
- gVisor documentation (runsc as a drop-in Docker runtime; Sentry userspace kernel; ptrace restriction; overhead characteristics).