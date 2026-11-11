"""`readablestem eval cost`: what a page costs, by page kind and by step.

Made from one measured replay of the corpus (`eval.measure`): every upload converted as the
worker converts a job, under the production profile, with the recorded answers in place of
the provider. The dollars are the ones the recording was made with, priced by
`models.pricing`; the replay spends nothing.

How a page gets its dollars. Every request a conversion sends is a step fact with the page it
was about (`core.types.CallFact`). A page's dollars are its requests' dollars, plus an even
share of the requests that are about the whole document, so the pages of a document add up
to the document and the kinds add up to the run. A page has one kind (`measure.page_kinds`).

What is left out, and said in the report:

- an upload whose recording does not hold every answer it asks for (`Converted.missed`): a
  refused request costs nothing, so its pages would look cheaper than they are;
- an upload this machine cannot replay (its answers are kept out of the repository), and one
  whose conversion failed.

The steps of the accuracy round that are not built or are off (`ADDED`) are listed with what
this run spent on them, which is 0 until they run, and the estimate from the round's
measurements beside it, so the cost of turning one on is on the same page. An estimate is a
figure from another set of pages; it becomes a measurement when the step's calls are in the
recording.

Two medians, which differ a great deal. Most pages of ordinary text send nothing to a
model, so the median page of the `typical` kind costs nothing, and the money is in the pages
with equations, figures and tables. A document's dollars a page (its cost divided by its
pages) spreads those over its text pages, and its median over documents is what "a typical
page costs" has meant in the audits. The report prints both and names them.

The corpus is a development set, chosen to cover kinds of document and not as a sample of
what people upload. The typical and dense figures the public pages are built from
(`ops.limits.TYPICAL_PAGE_USD`, `DENSE_PAGE_USD`) are replaced only from a replay of held-out
whole documents; the report prints them beside what this run measured.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from readablestem.eval import measure
from readablestem.eval.measure import Converted
from readablestem.ops import limits
from readablestem.ops.step_report import percentile

WHOLE_DOCUMENT = "left_out"


@dataclass(frozen=True)
class Added:
    """A step of the accuracy round: what it is expected to add, and on what evidence."""

    step: str  # the code its requests carry in the step facts
    name: str
    unit: str  # what one estimate is for
    usd: tuple[float, float] | None  # low and high estimate for one unit; None: not measured
    basis: str


# In the order the checks stop when a cap is reached, last first (the plan's WS10.13).
ADDED: tuple[Added, ...] = (
    Added(
        "page_audit",
        "page audit",
        "page",
        (0.0054, 0.010),
        "measured: $0.0054 in batch on 112 pages, $0.010 live on 37 calls",
    ),
    Added("text_check", "text check", "page", (0.0, 0.0), "measured: local, 5 ms a page"),
    Added(
        "line_strip_check",
        "line-strip check with a second read of flagged equations",
        "page dense with equations",
        (0.018, 0.018),
        "estimated from measured rates",
    ),
    Added(
        "table_read",
        "table read",
        "table",
        (0.050, 0.050),
        "measured at list price on 59 held-out tables; about $0.006 a page on papers",
    ),
    Added("figure_check", "figure checker", "figure", None, "not measured"),
    Added(
        "structure_read",
        "drawn chemical structure read",
        "structure",
        (0.0088, 0.0088),
        "measured on 112 drawings",
    ),
    Added(
        "scan_cheap_read",
        "cheap first read of a scanned page",
        "scanned page",
        (0.0008, 0.0008),
        "measured in the second round",
    ),
)


def page_dollars(done: Converted) -> list[dict[str, float]]:
    """For each page of `done`, the dollars of each step on it. A request about the whole
    document is shared evenly, so the pages add up to the requests."""
    pages: list[dict[str, float]] = [defaultdict(float) for _ in range(done.pages)]
    for call in done.facts.calls:
        if call.page is not None and 0 <= call.page < done.pages:
            pages[call.page][call.step] += call.cost_usd
        else:
            for page in pages:
                page[call.step] += call.cost_usd / done.pages
    return [dict(page) for page in pages]


def _spread(values: Sequence[float]) -> dict[str, float]:
    return {
        "p50": round(percentile(values, 0.50), 6),
        "p95": round(percentile(values, 0.95), 6),
        "mean": round(sum(values) / len(values), 6) if values else 0.0,
    }


def _reach(pool: float, low: float, high: float) -> list[int]:
    """How many pages `pool` dollars pay for, at the dearer and at the cheaper figure."""
    return [int(pool // high) if high > 0 else 0, int(pool // low) if low > 0 else 0]


def report(measured: Sequence[Converted], *, open_pool_usd: float) -> dict[str, Any]:
    """The figures of a measured replay, as plain data: the `metrics` of a cost record
    (`measure.record`), which `render` prints and `readablestem eval bar` can hold a bar
    against (`--measure kinds.typical.usd_page.p50`)."""
    failed = sorted(done.upload for done in measured if done.error)
    short = {done.upload: done.missed for done in measured if not done.error and done.missed}
    counted = [done for done in measured if not done.error and not done.missed and done.pages]

    by_kind: dict[str, list[float]] = defaultdict(list)
    by_kind_step: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    documents: dict[str, set[str]] = defaultdict(set)
    by_step: dict[str, list[float]] = defaultdict(list)  # dollars of each page the step ran on
    calls: dict[str, int] = defaultdict(int)
    models: dict[str, set[str]] = defaultdict(set)
    rows = []
    for done in counted:
        dollars = page_dollars(done)
        for kind, page in zip(done.kinds, dollars, strict=True):
            by_kind[kind].append(sum(page.values()))
            documents[kind].add(done.upload)
            for step, usd in page.items():
                by_kind_step[kind][step] += usd
                by_step[step].append(usd)
        for call in done.facts.calls:
            calls[call.step] += 1
            models[call.step].add(call.model)
        from_calls = sum(call.cost_usd for call in done.facts.calls)
        rows.append(
            {
                "upload": done.upload,
                "pages": done.pages,
                "usd": round(done.usd, 6),
                "calls": done.calls,
                # Dollars of the result that no step fact carries: 0 unless a step is not
                # reported by `pipeline.step_facts`.
                "not_in_steps": round(done.usd - from_calls, 6),
            }
        )

    pages = sum(len(values) for values in by_kind.values())
    total = sum(sum(values) for values in by_kind.values())
    kinds = {
        kind: {
            "pages": len(by_kind[kind]),
            "documents": len(documents[kind]),
            "usd": round(sum(by_kind[kind]), 6),
            "usd_page": _spread(by_kind[kind]),
            "steps": {
                step: round(usd / len(by_kind[kind]), 6)
                for step, usd in sorted(by_kind_step[kind].items())
            },
        }
        for kind in measure.KINDS
        if by_kind[kind]
    }
    steps = {
        step: {
            "models": sorted(models[step]),
            "calls": calls[step],
            "pages": sum(1 for usd in values if usd > 0),
            "usd": round(sum(values), 6),
            "usd_page_all": round(sum(values) / pages, 6) if pages else 0.0,
            "usd_page": _spread([usd for usd in values if usd > 0]),
        }
        for step, values in sorted(by_step.items(), key=lambda item: -sum(item[1]))
    }
    added = [
        {
            "step": item.step,
            "name": item.name,
            "unit": item.unit,
            "estimate_usd": list(item.usd) if item.usd else None,
            "basis": item.basis,
            "calls": calls.get(item.step, 0),
            "spent_usd": round(sum(by_step.get(item.step, ())), 6),
        }
        for item in ADDED
    ]

    a_document = _spread([row["usd"] / row["pages"] for row in rows])
    dense = kinds["dense_math"]["usd_page"] if "dense_math" in kinds else None
    audit = next(item.usd for item in ADDED if item.step == "page_audit")
    strip = next(item.usd for item in ADDED if item.step == "line_strip_check")
    assert audit is not None and strip is not None
    kept_typical = limits.TYPICAL_PAGE_USD
    kept_dense = limits.DENSE_PAGE_USD
    return {
        "uploads": {
            "measured": len(counted),
            "answers_missing": short,
            "not_measured": failed,
        },
        "pages": pages,
        "usd": round(total, 6),
        # A document's cost divided by its pages, over the documents.
        "usd_page_of_a_document": a_document,
        "kinds": kinds,
        "steps": steps,
        "added": added,
        "documents": rows,
        "constants": {
            "typical_page_usd": {"kept": kept_typical, "this_run_p50": a_document["p50"]},
            "dense_page_usd": {
                "kept": list(kept_dense),
                "this_run_p50_p95": [dense["p50"], dense["p95"]] if dense else None,
            },
        },
        # Pages a day's open pool pays for, from the kept figures: fewest and most.
        "reach": {
            "open_pool_usd": open_pool_usd,
            "typical_today": _reach(open_pool_usd, kept_typical, kept_typical)[0],
            "typical_with_page_audit": _reach(
                open_pool_usd, kept_typical + audit[0], kept_typical + audit[1]
            ),
            "dense_with_every_check": _reach(
                open_pool_usd,
                kept_dense[0] + audit[0] + strip[0],
                kept_dense[1] + audit[1] + strip[1],
            ),
        },
    }


def _usd(value: float | None) -> str:
    return "-" if value is None else f"${value:.4f}"


def _table(head: Sequence[str], rows: Sequence[Sequence[str]]) -> list[str]:
    widths = [max(len(str(row[i])) for row in (head, *rows)) for i in range(len(head))]
    lines = ["  ".join(str(cell).ljust(width) for cell, width in zip(head, widths, strict=True))]
    lines += [
        "  ".join(str(cell).ljust(width) for cell, width in zip(row, widths, strict=True))
        for row in rows
    ]
    return [line.rstrip() for line in lines]


def render(record: dict[str, Any]) -> str:
    """A cost record as the lines `readablestem eval cost` prints."""
    made = record["metrics"]
    uploads = made["uploads"]
    lines = [
        f"cost of a page: {uploads['measured']} uploads, {made['pages']} pages, "
        f"${made['usd']:.2f} in the recording",
        *measure.header(record),
    ]
    if uploads["answers_missing"]:
        owed = sum(uploads["answers_missing"].values())
        lines.append(
            f"left out, answers missing from the recording ({owed} in all): "
            + ", ".join(f"{name} ({n})" for name, n in sorted(uploads["answers_missing"].items()))
        )
    if uploads["not_measured"]:
        lines.append("not measured: " + ", ".join(uploads["not_measured"]))

    spread = made["usd_page_of_a_document"]
    lines += [
        "",
        f"a document's dollars a page: {_usd(spread['p50'])} at the median, "
        f"{_usd(spread['p95'])} at p95, {_usd(spread['mean'])} on average",
        "",
        "by page kind (dollars of one page; a page of text alone sends nothing to a model):",
    ]
    lines += _table(
        ("kind", "pages", "documents", "median", "p95", "mean", "total"),
        [
            (
                kind,
                row["pages"],
                row["documents"],
                _usd(row["usd_page"]["p50"]),
                _usd(row["usd_page"]["p95"]),
                _usd(row["usd_page"]["mean"]),
                f"${row['usd']:.2f}",
            )
            for kind, row in made["kinds"].items()
        ],
    )
    lines += ["", "by step (dollars of a page the step ran on; mean over every page):"]
    lines += _table(
        ("step", "models", "calls", "pages", "median", "p95", "every page", "total"),
        [
            (
                step,
                ", ".join(row["models"]),
                row["calls"],
                row["pages"],
                _usd(row["usd_page"]["p50"]),
                _usd(row["usd_page"]["p95"]),
                _usd(row["usd_page_all"]),
                f"${row['usd']:.2f}",
            )
            for step, row in made["steps"].items()
        ],
    )
    names = list(made["steps"])
    lines += ["", "by page kind and step (mean dollars a page):"]
    lines += _table(
        ("kind", *names),
        [
            (kind, *(_usd(row["steps"].get(step)) for step in names))
            for kind, row in made["kinds"].items()
        ],
    )
    lines += ["", "steps of the accuracy round (spent in this run; estimate for one unit):"]
    lines += _table(
        ("step", "calls", "spent", "estimate", "for one", "basis"),
        [
            (
                row["name"],
                row["calls"],
                f"${row['spent_usd']:.4f}",
                "not known"
                if row["estimate_usd"] is None
                else _usd(row["estimate_usd"][0])
                if row["estimate_usd"][0] == row["estimate_usd"][1]
                else f"{_usd(row['estimate_usd'][0])} to {_usd(row['estimate_usd'][1])}",
                row["unit"],
                row["basis"],
            )
            for row in made["added"]
        ],
    )
    kept = made["constants"]
    dense = kept["dense_page_usd"]
    measured_dense = dense["this_run_p50_p95"]
    lines += [
        "",
        "the figures the public pages are built from (ops/limits.py), beside this run:",
        f"  typical page: kept {_usd(kept['typical_page_usd']['kept'])}; "
        f"this run's median document {_usd(kept['typical_page_usd']['this_run_p50'])} a page",
        f"  page dense with equations: kept {_usd(dense['kept'][0])} to {_usd(dense['kept'][1])}; "
        "this run's median and p95 "
        + (
            f"{_usd(measured_dense[0])} and {_usd(measured_dense[1])}"
            if measured_dense
            else "not measured"
        ),
    ]
    reach = made["reach"]
    lines += [
        "",
        f"what the open pool of ${reach['open_pool_usd']:.2f} a day pays for (from the kept "
        "figures; estimates where a check is not built):",
        f"  today: about {reach['typical_today']} typical pages",
        f"  with the page audit: {reach['typical_with_page_audit'][0]} to "
        f"{reach['typical_with_page_audit'][1]} typical pages",
        f"  with every check, pages dense with equations: {reach['dense_with_every_check'][0]} "
        f"to {reach['dense_with_every_check'][1]}",
    ]
    return "\n".join(lines)
