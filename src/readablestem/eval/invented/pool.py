"""Pooling and intervals for the invented-content rates (pre-registration, section 8).

A rate is pooled the way the pre-registration says: the numerators and denominators of a
section are summed over its runs and the sections are summed, never the section rates
averaged. Two intervals go with each rate:

- the **cluster bootstrap by section**: resample the sections with replacement, B =
  10,000, NumPy `default_rng(0)`, recompute the pooled rate on each resample, take the 2.5th
  and 97.5th percentiles; resamples whose denominator is 0 are skipped and counted. With
  few sections it tends to be a little narrow, and with fewer than two it does not exist;
- the **Wilson interval**, which treats every unit as independent and is narrower when
  units in a section are alike.

A difference between two systems uses the same resampled sections for both.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np

from readablestem.eval.invented.score import TYPES

RESAMPLES = 10_000
SEED = 0
# Types whose rate is per unit; the figure descriptions are judged on a sample (figures.py).
RATE_TYPES = tuple(kind for kind in TYPES if kind != "figure_description")
EQUATION_ALIGNED_WITHOUT_TAGS = "equation_aligned_without_tags"


def wilson(successes: int, n: int, *, z: float = 1.959964) -> list[float] | None:
    """Wilson score interval (treats the n units as independent)."""
    if n <= 0:
        return None
    p = successes / n
    denominator = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denominator
    half = z * np.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denominator
    return [max(0.0, float(centre - half)), min(1.0, float(centre + half))]


@dataclass(frozen=True)
class Interval:
    low: float | None
    high: float | None
    resamples: int
    skipped: int
    sections: int
    level: float = 0.95
    note: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "low": self.low,
            "high": self.high,
            "level": self.level,
            "resamples": self.resamples,
            "skipped": self.skipped,
            "sections": self.sections,
            "note": self.note,
        }


def _percentiles(values: np.ndarray, level: float) -> tuple[float, float]:
    tail = (1 - level) / 2 * 100
    low, high = np.percentile(values, [tail, 100 - tail])
    return float(low), float(high)


def cluster_bootstrap(
    numerators: Sequence[float],
    denominators: Sequence[float],
    *,
    resamples: int = RESAMPLES,
    seed: int = SEED,
    level: float = 0.95,
) -> Interval:
    """Percentile interval of the pooled rate (sum of numerators over sum of denominators)
    under resampling of sections."""
    sections = len(numerators)
    if sections != len(denominators):
        raise ValueError("numerators and denominators differ in length")
    num = np.asarray(numerators, dtype=float)
    den = np.asarray(denominators, dtype=float)
    # A resample of one section that has units is that section's rate again and again: no
    # interval. Sections with no unit of this type stay in the draw (they are part of the
    # set) but do not count towards the two.
    if sections < 2 or int((den > 0).sum()) < 2:
        return Interval(None, None, 0, 0, sections, level, "needs 2 or more sections with units")
    draw = np.random.default_rng(seed).integers(0, sections, size=(resamples, sections))
    total_den = den[draw].sum(axis=1)
    valid = total_den > 0
    skipped = int((~valid).sum())
    if not valid.any():
        return Interval(None, None, resamples, skipped, sections, level, "no valid resample")
    rates = num[draw].sum(axis=1)[valid] / total_den[valid]
    low, high = _percentiles(rates, level)
    return Interval(low, high, resamples, skipped, sections, level)


def paired_difference(
    a_numerators: Sequence[float],
    a_denominators: Sequence[float],
    b_numerators: Sequence[float],
    b_denominators: Sequence[float],
    *,
    resamples: int = RESAMPLES,
    seed: int = SEED,
    level: float = 0.95,
) -> tuple[float | None, Interval]:
    """Pooled rate of A minus pooled rate of B, with the interval from resampling the same
    sections for both (rows of the four sequences are the same sections in the same order)."""
    sections = len(a_numerators)
    if not (sections == len(a_denominators) == len(b_numerators) == len(b_denominators)):
        raise ValueError("the four sequences must list the same sections")
    a_den, b_den = sum(a_denominators), sum(b_denominators)
    point = None
    if a_den > 0 and b_den > 0:
        point = sum(a_numerators) / a_den - sum(b_numerators) / b_den
    an, ad = np.asarray(a_numerators, float), np.asarray(a_denominators, float)
    bn, bd = np.asarray(b_numerators, float), np.asarray(b_denominators, float)
    if sections < 2 or int(((ad > 0) & (bd > 0)).sum()) < 2:
        return point, Interval(
            None, None, 0, 0, sections, level, "needs 2 or more sections with units in both"
        )
    draw = np.random.default_rng(seed).integers(0, sections, size=(resamples, sections))
    a_total, b_total = ad[draw].sum(axis=1), bd[draw].sum(axis=1)
    valid = (a_total > 0) & (b_total > 0)
    skipped = int((~valid).sum())
    if not valid.any():
        return point, Interval(None, None, resamples, skipped, sections, level, "no valid resample")
    diff = (
        an[draw].sum(axis=1)[valid] / a_total[valid] - bn[draw].sum(axis=1)[valid] / b_total[valid]
    )
    low, high = _percentiles(diff, level)
    return point, Interval(low, high, resamples, skipped, sections, level)


# ------------------------------------------------------------------ pooling reports


def _share(
    counts: Mapping[str, int], *, content_only: bool, without_tags: bool = False
) -> tuple[int, int]:
    """(units, unsupported), with the tool's own text taken out when `content_only` and the
    equations flagged only for a printed equation number taken out of the unsupported when
    `without_tags`."""
    n, bad = counts["n"], counts["unsupported"]
    if content_only:
        n -= counts["n_authored"]
        bad -= counts["unsupported_authored"]
    if without_tags:
        bad -= counts.get("tag_only", 0) - (
            counts.get("tag_only_authored", 0) if content_only else 0
        )
    return n, bad


def section_totals(
    entries: Sequence[Mapping[str, Any]],
    kind: str,
    *,
    content_only: bool,
    without_tags: bool = False,
) -> dict[str, tuple[int, int, int]]:
    """Per section: (units, unsupported, PDF pages), summed over the section's runs.

    Each entry is `{"section": id, "score": Score.to_dict()}`."""
    totals: dict[str, list[int]] = {}
    for entry in entries:
        score = entry["score"]
        n, bad = _share(score["counts"][kind], content_only=content_only, without_tags=without_tags)
        row = totals.setdefault(entry["section"], [0, 0, 0])
        row[0] += n
        row[1] += bad
        row[2] += score["reference"]["pages"]
    return {section: (row[0], row[1], row[2]) for section, row in totals.items()}


def pooled(
    entries: Sequence[Mapping[str, Any]],
    kind: str,
    *,
    content_only: bool = False,
    without_tags: bool = False,
) -> dict[str, Any]:
    """The pooled rate of one type over entries: counts, rate, Wilson and cluster
    intervals, the per-section rows and the count per PDF page."""
    by_section = section_totals(entries, kind, content_only=content_only, without_tags=without_tags)
    sections = sorted(by_section)
    dens = [by_section[s][0] for s in sections]
    nums = [by_section[s][1] for s in sections]
    pages = sum(by_section[s][2] for s in sections)
    n, bad = sum(dens), sum(nums)
    return {
        "n": n,
        "unsupported": bad,
        "rate": bad / n if n else None,
        "wilson95": wilson(bad, n),
        "cluster95": cluster_bootstrap(nums, dens).to_dict(),
        "per_pdf_page": bad / pages if pages else None,
        "pdf_pages": pages,
        "per_section": {
            s: {"n": by_section[s][0], "unsupported": by_section[s][1]} for s in sections
        },
    }


def equation_symbols(entries: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Added symbols per 1,000 output symbols, over aligned equations."""
    added = sum(e["score"]["equation_symbols"]["added"] for e in entries)
    in_tags = sum(e["score"]["equation_symbols"].get("added_in_tags", 0) for e in entries)
    total = sum(e["score"]["equation_symbols"]["aligned_output_symbols"] for e in entries)
    return {
        "added": added,
        "added_in_tags": in_tags,
        "aligned_output_symbols": total,
        "per_1000": 1000 * added / total if total else None,
        "per_1000_without_tags": 1000 * (added - in_tags) / total if total else None,
        "aligned_pairs": sum(e["score"]["equation_symbols"]["aligned_pairs"] for e in entries),
    }


