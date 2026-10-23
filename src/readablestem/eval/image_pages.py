"""What the image page pass adds to a document-task item and to its report (ADR 0007).

The existing document scorers (`pipeline_eval.document_page_item`) already score the
equations, headings, review load and cost of a converted scan against the answer key; this
module adds what only the image page pass has:

- per section: the pass's own record (pages read and kept, calls, tokens, dollars, blocks by
  kind, `[illegible]` markers, blocks the reader was unsure of, decisions and notes the
  pages start with), and one row per matched equation saying whether it was right, in which
  way, and whether a person is asked about it (`flags`);
- over the sections: the pooled numbers, the cost and the time per page, how the model used
  its confidence, and the review flags' precision and recall for equations.

An equation is *asked about* when it is a required decision. A display equation is one fix
and is required on every image page (it has no text layer to be checked against); an inline
formula has no decision of its own and rides with its paragraph: it is asked about when its
paragraph is required (ADR 0007, "Trust"). A matched equation is *wrong* when its MathML is
not the answer key's under the lenient comparison (`exact_lenient`, the primary score) and
*wrong in the symbols* when its letters, digits and operators differ (`exact_symbols`, the
secondary one). Only equations that were matched to an answer-key equation are counted: an
equation the pass found where the key has none, or never found, is in the detection numbers.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from typing import Any

from readablestem.core.types import DocumentResult, Fix

PAGE_READING = "equation.page_reading"


def _safe_div(a: float, b: float) -> float | None:
    return a / b if b else None


def _asked_about(element_id: str, result: DocumentResult, fixes: Mapping[str, Fix]) -> bool:
    element = next((e for e in result.elements if e.id == element_id), None)
    fix = fixes.get(element_id)
    if element is not None and element.parent_id is not None:  # inline: its paragraph's item
        fix = fixes.get(element.parent_id)
    return fix is not None and fix.review == "required"


def _agreed(fix: Fix | None) -> bool | None:
    """Did the crop's reading and the page reader's meet? None when there was no second
    reading (an inline formula, or a box the pass could not use)."""
    if fix is None:
        return None
    for check in fix.checks:
        if check.name == PAGE_READING:
            return check.passed
    return None


def item_for(result: DocumentResult, pairs: Sequence[Mapping[str, object]]) -> dict | None:
    """The record of one section (None when the image page pass did not run)."""
    summary = result.image_pages
    if summary is None:
        return None
    fixes = {f.element_id: f for f in result.fixes}
    flags = []
    for pair in pairs:
        element_id = str(pair["element_id"])
        fix = fixes.get(element_id)
        flags.append(
            {
                "gold_id": pair["gold_id"],
                "display": pair.get("pred_display"),
                "wrong": not pair["exact_lenient"],
                "wrong_symbols": not pair.get("exact_symbols", pair["exact_lenient"]),
                "asked": _asked_about(element_id, result, fixes),
                "agreed": _agreed(fix),
            }
        )
    records = summary.per_page
    by_kind: Counter[str] = Counter()
    for record in records:
        by_kind.update(record.by_kind)
    return {
        "pages": summary.pages,
        "converted": summary.converted,
        "fell_back": summary.fell_back,
        "outcomes": dict(Counter(r.outcome for r in records)),
        "calls": summary.calls,
        "cost_usd": summary.cost_usd,
        "latency_ms": sum(r.latency_ms for r in records),
        "input_tokens": sum(r.input_tokens for r in records),
        "output_tokens": sum(r.output_tokens for r in records),
        "blocks": summary.blocks,
        "by_kind": dict(sorted(by_kind.items())),
        "illegible": sum(r.illegible for r in records),
        "low_confidence": sum(r.low_confidence for r in records),
        "kept_as_image": sum(r.kept_as_image for r in records),
        "omitted": sum(r.omitted for r in records),
        "omitted_pages": sum(1 for r in records if r.omitted),
        "required": sum(r.required for r in records),
        "optional": sum(r.optional for r in records),
        "models": sorted({r.model for r in records if r.model}),
        "flags": flags,
    }


def _flag_numbers(rows: Sequence[Mapping[str, Any]], *, key: str, gold: int) -> dict:
    """Precision and recall of "asked about" for equations wrong by `key` ("wrong" or
    "wrong_symbols"), and the wrong ones nobody is asked about per 100 answer-key equations."""
    wrong = [r for r in rows if r[key]]
    asked = [r for r in rows if r["asked"]]
    wrong_asked = [r for r in wrong if r["asked"]]
    return {
        "matched": len(rows),
        "wrong": len(wrong),
        "asked": len(asked),
        "precision": _safe_div(len(wrong_asked), len(asked)),
        "recall": _safe_div(len(wrong_asked), len(wrong)),
        "wrong_not_asked": len(wrong) - len(wrong_asked),
        "wrong_not_asked_per_100_gold": _safe_div(100 * (len(wrong) - len(wrong_asked)), gold),
    }


def metrics(items: Sequence[Mapping[str, Any]], gold_equations: int) -> dict | None:
    """The pooled numbers of every item that has an image page record (None when none has)."""
    rows: list[dict[str, Any]] = [
        i["image_pages"] for i in items if isinstance(i.get("image_pages"), dict)
    ]
    if not rows:
        return None
    pages = sum(r["pages"] for r in rows)
    converted = sum(r["converted"] for r in rows)
    cost = sum(r["cost_usd"] for r in rows)
    latency = sum(r["latency_ms"] for r in rows)
    flags = [f for r in rows for f in r["flags"]]
    display = [f for f in flags if f["display"] == "block"]
    inline = [f for f in flags if f["display"] == "inline"]
    agreed = [f for f in display if f["agreed"] is True]
    disagreed = [f for f in display if f["agreed"] is False]
    outcomes: Counter[str] = Counter()
    for r in rows:
        outcomes.update(r["outcomes"])
    blocks = sum(r["blocks"] for r in rows)
    return {
        "pages": pages,
        "converted": converted,
        "fell_back": pages - converted,
        "outcomes": dict(sorted(outcomes.items())),
        "calls": sum(r["calls"] for r in rows),
        "cost_usd": cost,
        "cost_per_page_usd": _safe_div(cost, pages),
        "seconds_per_page": _safe_div(latency / 1000, pages),
        "input_tokens_per_page": _safe_div(sum(r["input_tokens"] for r in rows), pages),
        "output_tokens_per_page": _safe_div(sum(r["output_tokens"] for r in rows), pages),
        "blocks": blocks,
        "blocks_per_page": _safe_div(blocks, converted),
        "by_kind": dict(sorted(sum((Counter(r["by_kind"]) for r in rows), Counter()).items())),
        "illegible": sum(r["illegible"] for r in rows),
        "low_confidence_share": _safe_div(sum(r["low_confidence"] for r in rows), blocks),
        "kept_as_image": sum(r["kept_as_image"] for r in rows),
        "omitted": sum(r.get("omitted", 0) for r in rows),
        "omitted_pages": sum(r.get("omitted_pages", 0) for r in rows),
        "required_per_converted_page": _safe_div(sum(r["required"] for r in rows), converted),
        "optional_per_converted_page": _safe_div(sum(r["optional"] for r in rows), converted),
        "models": sorted({m for r in rows for m in r["models"]}),
        "equation_flags": {
            "all": _flag_numbers(flags, key="wrong", gold=gold_equations),
            "all_symbols": _flag_numbers(flags, key="wrong_symbols", gold=gold_equations),
            "display": _flag_numbers(display, key="wrong", gold=gold_equations),
            "inline": _flag_numbers(inline, key="wrong", gold=gold_equations),
            "inline_symbols": _flag_numbers(inline, key="wrong_symbols", gold=gold_equations),
        },
        # The second reading: how often the crop and the page agree, and how right each is.
        "second_reading": {
            "display_matched": len(display),
            "agreed": len(agreed),
            "agreed_wrong": sum(f["wrong"] for f in agreed),
            "disagreed": len(disagreed),
            "disagreed_wrong": sum(f["wrong"] for f in disagreed),
            "no_second_reading": sum(f["agreed"] is None for f in display),
        },
    }
