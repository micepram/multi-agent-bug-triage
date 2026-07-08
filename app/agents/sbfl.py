"""Spectrum-based fault localization (Ochiai).

Given, for each program element, how many failing and passing tests execute it,
Ochiai suspiciousness ranks elements that failing tests hit and passing tests
avoid. This is pure math over a coverage spectrum; gathering the spectrum from
real per-test coverage runs (Python ``coverage`` / Java JaCoCo, both sandboxed)
is a separate seam.
"""

from __future__ import annotations

import math

from pydantic import BaseModel


class Spectrum(BaseModel):
    """Per-element failing/passing coverage counts from a test run."""

    total_failed: int
    total_passed: int
    # element (e.g. "path/to/file.py" or "file.py:line") -> (failed_cov, passed_cov)
    coverage: dict[str, tuple[int, int]]


def ochiai(*, failed_cov: int, passed_cov: int, total_failed: int) -> float:
    """Ochiai suspiciousness in [0, 1]; 0 when the denominator is degenerate."""
    denominator = math.sqrt(total_failed * (failed_cov + passed_cov))
    if denominator == 0:
        return 0.0
    return failed_cov / denominator


def rank_suspicious(spectrum: Spectrum) -> list[tuple[str, float]]:
    """Rank elements by descending Ochiai suspiciousness (ties by element name)."""
    scored = [
        (
            element,
            ochiai(
                failed_cov=failed_cov,
                passed_cov=passed_cov,
                total_failed=spectrum.total_failed,
            ),
        )
        for element, (failed_cov, passed_cov) in spectrum.coverage.items()
    ]
    scored.sort(key=lambda item: (-item[1], item[0]))
    return scored