def summarize(entries: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Every rate of one system's entries, with and without the tool's own text, plus the
    counts nothing could be said about (TeX that did not convert, short equations)."""
    result: dict[str, Any] = {
        "entries": len(entries),
        "sections": len({e["section"] for e in entries}),
    }
    for label, content_only in (("all_text", False), ("content_only", True)):
        result[label] = {
            kind: pooled(entries, kind, content_only=content_only) for kind in RATE_TYPES
        }
        # The pre-registered rule's flag, less equations flagged only for the equation
        # number the system kept inside them (score.equation_tag): reported next to it.
        result[label][EQUATION_ALIGNED_WITHOUT_TAGS] = pooled(
            entries, "equation_aligned", content_only=content_only, without_tags=True
        )
    result["equation_symbols"] = equation_symbols(entries)
    result["unchecked"] = {
        kind: sum(e["score"]["counts"][kind]["unchecked"] for e in entries) for kind in RATE_TYPES
    }
    result["too_short"] = {
        kind: sum(e["score"]["counts"][kind]["too_short"] for e in entries) for kind in RATE_TYPES
    }
    result["descriptions"] = sum(e["score"]["counts"]["figure_description"]["n"] for e in entries)
    return result


def compare(
    a: Sequence[Mapping[str, Any]],
    b: Sequence[Mapping[str, Any]],
    kind: str,
    *,
    content_only: bool = False,
    level: float = 0.95,
) -> dict[str, Any]:
    """Pooled rate of A minus that of B on the sections both completed."""
    ta = section_totals(a, kind, content_only=content_only)
    tb = section_totals(b, kind, content_only=content_only)
    common = sorted(set(ta) & set(tb))
    point, interval = paired_difference(
        [ta[s][1] for s in common],
        [ta[s][0] for s in common],
        [tb[s][1] for s in common],
        [tb[s][0] for s in common],
        level=level,
    )
    return {"kind": kind, "sections": common, "difference": point, "interval": interval.to_dict()}
