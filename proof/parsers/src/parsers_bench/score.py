"""Score one arm's intermediate form with the shared scorers.

Equations and headings go through ReadableSTEM's own evaluation code (`eval.pipeline_eval`,
`eval.equations`), invented content through `invented.py`, cost and time come from the
runner's `run.json`. Every number is a count or a rate with its denominator. With two pages
the intervals are wide and nothing here is a result: `plumbing_only` is set whenever the gold
was cut to a few pages of a development section.
"""

from __future__ import annotations

from typing import Any

from readablestem.eval import pipeline_eval as pe

from parsers_bench import equations as eqs
from parsers_bench import invented
from parsers_bench.devgold import GoldSubset
from parsers_bench.intermediate import Intermediate
from parsers_bench.symbols import MIN_SYMBOLS, SymbolStream, symbol_sequence


def _wilson(successes: int, n: int) -> list[float] | None:
    interval = pe.wilson_interval(successes, n)
    return list(interval) if interval else None


def heading_scores(doc: Intermediate, gold: GoldSubset, page_id: str) -> dict[str, Any]:
    predicted = [{"level": h.level, "text": h.text, "status": "accepted"} for h in doc.headings]
    item = pe.heading_page_item(page_id, gold.headings, predicted)
    metrics = pe.heading_metrics([item])
    levels = sorted({h.level for h in doc.headings})
    return {
        "gold_headings": metrics["gold_headings"],
        "predicted_headings": metrics["predicted_headings"],
        "matched": metrics["matched"],
        "precision": metrics["precision"],
        "recall": metrics["recall"],
        "f1": metrics["f1"],
        "level_accuracy": metrics["level_accuracy"],
        "distinct_levels_in_output": levels,
        "recall_wilson95": _wilson(metrics["matched"], metrics["gold_headings"]),
        "item": item,
    }


def _equation_signature(row: dict[str, Any]) -> str:
    return pe.math_signature(row["mathml"])


def _retained(doc: Intermediate, gold: GoldSubset) -> dict[str, Any]:
    """Equation content kept in any form (PARSERS-PLAN 4.4d): is the gold equation's symbol
    sequence found in the output text, wherever and however it is written?"""
    pieces: list[tuple[int, str]] = []
    pieces += [(h.order, h.text) for h in doc.headings]
    pieces += [(b.order, b.text) for b in doc.blocks]
    pieces += [(f.order, f"{f.caption or ''} {f.text_alternative or ''}") for f in doc.figures]
    for eq in doc.equations:
        text = eq.raw or ""
        if eq.latex:
            mathml, _ = eqs.convert(
                eqs.prepare(eq.latex, eq.display, "allowlist_free"), eq.display, "allowlist_free"
            )
            text = f"{text} {pe.math_signature(mathml) or eq.latex}"
        pieces.append((eq.order, text))
    stream = SymbolStream(symbol_sequence(" ".join(t for _, t in sorted(pieces))))
    checked = kept = too_short = 0
    for record in gold.equations:
        needle = symbol_sequence(pe.math_signature(record["mathml"]))
        if len(needle) < MIN_SYMBOLS:
            too_short += 1
            continue
        checked += 1
        kept += stream.contains(needle)
    return {
        "checked": checked,
        "retained_in_any_form": kept,
        "not_found": checked - kept,
        "too_short_to_check": too_short,
        "rate": kept / checked if checked else None,
        "wilson95": _wilson(kept, checked),
    }


def figure_scores(doc: Intermediate, gold: GoldSubset) -> dict[str, Any]:
    described = sum(bool(f.text_alternative) for f in doc.figures)
    on_pages = gold.figures_on_pages
    return {
        "figures_on_pages_by_layout": on_pages,
        "figures_in_output": len(doc.figures),
        "image_kept": sum(f.image_kept for f in doc.figures),
        "with_text_alternative": described,
        "no_description": max(on_pages - described, 0),
        "note": "descriptive; the judge-based figure measures need the model judge and are not run",
    }


def cost_and_time(run: dict[str, Any]) -> dict[str, Any]:
    measurement = run["measurement"]
    return {
        "api_cost_usd": run.get("api_cost_usd", 0.0),
        "api_calls": run.get("api_calls", 0),
        "pages": run["input"]["pages"],
        "wall_seconds": run["wall_seconds"],
        "wall_seconds_per_page": run["wall_seconds_per_page"],
        "user_cpu_seconds": measurement["user_seconds"],
        "peak_rss_mb": round(measurement["max_rss_kb"] / 1024, 1),
        "load_average_1m_before": measurement["load_before"],
        "load_average_1m_after": measurement["load_after"],
        "host": run["host"],
        "note": "CPU only, a fresh process (model load included); times on a shared box are "
        "upper bounds",
    }


def score_arm(
    doc: Intermediate,
    run: dict[str, Any],
    gold: GoldSubset,
    reference: invented.Reference,
    *,
    page_id: str,
    plumbing_only: bool = True,
) -> dict[str, Any]:
    """Everything scored for one arm on one input, plus the items behind the numbers."""
    variants = {
        v: eqs.score_variant(doc.equations, gold.equations, v, page_id=page_id)
        for v in eqs.VARIANTS
    }
    primary = variants["primary"]
    rows_by_id = {f"x{r['order']}": r for r in primary["predicted_rows"]}
    pairs = primary["item"]["pairs"]
    gold_by_id = {g["id"]: g for g in gold.equations}
    aligned_out = [_equation_signature(rows_by_id[p["element_id"]]) for p in pairs]
    aligned_gold = [pe.math_signature(gold_by_id[p["gold_id"]]["mathml"]) for p in pairs]
    aligned_ids = {p["element_id"] for p in pairs}
    unaligned = [
        symbol_sequence(_equation_signature(r))
        for key, r in rows_by_id.items()
        if key not in aligned_ids and r["mathml"] is not None
    ]
    invented_rates = invented.text_rates(doc, reference)
    invented_rates.update(invented.equation_rates(aligned_out, aligned_gold, unaligned, reference))
    headings = heading_scores(doc, gold, page_id)
    summary = {
        "arm": doc.arm,
        "section": doc.section,
        "status": doc.status,
        "plumbing_only": plumbing_only,
        "gold": gold.counts(),
        "output": doc.counts(),
        "equations": {
            "does_not_transcribe_by_design": not any(e.latex or e.mathml for e in doc.equations),
            **{
                name: {k: v for k, v in row.items() if k not in ("item", "predicted_rows")}
                for name, row in variants.items()
            },
            "retained_in_any_form": _retained(doc, gold),
        },
        "headings": {k: v for k, v in headings.items() if k != "item"},
        "invented_content": invented_rates,
        "figures": figure_scores(doc, gold),
        "cost_and_time": cost_and_time(run),
    }
    items = {
        "equations": {
            name: {"item": row["item"], "rows": row["predicted_rows"]}
            for name, row in variants.items()
        },
        "headings": headings["item"],
    }
    return {"summary": summary, "items": items}
