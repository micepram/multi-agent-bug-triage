"""Bisection agent (spec Phase 4).

Bounded, skip-aware, determinism-gated ``git bisect run`` inside the sandbox. The
generated bisect script builds and runs the repro per candidate commit and exits
with the git skip code (125) on build/environment failure — never a "bad" code —
so toolchain drift is skipped, not misreported. If too many commits skip, the
result is marked inconclusive and the pipeline falls back. A false introducing
commit is worse than none.

``StubBisectionAgent`` remains for the Phase 2 skeleton path and tests.
"""

from __future__ import annotations

import re
import shlex

from app.agents.regression import RegressionProbe, regression_gate
from app.agents.types import BisectionOutcome, BugReport, Repro
from app.sandbox.interface import Sandbox
from app.sandbox.profiles import profile_for

_BISECT_TIMEOUT = 3600
_SCRIPT_PATH = "/workspace/bisect.sh"
_LOG_PATH = "/workspace/bisect_log.txt"
_FIRST_BAD_RE = re.compile(r"([0-9a-f]{7,40}) is the first bad commit")


class StubBisectionAgent:
    """Always inconclusive (Phase 2). Never reports a false introducing commit."""

    def bisect(self, report: BugReport, repro: Repro, sandbox: Sandbox) -> BisectionOutcome:
        return BisectionOutcome(
            good_ref=report.last_good_ref,
            bad_ref=report.base_ref,
            introducing_commit=None,
            skip_ratio=None,
            conclusive=False,
        )


def build_bisect_script(
    build_cmd: list[str],
    repro_cmd: list[str],
    *,
    log_path: str = _LOG_PATH,
) -> str:
    """Generate the skip-aware bisect script.

    Build failure -> exit 125 (git bisect skip). Otherwise the repro decides:
    bug absent (exit 0) -> good (exit 0); bug present (nonzero) -> bad (exit 1).
    Each decision is appended to the log so the skip ratio can be measured.
    """
    build = shlex.join(build_cmd)
    repro = shlex.join(repro_cmd)
    log = shlex.quote(log_path)
    return (
        "#!/bin/sh\n"
        f"if ! {build}; then\n"
        f"  echo skip >> {log}\n"
        "  exit 125\n"
        "fi\n"
        f"if {repro}; then\n"
        f"  echo good >> {log}\n"
        "  exit 0\n"
        "else\n"
        f"  echo bad >> {log}\n"
        "  exit 1\n"
        "fi\n"
    )


def parse_introducing_commit(bisect_output: str) -> str | None:
    """Extract the first-bad-commit sha from ``git bisect run`` output, if any."""
    match = _FIRST_BAD_RE.search(bisect_output)
    return match.group(1) if match else None


def skip_ratio_from_log(log_text: str) -> float:
    """Fraction of bisect steps that skipped (build/env failure)."""
    decisions = [line.strip() for line in log_text.splitlines() if line.strip()]
    if not decisions:
        return 0.0
    skipped = sum(1 for d in decisions if d == "skip")
    return skipped / len(decisions)


class GitBisectionAgent:
    """Bounded, skip-aware, determinism-gated bisection via ``git bisect run``."""

    def __init__(
        self,
        *,
        max_skip_ratio: float = 0.5,
        build_cmd: list[str] | None = None,
        probe: RegressionProbe | None = None,
    ) -> None:
        self._max_skip_ratio = max_skip_ratio
        self._build_cmd_override = build_cmd
        self._probe = probe

    def bisect(self, report: BugReport, repro: Repro, sandbox: Sandbox) -> BisectionOutcome:
        # Determinism prerequisite: flaky repros never enter bisection.
        if not repro.deterministic:
            return BisectionOutcome(bad_ref=report.base_ref, conclusive=False, skip_ratio=None)

        decision = regression_gate(report, repro, sandbox, probe=self._probe)
        if not decision.is_regression or decision.good_ref is None:
            return BisectionOutcome(bad_ref=report.base_ref, conclusive=False)

        good_ref, bad_ref = decision.good_ref, decision.bad_ref
        self._install_scripts(repro, sandbox)
        output = self._run_bisect(good_ref, bad_ref, sandbox)
        introducing = parse_introducing_commit(output)
        skip_ratio = skip_ratio_from_log(self._read_log(sandbox))

        # A high skip ratio is inconclusive: never report a false introducing commit.
        if introducing is None or skip_ratio > self._max_skip_ratio:
            return BisectionOutcome(
                good_ref=good_ref,
                bad_ref=bad_ref,
                introducing_commit=None,
                skip_ratio=skip_ratio,
                conclusive=False,
            )

        return BisectionOutcome(
            good_ref=good_ref,
            bad_ref=bad_ref,
            introducing_commit=introducing,
            introducing_files=self._introducing_files(introducing, sandbox),
            skip_ratio=skip_ratio,
            conclusive=True,
        )

    def _install_scripts(self, repro: Repro, sandbox: Sandbox) -> None:
        # Build/repro commands per the repo's execution profile, so bisection
        # works for both the Python and JVM profiles.
        profile = profile_for(repro.language)
        build_cmd = self._build_cmd_override or profile.build_cmd
        repro_cmd = profile.repro_cmd(profile.repro_filename)
        script = build_bisect_script(build_cmd, repro_cmd)
        sandbox.write_file(_SCRIPT_PATH, script.encode("utf-8"))
        sandbox.write_file(profile.repro_filename, repro.script.encode("utf-8"))

    def _run_bisect(self, good_ref: str, bad_ref: str, sandbox: Sandbox) -> str:
        cmd = [
            "sh",
            "-c",
            f"git bisect start {shlex.quote(bad_ref)} {shlex.quote(good_ref)} "
            f"&& git bisect run sh {_SCRIPT_PATH}; git bisect reset",
        ]
        return sandbox.run(cmd, timeout=_BISECT_TIMEOUT).stdout

    def _read_log(self, sandbox: Sandbox) -> str:
        try:
            return sandbox.read_file(_LOG_PATH).decode("utf-8", "replace")
        except (FileNotFoundError, OSError):
            return ""

    def _introducing_files(self, sha: str, sandbox: Sandbox) -> list[str]:
        result = sandbox.run(
            ["git", "show", "--name-only", "--pretty=format:", sha], timeout=_BISECT_TIMEOUT
        )
        return [line.strip() for line in result.stdout.splitlines() if line.strip()]
