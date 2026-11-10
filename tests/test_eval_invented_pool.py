"""Pooling and intervals: sums over sections, never means of rates (pre-registration, 8)."""

from __future__ import annotations

import pytest

from readablestem.eval.invented import pool
from readablestem.eval.invented.score import TYPES


def entry(section, run="1", pages=10, **counts):
    """A score dict with the given (n, unsupported[, n_authored, unsupported_authored])."""
    full = {
        kind: {
            "n": 0,
            "unsupported": 0,
            "n_authored": 0,
            "unsupported_authored": 0,
            "unchecked": 0,
            "too_short": 0,
        }
        for kind in TYPES
    }
    for kind, values in counts.items():
        names = ("n", "unsupported", "n_authored", "unsupported_authored")
        full[kind].update(dict(zip(names, values, strict=False)))
    return {
        "section": section,
        "run": run,
        "score": {
            "counts": full,
            "reference": {"pages": pages},
            "equation_symbols": {"added": 0, "aligned_output_symbols": 0, "aligned_pairs": 0},
        },
    }


def test_wilson_matches_the_known_value_and_is_none_for_no_units():
    low, high = pool.wilson(0, 10)
    assert low == 0.0 and high == pytest.approx(0.2775, abs=1e-4)
    low, high = pool.wilson(5, 10)
    assert (low, high) == pytest.approx((0.2366, 0.7634), abs=1e-4)
    assert pool.wilson(0, 0) is None


def test_the_cluster_interval_is_reproducible_and_holds_the_pooled_rate():
    nums, dens = [0, 5, 10, 2], [10, 10, 10, 10]
    first = pool.cluster_bootstrap(nums, dens)
    again = pool.cluster_bootstrap(nums, dens)
    assert first == again
    assert first.low <= 17 / 40 <= first.high
    assert first.resamples == 10_000 and first.skipped == 0 and first.sections == 4
    wider = [0, 5, 10, 2, 7, 3, 9, 1], [10, 12, 9, 11, 10, 8, 10, 12]
    assert pool.cluster_bootstrap(*wider, seed=1) != pool.cluster_bootstrap(*wider)  # the seed


def test_the_rate_is_pooled_by_sums_not_by_averaging_section_rates():
    entries = [
        entry("a", sentence=(1, 1)),  # rate 1.0
        entry("b", sentence=(99, 0)),  # rate 0.0
    ]
    row = pool.pooled(entries, "sentence")
    assert (row["n"], row["unsupported"]) == (100, 1)
    assert row["rate"] == pytest.approx(0.01)  # not 0.5


def test_compare_uses_only_the_sections_both_systems_have():
    a = [
        entry("s1", sentence=(10, 8)),
        entry("s2", sentence=(10, 6)),
        entry("s3", sentence=(10, 7)),
    ]
    b = [entry("s1", sentence=(10, 1)), entry("s2", sentence=(10, 0))]
    result = pool.compare(a, b, "sentence", level=0.99)
    assert result["sections"] == ["s1", "s2"]
    assert result["difference"] == pytest.approx(14 / 20 - 1 / 20)
    assert result["interval"]["level"] == 0.99
