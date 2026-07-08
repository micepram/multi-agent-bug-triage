"""Draft pull-request handoff (spec Section 10; safety rail #2).

DRAFT PRs ONLY, human-gated. Implementations must never merge, write to a default
branch, force-push, close issues, or change repo settings/permissions. The
publisher's whole job is to package a candidate patch into a *draft* PR for a
human reviewer.

The dev/test publisher builds the draft-PR object without pushing anything, which
is exactly what the Phase 2 acceptance check exercises.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from pydantic import BaseModel

from app.agents.types import BugReport, Candidate


class DraftPR(BaseModel):
    repo: str
    title: str
    body: str
    head_branch: str
    base_branch: str
    diff: str
    draft: bool = True
    pushed: bool = False


@runtime_checkable
class PullRequestPublisher(Protocol):
    def open_draft(self, report: BugReport, patch: Candidate) -> DraftPR:
        """Package a candidate into a draft PR. Never merges or pushes to default."""
        ...


class LocalDraftPRPublisher:
    """Builds the draft-PR object locally without pushing (dev + Phase 2 accept)."""

    def open_draft(self, report: BugReport, patch: Candidate) -> DraftPR:
        source = report.source_ref or "report"
        head = f"triage/fix-{source}"
        title = f"Draft fix: {report.title}"
        body = (
            f"Automated draft fix for {report.repo} ({report.source}:{source}).\n\n"
            f"Strategy: {patch.kind}. Files touched: {patch.files_touched}, "
            f"lines: {patch.lines_touched}.\n\n"
            "This is a DRAFT for human review. Do not merge without verification."
        )
        return DraftPR(
            repo=report.repo,
            title=title,
            body=body,
            head_branch=head,
            base_branch=report.base_ref,
            diff=patch.diff,
            draft=True,
            pushed=False,
        )
