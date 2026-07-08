"""Unit tests for the skip-aware bisect script and its output/log parsers.

The generated script encodes the load-bearing rule: a build/environment failure
exits 125 (git bisect skip), never a "bad" code. Its exit semantics are verified
by actually running it with ``sh`` using true/false stand-ins (our own script,
no repo code). The parsers extract the introducing commit and the skip ratio.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from app.agents.bisection import (
    build_bisect_script,
    parse_introducing_commit,
    skip_ratio_from_log,
)


def _run_script(tmp_path: Path, build_cmd: list[str], repro_cmd: list[str]) -> tuple[int, str]:
    log = tmp_path / "bisect_log.txt"
    script = build_bisect_script(build_cmd, repro_cmd, log_path=str(log))
    path = tmp_path / "bisect.sh"
    path.write_text(script)
    result = subprocess.run(["sh", str(path)], capture_output=True)
    logged = log.read_text().strip() if log.exists() else ""
    return result.returncode, logged


def test_build_failure_exits_skip_code_125(tmp_path: Path) -> None:
    code, logged = _run_script(tmp_path, build_cmd=["false"], repro_cmd=["true"])
    assert code == 125  # git bisect skip, never "bad"
    assert logged == "skip"


def test_bug_present_exits_bad_code_1(tmp_path: Path) -> None:
    # build succeeds, repro "fails" (bug present) -> bad
    code, logged = _run_script(tmp_path, build_cmd=["true"], repro_cmd=["false"])
    assert code == 1
    assert logged == "bad"


def test_bug_absent_exits_good_code_0(tmp_path: Path) -> None:
    # build succeeds, repro passes (bug absent) -> good
    code, logged = _run_script(tmp_path, build_cmd=["true"], repro_cmd=["true"])
    assert code == 0
    assert logged == "good"


def test_parse_introducing_commit_from_bisect_output() -> None:
    output = (
        "Bisecting: 3 revisions left to test after this (roughly 2 steps)\n"
        "abcdef1234567890abcdef1234567890abcdef12 is the first bad commit\n"
        "commit abcdef1234567890abcdef1234567890abcdef12\n"
    )
    assert parse_introducing_commit(output) == "abcdef1234567890abcdef1234567890abcdef12"


def test_parse_introducing_commit_returns_none_when_absent() -> None:
    assert parse_introducing_commit("Bisecting: 2 revisions left\n") is None
    assert parse_introducing_commit("only skipped commits left to test") is None


def test_skip_ratio_from_log() -> None:
    assert skip_ratio_from_log("good\nbad\ngood\n") == 0.0
    assert skip_ratio_from_log("good\nskip\nskip\nbad\n") == 0.5
    assert skip_ratio_from_log("") == 0.0
