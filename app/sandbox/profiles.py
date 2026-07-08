"""Per-language execution profiles and repo detection (spec Phase 5).

A profile supplies the language-specific sandbox image and the build, test, lint,
and repro commands. Reproduction, Validation, Fix, and Bisection go through a
profile so the orchestrator and the rest of the pipeline stay language-agnostic.

The JVM profile's commands detect Maven (``pom.xml``) vs. Gradle at runtime in
the sandbox, so a single Java profile covers both build systems without threading
the build system through the agents. Both profiles surface a build failure
distinctly from a genuine test failure (the bisect script maps build failure to
the skip code), which is what the skip-aware bisection depends on.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Protocol, runtime_checkable

from app.agents.types import Language
from app.sandbox.interface import Sandbox

# Markers that identify the build system / language of a repository.
_JAVA_MARKERS = frozenset(
    {"pom.xml", "build.gradle", "build.gradle.kts", "settings.gradle", "settings.gradle.kts"}
)
_PYTHON_MARKERS = frozenset({"pyproject.toml", "setup.py", "setup.cfg", "requirements.txt"})


@dataclass(frozen=True)
class ExecutionProfile:
    """Language-specific sandbox image and commands."""

    language: Language
    image: str
    build_cmd: list[str]
    test_cmd: list[str]
    lint_cmd: list[str]
    repro_filename: str
    # Template for running the repro; ``{path}`` and ``{name}`` are substituted.
    _repro_template: tuple[str, ...]

    def repro_cmd(self, path: str) -> list[str]:
        name = PurePosixPath(path).stem
        return [part.format(path=path, name=name) for part in self._repro_template]


_PYTHON_PROFILE = ExecutionProfile(
    language=Language.PYTHON,
    image="python:3.12-slim",
    build_cmd=["pip", "install", "-e", "."],
    test_cmd=["pytest", "-q"],
    lint_cmd=["ruff", "check", "."],
    repro_filename="repro_test.py",
    _repro_template=("python", "{path}"),
)

# Java commands branch on pom.xml vs. Gradle at runtime inside the sandbox.
_JAVA_BUILD = "if [ -f pom.xml ]; then mvn -q -B -DskipTests install; else ./gradlew assemble; fi"
_JAVA_TEST = "if [ -f pom.xml ]; then mvn -q -B test; else ./gradlew test; fi"
_JAVA_REPRO = (
    "if [ -f pom.xml ]; then mvn -q -B -Dtest={name} test; else ./gradlew test --tests {name}; fi"
)

_JAVA_PROFILE = ExecutionProfile(
    language=Language.JAVA,
    image="eclipse-temurin:21-jdk",
    build_cmd=["sh", "-c", _JAVA_BUILD],
    test_cmd=["sh", "-c", _JAVA_TEST],
    # SpotBugs static analysis via the build tool; no-op friendly.
    lint_cmd=["sh", "-c", "if [ -f pom.xml ]; then mvn -q -B spotbugs:check || true; fi"],
    repro_filename="ReproTest.java",
    _repro_template=("sh", "-c", _JAVA_REPRO),
)

_PROFILES: dict[Language, ExecutionProfile] = {
    Language.PYTHON: _PYTHON_PROFILE,
    Language.JAVA: _JAVA_PROFILE,
}


def detect_language(filenames: Iterable[str]) -> Language | None:
    """Detect the repo language from top-level marker files.

    A JVM build wins over Python markers in a polyglot repo, since execution is
    driven by the build system.
    """
    basenames = {PurePosixPath(name).name for name in filenames}
    if basenames & _JAVA_MARKERS:
        return Language.JAVA
    if basenames & _PYTHON_MARKERS:
        return Language.PYTHON
    return None


def profile_for(language: Language) -> ExecutionProfile:
    return _PROFILES[language]


@runtime_checkable
class LanguageDetector(Protocol):
    def detect(self, sandbox: Sandbox) -> Language | None:
        """Detect the repo's language by inspecting the prepared workspace."""
        ...


class SandboxLanguageDetector:
    """Detect language from the tracked files of the prepared workspace."""

    def detect(self, sandbox: Sandbox) -> Language | None:
        result = sandbox.run(["git", "ls-files"], timeout=60)
        return detect_language(result.stdout.splitlines())
