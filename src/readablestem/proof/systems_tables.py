"""Tables that put ReadableSTEM next to the other systems (chatbot recipe, open parsers).

Every system is reduced to the same thing: per section, a vector of numerators and
denominators for each metric, summed over the system's repeats (preregistration section
8.1). Point estimates pool the sections; the interval to quote is the cluster bootstrap by
section; the Wilson interval is shown for the plain rates. A paired difference resamples
the same sections for both systems (section 8.2). A section a system failed on is a
section of zeros in the numerators with the gold items in the denominators: it is a
failure of that system, not a missing one (section 9).

`Vectors` is `metric -> section -> tuple`; the metric specs say how a tuple becomes a
number. The claims rule of section 8.3 is in `verdict`: "better" only when the
Bonferroni-corrected interval of the difference excludes 0.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from readablestem.eval import pipeline_eval as pe
from readablestem.eval.invented import pool as inv_pool
from readablestem.proof import a11y_tables, stats
from readablestem.proof.goldinfo import section_of_item_id
from readablestem.proof.report import NA, Table, fraction, interval, num, usd
from readablestem.proof.runner import ArmResults
from readablestem.proof.systems import SectionScore

Vectors = dict[str, dict[str, tuple[float, ...]]]


@dataclass(frozen=True)
class MetricSpec:
    key: str
    label: str
    statistic: stats.Statistic = stats.ratio
    scale: float = 1.0
    digits: int = 4
    wilson: bool = True
    better: str = "higher"  # "higher" | "lower": which direction is better for a claim


METRICS: dict[str, MetricSpec] = {
    m.key: m
    for m in [
        MetricSpec("e2e", "end_to_end_exact_lenient (all gold equations)"),
        MetricSpec("e2e_clean", "end_to_end_exact_lenient, without G1 equations"),
        MetricSpec("e2e_free", "end_to_end_exact_lenient, allowlist off (descriptive row)"),
        MetricSpec(
            "unflagged",
            "unflagged_wrong_per_100",
            scale=100.0,
            digits=2,
            better="lower",
            wilson=False,
        ),
        MetricSpec("matched_exact", "matched_exact_lenient (aligned pairs only)"),
        MetricSpec("recall", "detection_recall"),
        MetricSpec("precision", "detection_precision"),
        MetricSpec("h_f1", "heading F1", statistic=stats.f1_of_counts, wilson=False),
        MetricSpec("h_precision", "heading precision"),
        MetricSpec("h_recall", "heading recall"),
        MetricSpec("h_level", "heading level_accuracy (among matched)"),
        MetricSpec("e2e_norm", "end_to_end_exact_lenient, markup normalised (descriptive row)"),
        MetricSpec(
            "inv_sentence",
            "invented prose sentences (text-layer mismatch, rule unaudited)",
            better="lower",
        ),
        MetricSpec(
            "inv_number",
            "invented numbers (text-layer mismatch, rule unaudited)",
            better="lower",
        ),
        MetricSpec(
            "inv_sentence_nocomment",
            "prose sentences not in the text layer, HTML comments removed (descriptive row)",
            better="lower",
        ),
        MetricSpec("inv_heading", "headings the PDF does not support", better="lower"),
        MetricSpec("inv_eq_aligned", "aligned equations with an added symbol", better="lower"),
        MetricSpec(
            "inv_eq_unaligned",
            "equations with no gold partner and no match in the PDF",
            better="lower",
        ),
        MetricSpec("coverage", "coverage: PDF word 6-grams found in the output"),
        MetricSpec("fig_pass", "figure judge_pass_rate (no description = fail)"),
        MetricSpec("fig_factual", "figure judge_factual_error_rate (among judged)", better="lower"),
        MetricSpec("fig_nodesc", "figures with no description", better="lower"),
    ]
}

FAMILY_A = (
    ("e2e", "end_to_end_exact_lenient"),
    ("unflagged", "unflagged_wrong_per_100"),
    ("inv_sentence", "invented prose sentence rate"),
    ("inv_number", "invented number rate"),
    ("h_f1", "heading F1"),
)
INVENTED_KEYS = {
    "inv_sentence": "sentence",
    "inv_number": "number",
    "inv_heading": "heading",
    "inv_eq_aligned": "equation_aligned",
    "inv_eq_unaligned": "equation_unaligned",
}


# ----------------------------------------------------------------------------- vectors


class VectorBuilder:
    def __init__(self) -> None:
        self.v: Vectors = defaultdict(dict)

    def add(self, key: str, section: str, values: Sequence[float]) -> None:
        old = self.v[key].get(section)
        new = tuple(float(x) for x in values)
        self.v[key][section] = (
            new if old is None else tuple(a + b for a, b in zip(old, new, strict=True))
        )

    def done(self) -> Vectors:
        return {k: dict(v) for k, v in self.v.items()}


def _eq_vectors(b: VectorBuilder, item: dict[str, Any], g1: frozenset[str], section: str) -> None:
    c = a11y_tables.document_counts(g1)(item)
    for key in ("e2e", "e2e_clean", "unflagged", "matched_exact", "recall", "precision"):
        b.add(key, section, c[key])


def _heading_vectors(b: VectorBuilder, item: dict[str, Any], section: str) -> None:
    c = a11y_tables.heading_counts(item)
    b.add("h_f1", section, c["f1"])
    b.add("h_precision", section, c["precision"])
    b.add("h_recall", section, c["recall"])
    b.add("h_level", section, c["level"])


def _invented_vectors(b: VectorBuilder, score: dict[str, Any] | None, section: str) -> None:
    for key, kind in INVENTED_KEYS.items():
        if score is None:
            b.add(key, section, (0, 0))
            continue
        counts = score["counts"][kind]
        b.add(key, section, (counts["unsupported"], counts["n"]))


def system_vectors(scores: Sequence[SectionScore], g1: frozenset[str]) -> Vectors:
    b = VectorBuilder()
    for s in scores:
        _eq_vectors(b, s.equation, g1, s.section)
        # allowlist-free row: only e2e
        free = a11y_tables.document_counts(g1)(s.equation_free)
        b.add("e2e_free", s.section, free["e2e"])
        _heading_vectors(b, s.heading, s.section)
        _invented_vectors(b, s.invented, s.section)
        b.add("coverage", s.section, s.coverage)
        rows = s.figures
        judged = [r for r in rows if r["outcome"] == "judged"]
        nodesc = [r for r in rows if r["outcome"] == "no_description"]
        measured = len(judged) + len(nodesc)
        b.add("fig_pass", s.section, (sum(bool(r["passed"]) for r in judged), measured))
        b.add(
            "fig_factual", s.section, (sum(bool(r["factual_error"]) for r in judged), len(judged))
        )
        b.add("fig_nodesc", s.section, (len(nodesc), len(rows)))
    return b.done()


def a11y_vectors(
    arm: ArmResults,
    g1: frozenset[str],
    invented_entries: Sequence[Mapping[str, Any]] | None,
    coverage: Mapping[str, tuple[float, float]] | None,
) -> Vectors:
    b = VectorBuilder()
    for item in arm.items("document"):
        _eq_vectors(b, item, g1, item["id"])
        # allowlist-free row does not apply: ReadableSTEM's own allowlist is its product rule
    for item in arm.items("headings"):
        _heading_vectors(b, item, item["id"])
    for item in arm.items("figures"):
        section = section_of_item_id(item["id"])
        c = a11y_tables.figure_counts(item)
        b.add("fig_pass", section, c["judge_pass"])
        b.add("fig_factual", section, c["factual"])
        b.add("fig_nodesc", section, (0.0 if item["has_proposal"] else 1.0, 1))
    if invented_entries is not None:
        for entry in invented_entries:
            _invented_vectors(b, entry["score"], entry["section"])
    if coverage:
        for section, pair in coverage.items():
            b.add("coverage", section, pair)
    return b.done()


# ------------------------------------------------------------------------------ cells


def value_of(spec: MetricSpec, vectors: Vectors) -> float | None:
    per = vectors.get(spec.key)
    if not per:
        return None
    v = stats.point(per, spec.statistic)
    return None if v is None else v * spec.scale


def cell(spec: MetricSpec, vectors: Vectors, n_boot: int) -> str:
    per = vectors.get(spec.key)
    if not per:
        return NA
    value = value_of(spec, vectors)
    if value is None:
        return NA
    cluster = stats.cluster_bootstrap(per, spec.statistic, n_boot=n_boot)
    if cluster.low is None or cluster.high is None or cluster.n_sections < 2:
        return num(value, spec.digits)
    low, high = cluster.low * spec.scale, cluster.high * spec.scale
    return f"{num(value, spec.digits)} {interval(low, high, spec.digits)}"


def count_cell(spec: MetricSpec, vectors: Vectors) -> str:
    per = vectors.get(spec.key)
    if not per:
        return NA
    total = [sum(col) for col in zip(*per.values(), strict=True)]
    if len(total) == 2:
        return fraction(total[0], total[1])
    if len(total) == 3:
        return f"{fraction(total[0], total[1])} pred, {fraction(total[0], total[2])} gold"
    return NA


def wilson_cell(spec: MetricSpec, vectors: Vectors) -> str:
    per = vectors.get(spec.key)
    if not per or not spec.wilson or spec.statistic is not stats.ratio or spec.scale != 1.0:
        return NA
    total = [sum(col) for col in zip(*per.values(), strict=True)]
    w = stats.wilson(total[0], total[1])
    return interval(w[0], w[1], spec.digits) if w else NA


def metrics_table(
    title: str,
    keys: Sequence[str],
    systems: Sequence[tuple[str, Vectors]],
    n_boot: int,
    note: str = "",
) -> Table:
    """Rows are metrics, columns are systems; each cell is `value [cluster 95%]`."""
    headers = ["Metric", *[name for name, _ in systems]]
    rows = []
    for key in keys:
        spec = METRICS[key]
        rows.append([spec.label, *[cell(spec, v, n_boot) for _, v in systems]])
    return Table(title, headers, rows, note)


def counts_table(title: str, keys: Sequence[str], systems: Sequence[tuple[str, Vectors]]) -> Table:
    headers = ["Count", *[name for name, _ in systems]]
    rows = [[METRICS[k].label, *[count_cell(METRICS[k], v) for _, v in systems]] for k in keys]
    return Table(title, headers, rows)


def wilson_table(title: str, keys: Sequence[str], systems: Sequence[tuple[str, Vectors]]) -> Table:
    headers = ["Wilson 95% (items treated as independent)", *[name for name, _ in systems]]
    rows = [[METRICS[k].label, *[wilson_cell(METRICS[k], v) for _, v in systems]] for k in keys]
    rows = [r for r in rows if any(c != NA for c in r[1:])]
    return Table(title, headers, rows)


# ---------------------------------------------------------------------------- claims


@dataclass(frozen=True)
class Comparison:
    label: str
    key: str
    other: str
    a: float | None
    b: float | None
    difference: float | None
    narrow: stats.Interval  # the Bonferroni-corrected interval the claim rests on
    wide: stats.Interval  # 95%
    result: str
    p_sign_flip: float | None = None  # robustness check beside the registered interval
    n_sections: int = 0


def verdict(spec: MetricSpec, narrow: stats.Interval, other: str, a_name: str) -> str:
    if narrow.low is None or narrow.high is None:
        return "not computed"
    if narrow.low > 0:
        win = a_name if spec.better == "higher" else other
        return f"{win} is better"
    if narrow.high < 0:
        win = other if spec.better == "higher" else a_name
        return f"{win} is better"
    return "no difference this set can detect"


# A percentile bootstrap over a handful of sections only returns the sections' own extremes
# (3 sections give the same 95% and 99% interval), so no verdict is written below this.
MIN_SECTIONS_FOR_A_VERDICT = 10


def final_result(
    base: str,
    *,
    p: float | None,
    k: int,
    n_sections: int,
    min_sections: int = MIN_SECTIONS_FOR_A_VERDICT,
) -> str:
    """The registered verdict, qualified where it rests on the bootstrap alone.

    Fewer than `min_sections` sections: no verdict at all. A "better" whose sign-flip p-value
    is above the same Bonferroni threshold (0.05 / k) becomes "higher": the registered
    interval excludes 0 but the permutation test does not agree.
    """
    if n_sections < min_sections:
        return f"not tested, {n_sections} sections"
    if base.endswith(" is better"):
        who = base.removesuffix(" is better")
        if p is None or p > 0.05 / k:
            shown = "n/a" if p is None else f"{p:.4f}"
            return (
                f"{who} is higher by point estimate: the registered interval excludes 0, "
                f"the sign-flip test does not (p = {shown})"
            )
    return base


def compare(
    key: str,
    label: str,
    a: Vectors,
    b: Vectors,
    *,
    a_name: str,
    other: str,
    k: int,
    n_boot: int,
    min_sections: int = MIN_SECTIONS_FOR_A_VERDICT,
) -> Comparison:
    spec = METRICS[key]
    pa, pb = a.get(key), b.get(key)
    if pa and pb:
        # A section with no entry on one side (a conversion that failed has no units) is a
        # section of zeros there, so both sides resample the same sections.
        width = len(next(iter(pa.values())))
        zeros = (0.0,) * width
        sections = set(pa) | set(pb)
        pa = {s: pa.get(s, zeros) for s in sections}
        pb = {s: pb.get(s, zeros) for s in sections}
    if not pa or not pb or set(pa) != set(pb):
        empty = stats.Interval(None, None, 0, 0, 0.95)
        return Comparison(label, key, other, None, None, None, empty, empty, "not computed")
    level = 1 - 0.05 / k
    narrow = stats.paired_difference(pa, pb, spec.statistic, level=level, n_boot=n_boot)[1]
    estimate, wide = stats.paired_difference(pa, pb, spec.statistic, level=0.95, n_boot=n_boot)
    va, vb = value_of(spec, a), value_of(spec, b)
    scale = spec.scale
    scaled = _scaled(narrow, scale), _scaled(wide, scale)
    flip = stats.sign_flip(pa, pb, spec.statistic)
    result = final_result(
        verdict(spec, narrow, other, a_name),
        p=flip.p,
        k=k,
        n_sections=len(pa),
        min_sections=min_sections,
    )
    return Comparison(
        label,
        key,
        other,
        va,
        vb,
        None if estimate is None else estimate * scale,
        scaled[0],
        scaled[1],
        result,
        flip.p,
        len(pa),
    )


def _scaled(i: stats.Interval, scale: float) -> stats.Interval:
    if scale == 1.0 or i.low is None or i.high is None:
        return i
    return stats.Interval(i.low * scale, i.high * scale, i.skipped, i.n_sections, i.level)


CLAIMS_NOTE = (
    "Sign-flip p: two-sided p-value of a paired sign-flip permutation test on the per-section "
    "differences, a robustness check that is not part of the registered rule; the Bonferroni "
    "threshold for it is {threshold:.5f}. With fewer than {min} sections no verdict is "
    "written (a bootstrap over so few sections only returns the sections' own extremes), "
    'and a "better" that the sign-flip test does not support is written as "higher by point '
    'estimate".'
)


def claims_table(
    title: str, comparisons: Sequence[Comparison], *, a_name: str, k: int, note: str = ""
) -> Table:
    level = 100 * (1 - 0.05 / k)
    note = (note + " " if note else "") + CLAIMS_NOTE.format(
        min=MIN_SECTIONS_FOR_A_VERDICT, threshold=0.05 / k
    )
    rows = []
    for c in comparisons:
        spec = METRICS[c.key]
        rows.append(
            [
                c.label,
                c.other,
                num(c.a, spec.digits),
                num(c.b, spec.digits),
                num(c.difference, spec.digits),
                interval(c.narrow.low, c.narrow.high, spec.digits),
                interval(c.wide.low, c.wide.high, spec.digits),
                "n/a" if c.p_sign_flip is None else f"{c.p_sign_flip:.4f}",
                f"**{c.result}**",
            ]
        )
    return Table(
        title,
        [
            "Measure",
            "Against",
            a_name,
            "Other",
            f"Difference ({a_name} minus other)",
            f"Cluster {level:.3f}% (k = {k}): the claim",
            "Cluster 95%",
            "Sign-flip p",
            "Result",
        ],
        rows,
        note,
    )


# ------------------------------------------------------------------------ cost, time


def percentiles(values: list[float]) -> list[str]:
    if not values:
        return [NA] * 4
    return [
        num(pe.percentile(values, 50), 5),
        num(pe.percentile(values, 95), 5),
        num(min(values), 5),
        num(max(values), 5),
    ]


@dataclass
class ArmTotals:
    """Measurements per PDF page for one arm, over sections and repeats."""

    cost_per_page: list[float] = field(default_factory=list)
    seconds_per_page: list[float] = field(default_factory=list)
    peak_rss_mb: list[float] = field(default_factory=list)
    statuses: dict[str, int] = field(default_factory=dict)
    total_cost: float = 0.0
    judge_cost: float = 0.0


def cost_time_table(title: str, totals: Mapping[str, ArmTotals], note: str = "") -> Table:
    rows = []
    for name, t in totals.items():
        rows.append(
            [
                name,
                usd(t.total_cost, 4) if t.total_cost else "$0",
                *percentiles(t.cost_per_page),
                *percentiles(t.seconds_per_page),
                num(max(t.peak_rss_mb), 0) if t.peak_rss_mb else NA,
            ]
        )
    return Table(
        title,
        [
            "System",
            "Total API cost",
            "Cost/page median",
            "p95",
            "min",
            "max",
            "Seconds/page median",
            "p95",
            "min",
            "max",
            "Peak memory (MB)",
        ],
        rows,
        note,
    )


def failure_table(title: str, rows_in: Mapping[str, tuple[Mapping[str, int], int]]) -> Table:
    rows = []
    for name, (counts, expected) in rows_in.items():
        rows.append(
            [
                name,
                num(sum(counts.values())),
                num(expected),
                *[
                    num(counts.get(s, 0))
                    for s in ("ok", "truncated", "empty", "timeout", "crashed")
                ],
                num(counts.get("not_run", 0)),
            ]
        )
    return Table(
        title,
        [
            "System",
            "Outputs",
            "Expected",
            "ok",
            "truncated",
            "empty",
            "timeout",
            "crashed",
            "not run",
        ],
        rows,
        note="A section that is not ok scores as found nothing: its gold items stay in every "
        "denominator.",
    )


def invented_pool_table(
    title: str,
    systems: Sequence[tuple[str, Sequence[Mapping[str, Any]]]],
    *,
    content_only: bool = False,
) -> Table:
    """The invented-content rates of each system as `eval.invented.pool` pools them."""
    kinds = [
        "sentence",
        "number",
        "heading",
        "equation_aligned",
        "equation_unaligned",
        "table_cell",
    ]
    rows = []
    for kind in kinds:
        row = [inv_pool_label(kind)]
        for _, entries in systems:
            p = inv_pool.pooled(entries, kind, content_only=content_only) if entries else None
            if p is None or not p["n"]:
                row.append(NA if p is None else f"0/{p['n']}")
                continue
            c = p["cluster95"]
            ci = interval(c["low"], c["high"], 3) if c["low"] is not None else "interval n/a"
            row.append(f"{num(p['rate'], 3)} ({p['unsupported']}/{p['n']}) {ci}")
        rows.append(row)
    return Table(title, ["Unit", *[n for n, _ in systems]], rows)


def inv_pool_label(kind: str) -> str:
    return {
        "sentence": "prose sentences (7+ words)",
        "number": "numbers in prose and headings",
        "heading": "headings",
        "equation_aligned": "equations aligned to gold (added symbol)",
        "equation_unaligned": "equations with no gold partner",
        "table_cell": "table cells",
    }[kind]
