"""`readablestem eval pictures`: A18's count of pictures a student does not get converted.

Made from the same measured replay as `eval outputs` (`eval.measure`, the recorded answers in
place of the provider, nothing spent): each conversion's stored result is read for what A18
asks, with no model and no new conversion step. Per document and added up:

- the pictures not converted, by reason: a figure shown as the picture of the original, named
  by why (`handwriting_on_page`, `scanned_page`, a failed or rejected answer, no description
  proposed ...). Equations, tables and paragraphs shown as pictures are counted apart: they
  are not images of the page's own;
- the content pictures with empty alt text on the student page: no alt, or an empty one, on a
  picture the page does not mark as decoration (the audit's `out_alt_missing` on the page);
- the regions called handwriting, each with its page and box (numbers, never words), and the
  notices that go with them;
- against a truth list (`data/eval/pen-truth.json`): of the regions called handwriting, how
  many were seen as pen strokes, how many as something else (typeset or vector drawings,
  glyph outlines: the count that must be 0), and how many nobody has looked at yet.

The truth list is written by someone looking at a render of the region, not derived from the
rule that makes the call: a list built from the pen rule would score the rule against itself.
It holds one row per region: the document, the 1-based page, the box in points, `pen` or `not_pen`,
and who looked. A region matches a row on the same page whose box overlaps it by at least
half (intersection over union), so a change of crop does not lose the label.

Counts only: nothing of a document's words is kept. Nothing here gates a release or changes
an output. The corpus is a development set.
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

from readablestem.core.types import KEPT_ORIGINAL_STATUSES, DocumentResult, Fix, Kind
from readablestem.eval import measure
from readablestem.eval.measure import Converted
from readablestem.pipeline import checked, output_audit
from readablestem.pipeline.notices import HANDWRITING_ON_PAGE, HANDWRITTEN_PAGE, KEPT_IMAGE_CODES

TRUTH = Path(__file__).resolve().parents[3] / "data" / "eval" / "pen-truth.json"
PEN, NOT_PEN = "pen", "not_pen"
# A box is the same region when it overlaps the labeled one by at least this much.
MATCH = 0.5
# The reason of a part that is a figure shown as its picture and has none of its own: the
# fix said nothing (`no_answer`), proposed nothing (`no_proposal`), or proposed no alt text
# (`no_description`); `unproven_decoration` is a "decorative" answer the page does not take.
NO_ANSWER, NO_PROPOSAL, NO_DESCRIPTION, UNPROVEN = (
    "no_answer",
    "no_proposal",
    "no_description",
    "unproven_decoration",
)
# What goes with them: the notices of a figure kept as an image, and the one that says the
# drawings of a page were not looked at for handwriting.
NOTICES = KEPT_IMAGE_CODES | {"drawings_not_analysed"}
IMAGE_KINDS = frozenset({"figure", "page", "handwriting"})


def audit_set(upload: str) -> bool:
    """Whether an upload is one of the 39 audit documents of A18 (AQ4): the public `r` files
    and the self-made `s` files of the corpus, as the corpus held them then (`s05`, the form,
    came later). Variants of a file (`name+variant`) are not."""
    return upload[:1] in ("r", "s") and upload[1:3].isdigit() and upload != "s05_form_acroform"


def _reason(fix: Fix | None) -> str:
    """Why a figure is shown as its picture."""
    if fix is None:
        return NO_ANSWER
    if fix.status in KEPT_ORIGINAL_STATUSES:
        return fix.reason or fix.status.value
    if fix.figure is None:
        return NO_PROPOSAL
    return UNPROVEN if fix.figure.decorative else NO_DESCRIPTION


def count_document(result: DocumentResult) -> dict[str, Any]:
    """A18's counts of one converted document, as plain data with none of its words."""
    summary = checked.of_result(result).converted
    first: dict[str, Fix] = {}
    for fix in result.fixes:
        first.setdefault(fix.element_id, fix)  # first wins, like DocumentResult.fix_for
    reasons: Counter[str] = Counter()
    other: Counter[str] = Counter()
    for picture in summary.pictures:
        if picture.as_text:
            continue
        if picture.kind in IMAGE_KINDS:
            reasons[_reason(first.get(picture.element_id))] += 1
        else:
            other[picture.kind] += 1
    figures = [
        (element, first.get(element.id))
        for element in sorted(result.elements, key=lambda e: e.order)
        if element.kind is Kind.FIGURE
    ]
    regions = [
        {"page": element.page + 1, "bbox": [round(v, 1) for v in element.bbox]}
        for element, fix in figures
        if fix is not None and fix.reason == HANDWRITING_ON_PAGE
    ]
    notices: Counter[str] = Counter()
    for notice in result.notices:
        if notice.code in NOTICES:
            notices[notice.code] += notice.count
    page = output_audit.read_html(result.html)
    return {
        "images": summary.figures,
        "not_converted": dict(sorted(reasons.items())),
        "other_pictures": dict(sorted(other.items())),
        "empty_alt": sum(
            1 for p in page.pictures if not p.decorative and not (p.alt or "").strip()
        ),
        "decorative": sum(1 for p in page.pictures if p.decorative),
        "handwriting_regions": regions,
        "handwritten_pages": sum(
            1 for _, fix in figures if fix is not None and fix.reason == HANDWRITTEN_PAGE
        ),
        "notices": dict(sorted(notices.items())),
    }


