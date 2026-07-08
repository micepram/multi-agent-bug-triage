"""Phase 5 acceptance (unit tier): the real agents drive the Java profile.

Proves language-agnosticism at the agent level: with a Java repo, Reproduction
detects Java and runs mvn/gradle, and Validation runs the Java suite — the same
code paths as Python, only the profile differs. The full Multi-SWE-bench slice is
the eval tier.
"""

from __future__ import annotations

from app.agents.reproduction import ReproductionAgent
from app.agents.types import BugReport, Candidate, Language, Repro
from app.agents.validation import ValidationAgent
from app.sandbox.interface import RunResult
from app.sandbox.profiles import SandboxLanguageDetector

REPORT = BugReport(repo="apache/commons", base_ref="HEAD", source="multiswebench", title="NPE")


class RecordingSandbox:
    """Records commands; git ls-files reports the repo's marker files."""

    def __init__(self, *, files: str, exit_code: int = 0) -> None:
        self._files = files
        self._exit_code = exit_code
        self.commands: list[list[str]] = []

    def prepare(self, repo, ref):  # type: ignore[no-untyped-def]
        return None

    def run(self, cmd, timeout, *, network=False) -> RunResult:  # type: ignore[no-untyped-def]
        self.commands.append(cmd)
        if "ls-files" in cmd:
            return RunResult(
                stdout=self._files, stderr="", exit_code=0, duration=0.0, timed_out=False
            )
        return RunResult(
            stdout="", stderr="", exit_code=self._exit_code, duration=0.0, timed_out=False
        )

    def read_file(self, path):  # type: ignore[no-untyped-def]
        return b""

    def write_file(self, path, data):  # type: ignore[no-untyped-def]
        return None

    def destroy(self):  # type: ignore[no-untyped-def]
        return None


class FixedSynth:
    def synthesize(self, report: BugReport, attempt: int) -> str | None:
        return "class ReproTest {}"


def _java_repro() -> Repro:
    return Repro(
        script="class ReproTest {}",
        language=Language.JAVA,
        reproduced=True,
        reproduce_rate=1.0,
        n_runs=5,
    )


class _Reviewer:
    def review(self, report: BugReport, repro: Repro, candidate: Candidate) -> float:
        return 0.9


def test_reproduction_detects_java_and_runs_build_tool() -> None:
    sbx = RecordingSandbox(files="pom.xml\nsrc/main/java/App.java\n", exit_code=1)
    agent = ReproductionAgent(FixedSynth(), n_runs=2, detector=SandboxLanguageDetector())
    repro = agent.reproduce(REPORT, sbx)

    assert repro.language is Language.JAVA
    repro_runs = " ".join(" ".join(c) for c in sbx.commands if "ls-files" not in c)
    assert "mvn" in repro_runs or "gradle" in repro_runs


def test_validation_runs_java_suite_for_a_java_repro() -> None:
    sbx = RecordingSandbox(files="pom.xml\n", exit_code=0)
    candidate = Candidate(kind="selected", diff="d", files_touched=1, lines_touched=2)
    ValidationAgent(_Reviewer()).validate(REPORT, _java_repro(), candidate, sbx)

    all_cmds = " ".join(" ".join(c) for c in sbx.commands)
    assert "mvn" in all_cmds or "gradle" in all_cmds
    assert "pytest" not in all_cmds  # never the Python suite for a Java repro
