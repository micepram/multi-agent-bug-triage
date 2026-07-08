"""Unit tests for spectrum-based fault localization (Ochiai).

Pure math over a coverage spectrum: an element executed by many failing tests and
few passing tests is more suspicious. No sandbox needed here; collecting the
spectrum from real test runs is a separate seam.
"""

from __future__ import annotations

import math

from app.agents.sbfl import Spectrum, ochiai, rank_suspicious


def test_ochiai_is_one_when_only_failing_tests_cover() -> None:
    # covered by all 4 failing tests, no passing tests
    assert ochiai(failed_cov=4, passed_cov=0, total_failed=4) == 1.0


def test_ochiai_is_zero_when_no_failing_test_covers() -> None:
    assert ochiai(failed_cov=0, passed_cov=5, total_failed=4) == 0.0


def test_ochiai_between_zero_and_one_for_mixed_coverage() -> None:
    score = ochiai(failed_cov=2, passed_cov=2, total_failed=4)
    expected = 2 / math.sqrt(4 * (2 + 2))
    assert math.isclose(score, expected)
    assert 0.0 < score < 1.0


def test_ochiai_zero_denominator_is_safe() -> None:
    assert ochiai(failed_cov=0, passed_cov=0, total_failed=0) == 0.0


def test_rank_orders_by_descending_suspiciousness() -> None:
    spectrum = Spectrum(
        total_failed=2,
        total_passed=3,
        coverage={
            "src/a.py": (2, 0),  # only failing -> most suspicious
            "src/b.py": (1, 3),  # mixed
            "src/c.py": (0, 3),  # only passing -> not suspicious
        },
    )
    ranked = rank_suspicious(spectrum)
    files = [element for element, _ in ranked]
    assert files[0] == "src/a.py"
    assert files[-1] == "src/c.py"
    assert all(0.0 <= score <= 1.0 for _, score in ranked)
