"""`readablestem eval burden`: how many decisions a page asks of the person who reviews it.

Made from one measured replay of the corpus (`eval.measure`): every upload converted as the
worker converts a job, under the production profile, with the recorded answers in place of
the provider. The replay spends nothing.

What is counted. A decision is what the review screen counts, the documents list shows and
the review record totals: `core.triage.decisions`, the fixes the pipeline marked `required`,
with a group of repeated headings as one decision. It is the count before anyone reviews, so
it says what a conversion asks for, not what is left. A decision is on the page of its first
member. This module does not count: it adds up what `triage.decisions` returned.

Three figures, which answer different questions:

- decisions a page over the whole set (all decisions over all pages);
- a document's decisions a page, at the median and the 95th percentile over documents: what
  one instructor meets;
- a page's decisions, at the median and the 95th percentile over pages, by page kind
  (`measure.page_kinds`): most pages of ordinary text ask nothing, so the median page is 0
  and the load is in the pages with equations, figures and tables.

Each decision carries the reasons it was asked (`triage.tier_reasons`: a failed check's
code, the fix's own reason, a repeated heading, or `below_accept` when only the confidence
held it back). The report counts decisions by reason, which is where a flag comes from. A
decision with two reasons is counted under both, so the reasons add up to more than the
decisions.

What is left out, and said in the report: an upload whose recording does not hold every
answer it asks for (an unanswered request fails its element, and a failed element is a
decision the recording made, not the conversion), an upload this machine cannot replay, and
one whose conversion failed.

The bar (`judge`): against an earlier record of the same set, no page kind and not the whole
set asks for more decisions a page than before, by more than the allowance. Fewer decisions
is not better by itself: a fix that stops asking about something wrong lowers this figure
and raises the silent errors, which the page audit and the lane evals count. The bar is one
of two that are read together.

The corpus is a development set, chosen to cover kinds of document and not as a sample of
what people upload.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from readablestem.eval import measure
from readablestem.eval.measure import Converted
from readablestem.ops.step_report import percentile

# How many more decisions a page a record may ask for than the one before it.
ALLOWANCE = 0.0
# A page kind with fewer pages than this is printed and not held to the bar: one page that
# gains a decision would move it more than the allowance.
MIN_PAGES = 10


def page_counts(done: Converted) -> list[int]:
    """The decisions on each page of `done`, in page order."""
    counts = [0] * done.pages
    for decision in done.decisions or []:
        page = decision["page"]
        if page is not None and 0 <= page < done.pages:
            counts[page] += 1
    return counts


def _spread(values: Sequence[float]) -> dict[str, float]:
    return {
        "p50": round(percentile(values, 0.50), 4) if values else 0.0,
        "p95": round(percentile(values, 0.95), 4) if values else 0.0,
        "mean": round(sum(values) / len(values), 4) if values else 0.0,
        "max": round(max(values), 4) if values else 0.0,
    }


def report(measured: Sequence[Converted]) -> dict[str, Any]:
    """The figures of a measured replay, as plain data: the `metrics` of a burden record
    (`measure.record`), which `render` prints and `readablestem eval bar` can hold a bar
    against (`--measure per_page`)."""
    failed = sorted(done.upload for done in measured if done.error or done.decisions is None)
    short = {done.upload: done.missed for done in measured if not done.error and done.missed}
    counted = [
        done
        for done in measured
        if not done.error and not done.missed and done.pages and done.decisions is not None
    ]

    by_kind: dict[str, list[int]] = defaultdict(list)
    documents: dict[str, set[str]] = defaultdict(set)
    items: Counter[str] = Counter()
    reasons: Counter[str] = Counter()
    every_page: list[int] = []
    rows: list[dict[str, Any]] = []
    for done in counted:
        counts = page_counts(done)
        every_page += counts
        for kind, count in zip(done.kinds, counts, strict=True):
            by_kind[kind].append(count)
            documents[kind].add(done.upload)
        for decision in done.decisions or []:
            items[decision["kind"]] += 1
            reasons.update(decision["reasons"])
        rows.append(
            {
                "upload": done.upload,
                "pages": done.pages,
                "decisions": sum(counts),
                "per_page": round(sum(counts) / done.pages, 4),
            }
        )
    pages, decisions = len(every_page), sum(every_page)
    return {
        "uploads": {
            "measured": len(counted),
            "answers_missing": dict(sorted(short.items())),
            "not_measured": failed,
        },
        "pages": pages,
        "decisions": decisions,
        "per_page": round(decisions / pages, 4) if pages else 0.0,
        "document": _spread([row["per_page"] for row in rows]),
        "page": _spread(every_page)
        | {"none": round(sum(1 for n in every_page if n == 0) / pages, 4) if pages else 0.0},
        "kinds": {
            kind: {
                "pages": len(counts),
                "documents": len(documents[kind]),
                "decisions": sum(counts),
                "per_page": round(sum(counts) / len(counts), 4),
                "page": _spread(counts),
            }
            for kind, counts in sorted(by_kind.items())
        },
        "items": dict(sorted(items.items(), key=lambda pair: (-pair[1], pair[0]))),
        "reasons": {
            code: {
                "decisions": count,
                "per_100_pages": round(100 * count / pages, 2) if pages else 0.0,
            }
            for code, count in sorted(reasons.items(), key=lambda pair: (-pair[1], pair[0]))
        },
        "documents": sorted(rows, key=lambda row: (-row["per_page"], row["upload"])),
    }


@dataclass(frozen=True)
class Verdict:
    """What the bar says of a record against an earlier one."""

    judged: bool  # False: the two cannot be compared, and `why` says so
    why: str = ""
    more: list[str] = field(default_factory=list)  # one line per figure over the allowance
    same: bool = False  # the bar was "the same decisions", and `more` says where they differ

    @property
    def met(self) -> bool:
        return self.judged and not self.more


def judge(
    now: dict[str, Any],
    before: dict[str, Any],
    *,
    allowance: float = ALLOWANCE,
    same: bool = False,
) -> Verdict:
    """The bar, on two burden records: the whole set and each page kind with enough pages
    ask for no more decisions a page than in `before`, by more than `allowance`. Only two
    records of the same documents are compared.

    `same`: the bar of a record that is kept up to date with the code. Every upload measured
    now asks for as many decisions on as many pages as it did in `before`, fewer or more:
    a rule that stops asking about something wrong is as much a change as one that asks more.
    An upload that was not measured now (this machine cannot replay it) is not compared, so a
    machine with fewer recordings than the one that made `before` can still hold the bar."""
    one, other = now["metrics"], before["metrics"]
    if same:
        # The set's hash names the uploads measured, so a machine with fewer recordings has
        # another one; the uploads themselves are what is compared.
        if not one["documents"]:
            return Verdict(False, "no upload was measured, so none could be compared")
        kept = {row["upload"]: row for row in other["documents"]}
        differ = []
        for row in one["documents"]:
            was = kept.get(row["upload"])
            if was is None:
                differ.append(f"{row['upload']}: measured now, not in the record")
            elif (row["pages"], row["decisions"]) != (was["pages"], was["decisions"]):
                differ.append(
                    f"{row['upload']}: {row['decisions']} decisions on {row['pages']} pages, "
                    f"{was['decisions']} on {was['pages']} in the record"
                )
        return Verdict(True, more=differ, same=True)
    here = (now.get("split") or {}).get("set_sha256")
    there = (before.get("split") or {}).get("set_sha256")
    if not here or here != there:
        return Verdict(False, "the two records are not of the same documents")
    if one["uploads"]["measured"] != other["uploads"]["measured"]:
        return Verdict(
            False,
            f"{other['uploads']['measured']} uploads were measured then, "
            f"{one['uploads']['measured']} now",
        )
    more = []
    if one["per_page"] > other["per_page"] + allowance:
        more.append(
            f"the whole set: {one['per_page']:.3f} decisions a page, {other['per_page']:.3f} "
            f"before (allowed {allowance:+.3f})"
        )
    for kind, row in one["kinds"].items():
        earlier = other["kinds"].get(kind)
        if earlier is None or min(row["pages"], earlier["pages"]) < MIN_PAGES:
            continue
        if row["per_page"] > earlier["per_page"] + allowance:
            more.append(
                f"{kind}: {row['per_page']:.3f} decisions a page, {earlier['per_page']:.3f} "
                f"before (allowed {allowance:+.3f})"
            )
    return Verdict(True, more=more)


def _n(value: float) -> str:
    return f"{value:.2f}"


def render(record: dict[str, Any], verdict: Verdict | None = None, *, worst: int = 10) -> str:
    """A burden record as the lines `readablestem eval burden` prints."""
    made = record["metrics"]
    uploads = made["uploads"]
    lines = [
        f"decisions a page: {uploads['measured']} uploads, {made['pages']} pages, "
        f"{made['decisions']} required decisions",
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

    document, page = made["document"], made["page"]
    lines += [
        "",
        f"the whole set: {made['per_page']:.3f} decisions a page",
        f"a document's decisions a page: {_n(document['p50'])} at the median, "
        f"{_n(document['p95'])} at p95, {_n(document['max'])} at the most",
        f"a page's decisions: {_n(page['p50'])} at the median, {_n(page['p95'])} at p95, "
        f"{_n(page['max'])} at the most; {page['none']:.0%} of pages ask nothing",
        "",
        "by page kind (decisions of one page):",
    ]
    lines += measure.table(
        ("kind", "pages", "documents", "decisions", "a page", "median", "p95", "most"),
        [
            (
                kind,
                row["pages"],
                row["documents"],
                row["decisions"],
                f"{row['per_page']:.3f}",
                _n(row["page"]["p50"]),
                _n(row["page"]["p95"]),
                _n(row["page"]["max"]),
            )
            for kind, row in made["kinds"].items()
        ],
    )
    lines += ["", "by what is decided:"]
    lines += measure.table(("item", "decisions"), list(made["items"].items()))
    lines += ["", "by why it was asked (a decision with two reasons is under both):"]
    lines += measure.table(
        ("reason", "decisions", "per 100 pages"),
        [
            (code, row["decisions"], f"{row['per_100_pages']:.1f}")
            for code, row in made["reasons"].items()
        ],
    )
    lines += ["", f"the {worst} documents that ask the most a page:"]
    lines += measure.table(
        ("upload", "pages", "decisions", "a page"),
        [
            (row["upload"], row["pages"], row["decisions"], _n(row["per_page"]))
            for row in made["documents"][:worst]
        ],
    )
    if verdict is not None:
        lines.append("")
        if not verdict.judged:
            lines.append(f"The bar was not judged: {verdict.why}.")
        elif verdict.met and verdict.same:
            lines.append("The bar is met: every upload asks for the decisions the record has.")
        elif verdict.met:
            lines.append("The bar is met: no page kind asks for more decisions than before.")
        else:
            lines.append("The bar is NOT met:")
            lines += [f"  {line}" for line in verdict.more]
    return "\n".join(lines)