# ----- the truth list


def read_truth(path: Path = TRUTH) -> list[dict[str, Any]]:
    """The rows of the truth list: each a region someone looked at (document, page, bbox,
    label `pen` or `not_pen`, seen_by)."""
    rows = json.loads(path.read_text(encoding="utf-8"))["regions"]
    for row in rows:
        if row["label"] not in (PEN, NOT_PEN):
            raise ValueError(f"truth list: label of {row['document']} page {row['page']}")
    return rows


def _overlap(one: Sequence[float], two: Sequence[float]) -> float:
    """Intersection over union of two boxes."""
    wide = min(one[2], two[2]) - max(one[0], two[0])
    high = min(one[3], two[3]) - max(one[1], two[1])
    if wide <= 0 or high <= 0:
        return 0.0
    both = wide * high
    area = lambda b: max(b[2] - b[0], 0.0) * max(b[3] - b[1], 0.0)  # noqa: E731
    return both / (area(one) + area(two) - both)


def against_truth(
    called: Mapping[str, Sequence[Mapping[str, Any]]], truth: Iterable[Mapping[str, Any]]
) -> dict[str, Any]:
    """The regions called handwriting (`called`: by upload, each with page and bbox) against
    the truth list. `pen`: called and seen as pen strokes. `not_pen`: called and seen as
    something else (a typeset or vector drawing, a glyph outline): the count that must be 0.
    `unlabeled`: called and not looked at yet, listed by upload, page and box for whoever
    labels them. `missed`: seen as pen strokes and not called, by upload."""
    rows = list(truth)
    verdict: Counter[str] = Counter()
    unlabeled: list[dict[str, Any]] = []
    used: set[int] = set()
    for upload, regions in called.items():
        for region in regions:
            best, found = MATCH, None
            for index, row in enumerate(rows):
                if row["document"] != upload or row["page"] != region["page"]:
                    continue
                score = _overlap(row["bbox"], region["bbox"])
                if score >= best:
                    best, found = score, index
            if found is None:
                verdict["unlabeled"] += 1
                unlabeled.append({"upload": upload, **region})
            else:
                used.add(found)
                verdict[rows[found]["label"]] += 1
    missed = Counter(
        row["document"]
        for index, row in enumerate(rows)
        if row["label"] == PEN and index not in used and row["document"] in called
    )
    return {
        PEN: verdict[PEN],
        NOT_PEN: verdict[NOT_PEN],
        "unlabeled": verdict["unlabeled"],
        "missed": dict(sorted(missed.items())),
        "unlabeled_regions": unlabeled,
        "labeled": len(rows),
    }


# ----- the report

_TOTALS = ("images", "empty_alt", "decorative", "handwritten_pages")


