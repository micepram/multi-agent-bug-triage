"""Phase 2 acceptance (unit tier): the eval harness drives the pipeline over a
slice, every instance yields an outcome, and none crash the orchestrator.

The real end-to-end run on SWE-bench with the gVisor sandbox is the eval tier;
here we prove the harness + orchestrator wiring with fake agents and a fake
sandbox so it is deterministic and offline.
"""

from __future__ import annotations

import json
from pathlib import Path

from app.agents.types import (
    BisectionOutcome,
    BugReport,
    Candidate,
    Language,
    Localization,
    Repro,
    Severity,
    TriageRecord,
    ValidationOutcome,
)
from app.eval.runner import run_slice
from app.eval.swebench import Instance, load_instances, to_bug_report
from app.orchestrator.confidence import GateConfig
from app.orchestrator.dag import Agents, Orchestrator, RunOutcome
from app.orchestrator.store import InMemoryStore
from app.sandbox.interface import RunResult, Workspace
from app.vcs.publisher import LocalDraftPRPublisher


class _Sandbox:
    def prepare(self, repo: str, ref: str) -> Workspace:
        return Workspace(repo=repo, ref=ref, volume="v")

    def run(self, cmd, timeout, *, network=False) -> RunResult:  # type: ignore[no-untyped-def]
        return RunResult(stdout="", stderr="", exit_code=0, duration=0.0, timed_out=False)

    def read_file(self, path: str) -> bytes:
        return b""

    def write_file(self, path: str, data: bytes) -> None:
        return None

    def destroy(self) -> None:
        return None


def _agents(*, escalate: bool) -> Agents:
    repro = Repro(
        script="s", language=Language.PYTHON, reproduced=True, reproduce_rate=1.0, n_runs=5
    )
    validation = ValidationOutcome(
        repro_after_patch_passes=not escalate,  # hard-gate fail when escalate
        regression_free=True,
        static_analysis_clean=1.0,
        reviewer_verdict=1.0,
    )
    candidate = Candidate(kind="revert", diff="d", files_touched=1, lines_touched=3)

    class T:
        def triage(self, report: BugReport) -> TriageRecord:
            return TriageRecord(severity=Severity.HIGH, component="runtime")

    class R:
        def reproduce(self, report, sandbox) -> Repro:  # type: ignore[no-untyped-def]
            return repro

    class B:
        def bisect(self, report, repro, sandbox) -> BisectionOutcome:  # type: ignore[no-untyped-def]
            return BisectionOutcome(conclusive=False)

    class L:
        def localize(self, report, repro, bisection, sandbox) -> Localization:  # type: ignore[no-untyped-def]
            return Localization(locations=[])

    class F:
        def generate_candidates(self, *a) -> list[Candidate]:  # type: ignore[no-untyped-def]
            return [candidate]

        def select(self, candidates, report) -> Candidate | None:  # type: ignore[no-untyped-def]
            return candidates[0] if candidates else None

    class V:
        def validate(self, *a) -> ValidationOutcome:  # type: ignore[no-untyped-def]
            return validation

    return Agents(
        triage=T(), reproduction=R(), bisection=B(), localize=L(), fix=F(), validation=V()
    )


def _make_run_one(store: InMemoryStore, *, escalate_ids: set[str]):  # type: ignore[no-untyped-def]
    def run_one(instance: Instance) -> RunOutcome:
        orch = Orchestrator(
            agents=_agents(escalate=instance.instance_id in escalate_ids),
            sandbox_factory=_Sandbox,
            store=store,
            publisher=LocalDraftPRPublisher(),
            threshold=0.7,
            gate_config=GateConfig(),
        )
        return orch.run(to_bug_report(instance))

    return run_one


def _instances() -> list[Instance]:
    return [
        Instance(instance_id="a-1", repo="octo/a", base_commit="c1", problem_statement="Crash"),
        Instance(instance_id="b-2", repo="octo/b", base_commit="c2", problem_statement="Error"),
    ]


def test_slice_runs_end_to_end_without_crashing() -> None:
    store = InMemoryStore()
    report = run_slice(_instances(), _make_run_one(store, escalate_ids={"b-2"}), lambda i, o: True)
    assert report.n == 2
    assert report.crashed == 0
    assert report.draft_pr == 1
    assert report.escalated == 1
    for r in report.results:
        assert r.status in {"draft_pr", "escalated"}
    # A complete audit trail exists for every run.
    assert len(store.events) > 0


def test_runner_catches_a_crash_without_aborting_slice() -> None:
    def run_one(instance: Instance) -> RunOutcome:
        if instance.instance_id == "a-1":
            raise RuntimeError("boom")
        return RunOutcome(run_id="x", status="draft_pr", confidence=0.8)

    report = run_slice(_instances(), run_one, lambda i, o: True)
    assert report.n == 2
    assert report.crashed == 1
    crashed = next(r for r in report.results if r.status == "crashed")
    assert crashed.error is not None


def test_swebench_loader_and_mapping(tmp_path: Path) -> None:
    path = tmp_path / "slice.jsonl"
    path.write_text(
        json.dumps(
            {
                "instance_id": "octo__a-1",
                "repo": "octo/a",
                "base_commit": "deadbeef",
                "problem_statement": "Title line\n\nDetails here.",
                "fail_to_pass": ["tests/test_x.py::test_y"],
            }
        )
        + "\n"
    )
    instances = load_instances(path)
    assert len(instances) == 1
    report = to_bug_report(instances[0])
    assert report.repo == "octo/a"
    assert report.base_ref == "deadbeef"
    assert report.source == "swebench"
    assert report.title == "Title line"
