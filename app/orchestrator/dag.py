"""The agent DAG as an explicit, audited state machine (spec Sections 3, 5).

Runs Triage -> Reproduction -> (Bisection?) -> Localize -> Fix -> Validation ->
confidence gate -> draft PR or escalate. Every node writes an append-only
``agent_events`` row on entry and exit. Any hard-gate failure or a composite
below threshold routes to ESCALATE with a self-contained report.

Untrusted code runs only through the injected ``Sandbox``; the orchestrator
itself never executes repo commands directly.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import cast

from pydantic import BaseModel

from app.agents.protocols import (
    BisectionAgent,
    FixAgent,
    LocalizeAgent,
    ReproductionAgent,
    TriageAgent,
    ValidationAgent,
)
from app.agents.types import (
    BisectionOutcome,
    BugReport,
    Candidate,
    Localization,
    Repro,
    TriageRecord,
    ValidationOutcome,
)
from app.orchestrator.confidence import GateConfig, GateDecision, Signals, evaluate
from app.orchestrator.escalation import build_escalation_report
from app.orchestrator.store import AgentEvent, Store
from app.sandbox.interface import Sandbox
from app.vcs.publisher import DraftPR, PullRequestPublisher


@dataclass
class Agents:
    triage: TriageAgent
    reproduction: ReproductionAgent
    bisection: BisectionAgent
    localize: LocalizeAgent
    fix: FixAgent
    validation: ValidationAgent


class RunOutcome(BaseModel):
    run_id: str
    status: str  # 'draft_pr' | 'escalated'
    confidence: float | None = None
    escalation_reason: str | None = None
    selected_patch: Candidate | None = None
    draft_pr: DraftPR | None = None
    report: dict[str, object] | None = None


class Orchestrator:
    """Hand-rolled DAG state machine with persisted state and an escalation exit."""

    def __init__(
        self,
        *,
        agents: Agents,
        sandbox_factory: Callable[[], Sandbox],
        store: Store,
        publisher: PullRequestPublisher,
        threshold: float,
        gate_config: GateConfig,
        max_repro_attempts: int = 3,
    ) -> None:
        self._agents = agents
        self._sandbox_factory = sandbox_factory
        self._store = store
        self._publisher = publisher
        self._threshold = threshold
        self._gate_config = gate_config
        self._max_repro_attempts = max_repro_attempts

    def run(self, report: BugReport) -> RunOutcome:
        run_id = self._store.create_run(report)

        # --- Triage --------------------------------------------------------
        triage = cast(
            TriageRecord,
            self._node(run_id, "triage", lambda: self._agents.triage.triage(report)),
        )
        self._store.set_triage(run_id, triage.severity.value, triage.component)
        if triage.is_duplicate:
            return self._escalate(
                run_id,
                "likely_duplicate",
                build_escalation_report(report=report, reason="likely_duplicate", triage=triage),
            )

        sandbox = self._sandbox_factory()
        try:
            sandbox.prepare(report.repo, report.base_ref)

            # --- Reproduction (retry synthesis up to N attempts) -----------
            repro = self._reproduce(run_id, report, sandbox)
            if repro is None or not repro.reproduced:
                return self._escalate(
                    run_id,
                    "no_repro",
                    build_escalation_report(
                        report=report,
                        reason="no_repro",
                        triage=triage,
                        repro=repro,
                        repro_failure="repro did not reproduce after retries",
                    ),
                )

            # --- Regression gate + Bisection (stubbed inconclusive in P2) ---
            bisection = self._bisection(run_id, report, repro, sandbox)
            self._store.save_bisection(run_id, bisection)

            # --- Localize --------------------------------------------------
            localization = cast(
                Localization,
                self._node(
                    run_id,
                    "localize",
                    lambda: self._agents.localize.localize(report, repro, bisection, sandbox),
                ),
            )

            # --- Fix (k candidates -> suite -> selection) ------------------
            candidates = cast(
                list[Candidate],
                self._node(
                    run_id,
                    "fix",
                    lambda: self._agents.fix.generate_candidates(
                        report, repro, localization, bisection, sandbox
                    ),
                ),
            )
            selected = self._agents.fix.select(candidates, report)
            for cand in candidates:
                cand.selected = cand is selected
                self._store.save_patch(run_id, cand)
            if selected is None:
                return self._escalate(
                    run_id,
                    "no_candidate",
                    build_escalation_report(
                        report=report,
                        reason="no_candidate",
                        triage=triage,
                        repro=repro,
                        bisection=bisection,
                        candidates=candidates,
                    ),
                )

            # --- Validation ------------------------------------------------
            validation = cast(
                ValidationOutcome,
                self._node(
                    run_id,
                    "validation",
                    lambda: self._agents.validation.validate(report, repro, selected, sandbox),
                ),
            )

            # --- Confidence gate -------------------------------------------
            decision = self._gate(repro, bisection, validation, selected)
            if decision.escalate:
                reason = self._gate_reason(decision)
                return self._escalate(
                    run_id,
                    reason,
                    build_escalation_report(
                        report=report,
                        reason=reason,
                        triage=triage,
                        repro=repro,
                        bisection=bisection,
                        candidates=candidates,
                        gate=decision,
                    ),
                    confidence=decision.composite,
                )

            # --- Draft PR --------------------------------------------------
            draft = self._publisher.open_draft(report, selected)
            self._store.finish_run(run_id, "draft_pr", decision.composite)
            return RunOutcome(
                run_id=run_id,
                status="draft_pr",
                confidence=decision.composite,
                selected_patch=selected,
                draft_pr=draft,
            )
        finally:
            sandbox.destroy()

    # -- node helpers -------------------------------------------------------

    def _reproduce(self, run_id: str, report: BugReport, sandbox: Sandbox) -> Repro | None:
        self._store.record_event(AgentEvent(run_id=run_id, agent="reproduction", phase="enter"))
        started = time.monotonic()
        repro: Repro | None = None
        for _attempt in range(self._max_repro_attempts):
            repro = self._agents.reproduction.reproduce(report, sandbox)
            if repro.reproduced:
                break
        self._store.record_event(
            AgentEvent(
                run_id=run_id,
                agent="reproduction",
                phase="exit",
                outputs=(repro.model_dump() if repro else {}),
                signals={"reproduce_rate": repro.reproduce_rate if repro else 0.0},
                duration_ms=int((time.monotonic() - started) * 1000),
            )
        )
        return repro

    def _bisection(
        self, run_id: str, report: BugReport, repro: Repro, sandbox: Sandbox
    ) -> BisectionOutcome:
        # The bisection agent self-gates on determinism and regression-ness and
        # returns inconclusive when bisection does not apply, so the orchestrator
        # always invokes it and records the result.
        self._store.record_event(AgentEvent(run_id=run_id, agent="bisection", phase="enter"))
        outcome = self._agents.bisection.bisect(report, repro, sandbox)
        self._store.record_event(
            AgentEvent(
                run_id=run_id,
                agent="bisection",
                phase="exit",
                outputs=outcome.model_dump(),
                signals={"bisection_certainty": outcome.certainty},
            )
        )
        return outcome

    def _gate(
        self,
        repro: Repro,
        bisection: BisectionOutcome,
        validation: ValidationOutcome,
        candidate: Candidate,
    ) -> GateDecision:
        signals = Signals(
            reproduce_rate=repro.reproduce_rate,
            bisection_certainty=bisection.certainty,
            repro_after_patch_passes=validation.repro_after_patch_passes,
            regression_free=validation.regression_free,
            static_analysis_clean=validation.static_analysis_clean,
            reviewer_verdict=validation.reviewer_verdict,
            files_touched=candidate.files_touched,
            lines_touched=candidate.lines_touched,
        )
        return evaluate(signals, threshold=self._threshold, config=self._gate_config)

    @staticmethod
    def _gate_reason(decision: GateDecision) -> str:
        if decision.hard_gate_failures:
            return "gate_failed:" + ",".join(decision.hard_gate_failures)
        return "gate_failed:low_confidence"

    def _node(self, run_id: str, agent: str, fn: Callable[[], object]) -> object:
        self._store.record_event(AgentEvent(run_id=run_id, agent=agent, phase="enter"))
        started = time.monotonic()
        result = fn()
        outputs = result.model_dump() if isinstance(result, BaseModel) else {}
        self._store.record_event(
            AgentEvent(
                run_id=run_id,
                agent=agent,
                phase="exit",
                outputs=outputs,
                duration_ms=int((time.monotonic() - started) * 1000),
            )
        )
        return result

    def _escalate(
        self,
        run_id: str,
        reason: str,
        report: dict[str, object],
        *,
        confidence: float | None = None,
    ) -> RunOutcome:
        self._store.save_escalation(run_id, reason, report)
        self._store.finish_run(run_id, "escalated", confidence)
        return RunOutcome(
            run_id=run_id,
            status="escalated",
            confidence=confidence,
            escalation_reason=reason,
            report=report,
        )
