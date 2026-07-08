"""End-to-end DAG tests with fake agents (Phase 2 skeleton).

Proves the whole pipeline runs and routes correctly before any LLM is involved:
draft-PR on a clean pass, and escalation on duplicate, no-repro, and hard-gate
failure. Also asserts the audit trail is written append-only for every node.
"""

from __future__ import annotations

from app.agents.types import (
    BisectionOutcome,
    BugReport,
    Candidate,
    DuplicateMatch,
    Language,
    Localization,
    Repro,
    Severity,
    TriageRecord,
    ValidationOutcome,
)
from app.orchestrator.confidence import GateConfig
from app.orchestrator.dag import Agents, Orchestrator
from app.orchestrator.store import InMemoryStore
from app.sandbox.interface import RunResult
from app.vcs.publisher import LocalDraftPRPublisher

REPORT = BugReport(
    repo="octo/repo",
    base_ref="v1.2.0",
    source="swebench",
    source_ref="octo__repo-42",
    title="Crash on empty input",
    body="Passing an empty list raises IndexError.",
)


class FakeSandbox:
    def __init__(self) -> None:
        self.prepared = False
        self.destroyed = False

    def prepare(self, repo: str, ref: str):  # type: ignore[no-untyped-def]
        self.prepared = True
        from app.sandbox.interface import Workspace

        return Workspace(repo=repo, ref=ref, volume="vol")

    def run(self, cmd, timeout, *, network=False) -> RunResult:  # type: ignore[no-untyped-def]
        return RunResult(stdout="", stderr="", exit_code=0, duration=0.1, timed_out=False)

    def read_file(self, path: str) -> bytes:
        return b""

    def write_file(self, path: str, data: bytes) -> None:
        return None

    def destroy(self) -> None:
        self.destroyed = True


class FakeTriage:
    def __init__(self, record: TriageRecord) -> None:
        self._record = record

    def triage(self, report: BugReport) -> TriageRecord:
        return self._record


class FakeReproduction:
    def __init__(self, repro: Repro) -> None:
        self._repro = repro
        self.calls = 0

    def reproduce(self, report: BugReport, sandbox: object) -> Repro:
        self.calls += 1
        return self._repro


class StubBisection:
    """Phase 2: always inconclusive so the SBFL/localize fallback is exercised."""

    def bisect(self, report: BugReport, repro: Repro, sandbox: object) -> BisectionOutcome:
        return BisectionOutcome(conclusive=False)


class FakeLocalize:
    def localize(self, report, repro, bisection, sandbox) -> Localization:  # type: ignore[no-untyped-def]
        return Localization(locations=[])


class FakeFix:
    def __init__(self, candidates: list[Candidate]) -> None:
        self._candidates = candidates

    def generate_candidates(self, report, repro, localization, bisection, sandbox):  # type: ignore[no-untyped-def]
        return self._candidates

    def select(self, candidates: list[Candidate], report: BugReport) -> Candidate | None:
        return candidates[0] if candidates else None


class FakeValidation:
    def __init__(self, outcome: ValidationOutcome) -> None:
        self._outcome = outcome

    def validate(self, report, repro, candidate, sandbox) -> ValidationOutcome:  # type: ignore[no-untyped-def]
        return self._outcome


def _repro(reproduced: bool = True, rate: float = 1.0) -> Repro:
    return Repro(
        script="assert f([]) is not None",
        language=Language.PYTHON,
        reproduced=reproduced,
        reproduce_rate=rate,
        n_runs=5,
        stack_trace="IndexError",
    )


def _candidate() -> Candidate:
    return Candidate(kind="revert", diff="--- a\n+++ b\n", files_touched=1, lines_touched=4)


def _clean_validation() -> ValidationOutcome:
    return ValidationOutcome(
        repro_after_patch_passes=True,
        regression_free=True,
        static_analysis_clean=1.0,
        reviewer_verdict=1.0,
    )


def _orchestrator(
    *,
    triage: TriageRecord,
    repro: Repro,
    candidates: list[Candidate],
    validation: ValidationOutcome,
    store: InMemoryStore,
    reproduction: FakeReproduction | None = None,
) -> Orchestrator:
    agents = Agents(
        triage=FakeTriage(triage),
        reproduction=reproduction or FakeReproduction(repro),
        bisection=StubBisection(),
        localize=FakeLocalize(),
        fix=FakeFix(candidates),
        validation=FakeValidation(validation),
    )
    return Orchestrator(
        agents=agents,
        sandbox_factory=FakeSandbox,
        store=store,
        publisher=LocalDraftPRPublisher(),
        threshold=0.7,
        gate_config=GateConfig(),
    )


