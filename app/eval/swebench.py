"""SWE-bench Verified adapter (spec Section 8).

Each SWE-bench instance is an input (issue + repo@base_commit + fail-to-pass
test) and a ground truth (reference patch). This module loads instances from a
JSONL slice and maps them onto the pipeline's :class:`BugReport`, plus a repro
synthesizer that hands the fail-to-pass test to the Reproduction agent. The real
dataset download and per-instance environments belong to the eval tier; nothing
here reaches the network.
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, Field

from app.agents.types import BugReport


class Instance(BaseModel):
    """A single SWE-bench Verified instance."""

    instance_id: str
    repo: str  # owner/name
    base_commit: str
    problem_statement: str
    fail_to_pass: list[str] = Field(default_factory=list)
    test_patch: str = ""
    reference_patch: str = ""


def load_instances(path: str | Path) -> list[Instance]:
    """Load instances from a JSONL file (one instance per line)."""
    lines = Path(path).read_text().splitlines()
    return [Instance.model_validate(json.loads(line)) for line in lines if line.strip()]


def to_bug_report(instance: Instance) -> BugReport:
    """Map an instance onto the pipeline input."""
    title = (
        instance.problem_statement.strip().splitlines()[0]
        if instance.problem_statement
        else (instance.instance_id)
    )
    return BugReport(
        repo=instance.repo,
        base_ref=instance.base_commit,
        source="swebench",
        source_ref=instance.instance_id,
        title=title[:200],
        body=instance.problem_statement,
    )


class InstanceReproSynthesizer:
    """Phase 2 synthesizer: the repro is the instance's fail-to-pass test."""

    def __init__(self, instance: Instance) -> None:
        self._instance = instance

    def synthesize(self, report: BugReport, attempt: int) -> str | None:
        if self._instance.test_patch:
            return self._instance.test_patch
        if self._instance.fail_to_pass:
            return "# fail-to-pass\n" + "\n".join(self._instance.fail_to_pass)
        return None
