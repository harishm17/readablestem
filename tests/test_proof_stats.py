"""Intervals of the proof tables: the cluster bootstrap by section, as the preregistration
fixes it (NumPy default_rng(0), resample whole sections, percentile interval)."""

from __future__ import annotations

import numpy as np
import pytest

from a11y_stem.proof import stats


def test_ratio_and_f1_of_counts_are_none_when_undefined():
    assert stats.ratio(np.array([3.0, 4.0])) == 0.75
    assert stats.ratio(np.array([0.0, 0.0])) is None
    # (matched, predicted, gold): precision 1/2, recall 1/4 -> F1 1/3
    assert stats.f1_of_counts(np.array([1.0, 2.0, 4.0])) == pytest.approx(1 / 3)
    assert stats.f1_of_counts(np.array([0.0, 2.0, 4.0])) is None  # precision + recall = 0
    assert stats.f1_of_counts(np.array([0.0, 0.0, 4.0])) is None  # nothing predicted


def test_the_point_estimate_pools_numerators_and_denominators():
    # A mean of the two section rates would be 0.5; the pooled rate is 4/12.
    per_section = {"a": [1.0, 2.0], "b": [3.0, 10.0]}
    assert stats.point(per_section, stats.ratio) == pytest.approx(4 / 12)
    assert stats.point({}, stats.ratio) is None


def test_identical_sections_give_a_point_interval():
    per_section = {f"s{i}": [3.0, 4.0] for i in range(5)}
    interval = stats.cluster_bootstrap(per_section, stats.ratio, n_boot=300)
    assert (interval.low, interval.high) == (0.75, 0.75)
    assert interval.skipped == 0 and interval.n_sections == 5 and interval.level == 0.95


def test_the_interval_is_the_same_every_time_and_holds_the_estimate():
    per_section = {"a": [0.0, 10.0], "b": [9.0, 10.0], "c": [5.0, 10.0], "d": [8.0, 10.0]}
    first = stats.cluster_bootstrap(per_section, stats.ratio, n_boot=500)
    again = stats.cluster_bootstrap(per_section, stats.ratio, n_boot=500)
    assert first == again
    estimate = stats.point(per_section, stats.ratio)
    assert first.low is not None and first.high is not None and estimate is not None
    assert first.low < estimate < first.high
    assert first.low >= 0.0 and first.high <= 0.9  # no section is better than 9/10


def test_the_resampling_unit_is_the_section_not_the_item():
    # Two sections, all-or-nothing: resampling items would give a narrow interval around
    # 0.5; resampling sections can give 0 or 1.
    per_section = {"a": [100.0, 100.0], "b": [0.0, 100.0]}
    interval = stats.cluster_bootstrap(per_section, stats.ratio, n_boot=400)
    assert (interval.low, interval.high) == (0.0, 1.0)


def test_resamples_with_an_undefined_metric_are_skipped_and_counted():
    # Section "b" has a zero denominator; a resample made only of "b" has no value.
    per_section = {"a": [1.0, 2.0], "b": [0.0, 0.0]}
    interval = stats.cluster_bootstrap(per_section, stats.ratio, n_boot=400)
    assert interval.skipped > 0
    assert interval.low == interval.high == 0.5
    nothing = stats.cluster_bootstrap({"b": [0.0, 0.0]}, stats.ratio, n_boot=50)
    assert (nothing.low, nothing.high, nothing.skipped) == (None, None, 50)


def test_no_sections_gives_an_empty_interval():
    empty = stats.cluster_bootstrap({}, stats.ratio, n_boot=10)
    assert (empty.low, empty.high, empty.n_sections) == (None, None, 0)


def test_a_paired_difference_uses_the_same_resample_for_both_sides():
    a = {"s1": [8.0, 10.0], "s2": [6.0, 10.0], "s3": [7.0, 10.0]}
    b = {"s1": [3.0, 10.0], "s2": [1.0, 10.0], "s3": [2.0, 10.0]}
    estimate, interval = stats.paired_difference(a, b, stats.ratio, n_boot=400)
    assert estimate == pytest.approx(0.5)
    # Same sections, a constant gap of 0.5 in every section: pairing makes it exact.
    assert interval.low == pytest.approx(0.5) and interval.high == pytest.approx(0.5)


def test_a_paired_difference_refuses_different_sections():
    with pytest.raises(ValueError, match="same sections"):
        stats.paired_difference({"a": [1.0, 2.0]}, {"b": [1.0, 2.0]}, stats.ratio, n_boot=10)


def test_wilson_rounds_counts_and_is_none_for_no_items():
    low, high = stats.wilson(5, 10) or (None, None)
    assert low is not None and high is not None and low < 0.5 < high
    assert stats.wilson(0, 0) is None
