"""Fifth fresh split: the registered tables from the recorded runs (`PREREGISTRATION-FRESH5.md`).

    uv run python scripts/fresh5_report.py [--out data/realworld/results-fresh5.json]

Reads the items of every arm on the five gold folders (ReadableSTEM `runs/<folder>/control` and
`candidate`, the chatbots' recorded answers under `runs/fresh5/chat/systems`, scored again here by
`realworld_run.chat_items`), and prints, as section 4 and 5 of the registration say:

- reading B (a rebuilt table's maths cells are inline equations) for every arm;
- the primary, spoken-equal end to end (`scripts/rw_spoken.py`), and `exact_lenient` beside it,
  pooled over the 26 units of strata A to C, with 95% cluster bootstrap intervals over units
  (`proof.stats`, 10,000 resamples, seed 0); silent errors (wrong under the primary, not a
  required decision) per 100 gold; flag recall; required decisions per page; unseen loss (silent
  plus undetected, per 100 gold); cost per page; detection recall and symbols credited;
- each stratum's point estimates, and the paired differences candidate minus every other arm on
  the units both answered, with the registered 97.5% interval and the sign-flip p;
- stratum D on its own: chemically right without the lone multiplication sign
  (`equations_chem.chem_right_excl_times`), the same with it, silent harm per 100 gold, and the
  secondaries, as aggregates only (two of its sections are private: no per-section number of
  theirs is printed or written);
- the five criteria of section 5 and stratum D's four, and the registered wording.

Nothing here calls a model.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path

from readablestem.eval import equations_chem as chem
from readablestem.eval import pipeline_eval as pe
from readablestem.proof import stats

REPO = Path(__file__).resolve().parent.parent
ROOT = Path.home() / ".cache" / "readablestem" / "realworld"
STRATUM = {"fresh5a": "A", "fresh5b": "B", "fresh5c": "C", "fresh5d": "D", "fresh5d-private": "D"}
PRIMARY_PARTS = ("fresh5a", "fresh5b", "fresh5c")
D_PARTS = ("fresh5d", "fresh5d-private")
PRIVATE_PARTS = ("fresh5d-private",)
CANDIDATE = "Candidate"
OPUS = "Chatbot, Opus"
# label -> (kind, tag or arm id)
ARMS = {
    CANDIDATE: ("rs", "candidate"),
    "Control": ("rs", "control"),
    OPUS: ("chat", "chatbot-opus-purdue"),
    "Chatbot, Opus, strong prompt": ("chat", "chatbot-opus-expert"),
    "Chatbot, Haiku": ("chat", "chatbot-haiku-purdue"),
}


def load_script(name: str):
    spec = importlib.util.spec_from_file_location(name, REPO / "scripts" / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


rep = load_script("realworld_report")
spoken = load_script("rw_spoken")
run = load_script("realworld_run")

COL = dict(rep.COL)
COL["exact_registered"] = len(rep.COLUMNS)


def unit_vector(item: dict, gold: dict[str, dict]) -> list[float]:
    """The report's count vector of one unit under the primary (spoken-equal: `exact` and
    `wrong_unasked` are read by what is spoken), with `exact_lenient`'s count added."""
    primary = spoken.refold(item, gold)
    vec = rep.paper_counts(primary)
    vec.append(float(sum(p["exact_lenient"] for p in item["pairs"])))
    return vec


def ratio(num: str, den: str, scale: float = 1.0):
    n, d = COL[num], COL[den]

    def statistic(sums) -> float | None:
        return float(scale * sums[n] / sums[d]) if sums[d] else None

    return statistic


def unseen_loss(sums) -> float | None:
    gold = sums[COL["gold"]]
    if not gold:
        return None
    undetected = gold - sums[COL["matched"]]
    return float(100.0 * (sums[COL["wrong_unasked"]] + undetected) / gold)