def _sum(counts: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """The counts of some documents, added up."""
    held = list(counts)
    total: dict[str, Any] = {
        "documents": len(held),
        **{name: sum(c[name] for c in held) for name in _TOTALS},
        "handwriting_regions": sum(len(c["handwriting_regions"]) for c in held),
    }
    for name in ("not_converted", "other_pictures", "notices"):
        added: Counter[str] = Counter()
        for c in held:
            added.update(c[name])
        total[name] = dict(sorted(added.items()))
    total["not_converted_all"] = sum(total["not_converted"].values())
    return total


def report(
    measured: Sequence[Converted], truth: Iterable[Mapping[str, Any]] = ()
) -> dict[str, Any]:
    """The figures of a measured replay, as plain data: the `metrics` of a pictures record
    (`measure.record`), which `render` prints. Under `sets`, the audit documents and the
    rest, each with `whole` (the uploads whose recording holds every answer) and `apart`
    (those lacking answers, as in `eval outputs`: an unanswered picture is kept as a picture
    with a flag, so their "not converted" counts are not production's). The regions called
    handwriting are made by layout before any model step: the truth list is read against
    every document, answered or not."""
    counted: dict[str, dict[str, Any]] = {}
    answers_missing: dict[str, int] = {}
    not_counted: list[str] = []
    for done in measured:
        found = (done.outputs or {}).get("pictures")
        if done.error or found is None:
            not_counted.append(done.upload)
            continue
        counted[done.upload] = found
        if done.missed:
            answers_missing[done.upload] = done.missed
    sets = {
        name: {
            kind: _sum(
                found
                for upload, found in counted.items()
                if audit_set(upload) == (name == "audit") and (upload in answers_missing) == apart
            )
            for kind, apart in (("whole", False), ("apart", True))
        }
        for name in ("audit", "rest")
    }
    return {
        "uploads": {
            "counted": len(counted),
            "answers_missing": dict(sorted(answers_missing.items())),
            "not_counted": sorted(not_counted),
        },
        "sets": sets,
        "by_upload": {name: found for name, found in sorted(counted.items())},
        "truth": against_truth(
            {name: found["handwriting_regions"] for name, found in counted.items()},
            truth,
        ),
    }


def _line(name: str, found: Mapping[str, Any]) -> tuple[Any, ...]:
    """A row of the tables: one document's counts, or a set's added up."""
    regions = found["handwriting_regions"]
    return (
        name,
        found["images"],
        sum(found["not_converted"].values()),
        found["empty_alt"],
        regions if isinstance(regions, int) else len(regions),
        found["handwritten_pages"],
    )


def render(record: dict[str, Any], worst: int = 15) -> str:
    """A pictures record as text. `worst`: how many documents to name."""
    metrics = record["metrics"]
    sets, truth = metrics["sets"], metrics["truth"]
    heads = ("images", "not converted", "empty alt", "handwriting regions", "handwritten pages")
    lines = [
        f"pictures not converted: {metrics['uploads']['counted']} uploads counted",
        *measure.header(record),
    ]
    if metrics["uploads"]["not_counted"]:
        lines.append("not counted: " + ", ".join(metrics["uploads"]["not_counted"]))
    lines += ["", "the 39 audit documents (the r and s files) and the rest of the corpus:"]
    rows = [
        (f"{name}, {kind}", sets[name][kind]["documents"], *_line("", sets[name][kind])[1:])
        for name in ("audit", "rest")
        for kind in ("whole", "apart")
    ]
    lines += measure.table(("set", "documents", *heads), rows)
    lines.append(
        "(apart: the recording lacks answers; a picture asked about is kept with a flag, and "
        "model_refused there is the replay's stand-in for an answer it does not hold)"
    )
    for name in ("audit", "rest"):
        for kind in ("whole", "apart"):
            held = sets[name][kind]
            if not held["documents"]:
                continue
            lines += ["", f"{name}, {kind}: images not converted, by reason"]
            lines += [f"  {why}: {n}" for why, n in held["not_converted"].items()] or ["  none"]
            if held["other_pictures"]:
                lines.append(
                    "  other parts shown as a picture: "
                    + ", ".join(f"{part} {n}" for part, n in held["other_pictures"].items())
                )
            if held["notices"]:
                lines.append(
                    "  notices: " + ", ".join(f"{code} {n}" for code, n in held["notices"].items())
                )
    lines += [
        "",
        "regions called handwriting in the corpus: "
        f"seen as pen strokes {truth[PEN]}, as something else {truth[NOT_PEN]} (must be 0), "
        f"not looked at yet {truth['unlabeled']} ({truth['labeled']} rows in the truth list)",
    ]
    if truth["missed"]:
        lines.append(
            "labeled pen, not called: "
            + ", ".join(f"{name} {n}" for name, n in truth["missed"].items())
        )
    named = sorted(
        (
            (name, found)
            for name, found in metrics["by_upload"].items()
            if audit_set(name)
            and (found["not_converted"] or found["empty_alt"] or found["handwriting_regions"])
        ),
        key=lambda row: (-sum(row[1]["not_converted"].values()), -row[1]["empty_alt"], row[0]),
    )[:worst]
    if named:
        lines += ["", "audit documents with the most pictures not converted, empty alt or regions:"]
        apart = metrics["uploads"]["answers_missing"]
        lines += measure.table(
            ("upload", *heads, "answers missing"),
            [(*_line(name, found), apart.get(name, 0)) for name, found in named],
        )
    return "\n".join(lines)
