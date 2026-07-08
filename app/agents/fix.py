"""Phase 2 Fix agent: the revert strategy (spec Phase 2; SapFix full-revert).

The only strategy in Phase 2 is reverting a candidate commit — no LLM patch
generation. The target commit is the conclusive bisection introducing commit
when available, otherwise the most recent commit that touched the top localized
file. The revert runs in the sandbox and its diff becomes the candidate patch.

Runs git against untrusted repo history; all execution goes through the Sandbox.
"""

from __future__ import annotations

from app.agents.types import (
    BisectionOutcome,
    BugReport,
    Candidate,
    Localization,
    Repro,
)
from app.sandbox.interface import Sandbox

_GIT_TIMEOUT = 120


class RevertFixAgent:
    """Generate a single revert candidate for the suspected introducing commit."""

    def generate_candidates(
        self,
        report: BugReport,
        repro: Repro,
        localization: Localization,
        bisection: BisectionOutcome,
        sandbox: Sandbox,
    ) -> list[Candidate]:
        target = self._target_commit(bisection, localization, sandbox)
        if target is None:
            return []

        revert = sandbox.run(["git", "revert", "--no-commit", target], timeout=_GIT_TIMEOUT)
        if revert.exit_code != 0:
            return []

        diff_result = sandbox.run(["git", "diff", "--cached"], timeout=_GIT_TIMEOUT)
        diff = diff_result.stdout
        if not diff.strip():
            return []

        files, lines = _blast_radius(diff)
        return [Candidate(kind="revert", diff=diff, files_touched=files, lines_touched=lines)]

    def select(self, candidates: list[Candidate], report: BugReport) -> Candidate | None:
        """Phase 2 selection is trivial: the single revert survivor, if any."""
        return candidates[0] if candidates else None

    def _target_commit(
        self, bisection: BisectionOutcome, localization: Localization, sandbox: Sandbox
    ) -> str | None:
        if bisection.conclusive and bisection.introducing_commit:
            return bisection.introducing_commit
        if localization.locations:
            top_file = localization.locations[0].file
            result = sandbox.run(
                ["git", "log", "-n", "1", "--pretty=format:%H", "--", top_file],
                timeout=_GIT_TIMEOUT,
            )
            sha = result.stdout.strip()
            return sha or None
        return None


def _blast_radius(diff: str) -> tuple[int, int]:
    """Count files and changed lines in a unified diff (hunk +/- lines only)."""
    files = sum(1 for line in diff.splitlines() if line.startswith("diff --git"))
    lines = sum(
        1
        for line in diff.splitlines()
        if (line.startswith(("+", "-")) and not line.startswith(("+++", "---")))
    )
    return files, lines
