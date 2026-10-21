"""Secondary equation scores of the recorded held-out run, from recorded answers only.

Reads, never writes, the committed bundle (`proof/bundles/heldout-2026-10`: gold and the
chatbot arms' recorded replies) and two dumps of ReadableSTEM's own recorded run, made by
`scripts/heldout_replay_dump.py` inside a checkout of the tag `proof-heldout-2026-10`
(layout elements per section and the output HTML per run and section). It rebuilds each
arm's document-task items with the CURRENT scorers (`eval.pipeline_eval.document_page_item`:
the primary scorer, stream v2, the symbols scorer, split and merge credit) and prints the
pooled numbers. No model is called, nothing is spent. Reporting only: nothing here may be
used to tune anything (the held-out rules, docs/eval.md).

    uv run python scripts/heldout_secondary_scores.py DUMP_DIR [--out results.json]

The check at the start: the primary `end_to_end_exact_lenient` of ReadableSTEM's two runs
must equal the recorded 0.5004 and 0.5069 (PROOF.md), otherwise the reconstruction is not
the recorded run and the script stops.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from readablestem.core.types import Element, EquationFix, Fix, Kind, Status
from readablestem.eval import pipeline_eval as pe
from readablestem.proof import systems as S

BUNDLE = Path(__file__).resolve().parent.parent / "proof" / "bundles" / "heldout-2026-10"
RECORDED_PRIMARY = {1: 0.5003652300949598, 2: 0.5069393718042366}


def load_gold() -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for path in sorted((BUNDLE / "gold" / "openstax").glob("*/*.jsonl")):
        records = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
        equations = [r for r in records if r.get("kind") == "equation"]
        if equations:
            section = f"{path.parent.name}/{path.stem}"
            out[section] = equations
    return out


def html_math(html: str) -> dict[str, str]:
    """Element id -> MathML (outer) in the output HTML."""
    found: dict[str, str] = {}
    for m in re.finditer(
        r'<(?:div|span)[^>]*\bid="(p\d+-e\d+)"[^>]*>\s*(<math.*?</math>)', html, re.S
    ):
        found[m.group(1)] = m.group(2)
    for m in re.finditer(r'(<math[^>]*\bid="(p\d+-e\d+)"[^>]*>.*?</math>)', html, re.S):
        found[m.group(2)] = m.group(1)
    return found


def readablestem_items(dump: Path, run: int, gold: dict[str, list[dict]]) -> list[dict]:
    layout = json.loads((dump / "layout.json").read_text())
    html = json.loads((dump / "html.json").read_text())
    items = []
    for section, records in gold.items():
        elements = [
            Element(
                id=e["id"],
                kind=Kind.EQUATION,
                page=e["page"],
                bbox=tuple(e["bbox"]),  # type: ignore[arg-type]
                order=e["order"],
                text=e["text"],
                display=e["display"],
                parent_id=e["parent"],
            )
            for e in layout[section]
            if e["kind"] == "equation"
        ]
        math = html_math(html[f"{run}|{section}"])
        fixes = {
            eid: Fix(
                element_id=eid,
                kind=Kind.EQUATION,
                status=Status.ACCEPTED,
                confidence=None,
                equation=EquationFix(latex="", mathml=mathml, speech=""),
            )
            for eid, mathml in math.items()
            if any(el.id == eid for el in elements)
        }
        items.append(pe.document_page_item(section, records, elements, fixes, [], []))
    return items


def chatbot_items(arm: str, run: int, gold: dict[str, list[dict]]) -> list[dict]:
    arms = S.load_arms_dir(BUNDLE / "systems")
    items = []
    for section, records in gold.items():
        runs = [r for r in arms[arm].get(section, []) if r.run == run]
        out = runs[0].read() if runs else None
        items.append(S.equation_item(section, records, out))
    return items


def summarize(runs: list[list[dict]]) -> dict:
    """Per-run metrics, then the mean over runs (the credit sets are keyed by gold id, so
    two runs cannot be pooled into one call)."""
    per_run = [_summarize_run(items) for items in runs]
    keys = per_run[0]
    out = {k: sum(r[k] for r in per_run) / len(per_run) for k in keys}
    out["runs"] = len(per_run)
    counts = [section_counts(items) for items in runs]
    out["sections"] = {
        section: {
            key: sum(c[section][key] for c in counts) / len(counts) for key in counts[0][section]
        }
        for section in counts[0]
    }
    out["per_run_primary"] = [r["primary_end_to_end_exact_lenient"] for r in per_run]
    return out


def section_counts(items: list[dict]) -> dict[str, dict[str, int]]:
    """Per section: gold equations and how many are right under each reading (primary,
    stream v2 with credit, symbols with credit), for the cluster bootstrap."""
    out: dict[str, dict[str, int]] = {}
    for item in items:
        pairs = item["pairs"]
        v2_ids = {p["gold_id"] for p in pairs if p["exact_stream_v2"]}
        v2_ids |= set(item["stream_credited"]["split"]) | set(item["stream_credited"]["merged"])
        sym_ids = {p["gold_id"] for p in pairs if p["exact_symbols"]}
        sym_ids |= set(item["symbols_credited"]["split"]) | set(item["symbols_credited"]["merged"])
        out[item["id"]] = {
            "gold": item["n_gold"],
            "primary": sum(p["exact_lenient"] for p in pairs),
            "stream_v2_credit": len(v2_ids),
            "symbols_credit": len(sym_ids),
        }
    return out


def _summarize_run(items: list[dict]) -> dict:
    metrics = pe.document_metrics(items)
    gold = metrics["gold_equations"]
    return {
        "gold": gold,
        "primary_end_to_end_exact_lenient": metrics["end_to_end_exact_lenient"],
        "stream_v2_end_to_end_exact": metrics["secondary_stream_v2"]["end_to_end_exact"],
        "stream_v2_with_credit": metrics["secondary_stream_v2"]["end_to_end_exact_with_credit"],
        "symbols_end_to_end_exact": metrics["secondary_symbols"]["end_to_end_exact"],
        "symbols_with_credit": metrics["secondary_symbols"]["end_to_end_exact_with_credit"],
        "symbols_credited_split": metrics["secondary_symbols"]["credited_split"],
        "symbols_credited_merged": metrics["secondary_symbols"]["credited_merged"],
        "matched": metrics["matched"],
        "matched_exact_lenient": metrics["matched_exact_lenient"],
        "matched_symbols_exact": metrics["secondary_symbols"]["matched_exact"],
        "detection_recall": metrics["detection_recall"],
        "detection_precision": metrics["detection_precision"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("dump", type=Path, help="folder with layout.json and html.json")
    parser.add_argument("--out", type=Path)
    parser.add_argument(
        "--expect",
        type=float,
        nargs=2,
        metavar=("RUN1", "RUN2"),
        help="ReadableSTEM's primary end-to-end of the two runs the dump replays, to check "
        "(default: the registered run's 0.5004 and 0.5069)",
    )
    args = parser.parse_args()
    gold = load_gold()
    arms: dict[str, list[list[dict]]] = {}
    for run in (1, 2):
        items = readablestem_items(args.dump, run, gold)
        got = pe.document_metrics(items)["end_to_end_exact_lenient"]
        expected = args.expect[run - 1] if args.expect else RECORDED_PRIMARY[run]
        if abs(got - expected) > 1e-6:
            print(f"run {run}: rebuilt {got} != recorded {expected}", file=sys.stderr)
            return 2
        arms.setdefault("readablestem", []).append(items)
    for arm in ("chatbot-opus-purdue", "chatbot-haiku-purdue"):
        for run in (1, 2):
            arms.setdefault(arm, []).append(chatbot_items(arm, run, gold))
    result = {arm: summarize(runs) for arm, runs in arms.items()}
    text = json.dumps(result, indent=2)
    print(text)
    if args.out:
        args.out.write_text(text + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