METRICS = {
    "spoken-equal, end to end (primary)": ratio("exact", "gold"),
    "exact_lenient (secondary)": ratio("exact_registered", "gold"),
    "silent errors per 100 gold (wrong under the primary, not asked)": ratio(
        "wrong_unasked", "gold", 100.0
    ),
    "flag recall (symbols-wrong aligned equations that were a required decision)": ratio(
        "sym_wrong_flagged", "sym_wrong"
    ),
    "required decisions per PDF page": ratio("required_all", "pages"),
    "unseen loss per 100 gold (silent plus undetected)": unseen_loss,
    "cost per PDF page (USD)": ratio("cost", "pages"),
    "detection recall": ratio("matched", "gold"),
    "symbols, credited": ratio("symbols_credit", "gold"),
}
PAIRED = (
    "spoken-equal, end to end (primary)",
    "exact_lenient (secondary)",
    "silent errors per 100 gold (wrong under the primary, not asked)",
    "unseen loss per 100 gold (silent plus undetected)",
)
PRIMARY = "spoken-equal, end to end (primary)"


def load() -> tuple[dict, dict, dict]:
    """(gold by folder, items by arm and folder, unit -> folder)."""
    gold: dict[str, dict[str, dict]] = {}
    items: dict[str, dict[str, list[dict]]] = {label: {} for label in ARMS}
    for part in STRATUM:
        gdir = ROOT / "gold" / part
        gold[part] = {r["id"]: r for r in pe.load_gold(gdir) if r.get("kind") == "equation"}
        for label, (kind, ref) in ARMS.items():
            if kind == "rs":
                path = ROOT / "runs" / part / ref / "items.jsonl"
                rows = (
                    [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
                    if path.is_file()
                    else []
                )
            else:
                rows = run.chat_items(part, ref)
            items[label][part] = [rep.apply_reading(r, "cells") for r in rows]
    return gold, items, {}


def vectors_of(items: dict, gold: dict, parts: tuple[str, ...]) -> dict[str, dict[str, list]]:
    out: dict[str, dict[str, list]] = {}
    for label in ARMS:
        out[label] = {
            i["id"]: unit_vector(i, gold[part]) for part in parts for i in items[label][part]
        }
    return out


def fmt(value: float | None, digits: int = 3) -> str:
    return "n/a" if value is None else f"{value:.{digits}f}"


def with_interval(point, interval, digits=3) -> str:
    if point is None:
        return "n/a"
    return f"{fmt(point, digits)} [{fmt(interval.low, digits)}, {fmt(interval.high, digits)}]"


def table(headers: list[str], rows: list[list[str]]) -> str:
    out = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    return "\n".join(out + ["| " + " | ".join(r) + " |" for r in rows])


def pooled(vectors: dict, result: dict) -> str:
    labels = [k for k in ARMS if vectors[k]]
    rows = [
        ["units / gold equations / PDF pages"]
        + [
            f"{len(vectors[k])} / {int(sum(v[COL['gold']] for v in vectors[k].values()))}"
            f" / {int(sum(v[COL['pages']] for v in vectors[k].values()))}"
            for k in labels
        ]
    ]
    for name, statistic in METRICS.items():
        row = [name]
        for label in labels:
            pp = vectors[label]
            point = stats.point(pp, statistic)
            interval = stats.cluster_bootstrap(pp, statistic)
            row.append(with_interval(point, interval, 4 if "cost" in name else 3))
            result.setdefault(label, {})[name] = {
                "point": point, "low": interval.low, "high": interval.high, "units": len(pp),
            }  # fmt: skip
        rows.append(row)
    return table(["metric (95% cluster bootstrap over units)", *labels], rows)


def by_stratum(vectors: dict, unit_part: dict[str, str], result: dict) -> str:
    rows = []
    for stratum in ("A", "B", "C"):
        for name in (PRIMARY, "exact_lenient (secondary)", METRICS_SILENT):
            row = [stratum, name]
            for label in ARMS:
                pp = {u: v for u, v in vectors[label].items() if STRATUM[unit_part[u]] == stratum}
                point = stats.point(pp, METRICS[name]) if pp else None
                n = len(pp)
                row.append(f"{fmt(point)} ({n})" if pp else "n/a")
                result.setdefault(label, {}).setdefault(stratum, {})[name] = {
                    "point": point, "units": n,
                }  # fmt: skip
            rows.append(row)
    return table(["stratum", "metric", *[f"{k} (units)" for k in ARMS]], rows)


METRICS_SILENT = "silent errors per 100 gold (wrong under the primary, not asked)"


def paired(vectors: dict, unit_part: dict[str, str], result: list) -> str:
    rows = []
    for other in [k for k in ARMS if k != CANDIDATE and vectors[k]]:
        common = sorted(set(vectors[CANDIDATE]) & set(vectors[other]))
        if not common:
            continue
        a = {u: vectors[CANDIDATE][u] for u in common}
        b = {u: vectors[other][u] for u in common}
        for name in PAIRED:
            est, ci95 = stats.paired_difference(a, b, METRICS[name])
            _, ci975 = stats.paired_difference(a, b, METRICS[name], level=0.975)
            flip = stats.sign_flip(a, b, METRICS[name])
            strata = {}
            for stratum in ("A", "B", "C"):
                su = [u for u in common if STRATUM[unit_part[u]] == stratum]
                if su:
                    pa, pb = (
                        stats.point({u: a[u] for u in su}, METRICS[name]),
                        stats.point({u: b[u] for u in su}, METRICS[name]),
                    )
                    strata[stratum] = None if pa is None or pb is None else pa - pb
            rows.append(
                [
                    f"Candidate minus {other}", name, str(len(common)),
                    with_interval(est, ci95), with_interval(est, ci975), fmt(flip.p, 4),
                    ", ".join(f"{s} {fmt(v)}" for s, v in strata.items()),
                ]
            )  # fmt: skip
            result.append(
                {
                    "other": other, "metric": name, "units": len(common), "estimate": est,
                    "low": ci95.low, "high": ci95.high, "low_975": ci975.low,
                    "high_975": ci975.high, "sign_flip_p": flip.p, "strata": strata,
                    "missing_units": sorted(set(vectors[CANDIDATE]) - set(vectors[other])),
                }
            )  # fmt: skip
    return table(
        [
            "difference", "metric", "units", "estimate [95%]", "[97.5%, k = 2]",
            "sign-flip p", "by stratum (point)",
        ],
        rows,
    )  # fmt: skip


def wording(row: dict | None) -> str:
    """The registered wording for a pair on the primary (section 5)."""
    if row is None or row["estimate"] is None:
        return "not compared"
    strata_ok = all(v is None or v >= -0.02 for v in row["strata"].values())
    if (
        row["estimate"] > 0
        and row["low_975"] is not None
        and row["low_975"] > 0
        and row["sign_flip_p"] is not None
        and row["sign_flip_p"] < 0.025
        and strata_ok
    ):
        return "beats"
    if row["high_975"] is not None and row["high_975"] < 0:
        return "lower"
    if row["low_975"] is not None and row["low_975"] <= 0 <= (row["high_975"] or 0):
        side = "higher" if row["estimate"] > 0 else "lower"
        return f"no difference this set can detect ({side} by point estimate)"
    return "higher by point estimate"


def criteria(arms: dict, pairs: list[dict], all_units: dict) -> list[tuple[str, bool | None, str]]:
    def get(label: str, name: str):
        return (arms.get(label, {}).get(name) or {}).get("point")

    def pair(other: str, name: str = PRIMARY):
        return next((p for p in pairs if p["other"] == other and p["metric"] == name), None)

    out = []
    p = pair(OPUS)
    c1 = wording(p) == "beats"
    out.append(("1. primary, candidate minus Opus chatbot", c1, wording(p)))
    cand_silent, opus_silent = get(CANDIDATE, METRICS_SILENT), get(OPUS, METRICS_SILENT)
    ctrl_silent = get("Control", METRICS_SILENT)
    c2 = None
    if None not in (cand_silent, opus_silent, ctrl_silent):
        c2 = (
            cand_silent <= 4.0 and cand_silent <= 0.5 * opus_silent
            and cand_silent <= ctrl_silent + 0.5
        )  # fmt: skip
    out.append(
        (
            "2. silent errors <= 4.0, <= half the Opus chatbot's, <= control + 0.5",
            c2,
            f"candidate {fmt(cand_silent, 2)}, Opus {fmt(opus_silent, 2)}, "
            f"control {fmt(ctrl_silent, 2)}",
        )
    )
    recall = get(
        CANDIDATE, "flag recall (symbols-wrong aligned equations that were a required decision)"
    )
    per_page = get(CANDIDATE, "required decisions per PDF page")
    c3 = None if None in (recall, per_page) else (recall >= 0.60 and per_page <= 4.0)
    out.append(
        (
            "3. flag recall >= 0.60 at <= 4.0 required decisions a page",
            c3,
            f"recall {fmt(recall)}, {fmt(per_page, 2)} a page",
        )
    )
    unseen = "unseen loss per 100 gold (silent plus undetected)"
    cu, ou = get(CANDIDATE, unseen), get(OPUS, unseen)
    c4 = None if None in (cu, ou) else cu <= ou
    out.append(("4. unseen loss <= the Opus chatbot's", c4, f"{fmt(cu, 2)} vs {fmt(ou, 2)}"))
    cost = all_units.get(CANDIDATE)
    c5 = None if cost is None else cost <= 0.07
    out.append(("5. cost <= $0.07 a page (all 29 units)", c5, f"${fmt(cost, 4)} a page"))
    return out


def stratum_d(items: dict, gold: dict, result: dict) -> str:
    rows = []
    for label in ARMS:
        its = [i for part in D_PARTS for i in items[label][part]]
        if not its:
            continue
        right = chem.chem_right_excl_times(its)
        harm = chem.silent_harm_summary(its)
        vec = [unit_vector(i, gold[p]) for p in D_PARTS for i in items[label][p]]
        sums = [sum(col) for col in zip(*vec, strict=True)]
        g = sums[COL["gold"]]
        result[label] = {
            "units": len(its), "chem_right_excl_times": right, "silent_harm": harm,
            "spoken_equal": sums[COL["exact"]] / g if g else None,
            "exact_lenient": sums[COL["exact_registered"]] / g if g else None,
            "gold": g, "cost_usd": sums[COL["cost"]], "pages": sums[COL["pages"]],
        }  # fmt: skip
        rows.append(
            [
                label, str(len(its)),
                f"{fmt(right['rate'])} ({right['right']}/{right['n']})",
                f"{fmt(right['chem_right_rate'])} ({right['chem_right']}/{right['chem_right_n']})",
                f"{fmt(harm['per_100_gold'], 2)} ({harm['n']})",
                fmt(result[label]["spoken_equal"]), fmt(result[label]["exact_lenient"]),
            ]
        )  # fmt: skip
    return table(
        [
            "arm", "units", "chemically right, lone x left out (primary)",
            "chemically right, all", "silent harm per 100 gold (n)", "spoken-equal",
            "exact_lenient",
        ],
        rows,
    )  # fmt: skip


def d_criteria(d: dict) -> list[tuple[str, bool | None, str]]:
    def rate(label: str):
        return ((d.get(label) or {}).get("chem_right_excl_times") or {}).get("rate")

    cand, ctrl, opus = rate(CANDIDATE), rate("Control"), rate(OPUS)
    harm = ((d.get(CANDIDATE) or {}).get("silent_harm") or {}).get("per_100_gold")
    return [
        ("D1. candidate chemically right >= 0.80", None if cand is None else cand >= 0.80,
         fmt(cand)),
        ("D2. candidate >= control + 5 points", None if None in (cand, ctrl) else
         cand >= ctrl + 0.05, f"{fmt(cand)} vs {fmt(ctrl)}"),
        ("D3. candidate >= Opus chatbot (point)", None if None in (cand, opus) else
         cand >= opus, f"{fmt(cand)} vs {fmt(opus)}"),
        ("D4. silent harm <= 1 per 100", None if harm is None else harm <= 1.0, fmt(harm, 2)),
    ]  # fmt: skip


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--out", type=Path)
    parser.add_argument("--control-tag", default="control", help="a trial run's folder")
    parser.add_argument("--candidate-tag", default="candidate", help="a trial run's folder")
    args = parser.parse_args()
    ARMS["Control"] = ("rs", args.control_tag)
    ARMS[CANDIDATE] = ("rs", args.candidate_tag)
    gold, items, _ = load()
    unit_part = {i["id"]: part for label in ARMS for part in STRATUM for i in items[label][part]}
    result: dict = {"arms": {}, "paired": [], "stratum_d": {}}
    vectors = vectors_of(items, gold, PRIMARY_PARTS)
    print("## Strata A to C, pooled")
    print(pooled(vectors, result["arms"]))
    print()
    print("## By stratum (point estimates)")
    print(by_stratum(vectors, unit_part, result["arms"]))
    print()
    print("## Paired differences")
    print(paired(vectors, unit_part, result["paired"]))
    print()
    # cost on every unit of the split
    all_vectors = vectors_of(items, gold, tuple(STRATUM))
    all_cost = {
        label: stats.point(v, METRICS["cost per PDF page (USD)"]) if v else None
        for label, v in all_vectors.items()
    }
    result["cost_per_page_all_units"] = all_cost
    print("## Cost per PDF page, all 29 units (A to D)")
    print(table(["arm", "USD a page", "units"], [
        [k, fmt(all_cost[k], 4), str(len(all_vectors[k]))] for k in ARMS
    ]))  # fmt: skip
    print()
    print("## Stratum D (chemistry, aggregates only)")
    print(stratum_d(items, gold, result["stratum_d"]))
    print()
    print("## Criteria (section 5)")
    crit = criteria(result["arms"], result["paired"], all_cost)
    print(table(["criterion", "met", "value"], [[c, str(m), v] for c, m, v in crit]))
    met = all(m is True for _, m, _ in crit)
    result["criteria"] = [{"criterion": c, "met": m, "value": v} for c, m, v in crit]
    result["claim"] = met
    print(f"\nClaim 'ReadableSTEM beats the chatbot baseline on its equation evaluation': {met}")
    for other in [k for k in ARMS if k != CANDIDATE]:
        row = next(
            (p for p in result["paired"] if p["other"] == other and p["metric"] == PRIMARY), None
        )
        result.setdefault("wording", {})[other] = wording(row)
        print(f"- candidate vs {other}: {wording(row)}")
    dcrit = d_criteria(result["stratum_d"])
    result["stratum_d_criteria"] = [{"criterion": c, "met": m, "value": v} for c, m, v in dcrit]
    print()
    print(table(["stratum D criterion", "met", "value"], [[c, str(m), v] for c, m, v in dcrit]))
    # what each arm answered (public units named; private ones only counted)
    coverage = {}
    for label in ARMS:
        answered = {i["id"] for part in STRATUM for i in items[label][part]}
        statuses = {i["id"]: i.get("status", "ok") for part in STRATUM for i in items[label][part]}
        public_all = [u for p in STRATUM if p not in PRIVATE_PARTS for u in run.unit_pdfs(p)]
        coverage[label] = {
            "answered_units": len(answered),
            "missing_public_units": sorted(u for u in public_all if u not in answered),
            "missing_private_units": sum(
                u not in answered for p in PRIVATE_PARTS for u in run.unit_pdfs(p)
            ),
            "not_ok_public": {
                u: s for u, s in statuses.items()
                if s != "ok" and unit_part.get(u) not in PRIVATE_PARTS
            },
        }  # fmt: skip
    result["coverage"] = coverage
    print()
    print("## Coverage")
    for label, c in coverage.items():
        print(f"- {label}: {json.dumps(c)}")
    if args.out:
        # per-unit numbers are not written: two units are private, and the tables are enough
        args.out.write_text(json.dumps(result, indent=1, default=float) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
