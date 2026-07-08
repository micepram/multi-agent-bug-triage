"""Unit tests for language detection and execution profiles (spec Phase 5).

Repo detection picks a Python or JVM profile; the profile supplies the language-
specific build/test/lint/repro commands and sandbox image. The orchestrator and
agents stay language-agnostic by going through the profile.
"""

from __future__ import annotations

from app.agents.types import Language
from app.sandbox.profiles import detect_language, profile_for


def test_detects_python_from_markers() -> None:
    assert detect_language(["README.md", "pyproject.toml", "src/x.py"]) is Language.PYTHON
    assert detect_language(["setup.py"]) is Language.PYTHON
    assert detect_language(["requirements.txt"]) is Language.PYTHON


def test_detects_java_from_maven_or_gradle() -> None:
    assert detect_language(["pom.xml", "src/Main.java"]) is Language.JAVA
    assert detect_language(["build.gradle"]) is Language.JAVA
    assert detect_language(["build.gradle.kts", "settings.gradle"]) is Language.JAVA


def test_java_markers_win_when_both_present() -> None:
    # A polyglot repo with a JVM build is treated as Java for execution.
    assert detect_language(["pom.xml", "pyproject.toml"]) is Language.JAVA


def test_unknown_repo_returns_none() -> None:
    assert detect_language(["README.md", "LICENSE"]) is None


def test_python_profile_commands() -> None:
    profile = profile_for(Language.PYTHON)
    assert profile.language is Language.PYTHON
    assert "python" in profile.image
    assert profile.repro_filename.endswith(".py")
    assert profile.repro_cmd(profile.repro_filename) == ["python", profile.repro_filename]
    assert "pytest" in " ".join(profile.test_cmd)
    assert "ruff" in " ".join(profile.lint_cmd)


def test_java_profile_commands_adapt_to_maven_or_gradle() -> None:
    profile = profile_for(Language.JAVA)
    assert profile.language is Language.JAVA
    assert profile.repro_filename.endswith(".java")
    # Build/test must handle both Maven (pom.xml) and Gradle at runtime.
    build = " ".join(profile.build_cmd)
    test = " ".join(profile.test_cmd)
    assert "pom.xml" in build and "mvn" in build and "gradle" in build
    assert "mvn" in test and "gradle" in test


def test_java_repro_runs_the_placed_test_class() -> None:
    profile = profile_for(Language.JAVA)
    cmd = " ".join(profile.repro_cmd("ReproTest.java"))
    assert "ReproTest" in cmd
    assert "mvn" in cmd and "gradle" in cmd
