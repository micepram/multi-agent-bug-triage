"""Fix agents (spec Phases 2 and 3).

- ``RevertFixAgent`` (Phase 2): the SapFix full-revert trivial strategy — revert
  the suspected introducing commit and use its diff as the candidate patch.
- ``LLMFixAgent`` (Phase 3): generate k candidate patches from the failure
  context, run the suite on each, then a Selection step ranks survivors against
  the natural-language issue description.

Both run git / untrusted generated patches; all execution goes through the
Sandbox.
"""

from __future__ import annotations

import re

from app.agents.types import (
    BisectionOutcome,
    BugReport,
    Candidate,
    Localization,
    Repro,
)
from app.providers.base import Message
from app.providers.client import LLMClient
from app.sandbox.interface import Sandbox
from app.sandbox.profiles import profile_for

_GIT_TIMEOUT = 120
_SUITE_TIMEOUT = 600
_PATCH_PATH = "candidate.patch"


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


_FIX_PROMPT = (
    "You are a software-repair assistant. Produce a MINIMAL unified diff that "
    "fixes the reported bug. Touch as few files and lines as possible. Respond "
    "ONLY with the diff, in ```diff fences."
)

_SELECT_PROMPT = (
    "You are a patch reviewer. Given the issue description and several candidate "
    "diffs, choose the one that best addresses the reported symptom (not merely "
    "one that makes a test pass). Respond ONLY with the integer index of the best "
    "candidate."
)


class LLMFixAgent:
    """Generate k candidate patches, run the suite on each, then select the best.

    Runs untrusted generated patches; all application and test execution goes
    through the Sandbox.
    """

    def __init__(
        self,
        llm: LLMClient,
        selection_llm: LLMClient,
        *,
        k: int = 4,
        blast_radius_lines_cap: int = 400,
        test_cmd: list[str] | None = None,
    ) -> None:
        self._llm = llm
        self._selection_llm = selection_llm
        self._k = k
        self._cap = blast_radius_lines_cap
        self._test_cmd_override = test_cmd

    def generate_candidates(
        self,
        report: BugReport,
        repro: Repro,
        localization: Localization,
        bisection: BisectionOutcome,
        sandbox: Sandbox,
    ) -> list[Candidate]:
        test_cmd = self._test_cmd_override or profile_for(repro.language).test_cmd
        candidates: list[Candidate] = []
        for _ in range(self._k):
            diff = _extract_diff(
                self._llm.complete(self._fix_messages(report, repro, localization)).text
            )
            if not diff:
                continue
            files, lines = _blast_radius(diff)
            if files == 0 or lines == 0 or lines > self._cap:
                continue  # prefer minimal, applicable diffs; cap the blast radius
            suite_passed = self._run_suite_with_patch(diff, sandbox, test_cmd)
            candidates.append(
                Candidate(
                    kind="llm_candidate",
                    diff=diff,
                    files_touched=files,
                    lines_touched=lines,
                    suite_passed=suite_passed,
                )
            )
        return candidates

    def select(self, candidates: list[Candidate], report: BugReport) -> Candidate | None:
        """Rank against the issue description; prefer suite-passing survivors."""
        if not candidates:
            return None
        survivors = [c for c in candidates if c.suite_passed] or candidates
        chosen = (
            survivors[0]
            if len(survivors) == 1
            else survivors[self._select_index(survivors, report)]
        )
        chosen.kind = "selected"
        chosen.selected = True
        return chosen

    def _run_suite_with_patch(self, diff: str, sandbox: Sandbox, test_cmd: list[str]) -> bool:
        sandbox.write_file(_PATCH_PATH, diff.encode("utf-8"))
        applied = sandbox.run(["git", "apply", _PATCH_PATH], timeout=_GIT_TIMEOUT)
        if applied.exit_code != 0:
            sandbox.run(["git", "checkout", "--", "."], timeout=_GIT_TIMEOUT)
            return False
        suite = sandbox.run(test_cmd, timeout=_SUITE_TIMEOUT)
        # Reset the tree so each candidate is evaluated against a clean checkout.
        sandbox.run(["git", "checkout", "--", "."], timeout=_GIT_TIMEOUT)
        return suite.exit_code == 0

    def _select_index(self, survivors: list[Candidate], report: BugReport) -> int:
        listing = "\n\n".join(f"[{i}]\n{c.diff}" for i, c in enumerate(survivors))
        messages = [
            Message(role="system", content=_SELECT_PROMPT),
            Message(
                role="user",
                content=f"Issue: {report.title}\n{report.body}\n\nCandidates:\n{listing}",
            ),
        ]
        text = self._selection_llm.complete(messages).text
        match = re.search(r"\d+", text)
        if match is None:
            return _smallest_blast_index(survivors)
        idx = int(match.group())
        return idx if 0 <= idx < len(survivors) else _smallest_blast_index(survivors)

    def _fix_messages(
        self, report: BugReport, repro: Repro, localization: Localization
    ) -> list[Message]:
        files = "\n".join(f"- {loc.file}" for loc in localization.locations)
        return [
            Message(role="system", content=_FIX_PROMPT),
            Message(
                role="user",
                content=(
                    f"Issue: {report.title}\n{report.body}\n\n"
                    f"Stack trace:\n{repro.stack_trace}\n\n"
                    f"Suspected files:\n{files}"
                ),
            ),
        ]


def _extract_diff(text: str) -> str:
    """Pull a unified diff out of LLM output, stripping ```diff fences."""
    fence = re.search(r"```(?:diff)?\n(.*?)```", text, re.DOTALL)
    candidate = fence.group(1) if fence else text
    return candidate if "diff --git" in candidate or candidate.lstrip().startswith("---") else ""


def _smallest_blast_index(survivors: list[Candidate]) -> int:
    return min(range(len(survivors)), key=lambda i: survivors[i].lines_touched)


def _blast_radius(diff: str) -> tuple[int, int]:
    """Count files and changed lines in a unified diff (hunk +/- lines only)."""
    files = sum(1 for line in diff.splitlines() if line.startswith("diff --git"))
    lines = sum(
        1
        for line in diff.splitlines()
        if (line.startswith(("+", "-")) and not line.startswith(("+++", "---")))
    )
    return files, lines
