"""Real-world set, step 4: the tables of a part (dev or held-out) from the recorded runs.

    uv run python scripts/realworld_report.py --part dev [--out data/realworld/results-dev.json]

Reads the items of each arm that was run (ReadableSTEM `runs/PART/rs/items.jsonl`, the chatbots'
`runs/PART/chat/ARM.items.jsonl` made by `realworld_run.py chat-items`) and the gold, and prints:

- the registered metrics, pooled, with 95% cluster bootstrap intervals over papers
  (`proof.stats.cluster_bootstrap`, 10,000 resamples, seed 0): primary `exact_lenient`, symbols
  credited (the secondary), detection recall and precision, wrong equations nobody is asked
  about per 100 gold, unsupported predictions per prediction, cost per PDF page;
- paired differences ReadableSTEM minus each chatbot on the papers both ran on (cluster bootstrap
  and the sign-flip test), for the primary and the symbols-credited scores;
- the buckets of docs/eval-page-math.md (correct, scorer, span, recognition, no proposal,
  undetected) per arm, and the primary score by stratum: display or inline, single symbol or
  longer, style of paper, alignment environments;
- with `--examples N`: failures of ReadableSTEM, a seeded draw from every bucket.

Nothing here calls a model, and nothing in it may be used to change a system on the held-out part.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

from readablestem.eval import pipeline_eval as pe
from readablestem.proof import stats

ROOT = Path.home() / ".cache" / "readablestem" / "realworld"
REPO = Path(__file__).resolve().parent.parent
ARMS = {
    "ReadableSTEM": "rs/items.jsonl",
    "Chatbot, Opus": "chat/chatbot-opus-purdue.items.jsonl",
    "Chatbot, Haiku": "chat/chatbot-haiku-purdue.items.jsonl",
}


CHAT_ARM_IDS = {"Chatbot, Opus": "chatbot-opus-purdue", "Chatbot, Haiku": "chatbot-haiku-purdue"}


def rescored_chat_items(part: str, label: str) -> list[dict]:
    """A chatbot arm's items made again from its recorded answers by the code as it is now.

    The items of the registered reports were made when the equation converter was the one that
    also made the gold; the converter has changed since (bracket-script grouping,
    docs/chemistry.md),
    so a ReadableSTEM run of today and a chatbot item file of the registration are scored by
    two converters. Reading the answers again scores every arm by one."""
    spec = importlib.util.spec_from_file_location(
        "realworld_run_for_report", REPO / "scripts" / "realworld_run.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["realworld_run_for_report"] = module
    spec.loader.exec_module(module)
    return module.chat_items(part, CHAT_ARM_IDS[label])


def apply_reading(item: dict, reading: str) -> dict:
    """ReadableSTEM's item as the registered scorer made it (`registered`), or as an HTML reader
    counts it (`cells`): the maths cells of a rebuilt table are equations (`pe.with_table_maths`).
    A chatbot's item and a page with no maths cell are the same either way."""
    if reading == "cells" and "table_maths_reading" in item:
        return item | item["table_maths_reading"]
    return item


