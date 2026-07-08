"""Phase 2 rule-based Triage agent (spec Section 5).

Deterministic, offline severity and component classification plus a pluggable
duplicate finder. The duplicate finder is a seam: Phase 2 defaults to "no
duplicates"; Phase 3 injects a pgvector semantic-search implementation behind the
same ``DuplicateFinder`` protocol. High-confidence duplicates short-circuit to an
escalation note rather than proceeding down the pipeline.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from pydantic import BaseModel

from app.agents.types import BugReport, DuplicateMatch, Severity, TriageRecord

# Ordered most- to least-severe: first matching bucket wins.
_SEVERITY_KEYWORDS: list[tuple[Severity, tuple[str, ...]]] = [
    (Severity.CRITICAL, ("crash", "segfault", "data loss", "corruption", "security", "cve")),
    (Severity.HIGH, ("exception", "error", "traceback", "fails", "broken", "regression")),
    (Severity.LOW, ("typo", "cosmetic", "docs", "documentation")),
]


class Taxonomy(BaseModel):
    """Per-repo component taxonomy (spec Section 5). Configured, not free-form."""

    components: dict[str, list[str]]
    default: str = "unknown"

    def classify(self, text: str) -> str:
        lowered = text.lower()
        for component, keywords in self.components.items():
            if any(kw in lowered for kw in keywords):
                return component
        return self.default


@runtime_checkable
class DuplicateFinder(Protocol):
    def find(self, report: BugReport) -> list[DuplicateMatch]:
        """Return candidate duplicate issues, most similar first."""
        ...


class NullDuplicateFinder:
    """Default finder: never reports a duplicate (Phase 2)."""

    def find(self, report: BugReport) -> list[DuplicateMatch]:
        return []


class RuleBasedTriageAgent:
    """Keyword severity + taxonomy component mapping + duplicate short-circuit."""

    def __init__(
        self,
        taxonomy: Taxonomy,
        duplicate_finder: DuplicateFinder | None = None,
        *,
        duplicate_threshold: float = 0.9,
    ) -> None:
        self._taxonomy = taxonomy
        self.duplicate_finder: DuplicateFinder = duplicate_finder or NullDuplicateFinder()
        self._duplicate_threshold = duplicate_threshold

    def triage(self, report: BugReport) -> TriageRecord:
        text = f"{report.title}\n{report.body}\n{' '.join(report.labels)}"
        severity = self._severity(text)
        component = self._taxonomy.classify(text)

        matches = self.duplicate_finder.find(report)
        top = matches[0] if matches else None
        is_duplicate = top is not None and top.score >= self._duplicate_threshold

        return TriageRecord(
            severity=severity,
            component=component,
            is_duplicate=is_duplicate,
            duplicate_confidence=top.score if top else 0.0,
            duplicate_of=top.issue_ref if (is_duplicate and top is not None) else None,
            top_matches=matches,
        )

    @staticmethod
    def _severity(text: str) -> Severity:
        lowered = text.lower()
        for severity, keywords in _SEVERITY_KEYWORDS:
            if any(kw in lowered for kw in keywords):
                return severity
        return Severity.MEDIUM
