"""Intervals for the proof tables: Wilson, and the cluster bootstrap by section.

The preregistration (section 8) fixes the method, and this module follows it:

- every headline number gets a **cluster bootstrap by section**: resample the sections
  with replacement, B = 10,000 times, NumPy `default_rng(0)`, recompute the pooled metric
  (sums of numerators and denominators over the resampled sections), take the 2.5th and
  97.5th percentiles (linear interpolation, NumPy's default). A resample for which the
  metric is undefined (a zero denominator) is skipped and counted;
- a **Wilson score interval** is given as well, labelled as treating items as independent;
  it is narrower when items in a section are alike, so the cluster interval is the one
  to quote;
- with repeats, a section's numerators and denominators are summed over the repeats
  before resampling: the run is not a resampling unit;
- a paired difference A - B uses the same resampled sections for both systems;
- as a robustness check beside the bootstrap, `sign_flip` gives the two-sided p-value of a
  paired sign-flip permutation test on the per-section differences (exact for up to 16
  sections). It is not part of the registered rule; the proof tables print it next to the
  registered interval so a reader can see when a verdict depends on the bootstrap alone.

The resample matrix is `rng.integers(0, n_sections, size=(B, n_sections))`, one draw for
the whole bootstrap. With 15 sections a percentile bootstrap is approximate and tends to be
a little narrow; the report says so.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

import numpy as np

from readablestem.eval.pipeline_eval import wilson_interval

N_BOOT = 10_000
SEED = 0
Statistic = Callable[[np.ndarray], float | None]


@dataclass(frozen=True)
class Interval:
    low: float | None
    high: float | None
    skipped: int  # resamples where the metric was undefined
    n_sections: int
    level: float


def ratio(sums: np.ndarray) -> float | None:
    """sums = (numerator, denominator)."""
    return float(sums[0] / sums[1]) if sums[1] else None


def f1_of_counts(sums: np.ndarray) -> float | None:
    """sums = (matched, predicted, gold): F1 of the pooled precision and recall."""
    matched, predicted, gold = sums
    if not predicted or not gold:
        return None
    precision, recall = matched / predicted, matched / gold
    return float(2 * precision * recall / (precision + recall)) if precision + recall else None


def _matrix(per_section: Mapping[str, Sequence[float]]) -> np.ndarray:
    return np.asarray([list(per_section[k]) for k in sorted(per_section)], dtype=float)


def point(per_section: Mapping[str, Sequence[float]], statistic: Statistic) -> float | None:
    matrix = _matrix(per_section)
    return statistic(matrix.sum(axis=0)) if len(matrix) else None


def cluster_bootstrap(
    per_section: Mapping[str, Sequence[float]],
    statistic: Statistic,
    *,
    level: float = 0.95,
    n_boot: int = N_BOOT,
    seed: int = SEED,
) -> Interval:
    """Percentile interval of `statistic` over resamples of the sections.

    `per_section` maps a section id to its count vector (summed over repeats); the
    vector's layout is whatever `statistic` expects.
    """
    matrix = _matrix(per_section)
    n = len(matrix)
    if n == 0:
        return Interval(None, None, 0, 0, level)
    rng = np.random.default_rng(seed)
    draws = rng.integers(0, n, size=(n_boot, n))
    sums = matrix[draws].sum(axis=1)  # (n_boot, k)
    values = [v for row in sums if (v := statistic(row)) is not None]
    skipped = n_boot - len(values)
    if not values:
        return Interval(None, None, skipped, n, level)
    alpha = (1 - level) / 2 * 100
    low, high = np.percentile(values, [alpha, 100 - alpha])
    return Interval(float(low), float(high), skipped, n, level)


def paired_difference(
    a: Mapping[str, Sequence[float]],
    b: Mapping[str, Sequence[float]],
    statistic: Statistic,
    *,
    level: float = 0.95,
    n_boot: int = N_BOOT,
    seed: int = SEED,
) -> tuple[float | None, Interval]:
    """A - B on the same sections: the pooled difference and its cluster interval.

    Both mappings must hold the same sections; the same resample is used for both.
    """
    if set(a) != set(b):
        raise ValueError("a paired difference needs the same sections on both sides")
    ma, mb = _matrix(a), _matrix(b)
    n = len(ma)
    if n == 0:
        return None, Interval(None, None, 0, 0, level)
    pa, pb = statistic(ma.sum(axis=0)), statistic(mb.sum(axis=0))
    estimate = None if pa is None or pb is None else pa - pb
    rng = np.random.default_rng(seed)
    draws = rng.integers(0, n, size=(n_boot, n))
    sa, sb = ma[draws].sum(axis=1), mb[draws].sum(axis=1)
    diffs = []
    for ra, rb in zip(sa, sb, strict=True):
        va, vb = statistic(ra), statistic(rb)
        if va is not None and vb is not None:
            diffs.append(va - vb)
    skipped = n_boot - len(diffs)
    if not diffs:
        return estimate, Interval(None, None, skipped, n, level)
    alpha = (1 - level) / 2 * 100
    low, high = np.percentile(diffs, [alpha, 100 - alpha])
    return estimate, Interval(float(low), float(high), skipped, n, level)


SIGN_FLIP_EXACT_MAX = 16
SIGN_FLIP_DRAWS = 200_000


@dataclass(frozen=True)
class SignFlip:
    p: float | None
    n_used: int  # sections with a difference that is not zero
    n_sections: int  # sections where the statistic exists on both sides


def sign_flip(
    a: Mapping[str, Sequence[float]],
    b: Mapping[str, Sequence[float]],
    statistic: Statistic,
    *,
    seed: int = SEED,
) -> SignFlip:
    """Two-sided paired sign-flip permutation test on per-section differences.

    The statistic is computed on each section on its own (the section's own F1 or rate), the
    differences A - B are formed, and the p-value is the share of sign assignments whose sum
    is at least as far from 0 as the observed sum. Sections where the statistic is undefined
    on either side are left out and counted. Exact when 16 sections or fewer are used,
    otherwise 200,000 seeded draws.
    """
    if set(a) != set(b):
        raise ValueError("a paired test needs the same sections on both sides")
    diffs: list[float] = []
    for key in sorted(a):
        va = statistic(np.asarray(a[key], dtype=float))
        vb = statistic(np.asarray(b[key], dtype=float))
        if va is not None and vb is not None:
            diffs.append(va - vb)
    n_sections = len(diffs)
    d = np.asarray([x for x in diffs if abs(x) > 1e-12])
    if len(d) == 0:
        return SignFlip(None, 0, n_sections)
    observed = abs(float(d.sum()))
    if len(d) <= SIGN_FLIP_EXACT_MAX:
        signs = ((np.arange(2 ** len(d))[:, None] >> np.arange(len(d))) & 1) * 2 - 1
        sums = np.abs(signs @ d)
    else:
        rng = np.random.default_rng(seed)
        signs = rng.integers(0, 2, size=(SIGN_FLIP_DRAWS, len(d))) * 2 - 1
        sums = np.abs(signs @ d)
    p = float(np.mean(sums >= observed - 1e-12))
    return SignFlip(p, len(d), n_sections)


def wilson(successes: float, n: float) -> tuple[float, float] | None:
    return wilson_interval(round(successes), round(n))
