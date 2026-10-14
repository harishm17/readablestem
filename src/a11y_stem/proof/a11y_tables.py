"""The tables of the a11y-stem arm: what the four eval tasks measured, with intervals.

Inputs are the per-item rows of `eval.pipeline_eval` (all repeats of a task pooled, a
section's counts summed over its repeats). Every headline row has:

- the pooled value and its counts;
- the cluster bootstrap interval by section (`proof.stats`), the one to quote;
- the Wilson interval, which treats items as independent and is narrower;
- the value of each run, so run-to-run variation is visible.

Which rows are primary (the ones comparisons may rest on) follows the preregistration
(section 7): end-to-end exact match of equations and unflagged wrong equations per 100 in
the document task, and heading F1. Everything else is secondary and descriptive.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import numpy as np

from a11y_stem.config import Settings
from a11y_stem.eval import pipeline_eval as pe
from a11y_stem.proof import stats
from a11y_stem.proof.goldinfo import SectionFacts, section_of_item_id
from a11y_stem.proof.report import (
    NA,
    Section,
    Table,
    fraction,
    interval,
    num,
    usd,
)
from a11y_stem.proof.runner import ArmResults, RunOutcome

Counts = dict[str, tuple[float, ...]]


@dataclass(frozen=True)
class Spec:
    key: str
    label: str
    statistic: stats.Statistic = stats.ratio
    scale: float = 1.0
    wilson: bool = True
    digits: int = 4
    primary: bool = False


@dataclass
class Collected:
    per_section: dict[str, dict[str, np.ndarray]]
    per_run: dict[str, dict[int, np.ndarray]]
    total: dict[str, np.ndarray]


def collect(
    outcomes: list[RunOutcome],
    counts: Callable[[dict[str, Any]], Counts],
    section_of: Callable[[dict[str, Any]], str],
) -> Collected:
    per_section: dict[str, dict[str, np.ndarray]] = defaultdict(dict)
    per_run: dict[str, dict[int, np.ndarray]] = defaultdict(dict)
    total: dict[str, np.ndarray] = {}
    for outcome in outcomes:
        for item in outcome.items:
            section = section_of(item)
            for key, vector in counts(item).items():
                v = np.asarray(vector, dtype=float)
                per_section[key][section] = per_section[key].get(section, 0.0) + v
                per_run[key][outcome.repeat] = per_run[key].get(outcome.repeat, 0.0) + v
                total[key] = total.get(key, 0.0) + v
    return Collected(dict(per_section), dict(per_run), total)


def _spec_row(spec: Spec, got: Collected, cfg: stats.Interval | None, n_boot: int) -> list[str]:
    total = got.total.get(spec.key)
    if total is None:
        return [spec.label, NA, NA, NA, NA, NA]
    value = spec.statistic(total)
    scaled = None if value is None else value * spec.scale
    cluster = stats.cluster_bootstrap(
        {s: tuple(v) for s, v in got.per_section[spec.key].items()},
        spec.statistic,
        n_boot=n_boot,
    )
    low = None if cluster.low is None else cluster.low * spec.scale
    high = None if cluster.high is None else cluster.high * spec.scale
    cluster_text = interval(low, high, spec.digits) if cluster.n_sections > 1 else NA
    wilson_text = NA
    if spec.wilson and spec.statistic is stats.ratio and spec.scale == 1.0:
        w = stats.wilson(total[0], total[1])
        wilson_text = interval(w[0], w[1], spec.digits) if w else NA
    per_run = " / ".join(
        num(
            None if (v := spec.statistic(vec)) is None else v * spec.scale,
            spec.digits,
        )
        for _, vec in sorted(got.per_run[spec.key].items())
    )
    count = fraction(total[0], total[1]) if len(total) == 2 else NA
    label = f"**{spec.label}**" if spec.primary else spec.label
    return [label, num(scaled, spec.digits), count, cluster_text, wilson_text, per_run]


HEADERS = [
    "Metric",
    "Pooled",
    "Count",
    "Cluster 95% (by section)",
    "Wilson 95% (items independent)",
    "Per run",
]


def metric_table(
    title: str, specs: list[Spec], got: Collected, *, n_boot: int, note: str = ""
) -> Table:
    rows = [_spec_row(s, got, None, n_boot) for s in specs]
    return Table(title, HEADERS, rows, note)


# --------------------------------------------------------------------------- document


def document_counts(g1: frozenset[str]) -> Callable[[dict[str, Any]], Counts]:
    def counts(item: dict[str, Any]) -> Counts:
        pairs = item["pairs"]
        gold, pred, matched = item["n_gold"], item["n_pred"], item["matched"]
        exact = sum(bool(p["exact_lenient"]) for p in pairs)
        exact_clean = sum(bool(p["exact_lenient"]) for p in pairs if p["gold_id"] not in g1)
        gold_g1 = sum(1 for p in pairs if p["gold_id"] in g1) + sum(
            1 for gid in item.get("missed_gold_ids", []) if gid in g1
        )
        unflagged = sum(
            1
            for p in pairs
            if not p["exact_lenient"] and p["gold_id"] not in g1 and p.get("review") != "required"
        )
        prose = item.get("unmatched_by_reason", {}).get("prose_math", 0)
        return {
            "e2e": (exact, gold),
            "e2e_clean": (exact_clean, gold - gold_g1),
            "unflagged": (unflagged, gold),
            "matched_exact": (exact, len(pairs)),
            "recall": (matched, gold),
            "precision": (matched, pred),
            "precision_clean": (matched, pred - prose),
        }

    return counts


DOCUMENT_SPECS = [
    Spec("e2e", "end_to_end_exact_lenient (all gold equations)", primary=True),
    Spec("e2e_clean", "end_to_end_exact_lenient, without G1 equations"),
    Spec(
        "unflagged",
        "unflagged_wrong_per_100 (wrong, not G1, not in a required decision)",
        scale=100.0,
        digits=2,
        primary=True,
    ),
    Spec("matched_exact", "matched_exact_lenient (aligned pairs only)"),
    Spec("recall", "detection_recall"),
    Spec("precision", "detection_precision"),
    Spec("precision_clean", "detection_precision, without prose maths"),
]


def document_section(arm: ArmResults, g1: frozenset[str], n_boot: int) -> Section:
    outcomes = arm.runs("document")
    got = collect(outcomes, document_counts(g1), lambda i: i["id"])
    pooled = pe.document_metrics(arm.items("document"))
    load = pooled["review_load"]
    table = metric_table(
        "Equations through the whole pipeline (document task)",
        DOCUMENT_SPECS,
        got,
        n_boot=n_boot,
        note=(
            "Rates have every gold equation as denominator; an equation with no aligned "
            "output counts as wrong. G1 equations (operators packed into OpenStax text) can "
            "fail exact match even when the answer is right. `unflagged_wrong_per_100` is "
            "wrong aligned equations that no required decision pointed at, per 100 gold "
            "equations."
        ),
    )
    review = Table(
        "Review load (what a person is asked to decide)",
        ["Measure", "Value"],
        [
            ["PDF pages converted", num(load["pdf_pages"])],
            ["Required decisions", num(load["required_decisions"])],
            ["Required fixes (several can share one decision)", num(load["required_fixes"])],
            ["**Required decisions per PDF page**", f"**{num(load['required_per_pdf_page'])}**"],
            ["Optional notes per PDF page", num(load["optional_per_pdf_page"])],
            ["Equation flags: precision", num(load["equation_flags"]["flag_precision"])],
            ["Equation flags: recall", num(load["equation_flags"]["flag_recall"])],
            ["Heading flags: precision", num(load["heading_flags"]["flag_precision"])],
            ["Heading flags: recall", num(load["heading_flags"]["flag_recall"])],
            ["Wrong items not asked about", num(len(load["wrong_not_required"]))],
        ],
        note=(
            "Flag precision is the share of required decisions that point at a real error; "
            "recall is the share of real errors a person is asked about. Both are measured "
            "against the gold set only; figures and equations with no gold partner have no "
            "answer key here."
        ),
    )
    reasons = load.get("required_by_reason", {})
    blocks: list[str | Table] = [table, review]
    if reasons:
        blocks.append(
            Table(
                "Reasons behind the required fixes",
                ["Reason", "Fixes"],
                [[k, num(v)] for k, v in sorted(reasons.items())],
                note="Counted per fix, and a fix can have several reasons, so the counts "
                "add up to more than the number of required decisions.",
            )
        )
    unmatched = pooled["unmatched_by_reason"]
    blocks.append(
        Table(
            "Predicted equations with no gold partner",
            ["Kind", "Count"],
            [[k, num(unmatched[k])] for k in pe.UNMATCHED_REASONS],
            note="`prose_math`: maths the source wrote as plain text, so the gold has no "
            "MathML for it; `fragment`: a piece of a gold equation or of a broken formula.",
        )
    )
    return Section("a11y-stem on the whole section (document task)", blocks, level=3)


def section_table(
    arm: ArmResults, facts: list[SectionFacts], g1: frozenset[str], title: str
) -> Table:
    """One row per section, repeats pooled: how the document task did where."""
    by_section: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for item in arm.items("document"):
        row = by_section[item["id"]]
        counts = document_counts(g1)(item)
        row["exact"] += counts["e2e"][0]
        row["gold"] += counts["e2e"][1]
        row["pages"] = max(row["pages"], item.get("pdf_pages", 0))
        row["required"] += item.get("review", {}).get("required_decisions", 0)
        row["calls"] += item.get("model_calls", 0)
        row["cost"] += item.get("cost_usd", 0.0) + item.get("verifier_cost_usd", 0.0)
        if item.get("error"):
            row["failed"] += 1
    rows = []
    for fact in facts:
        row = by_section.get(fact.id)
        if row is None:
            rows.append([fact.id, *[NA] * 7])
            continue
        rate = row["exact"] / row["gold"] if row["gold"] else None
        rows.append(
            [
                fact.id,
                num(int(row["pages"])),
                num(int(row["gold"])),
                num(int(row["exact"])),
                num(rate),
                num(int(row["required"])),
                num(int(row["calls"])),
                usd(row["cost"]),
            ]
        )
    return Table(
        title,
        ["Section", "PDF pages", "Gold equations", "Exact", "Rate", "Required", "Calls", "Cost"],
        rows,
        note="Counts are summed over the runs. Cost includes the second automated check.",
    )


def book_table(arm: ArmResults, facts: list[SectionFacts], g1: frozenset[str]) -> Table:
    """Per book, point estimates only (three sections each: no interval is meaningful)."""
    book_of = {f.id: f.id.split("/")[0] for f in facts}
    title_of = {f.id.split("/")[0]: f.book_title for f in facts}
    eq: dict[str, list[float]] = defaultdict(lambda: [0.0, 0.0])
    hd: dict[str, list[float]] = defaultdict(lambda: [0.0, 0.0, 0.0])
    for item in arm.items("document"):
        c = document_counts(g1)(item)["e2e"]
        book = book_of.get(item["id"], item["id"].split("/")[0])
        eq[book][0] += c[0]
        eq[book][1] += c[1]
    for item in arm.items("headings"):
        book = book_of.get(item["id"], item["id"].split("/")[0])
        hd[book][0] += item["matched"]
        hd[book][1] += item["n_pred"]
        hd[book][2] += item["n_gold"]
    rows = []
    for book in dict.fromkeys(book_of.values()):
        e = eq.get(book, [0.0, 0.0])
        h = hd.get(book, [0.0, 0.0, 0.0])
        f1 = stats.f1_of_counts(np.asarray(h))
        rows.append(
            [
                title_of.get(book, book),
                num(sum(1 for f in facts if book_of[f.id] == book)),
                num(e[0] / e[1] if e[1] else None),
                fraction(e[0], e[1]),
                num(f1),
            ]
        )
    return Table(
        "Per book (point estimates; each rests on a few sections)",
        ["Book", "Sections", "End-to-end exact (lenient)", "Count", "Heading F1"],
        rows,
    )


# --------------------------------------------------------------------------- equations


def equation_counts(g1: frozenset[str]) -> Callable[[dict[str, Any]], Counts]:
    def counts(item: dict[str, Any]) -> Counts:
        clean = item["id"] not in g1
        return {
            "lenient": (float(item["exact_lenient"]), 1),
            "lenient_clean": (float(item["exact_lenient"]) if clean else 0.0, 1 if clean else 0),
            "strict": (float(item["exact_strict"]), 1),
            "teds": (float(item["teds"]), 1),
            "proposal": (float(item["has_proposal"]), 1),
            "unflagged": (
                float(item["wrong"] and item["id"] not in g1 and item.get("review") != "required"),
                1,
            ),
        }

    return counts


EQUATION_SPECS = [
    Spec("lenient", "exact_lenient", primary=False),
    Spec("lenient_clean", "exact_lenient, without G1 equations"),
    Spec("strict", "exact_strict"),
    Spec("teds", "mean_teds", wilson=False),
    Spec("proposal", "proposal_rate"),
    Spec("unflagged", "unflagged wrong per 100 equations", scale=100.0, digits=2),
]


def equations_section(arm: ArmResults, g1: frozenset[str], n_boot: int) -> Section:
    got = collect(arm.runs("equations"), equation_counts(g1), lambda i: section_of_item_id(i["id"]))
    items = arm.items("equations")
    metrics = pe.equation_metrics(items)
    table = metric_table(
        "Each gold equation as a picture (equations task)",
        EQUATION_SPECS,
        got,
        n_boot=n_boot,
        note="Each equation is rendered to an image with its text layer and its surrounding "
        "text, as the whole-document run would give it. The picture comes from a browser "
        "print of the gold MathML, so it is cleaner than a crop from a real PDF.",
    )
    status = Table(
        "By status the pipeline gave the answer",
        ["Status", "n", "Share", "exact_lenient", "mean TEDS"],
        [
            [k, num(v["n"]), num(v["share"]), num(v["exact_lenient"]), num(v["mean_teds"])]
            for k, v in metrics["by_status"].items()
        ],
        note="Accepted answers should be more accurate than answers sent to review.",
    )
    display = Table(
        "By display",
        ["Display", "n", "Share", "exact_lenient", "mean TEDS"],
        [
            [k, num(v["n"]), num(v["share"]), num(v["exact_lenient"]), num(v["mean_teds"])]
            for k, v in metrics["by_display"].items()
        ],
    )
    return Section("Equations as pictures (equations task)", [table, status, display], level=3)


# ---------------------------------------------------------------------------- headings


def heading_counts(item: dict[str, Any]) -> Counts:
    return {
        "precision": (item["matched"], item["n_pred"]),
        "recall": (item["matched"], item["n_gold"]),
        "f1": (item["matched"], item["n_pred"], item["n_gold"]),
        "level": (item["level_correct"], item["matched"]),
    }


HEADING_SPECS = [
    Spec("f1", "heading F1", statistic=stats.f1_of_counts, primary=True),
    Spec("precision", "heading precision"),
    Spec("recall", "heading recall"),
    Spec("level", "level_accuracy (among matched headings)"),
]


def headings_section(arm: ArmResults, n_boot: int) -> Section:
    got = collect(arm.runs("headings"), heading_counts, lambda i: i["id"])
    metrics = pe.heading_metrics(arm.items("headings"))
    table = metric_table(
        "Headings found in the printed sections (headings task)",
        HEADING_SPECS,
        got,
        n_boot=n_boot,
        note="A PDF has no heading levels, so level accuracy compares each heading's level "
        "relative to the top level on its page. The headings task makes no model call and "
        "runs once. Most gold headings sit inside boxes (examples, notes, exercises); see "
        "recall by context.",
    )
    context = Table(
        "Recall by where the heading sits",
        ["Context", "Gold headings", "Recall"],
        [[k, num(v["n"]), num(v["recall"])] for k, v in metrics["recall_by_context"].items()],
    )
    errors = metrics["page_errors"]
    blocks: list[str | Table] = [table, context]
    if errors:
        blocks.append(
            "Sections the layout could not read (they score as found nothing): "
            + ", ".join(f"{code} x {n}" for code, n in errors.items())
        )
    return Section("Headings (headings task)", blocks, level=3)


# ----------------------------------------------------------------------------- figures


def figure_counts(item: dict[str, Any]) -> Counts:
    measured = item["judge_outcome"] in {"judged", "no_proposal"}
    judged = item["judge_outcome"] == "judged"
    return {
        "judge_pass": (float(bool(item["judge_pass"])) if measured else 0.0, 1 if measured else 0),
        "factual": (
            float(bool(item["judge_factual_error"])) if judged else 0.0,
            1 if judged else 0,
        ),
        "proposal": (float(item["has_proposal"]), 1),
        "checks": (float(item["checks_passed"]), 1),
    }


FIGURE_SPECS = [
    Spec("judge_pass", "judge_pass_rate (agreement with a model judge)"),
    Spec("factual", "judge_factual_error_rate (among judged)"),
    Spec("proposal", "proposal_rate"),
    Spec("checks", "check_pass_rate (the step's own deterministic checks)"),
]


def figures_section(arm: ArmResults, n_boot: int, judge_model: str = "gpt-6.1-sol") -> Section:
    got = collect(arm.runs("figures"), figure_counts, lambda i: section_of_item_id(i["id"]))
    metrics = pe.figure_metrics(arm.items("figures"))
    table = metric_table(
        "Alt text for each figure with an OpenStax reference (figures task)",
        FIGURE_SPECS,
        got,
        n_boot=n_boot,
        note=f"The judge is a model ({judge_model}) that sees the image, the candidate, the "
        "OpenStax alt text and the caption, never which system wrote the candidate. Its "
        "pass rate is agreement with that judge, not accuracy; it is not yet calibrated "
        "against human labels. A figure with no proposal counts as a fail. Judge cost is "
        "reported apart.",
    )
    by_status = metrics.get("by_status", {})
    blocks: list[str | Table] = [table]
    if by_status:
        blocks.append(
            Table(
                "Figures by status",
                ["Status", "n"],
                [[k, num(v)] for k, v in by_status.items()],
            )
        )
    return Section("Figure descriptions (figures task)", blocks, level=3)


# --------------------------------------------------------------------- cost and time


def _per_page(values: list[float]) -> list[str]:
    if not values:
        return [NA] * 4
    return [
        num(pe.percentile(values, 50), 5),
        num(pe.percentile(values, 95), 5),
        num(min(values), 5),
        num(max(values), 5),
    ]


def cost_section(arm: ArmResults, bundle_runs: list[dict[str, Any]], settings: Settings) -> Section:
    rows = []
    for outcome in arm.outcomes:
        cost = outcome.report.get("cost", {})
        rows.append(
            [
                f"{outcome.task} run {outcome.repeat}",
                num(outcome.report.get("n")),
                num(cost.get("model_calls")),
                usd(cost.get("pipeline_usd")),
                usd(cost.get("judge_usd")) if "judge_usd" in cost else NA,
                usd(cost.get("per_pdf_page_usd"), 5) if "per_pdf_page_usd" in cost else NA,
            ]
        )
    cost_table = Table(
        "Spend by run (list prices; models.pricing)",
        [
            "Run",
            "Items",
            "Model calls",
            "Pipeline cost",
            "Judge cost",
            "Pipeline cost per PDF page",
        ],
        rows,
        note="Pipeline cost includes the second automated check. Judge cost is never counted "
        "as the pipeline's. The per-page figure is for the document task only.",
    )
    # Per PDF page cost per section and run, from the document items.
    cost_pages = []
    for outcome in arm.runs("document"):
        for item in outcome.items:
            pages = item.get("pdf_pages", 0)
            if pages:
                cost_pages.append((item["cost_usd"] + item.get("verifier_cost_usd", 0.0)) / pages)
    per_page = Table(
        "Cost per PDF page across sections and runs (document task)",
        ["Median", "p95", "Min", "Max"],
        [_per_page(cost_pages)],
    )
    times = []
    for run in bundle_runs:
        if run["task"] != "document":
            continue
        for t in run.get("timing", []):
            if t["pdf_pages"]:
                times.append(t["latency_ms"] / 1000.0 / t["pdf_pages"])
    time_table = Table(
        "Seconds per PDF page, as measured in the recorded live run (document task)",
        ["Median", "p95", "Min", "Max"],
        [_per_page(times)],
        note="Wall clock of each section's conversion divided by its PDF pages, with the "
        "production concurrency (4 threads) on the recording machine. A replay cannot "
        "measure this, so it is read from the bundle.",
    )
    limits = _limits_table(arm, settings)
    blocks: list[str | Table] = [cost_table, per_page, time_table]
    if limits is not None:
        blocks.append(limits)
    return Section("Cost and time", blocks, level=3)


def _limits_table(arm: ArmResults, settings: Settings) -> Table | None:
    """Sections that would have hit the app's per-job limits (calls, dollars) for their
    page count: the app would have stopped them part-way."""
    from a11y_stem.web.worker import job_caps

    over = []
    for outcome in arm.runs("document"):
        for item in outcome.items:
            pages = item.get("pdf_pages", 0)
            if not pages:
                continue
            usd_cap, calls_cap = job_caps(settings, pages)
            spent = item["cost_usd"] + item.get("verifier_cost_usd", 0.0)
            if spent > usd_cap or item.get("model_calls", 0) > calls_cap:
                over.append(
                    [
                        item["id"],
                        f"run {outcome.repeat}",
                        num(pages),
                        f"{item.get('model_calls', 0)} / {calls_cap}",
                        f"{usd(spent)} / {usd(usd_cap, 2)}",
                    ]
                )
    return Table(
        "Sections over the app's per-job limits (production-configuration failures)",
        ["Section", "Run", "PDF pages", "Calls / limit", "Cost / limit"],
        over or [["none", "", "", "", ""]],
        note="The eval spends from one run-level budget and does not apply the app's per-job "
        "limits; a section listed here would have stopped part-way in the app.",
    )


def failures_section(arm: ArmResults) -> Section:
    """Everything that did not go through, by task."""
    rows = []
    for outcome in arm.outcomes:
        errors = outcome.report.get("metrics", {}).get("page_errors", {})
        rows.append(
            [
                f"{outcome.task} run {outcome.repeat}",
                "yes" if outcome.complete else "no",
                str(outcome.report.get("stopped_reason") or "none"),
                ", ".join(f"{k} x {v}" for k, v in errors.items()) or "none",
            ]
        )
    return Section(
        "Runs that did not finish, and sections that failed",
        [
            Table(
                "Completion by run",
                ["Run", "Complete", "Stopped because", "Sections the layout could not read"],
                rows,
            )
        ],
        level=3,
    )


def status_counter(items: list[dict[str, Any]]) -> Counter[str]:
    return Counter(str(i.get("status")) for i in items)