def load_buckets_module():
    spec = importlib.util.spec_from_file_location(
        "dev_buckets", REPO / "scripts" / "dev_buckets.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["dev_buckets"] = module
    spec.loader.exec_module(module)
    return module


# Columns of the per-paper count vectors.
COLUMNS = (
    "gold", "exact", "symbols_credit", "matched", "pred", "wrong_unasked", "unsupported",
    "cost", "pages", "required", "sym_wrong", "sym_wrong_flagged", "extra", "required_all",
)  # fmt: skip
COL = {name: i for i, name in enumerate(COLUMNS)}


def paper_counts(item: dict) -> list[float]:
    pairs = item["pairs"]
    credited = set(item["symbols_credited"]["split"]) | set(item["symbols_credited"]["merged"])
    symbol_ids = {p["gold_id"] for p in pairs if p["exact_symbols"]} | credited
    unsupported = sum(
        (not u["text_layer_supported"]) and not u["piece_of"] for u in item["unmatched"]
    )
    flagged = [p for p in pairs if p.get("review") == "required"]
    sym_wrong = [p for p in pairs if not p["exact_symbols"]]
    vec = [0.0] * len(COLUMNS)
    vec[COL["gold"]] = item["n_gold"]
    vec[COL["exact"]] = sum(p["exact_lenient"] for p in pairs)
    vec[COL["symbols_credit"]] = len(symbol_ids)
    vec[COL["matched"]] = len(pairs)
    vec[COL["pred"]] = item["n_pred"]
    vec[COL["wrong_unasked"]] = sum(
        (not p["exact_lenient"]) and p.get("review") != "required" for p in pairs
    )
    vec[COL["unsupported"]] = unsupported
    vec[COL["cost"]] = item.get("cost_usd", 0.0)
    vec[COL["pages"]] = item.get("pdf_pages", 0)
    vec[COL["required"]] = len(flagged)
    vec[COL["sym_wrong"]] = len(sym_wrong)
    vec[COL["sym_wrong_flagged"]] = sum(p.get("review") == "required" for p in sym_wrong)
    vec[COL["extra"]] = len(item["unmatched"])
    vec[COL["required_all"]] = item["review"]["required_decisions"]
    return vec


def ratio_of(num: str, den: str, scale: float = 1.0):
    n, d = COL[num], COL[den]

    def statistic(sums: np.ndarray) -> float | None:
        return float(scale * sums[n] / sums[d]) if sums[d] else None

    return statistic


METRICS = {
    "exact_lenient (primary)": ratio_of("exact", "gold"),
    "symbols, credited (secondary)": ratio_of("symbols_credit", "gold"),
    "detection recall": ratio_of("matched", "gold"),
    "detection precision": ratio_of("matched", "pred"),
    "wrong, not asked, per 100 gold": ratio_of("wrong_unasked", "gold", 100.0),
    # "unsupported" per prediction (the document task's text_layer_supported) needs the text of
    # the region an equation was cut from, which a chatbot's output has not:
    # scripts/realworld_invented.py reads every arm's output by one rule instead.
    "cost per PDF page (USD)": ratio_of("cost", "pages"),
    "flag recall (symbols-wrong aligned equations that were a decision)": ratio_of(
        "sym_wrong_flagged", "sym_wrong"
    ),
    "flag precision (decisions on aligned equations that were symbols-wrong)": ratio_of(
        "sym_wrong_flagged", "required"
    ),
    "required decisions per PDF page, all kinds": ratio_of("required_all", "pages"),
    "  of which on an aligned equation": ratio_of("required", "pages"),
}


def fmt(value: float | None, digits: int = 3) -> str:
    return "n/a" if value is None else f"{value:.{digits}f}"


def interval_text(point: float | None, interval: stats.Interval, digits: int = 3) -> str:
    if point is None:
        return "n/a"
    return f"{fmt(point, digits)} [{fmt(interval.low, digits)}, {fmt(interval.high, digits)}]"


def per_paper(items: list[dict]) -> dict[str, list[float]]:
    return {i["id"]: paper_counts(i) for i in items}


def table(headers: list[str], rows: list[list[str]]) -> str:
    out = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    out += ["| " + " | ".join(r) + " |" for r in rows]
    return "\n".join(out)


def float_kinds(part: str, gold: dict[str, dict]) -> dict[str, str]:
    """Gold id -> where the equation is typed: "text", or the floats it is in ("table", "figure",
    "figure>tikzpicture", "algorithm" ...). Not stored in the gold; derived again from the cached
    source by the extractor that made the gold (`arxiv_gold.analyse`)."""
    from readablestem.gold import arxiv_gold as ag
    from readablestem.gold import arxiv_tex as tex

    kinds: dict[str, str] = {}
    by_paper: dict[str, list[dict]] = defaultdict(list)
    for record in gold.values():
        by_paper[record["arxiv_id"]].append(record)
    for paper, records in by_paper.items():
        base = ROOT / "papers" / paper.replace("/", "_")
        analysis = ag.analyse(tex.read_sources(base / "src"), ag.page_texts_of(base / "paper.pdf"))
        if isinstance(analysis, str):
            continue
        by_key = {(e.item, e.latex): e.float_kind for e in analysis.equations}
        for record in records:
            kinds[record["id"]] = by_key.get((record["tex_item"], record["latex"])) or "text"
    return kinds


def strata_rows(
    items: list[dict], gold: dict[str, dict], kinds: dict[str, str]
) -> dict[str, dict[str, tuple[int, int, int]]]:
    """name -> stratum -> (gold, exact, detected) from the pairs."""
    out: dict[str, dict[str, list[int]]] = defaultdict(lambda: defaultdict(lambda: [0, 0, 0]))
    for item in items:
        pair_by_gold = {p["gold_id"]: p for p in item["pairs"]}
        for gid in [p["gold_id"] for p in item["pairs"]] + item["missed_gold_ids"]:
            g = gold[gid]
            pair = pair_by_gold.get(gid)
            keys = {
                "display": g["display"],
                "length": "single symbol" if g["tokens"] == 1 else "longer",
                "paper style": g.get("style") or "?",
                "environment": "align-like"
                if g["env"] in {"align", "eqnarray", "flalign", "alignat", "gather", "multline"}
                else ("inline" if g["display"] == "inline" else "single-line display"),
                "typed in": kinds.get(gid, "?"),
            }
            for name, value in keys.items():
                cell = out[name][value]
                cell[0] += 1
                cell[1] += bool(pair and pair["exact_lenient"])
                cell[2] += pair is not None
    return {k: {s: (v[0], v[1], v[2]) for s, v in d.items()} for k, d in out.items()}


def undetected_class(g: dict, kind: str) -> str:
    """Why an undetected gold equation is hard, by the first rule that fits."""
    if kind not in ("text", "algorithm"):
        return f"typed in a {kind.split('>')[0]}"
    if g["display"] == "block":
        return "display equation"
    if g["tokens"] == 1:
        return "single symbol in running text"
    if g["tokens"] <= 3:
        return "2 or 3 symbols in running text"
    return "longer inline expression"


def breakdown(items: list[dict], gold: dict[str, dict], kinds: dict[str, str], buckets) -> dict:
    """Counts behind the buckets: what the undetected are, and which way a span is wrong."""
    import difflib

    from readablestem.eval import equations_symbols as sym

    undetected: Counter[str] = Counter()
    span: Counter[str] = Counter()
    for item in items:
        for gid in item["missed_gold_ids"]:
            undetected[undetected_class(gold[gid], kinds.get(gid, "text"))] += 1
        for pair in item["pairs"]:
            g = gold[pair["gold_id"]]
            if buckets.classify_pair(pair, g["mathml"]) != "span":
                continue
            a = sym.symbols_of_latex(pair["pred_latex"])
            b = sym.symbols_of(g["mathml"])
            ops = {
                op[0]
                for op in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes()
                if op[0] != "equal"
            }
            span[
                "cut too long (extra symbols)"
                if ops == {"delete"}
                else "cut too short"
                if ops == {"insert"}
                else "both"
            ] += 1
    return {"undetected": dict(undetected), "span": dict(span)}


def examples(
    items: list[dict], gold: dict[str, dict], buckets, n: int, seed: str = "examples"
) -> list[dict]:
    """A seeded draw of failures, round-robin over the buckets."""
    import hashlib

    pool: dict[str, list[dict]] = defaultdict(list)
    for item in items:
        for pair in item["pairs"]:
            g = gold[pair["gold_id"]]
            bucket = buckets.classify_pair(pair, g["mathml"])
            if bucket != "correct":
                pool[bucket].append(
                    {
                        "bucket": bucket, "id": pair["gold_id"], "page": g["pdf_page"],
                        "display": g["display"], "gold_latex": g["latex"],
                        "pred_latex": pair.get("pred_latex"), "pdf_text": pair.get("pred_text"),
                    }
                )  # fmt: skip
        for gid in item["missed_gold_ids"]:
            g = gold[gid]
            pool["undetected"].append(
                {
                    "bucket": "undetected", "id": gid, "page": g["pdf_page"],
                    "display": g["display"], "gold_latex": g["latex"],
                    "pred_latex": None, "pdf_text": None,
                }
            )  # fmt: skip
    for entries in pool.values():
        entries.sort(key=lambda e: hashlib.sha256((seed + e["id"]).encode()).hexdigest())
    picked: list[dict] = []
    order = ["recognition", "span", "undetected", "no_proposal", "scorer"]
    while len(picked) < n and any(pool[b] for b in order):
        for b in order:
            if pool[b] and len(picked) < n:
                picked.append(pool[b].pop(0))
    return picked


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--part", choices=["dev", "heldout", "fresh", "fresh2"], required=True)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--examples", type=int, default=0)
    parser.add_argument(
        "--exclude", nargs="*", default=(), help="drop these papers from every arm (sensitivity)"
    )
    parser.add_argument("--tag", default="rs", help="ReadableSTEM's run folder under runs/PART")
    parser.add_argument(
        "--extra-arm",
        action="append",
        default=[],
        metavar="LABEL=TAG",
        help="another ReadableSTEM run (runs/PART/TAG) as an arm, to be compared with --tag's",
    )
    parser.add_argument(
        "--reading",
        choices=["registered", "cells"],
        default="registered",
        help="`cells`: ReadableSTEM's table cells with maths count as equations, as the "
        "chatbots' HTML is read",
    )
    parser.add_argument(
        "--rescore-chat",
        action="store_true",
        help="score the chatbot arms again from their recorded answers with the current code",
    )
    args = parser.parse_args()
    runs = ROOT / "runs" / args.part
    gold_dir = ROOT / "gold" / args.part
    gold = {r["id"]: r for r in pe.load_gold(gold_dir) if r.get("kind") == "equation"}
    buckets = load_buckets_module()
    items: dict[str, list[dict]] = {}
    for label, rel in ARMS.items():
        path = runs / (rel.replace("rs/", f"{args.tag}/") if rel.startswith("rs/") else rel)
        if path.is_file():
            rows_in = [json.loads(line) for line in path.read_text().splitlines() if line]
            if args.rescore_chat and label in CHAT_ARM_IDS:
                rows_in = rescored_chat_items(args.part, label)
            rows_in = [apply_reading(r, args.reading) for r in rows_in]
            items[label] = [r for r in rows_in if not any(e in r["id"] for e in args.exclude)]
    for spec in args.extra_arm:
        label, _, tag = spec.partition("=")
        path = runs / tag / "items.jsonl"
        rows_in = [
            apply_reading(json.loads(line), args.reading) for line in path.read_text().splitlines()
        ]
        items[label] = [r for r in rows_in if not any(e in r["id"] for e in args.exclude)]
    result: dict = {"part": args.part, "arms": {}}
    print(f"## {args.part}: " + ", ".join(f"{k}: {len(v)} papers" for k, v in items.items()))
    # pooled metrics with cluster intervals
    rows = []
    vectors = {label: per_paper(its) for label, its in items.items()}
    for name, statistic in METRICS.items():
        row = [name]
        for label in items:
            pp = vectors[label]
            point = stats.point(pp, statistic)
            interval = stats.cluster_bootstrap(pp, statistic)
            row.append(interval_text(point, interval, 4 if "cost" in name else 3))
            result["arms"].setdefault(label, {})[name] = {
                "point": point, "low": interval.low, "high": interval.high,
            }  # fmt: skip
        rows.append(row)
    sizes = ["gold equations / papers"] + [
        f"{int(sum(v[COL['gold']] for v in vectors[k].values()))} / {len(vectors[k])}"
        for k in items
    ]
    rows.insert(0, sizes)
    print(table(["metric (95% cluster bootstrap over papers)", *items], rows))
    # the papers every arm ran on (the Opus arm may be a subset)
    common = sorted(set.intersection(*(set(v) for v in vectors.values()))) if vectors else []
    if common and len(common) < max(len(v) for v in vectors.values()):
        rows = []
        for name, statistic in METRICS.items():
            row = [name]
            for label in items:
                pp = {k: vectors[label][k] for k in common}
                interval = stats.cluster_bootstrap(pp, statistic)
                row.append(
                    interval_text(stats.point(pp, statistic), interval, 4 if "cost" in name else 3)
                )
            rows.append(row)
        print()
        print(f"### the {len(common)} papers every arm ran on")
        print(table(["metric", *items], rows))
        result["common_papers"] = common
    # paired differences
    if "ReadableSTEM" in items:
        print()
        diff_rows = []
        for other in [k for k in items if k != "ReadableSTEM"]:
            common = sorted(set(vectors["ReadableSTEM"]) & set(vectors[other]))
            a = {k: vectors["ReadableSTEM"][k] for k in common}
            b = {k: vectors[other][k] for k in common}
            for name in (
                "exact_lenient (primary)",
                "symbols, credited (secondary)",
                "detection recall",
                "wrong, not asked, per 100 gold",
            ):
                est, ci = stats.paired_difference(a, b, METRICS[name])
                _, ci_reg = stats.paired_difference(a, b, METRICS[name], level=0.975)
                flip = stats.sign_flip(a, b, METRICS[name])
                diff_rows.append(
                    [
                        f"ReadableSTEM minus {other}", name, str(len(common)),
                        interval_text(est, ci), interval_text(est, ci_reg), fmt(flip.p, 3),
                    ]
                )  # fmt: skip
                result.setdefault("paired", []).append(
                    {
                        "other": other, "metric": name, "papers": len(common), "estimate": est,
                        "low": ci.low, "high": ci.high, "low_975": ci_reg.low,
                        "high_975": ci_reg.high, "sign_flip_p": flip.p,
                    }
                )  # fmt: skip
        print(
            table(
                ["difference", "metric", "papers", "estimate [95%]", "[97.5%, k=2]", "sign-flip p"],
                diff_rows,
            )
        )
    # buckets
    print()
    summaries = {label: buckets.summarize(its, gold) for label, its in items.items()}
    keys = [k for k in next(iter(summaries.values())) if k.startswith("bucket") or "credited" in k]
    print(
        table(
            ["bucket, per gold equation", *items],
            [[k, *[fmt(summaries[label][k]) for label in items]] for k in keys],
        )
    )
    result["buckets"] = {label: {k: summaries[label][k] for k in keys} for label in items}
    # strata
    print()
    kinds = float_kinds(args.part, gold)
    for label, its in items.items():
        st = strata_rows(its, gold, kinds)
        result.setdefault("strata", {})[label] = st
        rows = []
        for name, values in st.items():
            for value, (n, ex, det) in sorted(values.items()):
                rows.append([name, value, str(n), fmt(ex / n), fmt(det / n)])
        print(f"### {label}: by stratum")
        print(table(["stratum", "value", "gold", "exact_lenient", "detected"], rows))
        print()
    for label, its in items.items():
        counts = breakdown(its, gold, kinds, buckets)
        result.setdefault("breakdown", {})[label] = counts
        print(f"### {label}: undetected and span")
        for name, values in counts.items():
            print(f"- {name}: " + ", ".join(f"{k} {v}" for k, v in sorted(values.items())))
        print()
    # status of chatbot runs
    for label, its in items.items():
        counts = Counter(i.get("status", "ok") for i in its)
        if set(counts) != {"ok"}:
            print(f"{label}: run statuses {dict(counts)}")
    if args.examples and "ReadableSTEM" in items:
        picked = examples(items["ReadableSTEM"], gold, buckets, args.examples)
        result["examples"] = picked
        print("### failures of ReadableSTEM (seeded draw)")
        for e in picked:
            print(
                f"- {e['bucket']} {e['id']} (page {e['page']}, {e['display']}): "
                f"gold `{e['gold_latex']}`"
                f" ; wrote `{e['pred_latex']}` ; text layer `{(e['pdf_text'] or '')[:80]}`"
            )
    if args.out:
        args.out.write_text(json.dumps(result, indent=1, default=float) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
