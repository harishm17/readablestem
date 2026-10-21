"""Real-world set: two ReadableSTEM runs on the same part, paired.

    uv run python scripts/realworld_compare.py --part dev --a rs --b rs-pagemath \
        [--examples 10 --hurt 5] [--out FILE]

Both runs are items of the document task (`runs/PART/TAG/items.jsonl`). Prints, for B minus A:
the pooled registered metrics with paired cluster bootstrap intervals over papers (95%) and the
sign-flip test, the buckets, what the undetected and span failures are, the score by where the
equation was typed, how many gold equations changed from wrong to exact (helped) or the other way
(hurt), and a seeded draw of examples of each. Development part only; nothing here calls a model.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import realworld_report as rr

from readablestem.eval import pipeline_eval as pe
from readablestem.proof import stats


def load(part: str, tag: str) -> list[dict]:
    path = rr.ROOT / "runs" / part / tag / "items.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def pair_map(items: list[dict]) -> dict[str, dict]:
    return {p["gold_id"]: p for it in items for p in it["pairs"]}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--part", choices=["dev"], default="dev")
    parser.add_argument("--a", default="rs")
    parser.add_argument("--b", required=True)
    parser.add_argument("--examples", type=int, default=10)
    parser.add_argument("--hurt", type=int, default=5)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    gold = {
        r["id"]: r for r in pe.load_gold(rr.ROOT / "gold" / args.part) if r["kind"] == "equation"
    }
    kinds = rr.float_kinds(args.part, gold)
    buckets = rr.load_buckets_module()
    items = {"A": load(args.part, args.a), "B": load(args.part, args.b)}
    vec = {k: rr.per_paper(v) for k, v in items.items()}
    result: dict = {"a": args.a, "b": args.b}
    rows = []
    for name, statistic in rr.METRICS.items():
        pa, pb = stats.point(vec["A"], statistic), stats.point(vec["B"], statistic)
        est, ci = stats.paired_difference(vec["B"], vec["A"], statistic)
        flip = stats.sign_flip(vec["B"], vec["A"], statistic)
        rows.append(
            [name, rr.fmt(pa, 4), rr.fmt(pb, 4), rr.interval_text(est, ci, 4), rr.fmt(flip.p, 3)]
        )
        result.setdefault("metrics", {})[name] = {
            "a": pa, "b": pb, "diff": est, "low": ci.low, "high": ci.high, "p": flip.p,
        }  # fmt: skip
    print(rr.table(["metric", args.a, args.b, "B minus A [95% paired]", "sign-flip p"], rows))
    # page pass cost
    pm = sum(i.get("page_math_cost_usd", 0.0) for i in items["B"])
    pages = sum(i["pdf_pages"] for i in items["B"])
    print(f"\npage pass: ${pm:.4f} (${pm / pages:.4f} per PDF page)")
    summ = {k: rr.load_buckets_module().summarize(v, gold) for k, v in items.items()}
    keys = [k for k in summ["A"] if k.startswith("bucket") or "credited" in k]
    print()
    print(
        rr.table(
            ["bucket, per gold equation", args.a, args.b, "change"],
            [
                [
                    k,
                    rr.fmt(summ["A"][k]),
                    rr.fmt(summ["B"][k]),
                    f"{summ['B'][k] - summ['A'][k]:+.3f}",
                ]
                for k in keys
            ],
        )
    )
    result["buckets"] = {k: {"a": summ["A"][k], "b": summ["B"][k]} for k in keys}
    # undetected and span breakdowns
    bd = {k: rr.breakdown(v, gold, kinds, buckets) for k, v in items.items()}
    print()
    for part in ("undetected", "span"):
        names = sorted(set(bd["A"][part]) | set(bd["B"][part]))
        print(
            rr.table(
                [part, args.a, args.b, "change"],
                [
                    [
                        n,
                        str(bd["A"][part].get(n, 0)),
                        str(bd["B"][part].get(n, 0)),
                        f"{bd['B'][part].get(n, 0) - bd['A'][part].get(n, 0):+d}",
                    ]
                    for n in names
                ],
            )
        )
        print()
    result["breakdown"] = bd
    st = {k: rr.strata_rows(v, gold, kinds) for k, v in items.items()}
    rows = []
    for name, values in st["A"].items():
        for value, (n, ea, da) in sorted(values.items()):
            _, eb, db = st["B"][name][value]
            rows.append(
                [
                    name,
                    value,
                    str(n),
                    f"{ea / n:.3f}",
                    f"{eb / n:.3f}",
                    f"{da / n:.3f}",
                    f"{db / n:.3f}",
                ]
            )
    print(
        rr.table(
            [
                "stratum",
                "value",
                "gold",
                f"exact {args.a}",
                f"exact {args.b}",
                f"detected {args.a}",
                f"detected {args.b}",
            ],
            rows,
        )
    )
    # transitions
    pa, pb = pair_map(items["A"]), pair_map(items["B"])
    helped, hurt = [], []
    for gid in gold:
        ea = bool(pa.get(gid, {}).get("exact_lenient"))
        eb = bool(pb.get(gid, {}).get("exact_lenient"))
        if eb and not ea:
            helped.append(gid)
        elif ea and not eb:
            hurt.append(gid)
    exact_a = sum(bool(p["exact_lenient"]) for p in pa.values())
    exact_b = sum(bool(p["exact_lenient"]) for p in pb.values())
    print(f"\nexact: {exact_a} -> {exact_b}; helped {len(helped)}, hurt {len(hurt)}")
    result["transitions"] = {
        "helped": len(helped),
        "hurt": len(hurt),
        "exact_a": exact_a,
        "exact_b": exact_b,
    }

    def why(gid: str) -> str:
        a, b = pa.get(gid), pb.get(gid)
        bucket_a = "undetected" if a is None else buckets.classify_pair(a, gold[gid]["mathml"])
        bucket_b = "undetected" if b is None else buckets.classify_pair(b, gold[gid]["mathml"])
        return f"{bucket_a} -> {bucket_b}"

    flow = Counter()
    for gid in helped:
        flow[("helped", why(gid))] += 1
    for gid in hurt:
        flow[("hurt", why(gid))] += 1
    print("\nflows (bucket under A -> bucket under B):")
    for (kind, w), n in sorted(flow.items(), key=lambda x: (-x[1], x[0])):
        print(f"  {kind:6s} {w:32s} {n}")
    result["flows"] = {f"{k}: {w}": n for (k, w), n in flow.items()}

    def draw(ids: list[str], n: int, salt: str) -> list[dict]:
        ordered = sorted(ids, key=lambda g: hashlib.sha256((salt + g).encode()).hexdigest())
        # spread over the flows: round robin over the flow labels
        by: dict[str, list[str]] = {}
        for g in ordered:
            by.setdefault(why(g), []).append(g)
        out: list[dict] = []
        while len(out) < n and any(by.values()):
            for label in sorted(by, key=lambda x: -len(by[x])):
                if by[label] and len(out) < n:
                    g = by[label].pop(0)
                    a, b = pa.get(g), pb.get(g)
                    out.append(
                        {
                            "id": g, "flow": label, "page": gold[g]["pdf_page"],
                            "typed_in": kinds.get(g), "gold": gold[g]["latex"],
                            "a": (a or {}).get("pred_latex"), "b": (b or {}).get("pred_latex"),
                            "b_text": (b or {}).get("pred_text"),
                        }
                    )  # fmt: skip
        return out

    for title, ids, n, salt in (
        ("HELPED", helped, args.examples, "h"),
        ("HURT", hurt, args.hurt, "u"),
    ):
        print(f"\n### {title} (seeded draw)")
        picked = draw(ids, n, salt)
        result[title.lower()] = picked
        for e in picked:
            print(
                f"- {e['flow']} {e['id']} p{e['page']} [{e['typed_in']}]: gold `{e['gold'][:90]}` ;"
                f" {args.a} `{(e['a'] or '')[:90]}` ; {args.b} `{(e['b'] or '')[:90]}`"
            )
    if args.out:
        args.out.write_text(json.dumps(result, indent=1, default=float) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
