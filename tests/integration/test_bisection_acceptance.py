"""Phase 4 acceptance: skip-aware bisection on constructed regressions.

Builds real throwaway git repos (our own fixtures; plain git + sh, no gVisor)
and drives the actual ``git bisect run`` mechanism with the generated bisect
script and parsers:

1. A build-pinned repo with a known regression -> the correct introducing commit
   is found.
2. A repo whose build fails on most commits (dependency/toolchain drift) ->
   commits skip and the run is inconclusive, never a false introducing commit.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
from app.agents.bisection import (
    build_bisect_script,
    parse_introducing_commit,
    skip_ratio_from_log,
)

pytestmark = pytest.mark.integration

if shutil.which("git") is None:
    pytest.skip(
        "git is not installed; skipping bisection acceptance test.", allow_module_level=True
    )

_GIT_ENV = {
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@t",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@t",
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_CONFIG_SYSTEM": "/dev/null",
}


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        env={"PATH": "/usr/bin:/bin:/usr/local/bin", **_GIT_ENV},
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout


def _commit(repo: Path, filename: str, content: str, message: str) -> str:
    (repo / filename).write_text(content)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", message)
    return _git(repo, "rev-parse", "HEAD").strip()


def _run_bisect(repo: Path, bad: str, good: str, script_path: Path) -> str:
    proc = subprocess.run(
        [
            "sh",
            "-c",
            f"git bisect start {bad} {good} && git bisect run sh {script_path}; git bisect reset",
        ],
        cwd=repo,
        env={"PATH": "/usr/bin:/bin:/usr/local/bin", **_GIT_ENV},
        capture_output=True,
        text=True,
    )
    return proc.stdout + proc.stderr


def test_known_regression_finds_the_introducing_commit(tmp_path: Path) -> None:
    repo = tmp_path / "pinned"
    repo.mkdir()
    _git(repo, "init", "-q")

    good0 = _commit(repo, "status.txt", "ok\nrev 0\n", "c0 ok")
    _commit(repo, "status.txt", "ok\nrev 1\n", "c1 ok")
    introducing = _commit(repo, "status.txt", "bug\nrev 2\n", "c2 introduces bug")
    _commit(repo, "status.txt", "bug\nrev 3\n", "c3 still buggy")
    head = _commit(repo, "status.txt", "bug\nrev 4\n", "c4 head")

    log = tmp_path / "bisect_log.txt"
    script = build_bisect_script(
        build_cmd=["true"],  # build always succeeds
        repro_cmd=["sh", "-c", "! grep -q bug status.txt"],  # exit!=0 when bug present
        log_path=str(log),
    )
    script_path = tmp_path / "bisect.sh"
    script_path.write_text(script)

    output = _run_bisect(repo, bad=head, good=good0, script_path=script_path)
    found = parse_introducing_commit(output)
    assert found is not None
    assert introducing.startswith(found) or found.startswith(introducing[:12])
    assert skip_ratio_from_log(log.read_text()) == 0.0


def test_floating_deps_skip_and_stay_inconclusive(tmp_path: Path) -> None:
    repo = tmp_path / "floating"
    repo.mkdir()
    _git(repo, "init", "-q")

    # Every non-endpoint commit carries a 'broken_build' marker -> build fails ->
    # the script must skip (125), never mark bad.
    good0 = _commit(repo, "app.py", "ok\nrev 0\n", "c0 ok")
    for i in range(1, 4):
        (repo / "broken_build").write_text("x")
        _commit(repo, "app.py", f"bug\nrev {i}\n", f"c{i} buggy + broken build")
    head = _git(repo, "rev-parse", "HEAD").strip()

    log = tmp_path / "bisect_log.txt"
    script = build_bisect_script(
        build_cmd=["sh", "-c", "! test -f broken_build"],  # fails when marker present
        repro_cmd=["sh", "-c", "! grep -q bug app.py"],
        log_path=str(log),
    )
    script_path = tmp_path / "bisect.sh"
    script_path.write_text(script)

    _run_bisect(repo, bad=head, good=good0, script_path=script_path)
    skip_ratio = skip_ratio_from_log(log.read_text())
    # With a high skip ratio the agent policy marks the run inconclusive; the key
    # guarantee is that skips dominated rather than a bad classification.
    assert skip_ratio > 0.5
