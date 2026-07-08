"""Unit tests for the Phase 2 rule-based Triage agent.

No LLM: deterministic severity and component classification plus a pluggable
duplicate finder. Asserts structured output and the duplicate short-circuit
signal, not model prose.
"""

from __future__ import annotations

from app.agents.triage import DuplicateFinder, RuleBasedTriageAgent, Taxonomy
from app.agents.types import BugReport, DuplicateMatch, Severity

TAXONOMY = Taxonomy(
    components={
        "build": ["build", "compile", "setup.py", "maven"],
        "api": ["endpoint", "http", "request", "route"],
        "tests": ["test", "pytest", "assert"],
        "docs": ["docs", "readme"],
    },
    default="runtime",
)


def _report(title: str, body: str = "", **kw: object) -> BugReport:
    return BugReport(repo="octo/repo", base_ref="v1", source="manual", title=title, body=body, **kw)


class NoDuplicates:
    def find(self, report: BugReport) -> list[DuplicateMatch]:
        return []


def test_crash_is_critical() -> None:
    agent = RuleBasedTriageAgent(TAXONOMY, NoDuplicates())
    record = agent.triage(_report("Segfault crash on startup"))
    assert record.severity == Severity.CRITICAL


def test_component_mapped_from_taxonomy() -> None:
    agent = RuleBasedTriageAgent(TAXONOMY, NoDuplicates())
    record = agent.triage(_report("HTTP endpoint returns 500", "the request route breaks"))
    assert record.component == "api"


def test_unmatched_component_uses_default() -> None:
    agent = RuleBasedTriageAgent(TAXONOMY, NoDuplicates())
    record = agent.triage(_report("Weird behaviour in the widget"))
    assert record.component == "runtime"


def test_high_confidence_duplicate_short_circuits() -> None:
    class OneDuplicate:
        def find(self, report: BugReport) -> list[DuplicateMatch]:
            return [DuplicateMatch(issue_ref="#7", score=0.95)]

    agent = RuleBasedTriageAgent(TAXONOMY, OneDuplicate(), duplicate_threshold=0.9)
    record = agent.triage(_report("Crash on empty input"))
    assert record.is_duplicate is True
    assert record.duplicate_of == "#7"
    assert record.duplicate_confidence == 0.95


def test_low_confidence_match_does_not_flag_duplicate() -> None:
    class WeakMatch:
        def find(self, report: BugReport) -> list[DuplicateMatch]:
            return [DuplicateMatch(issue_ref="#7", score=0.4)]

    agent = RuleBasedTriageAgent(TAXONOMY, WeakMatch(), duplicate_threshold=0.9)
    record = agent.triage(_report("Crash on empty input"))
    assert record.is_duplicate is False
    assert record.top_matches  # still reported for context


def test_default_finder_is_optional() -> None:
    # Without a duplicate finder, triage still works and reports no duplicate.
    agent = RuleBasedTriageAgent(TAXONOMY)
    record = agent.triage(_report("Crash"))
    assert record.is_duplicate is False
    assert isinstance(agent.duplicate_finder, DuplicateFinder)
