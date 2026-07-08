"""Domain contracts that flow between agents (spec Sections 3, 5).

These are typed, provider-agnostic value objects. Agents consume and produce
them; the orchestrator wires them into a DAG. Keeping them here (not inside any
single agent) makes each agent independently testable against the contract.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class Severity(StrEnum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class Language(StrEnum):
    PYTHON = "python"
    JAVA = "java"


class BugReport(BaseModel):
    """The pipeline input: a bug report against a repo at a base ref."""

    repo: str  # owner/name
    base_ref: str  # commit or tag
    source: str  # 'github_issue' | 'manual' | 'swebench'
    source_ref: str | None = None  # issue number or instance id
    title: str
    body: str = ""
    labels: list[str] = Field(default_factory=list)
    # Optional last-good ref cited by the report; enables the regression gate.
    last_good_ref: str | None = None


class DuplicateMatch(BaseModel):
    issue_ref: str
    score: float


class TriageRecord(BaseModel):
    severity: Severity
    component: str
    is_duplicate: bool = False
    duplicate_confidence: float = 0.0
    duplicate_of: str | None = None
    top_matches: list[DuplicateMatch] = Field(default_factory=list)


class Repro(BaseModel):
    """A synthesized reproduction and its determinism evidence."""

    script: str
    language: Language
    reproduced: bool
    reproduce_rate: float = Field(ge=0.0, le=1.0)
    n_runs: int = Field(ge=0)
    stack_trace: str = ""
    exit_signal: int | None = None

    @property
    def deterministic(self) -> bool:
        """A repro is deterministic only if every run reproduced it."""
        return self.reproduced and self.reproduce_rate >= 1.0


class BisectionOutcome(BaseModel):
    good_ref: str | None = None
    bad_ref: str | None = None
    introducing_commit: str | None = None
    # Files touched by the introducing commit; folded into localization as a prior.
    introducing_files: list[str] = Field(default_factory=list)
    skip_ratio: float | None = None
    conclusive: bool = False

    @property
    def certainty(self) -> float | None:
        """1 - skip_ratio when conclusive; None otherwise (feeds confidence)."""
        if not self.conclusive or self.skip_ratio is None:
            return None
        return max(0.0, 1.0 - self.skip_ratio)


class FaultLocation(BaseModel):
    file: str
    symbol: str | None = None
    score: float = 0.0


class Localization(BaseModel):
    locations: list[FaultLocation] = Field(default_factory=list)


class Candidate(BaseModel):
    """A candidate patch and its per-candidate execution evidence."""

    kind: str  # 'revert' | 'llm_candidate' | 'selected'
    diff: str
    files_touched: int = Field(ge=0)
    lines_touched: int = Field(ge=0)
    suite_passed: bool | None = None
    repro_passed: bool | None = None
    regression_free: bool | None = None
    selected: bool = False


class ValidationOutcome(BaseModel):
    repro_after_patch_passes: bool
    regression_free: bool
    static_analysis_clean: float = Field(ge=0.0, le=1.0)
    reviewer_verdict: float = Field(ge=0.0, le=1.0)
