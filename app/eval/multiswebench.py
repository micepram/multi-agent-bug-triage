"""Multi-SWE-bench (Java) eval adapter (spec Sections 8, Phase 5).

Multi-SWE-bench provides human-validated Java GitHub issue instances with a
per-instance Docker environment, evaluated by running each project's built-in
test suite against post-PR behavior — which matches this system's validation
model. This adapter mirrors the SWE-bench one but tags instances as Java and
carries the per-instance image. It reaches no network.
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, Field

from app.agents.types import BugReport, Language


class MultiSWEBenchInstance(BaseModel):
    """A single Multi-SWE-bench Java instance."""

    instance_id: str
    repo: str
    base_commit: str
    problem_statement: str
    fail_to_pass: list[str] = Field(default_factory=list)
    reference_patch: str = ""
    # Per-instance Docker environment supplied by the benchmark.
    image: str = ""
    language: Language = Language.JAVA


def load_instances(path: str | Path) -> list[MultiSWEBenchInstance]:
    lines = Path(path).read_text().splitlines()
    return [
        MultiSWEBenchInstance.model_validate(json.loads(line)) for line in lines if line.strip()
    ]


def to_bug_report(instance: MultiSWEBenchInstance) -> BugReport:
    title = (
        instance.problem_statement.strip().splitlines()[0]
        if instance.problem_statement
        else instance.instance_id
    )
    return BugReport(
        repo=instance.repo,
        base_ref=instance.base_commit,
        source="multiswebench",
        source_ref=instance.instance_id,
        title=title[:200],
        body=instance.problem_statement,
    )