def _triage_ok() -> TriageRecord:
    return TriageRecord(severity=Severity.HIGH, component="runtime")


def test_clean_run_produces_a_draft_pr() -> None:
    store = InMemoryStore()
    orch = _orchestrator(
        triage=_triage_ok(),
        repro=_repro(),
        candidates=[_candidate()],
        validation=_clean_validation(),
        store=store,
    )
    outcome = orch.run(REPORT)

    assert outcome.status == "draft_pr"
    assert outcome.draft_pr is not None
    assert outcome.draft_pr.draft is True
    assert outcome.draft_pr.pushed is False
    assert outcome.confidence is not None and outcome.confidence >= 0.7
    assert store.runs[outcome.run_id]["status"] == "draft_pr"
    assert len(store.patches) == 1


def test_high_confidence_duplicate_short_circuits_to_escalation() -> None:
    store = InMemoryStore()
    dup = TriageRecord(
        severity=Severity.LOW,
        component="runtime",
        is_duplicate=True,
        duplicate_confidence=0.95,
        duplicate_of="#7",
        top_matches=[DuplicateMatch(issue_ref="#7", score=0.95)],
    )
    reproduction = FakeReproduction(_repro())
    orch = _orchestrator(
        triage=dup,
        repro=_repro(),
        candidates=[_candidate()],
        validation=_clean_validation(),
        store=store,
        reproduction=reproduction,
    )
    outcome = orch.run(REPORT)

    assert outcome.status == "escalated"
    assert outcome.escalation_reason == "likely_duplicate"
    # Reproduction must not run for a short-circuited duplicate.
    assert reproduction.calls == 0


def test_could_not_reproduce_escalates_after_retries() -> None:
    store = InMemoryStore()
    reproduction = FakeReproduction(_repro(reproduced=False, rate=0.0))
    orch = _orchestrator(
        triage=_triage_ok(),
        repro=_repro(),
        candidates=[_candidate()],
        validation=_clean_validation(),
        store=store,
        reproduction=reproduction,
    )
    outcome = orch.run(REPORT)

    assert outcome.status == "escalated"
    assert outcome.escalation_reason == "no_repro"
    assert reproduction.calls == 3  # retried up to 3 attempts


def test_hard_gate_failure_escalates_with_gate_reason() -> None:
    store = InMemoryStore()
    regressed = _clean_validation().model_copy(update={"regression_free": False})
    orch = _orchestrator(
        triage=_triage_ok(),
        repro=_repro(),
        candidates=[_candidate()],
        validation=regressed,
        store=store,
    )
    outcome = orch.run(REPORT)

    assert outcome.status == "escalated"
    assert outcome.escalation_reason is not None
    assert outcome.escalation_reason.startswith("gate_failed:")
    assert "regression" in outcome.escalation_reason


def test_no_candidate_escalates() -> None:
    store = InMemoryStore()
    orch = _orchestrator(
        triage=_triage_ok(),
        repro=_repro(),
        candidates=[],
        validation=_clean_validation(),
        store=store,
    )
    outcome = orch.run(REPORT)

    assert outcome.status == "escalated"
    assert outcome.escalation_reason == "no_candidate"


def test_every_node_writes_enter_and_exit_events() -> None:
    store = InMemoryStore()
    orch = _orchestrator(
        triage=_triage_ok(),
        repro=_repro(),
        candidates=[_candidate()],
        validation=_clean_validation(),
        store=store,
    )
    outcome = orch.run(REPORT)

    events = store.events_for(outcome.run_id)
    phases = {(e.agent, e.phase) for e in events}
    for agent in ("triage", "reproduction", "localize", "fix", "validation"):
        assert (agent, "enter") in phases, f"missing enter for {agent}"
        assert (agent, "exit") in phases, f"missing exit for {agent}"


def test_escalation_report_is_self_contained() -> None:
    store = InMemoryStore()
    regressed = _clean_validation().model_copy(update={"regression_free": False})
    orch = _orchestrator(
        triage=_triage_ok(),
        repro=_repro(),
        candidates=[_candidate()],
        validation=regressed,
        store=store,
    )
    outcome = orch.run(REPORT)

    report = outcome.report
    assert report is not None
    # A human reading only the report must see triage, repro, and the failing gate.
    assert "triage" in report
    assert "repro" in report
    assert "gate" in report
    assert len(store.escalations) == 1
