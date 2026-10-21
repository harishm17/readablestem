"""Span-level effect of the page math pass on a development set, with no equation model.

    uv run python scripts/dev_spans.py --max-usd 0.3 SECTION [SECTION ...]

For each dev-v2 section (substrings of "book/page") it builds the printed PDF, runs layout
alone and layout with the page pass's verified plan, aligns the equations each finds to the gold
by their text (`document_page_item` with no model answers), and prints detection recall and
precision and span fidelity (the span's text layer within FIDELITY of the gold equation) for
both, with the gold equations whose fidelity changed. The page pass's answers are kept in
`--answers` (asked once, replayed); the cost is the page pass only, about $0.001 per page.
Development sets only.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from readablestem.config import get_settings, with_provider
from readablestem.core.budget import InMemorySpendGuard, JobBudget
from readablestem.core.types import Element, Kind
from readablestem.eval import pipeline_eval as pe
from readablestem.eval.answers import CachedClient
from readablestem.eval.devset_media import DevSetMedia
from readablestem.gold.openstax import HttpFetcher
from readablestem.models import make_live_client
from readablestem.pipeline import layout, page_math
from readablestem.pipeline import page_math_reconcile as rec

FIDELITY = 0.99
ROOT = Path(__file__).resolve().parent.parent


def pass_elements(plan: page_math.MathPlan) -> list[Element]:
    """The page pass's own math spans as stand-in equations (their quoted text), to measure
    the model's cut apart from what reconciliation does with it."""
    out: list[Element] = []
    for page, page_plan in sorted(plan.pages.items()):
        rows = {r.id: r for r in page_plan.rows}
        for span in page_plan.spans:
            if span.role != "math":
                continue
            text = " ".join(rows[g.row].text[g.start : g.end] for g in span.segments)
            out.append(
                Element(
                    id=f"p{page + 1}-e{len(out)}",
                    kind=Kind.EQUATION,
                    page=page,
                    bbox=(0.0, 0.0, 1.0, 1.0),
                    order=len(out),
                    text=text,
                    display="block" if span.display == "block" else "inline",
                )
            )
    return out


def span_item(page_id: str, gold: list[dict], elements: list) -> dict:
    return pe.document_page_item(page_id, gold, elements, {}, [], [])


def fidelity(item: dict) -> dict[str, float]:
    return {p["gold_id"]: p["text_similarity"] for p in item["pairs"]}


def texts(item: dict) -> dict[str, str]:
    return {p["gold_id"]: p.get("pred_text", "") for p in item["pairs"]}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("sections", nargs="+")
    parser.add_argument("--gold", type=Path, default=ROOT / "data" / "dev-v2" / "dev")
    parser.add_argument(
        "--cache", type=Path, default=Path.home() / ".cache/readablestem/dev-v2/eval-cache"
    )
    parser.add_argument("--answers", type=Path, default=ROOT / ".local" / "answers-dev.jsonl")
    parser.add_argument("--max-usd", type=float, default=0.3)
    parser.add_argument("--offline", action="store_true", help="answer only from --answers")
    parser.add_argument("--effort", default=None, help="page pass effort (low, medium)")
    parser.add_argument("--examples", type=int, default=12)
    parser.add_argument("--dump", type=Path, help="per-gold fidelity of each column, as JSON")
    args = parser.parse_args()

    settings = with_provider(get_settings(), "anthropic")
    if args.effort:
        settings = settings.model_copy(update={"anthropic_effort_page_math": args.effort})
    budget = JobBudget(
        InMemorySpendGuard(cap_usd=args.max_usd), max_usd=args.max_usd, max_calls=2000
    )
    live = None if args.offline else make_live_client(settings, budget)
    client = CachedClient(live, args.answers, settings)
    records = pe.load_gold(args.gold)
    units = pe.select_units("document", records, None, None, args.sections)
    totals = {
        "base": [0, 0, 0, 0],
        "plan": [0, 0, 0, 0],
        "pass": [0, 0, 0, 0],
    }  # gold, matched, faithful, predicted
    changes: list[str] = []
    dump: dict[str, dict[str, dict[str, list]]] = {}
    with DevSetMedia(
        args.gold, args.cache, HttpFetcher(cache_dir=ROOT / ".cache" / "openstax")
    ) as media:
        pdfs = [media.page_pdf(unit) for unit in units]
    for unit, pdf in zip(units, pdfs, strict=True):
        page_id = pe._page_id(unit)
        gold = pe._of_kind(unit, "equation")
        collector = rec.PageMathHook()
        base = layout.analyze(pdf, page_math=collector)
        offered = {
            n: (rows, (lambda n=n, pdf=pdf: layout.render_page(pdf, n)))
            for n, rows in collector.rows.items()
        }
        result = page_math.run(client, settings, offered, concurrency=3)
        hook = rec.PageMathHook(result.plan)
        planned = layout.analyze(pdf, page_math=hook)
        items = {
            "base": span_item(page_id, gold, base.elements),
            "plan": span_item(page_id, gold, planned.elements),
            "pass": span_item(page_id, gold, pass_elements(result.plan)),
        }
        for name, item in items.items():
            faithful = sum(f >= FIDELITY for f in fidelity(item).values())
            row = totals[name]
            row[0] += item["n_gold"]
            row[1] += item["matched"]
            row[2] += faithful
            row[3] += item["n_pred"]
        fb, fp = fidelity(items["base"]), fidelity(items["plan"])
        dump[page_id] = {
            name: {g: [f, texts(item)[g]] for g, f in fidelity(item).items()}
            for name, item in items.items()
        }
        by_id = {r["id"]: r for r in gold}
        for gid in sorted(set(fb) | set(fp)):
            before, after = fb.get(gid, 0.0) >= FIDELITY, fp.get(gid, 0.0) >= FIDELITY
            if before != after:
                text = " ".join(
                    p.get("pred_text", "")
                    for it in (items["plan" if after else "base"],)
                    for p in it["pairs"]
                    if p["gold_id"] == gid
                )
                changes.append(
                    f"{'+' if after else '-'} {page_id[-28:]} {by_id[gid]['display']:6s} "
                    f"{by_id[gid].get('classes') or ''} pred={text[:70]!r}"
                )
        print(
            f"{page_id[-60:]:60s} gold={items['base']['n_gold']:3d} "
            f"faithful {sum(f >= FIDELITY for f in fb.values()):3d}"
            f"->{sum(f >= FIDELITY for f in fp.values()):3d} "
            f"matched {items['base']['matched']:3d}->{items['plan']['matched']:3d} "
            f"pred {items['base']['n_pred']:3d}->{items['plan']['n_pred']:3d} "
            f"hook={hook.stats}",
            flush=True,
        )
    for name, (gold_n, matched, faithful, predicted) in totals.items():
        print(
            f"{name:5s} recall {matched / gold_n:.3f}  "
            f"precision {matched / max(1, predicted):.3f}  "
            f"fidelity {faithful / gold_n:.3f}  ({matched} matched, {faithful} faithful, "
            f"{predicted} predicted, {gold_n} gold)"
        )
    if args.dump:
        import json

        args.dump.write_text(json.dumps(dump))
    print(f"page pass spend this run: ${client.live_cost_usd:.4f}")
    for line in changes[: args.examples * 3]:
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
