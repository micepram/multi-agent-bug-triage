"""Unit tests for the Multi-SWE-bench (Java) eval adapter (spec Phase 5).

Like the SWE-bench adapter but for Java instances, each carrying a per-instance
Docker environment. Loads a JSONL slice and maps instances onto the pipeline's
language-agnostic BugReport, tagged as Java.
"""

from __future__ import annotations

import json
from pathlib import Path

from app.agents.types import Language
from app.eval.multiswebench import (
    MultiSWEBenchInstance,
    load_instances,
    to_bug_report,
)


def test_loads_java_instances(tmp_path: Path) -> None:
    path = tmp_path / "java.jsonl"
    path.write_text(
        json.dumps(
            {
                "instance_id": "apache__commons-lang-123",
                "repo": "apache/commons-lang",
                "base_commit": "cafe1234",
                "problem_statement": "NPE in StringUtils.abbreviate\n\ndetails",
                "fail_to_pass": ["StringUtilsTest#testAbbreviate"],
                "image": "mswebench/apache_commons-lang:latest",
            }
        )
        + "\n"
    )
    instances = load_instances(path)
    assert len(instances) == 1
    assert isinstance(instances[0], MultiSWEBenchInstance)
    assert instances[0].language is Language.JAVA
    assert instances[0].image == "mswebench/apache_commons-lang:latest"


def test_maps_instance_to_java_bug_report() -> None:
    instance = MultiSWEBenchInstance(
        instance_id="apache__commons-lang-123",
        repo="apache/commons-lang",
        base_commit="cafe1234",
        problem_statement="NPE in StringUtils.abbreviate\n\ndetails",
    )
    report = to_bug_report(instance)
    assert report.repo == "apache/commons-lang"
    assert report.base_ref == "cafe1234"
    assert report.source == "multiswebench"
    assert report.title == "NPE in StringUtils.abbreviate"
