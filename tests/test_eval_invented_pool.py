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


def test_a_single_section_has_no_cluster_interval():
    interval = pool.cluster_bootstrap([3], [10])
    assert interval.low is None and "2 or more" in interval.note


def test_resamples_with_no_units_are_skipped_and_counted():
    interval = pool.cluster_bootstrap([0, 1, 2], [0, 4, 5])
    assert interval.skipped > 0 and interval.skipped < interval.resamples
    assert interval.low is not None and interval.low < interval.high


def test_one_section_with_units_among_many_has_no_cluster_interval():
    # Resampling would hand back that one section's rate as a zero-width interval.
    interval = pool.cluster_bootstrap([0, 3, 0], [0, 10, 0])
    assert interval.low is None and "with units" in interval.note
    point, paired = pool.paired_difference([0, 3, 0], [0, 10, 0], [0, 1, 1], [0, 10, 10])
    assert point is not None and paired.low is None and "with units" in paired.note


def test_the_rate_is_pooled_by_sums_not_by_averaging_section_rates():
    entries = [
        entry("a", sentence=(1, 1)),  # rate 1.0
        entry("b", sentence=(99, 0)),  # rate 0.0
    ]
    row = pool.pooled(entries, "sentence")
    assert (row["n"], row["unsupported"]) == (100, 1)
    assert row["rate"] == pytest.approx(0.01)  # not 0.5


def test_runs_of_one_section_are_summed_before_resampling():
    entries = [
        entry("a", "1", sentence=(10, 1)),
        entry("a", "2", sentence=(10, 3)),
        entry("b", "1", sentence=(20, 0)),
    ]
    row = pool.pooled(entries, "sentence")
    assert row["per_section"] == {
        "a": {"n": 20, "unsupported": 4},
        "b": {"n": 20, "unsupported": 0},
    }
    assert row["cluster95"]["sections"] == 2
    assert row["pdf_pages"] == 30  # pages of every output scored: 3 x 10
    assert row["per_pdf_page"] == pytest.approx(4 / 30)


def test_content_only_takes_the_tools_own_text_out_of_both_counts():
    entries = [entry("a", sentence=(10, 4, 4, 4)), entry("b", sentence=(10, 0, 1, 0))]
    everything = pool.pooled(entries, "sentence")
    content = pool.pooled(entries, "sentence", content_only=True)
    assert (everything["n"], everything["unsupported"]) == (20, 4)
    assert (content["n"], content["unsupported"]) == (15, 0)


def test_a_paired_difference_resamples_the_same_sections_for_both_systems():
    a_num, a_den = [5, 6, 7, 8], [10, 10, 10, 10]
    b_num, b_den = [1, 1, 2, 1], [10, 10, 10, 10]
    point, interval = pool.paired_difference(a_num, a_den, b_num, b_den)
    assert point == pytest.approx(26 / 40 - 5 / 40)
    assert interval.low > 0  # the lowest resample of A is above the highest of B
    same, flat = pool.paired_difference(a_num, a_den, a_num, a_den)
    assert same == 0 and flat.low == flat.high == 0
    with pytest.raises(ValueError, match="same sections"):
        pool.paired_difference([1], [1], [1, 2], [1, 2])


def test_bonferroni_level_gives_a_wider_interval():
    nums, dens = [3, 4, 5, 2, 6], [10] * 5
    narrow = pool.cluster_bootstrap(nums, dens, level=0.95)
    wide = pool.cluster_bootstrap(nums, dens, level=0.99)
    assert wide.low <= narrow.low and wide.high >= narrow.high


def test_summarize_reports_both_views_and_what_could_not_be_checked():
    entries = [entry("a", sentence=(10, 2, 2, 2)), entry("b", sentence=(10, 0))]
    entries[0]["score"]["counts"]["equation_unaligned"]["unchecked"] = 3
    entries[1]["score"]["counts"]["equation_unaligned"]["too_short"] = 4
    summary = pool.summarize(entries)
    assert summary["sections"] == 2 and summary["entries"] == 2
    assert summary["all_text"]["sentence"]["unsupported"] == 2
    assert summary["content_only"]["sentence"]["unsupported"] == 0
    assert summary["unchecked"]["equation_unaligned"] == 3
    assert summary["too_short"]["equation_unaligned"] == 4
    assert "figure_description" not in summary["all_text"]  # judged on a sample, not here


def test_equation_symbols_are_added_per_thousand_output_symbols():
    entries = [entry("a"), entry("b")]
    entries[0]["score"]["equation_symbols"] = {
        "added": 3,
        "aligned_output_symbols": 500,
        "aligned_pairs": 20,
    }
    entries[1]["score"]["equation_symbols"] = {
        "added": 0,
        "aligned_output_symbols": 500,
        "aligned_pairs": 20,
    }
    assert pool.equation_symbols(entries)["per_1000"] == pytest.approx(3.0)
    assert pool.equation_symbols([entry("c")])["per_1000"] is None


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


def test_equations_flagged_only_for_a_kept_equation_number_have_their_own_view():
    entries = [
        entry("a", equation_aligned=(10, 6, 2, 1)),
        entry("b", equation_aligned=(10, 4, 0, 0)),
    ]
    entries[0]["score"]["counts"]["equation_aligned"].update(tag_only=5, tag_only_authored=1)
    entries[1]["score"]["counts"]["equation_aligned"].update(tag_only=4)
    entries[0]["score"]["equation_symbols"] = {
        "added": 12,
        "added_in_tags": 10,
        "aligned_output_symbols": 1000,
        "aligned_pairs": 10,
    }
    summary = pool.summarize(entries)
    assert summary["all_text"]["equation_aligned"]["unsupported"] == 10  # the rule as written
    view = summary["all_text"][pool.EQUATION_ALIGNED_WITHOUT_TAGS]
    assert (view["n"], view["unsupported"]) == (20, 1)
    # the tool's own text taken out: 2 of 20 units, 1 of 10 unsupported, 1 of 5 tags
    own = summary["content_only"][pool.EQUATION_ALIGNED_WITHOUT_TAGS]
    assert (own["n"], own["unsupported"]) == (18, 1)
    symbols = summary["equation_symbols"]
    assert symbols["per_1000"] == pytest.approx(12.0)
    assert symbols["per_1000_without_tags"] == pytest.approx(2.0)
