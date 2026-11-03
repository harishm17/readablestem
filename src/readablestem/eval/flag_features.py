"""Features of every aligned equation of the recorded runs, and the fit of the flag policy
(`pipeline/flag_policy.py`, docs/eval-flag-policy.md). Reads recorded items only: no model call, no
PDF, $0.

    uv run python -m readablestem.eval.flag_features dump OUT.jsonl [--splits dev,dev-v2,fresh2,...]
    uv run python -m readablestem.eval.flag_features fit [--json OUT.json]
    uv run python -m readablestem.eval.flag_features report [--json OUT.json]

The splits (`SPLITS`) are two groups. The FIT splits, `dev` (25 arXiv papers, the line-context run)
and `dev-v2` (OpenStax, authored and chemistry documents), are the only ones the weights and the
threshold are chosen on. The VALIDATION splits `fresh2` to `fresh4` are scored with the fitted
constants as they are and nothing is chosen on them.

A row is one aligned equation (the maths cells of a rebuilt table count, as an HTML reader counts
them: `scripts/realworld_report.py --reading cells`). It carries the policy's features (computed by
`flag_policy.features` from the recorded pair, the same numbers the step computes live), what the
run did (`review`, `escalation`, the failed check codes), and the label: `wrong` is `not
exact_lenient`, the registered score. The recorded pair has no model-confidence number; the run's
`below_accept` and `low_confidence` reasons are the nearest recorded thing, and they only occur on
equations that were already a decision, so they cannot predict anything about the equations the
policy may add (`silent` rows). For the same reason `escalation` is the cheap/main comparison.

The chatbot arm's reading of the page, where it was recorded (`chat/chatbot-opus-purdue`), is a
diagnostic column (`chat_agree`: the canonical forms of the two readings agree), not a feature of
the policy: the product has no such reading at run time.

dev-v2 is scored by the registered scorer too, but on OpenStax maths that scorer calls a tree that
differs from the gold's wrong while speech and symbols agree (about 40% of its "wrong"), so the
FIT label of that split alone is "wrong under both the registered and the symbols scorer"
(`exact_lenient` and `exact_symbols` both false). Its reported numbers use the registered one.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from readablestem.pipeline import flag_policy as fp
from readablestem.proof import stats

ROOT = Path.home() / ".cache" / "readablestem" / "realworld"
ROUND4 = Path.home() / ".cache" / "a11y-stem" / "round4"
CHAT_ARM = "chatbot-opus-purdue"

TARGET_SILENT_PER_100 = 4.0  # the registered goal: wrong equations nobody is asked, per 100 gold
MAX_DECISIONS_PER_PAGE = 4.0  # ... at no more than this many required decisions a page


@dataclass(frozen=True)
class SplitSpec:
    name: str
    path: Path
    part: str  # the realworld part, for the chatbot arm's items (None: no chatbot arm)
    role: str  # "fit" or "validate"


SPLITS: dict[str, SplitSpec] = {
    "dev": SplitSpec("dev", ROOT / "runs/dev/line1/items.jsonl", "dev", "fit"),
    "dev-v2": SplitSpec("dev-v2", ROUND4 / "on.jsonl", "", "fit"),
    "fresh2": SplitSpec("fresh2", ROOT / "runs/fresh2/after/items.jsonl", "fresh2", "validate"),
    "fresh3": SplitSpec("fresh3", ROOT / "runs/fresh3/after/items.jsonl", "fresh3", "validate"),
    "fresh4": SplitSpec("fresh4", ROOT / "runs/fresh4/after/items.jsonl", "fresh4", "validate"),
}
FIT_SPLITS = tuple(n for n, s in SPLITS.items() if s.role == "fit")
VALIDATE_SPLITS = tuple(n for n, s in SPLITS.items() if s.role == "validate")
# The split the threshold is chosen on: the arXiv development papers, where the registered scorer
# and the goal mean what they say (see the module docstring on dev-v2).
THRESHOLD_SPLIT = "dev"


# ============================================================================ loading


def _items(path: Path) -> list[dict]:
    return [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]


def read_items(spec: SplitSpec) -> list[dict]:
    """The recorded items, each read as an HTML reader counts them (table cells are equations)."""
    out = []
    for item in _items(spec.path):
        if "table_maths_reading" in item:
            item = item | item["table_maths_reading"]
        out.append(item)
    return out


def chat_pairs(spec: SplitSpec) -> dict[str, dict[str, dict]]:
    """Paper id -> gold id -> the chatbot's aligned pair, where that arm was recorded."""
    if not spec.part:
        return {}
    path = ROOT / "runs" / spec.part / "chat" / f"{CHAT_ARM}.items.jsonl"
    if not path.is_file():
        return {}
    return {i["id"]: {p["gold_id"]: p for p in i["pairs"]} for i in _items(path)}


def _chat_agrees(ours: dict, chat: dict | None) -> bool | None:
    if chat is None:
        return None
    from readablestem.pipeline import math_canonical as mc

    display = ours.get("gold_display") == "block"
    return mc.compare(
        ours.get("pred_latex") or "", chat.get("pred_latex") or "", display=display
    ).holds_at(mc.Kind.NOTATION)


def _line_context(item: dict) -> bool:
    return "equation_line" in (item.get("prompt_versions") or {}).get("equation", "")


def rows_of(spec: SplitSpec, *, with_chat: bool = True) -> tuple[list[dict], list[dict]]:
    """(rows, items): a row per aligned equation with a proposal."""
    items = read_items(spec)
    chat = chat_pairs(spec) if with_chat else {}
    rows: list[dict] = []
    for item in items:
        line_context = _line_context(item)
        for pair in item["pairs"]:
            mathml, latex = pair.get("pred_mathml"), pair.get("pred_latex")
            if not mathml or latex is None:
                continue
            review = pair.get("review", "none")
            codes = [] if review == "none" else list(pair.get("tier_reasons") or [])
            feats = fp.features(
                display=pair.get("pred_display") or pair.get("gold_display"),
                escalation=pair.get("escalation"),
                codes=codes,
                text=pair.get("pred_text"),
                latex=latex,
                mathml=mathml,
            )
            wrong = not pair["exact_lenient"]
            fit_wrong = wrong and not pair["exact_symbols"] if spec.name == "dev-v2" else wrong
            rows.append(
                {
                    "split": spec.name,
                    "paper": item["id"],
                    "gold_id": pair["gold_id"],
                    "review": review,
                    "status": pair.get("status", "accepted"),
                    "escalation": pair.get("escalation"),
                    "codes": codes,
                    "wrong": wrong,
                    "fit_wrong": fit_wrong,
                    "sym_wrong": not pair["exact_symbols"],
                    "line_context": line_context,
                    "chat_agree": _chat_agrees(
                        pair, chat.get(item["id"], {}).get(pair["gold_id"]) if chat else None
                    ),
                    "features": feats,
                }
            )
    return rows, items


def candidate(row: dict) -> bool:
    """A row the policy may add a decision to: accepted, and not a decision already."""
    return row["status"] == "accepted" and row["review"] != "required"


# ============================================================================ the fit


def design(rows: Sequence[dict]) -> np.ndarray:
    return np.array([[r["features"][n] for n in fp.FEATURES] for r in rows], dtype=float)


def fit_logistic(
    x: np.ndarray, y: np.ndarray, l2: float, *, iterations: int = 50
) -> tuple[np.ndarray, float]:
    """L2-penalised logistic regression by Newton steps (no randomness: same data, same fit).
    The intercept is not penalised. Returns (weights, bias)."""
    n, k = x.shape
    a = np.hstack([x, np.ones((n, 1))])
    beta = np.zeros(k + 1)
    penalty = np.diag([l2] * k + [0.0])
    for _ in range(iterations):
        z = np.clip(a @ beta, -30, 30)
        p = 1.0 / (1.0 + np.exp(-z))
        grad = a.T @ (p - y) + penalty @ beta
        hess = (a * (p * (1 - p))[:, None]).T @ a + penalty + 1e-9 * np.eye(k + 1)
        step = np.linalg.solve(hess, grad)
        beta -= step
        if float(np.max(np.abs(step))) < 1e-10:
            break
    return beta[:k], float(beta[k])


def predict(x: np.ndarray, weights: np.ndarray, bias: float) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-np.clip(x @ weights + bias, -30, 30)))


def log_loss(y: np.ndarray, p: np.ndarray) -> float:
    p = np.clip(p, 1e-9, 1 - 1e-9)
    return float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))


def auroc(y: np.ndarray, p: np.ndarray) -> float | None:
    """Area under the ROC curve (ties count half)."""
    pos, neg = p[y == 1], p[y == 0]
    if not len(pos) or not len(neg):
        return None
    order = np.argsort(np.concatenate([pos, neg]), kind="mergesort")
    values = np.concatenate([pos, neg])[order]
    ranks = np.empty(len(values))
    i = 0
    while i < len(values):
        j = i
        while j + 1 < len(values) and values[j + 1] == values[i]:
            j += 1
        ranks[i : j + 1] = (i + j) / 2 + 1
        i = j + 1
    rank_of_pos = np.empty(len(values))
    rank_of_pos[order] = ranks
    return float(
        (rank_of_pos[: len(pos)].sum() - len(pos) * (len(pos) + 1) / 2) / (len(pos) * len(neg))
    )


def folds(rows: Sequence[dict], k: int = 5) -> list[int]:
    """The fold of each row: papers sorted by id, dealt round-robin, so a paper is never split."""
    papers = sorted({(r["split"], r["paper"]) for r in rows})
    fold_of = {p: i % k for i, p in enumerate(papers)}
    return [fold_of[(r["split"], r["paper"])] for r in rows]


L2_GRID = (0.3, 1.0, 3.0, 10.0, 30.0)


def cross_fit(rows: Sequence[dict], l2: float, k: int = 5) -> np.ndarray:
    """Out-of-fold probabilities: each paper's rows are scored by a model fit on the other folds."""
    x = design(rows)
    y = np.array([r["fit_wrong"] for r in rows], dtype=float)
    fold = np.array(folds(rows, k))
    out = np.zeros(len(rows))
    for f in range(k):
        train, test = fold != f, fold == f
        w, b = fit_logistic(x[train], y[train], l2)
        out[test] = predict(x[test], w, b)
    return out


def choose_l2(rows: Sequence[dict]) -> tuple[float, dict[float, float]]:
    """The penalty with the lowest out-of-fold log loss on the fit rows (fixed grid)."""
    y = np.array([r["fit_wrong"] for r in rows], dtype=float)
    losses = {l2: log_loss(y, cross_fit(rows, l2)) for l2 in L2_GRID}
    return min(losses, key=lambda v: (losses[v], v)), losses


# ============================================================================ the numbers

COLUMNS = (
    "gold", "pages", "required_all", "aligned", "wrong", "silent", "sym_wrong",
    "sym_wrong_flagged", "added", "added_wrong", "added_sym_wrong", "required_aligned",
)  # fmt: skip
COL = {name: i for i, name in enumerate(COLUMNS)}


def paper_vectors(
    rows: Sequence[dict], items: Sequence[dict], added: set[tuple[str, str]]
) -> dict[str, list[float]]:
    """Per paper: the counts the metrics are ratios of, with `added` (paper, gold id) rows
    turned into required decisions."""
    out: dict[str, list[float]] = {}
    by_paper = defaultdict(list)
    for r in rows:
        by_paper[r["paper"]].append(r)
    for item in items:
        v = [0.0] * len(COLUMNS)
        v[COL["gold"]] = item["n_gold"]
        v[COL["pages"]] = item.get("pdf_pages", 0)
        v[COL["required_all"]] = item["review"]["required_decisions"]
        for r in by_paper.get(item["id"], []):
            is_added = (r["paper"], r["gold_id"]) in added
            required = r["review"] == "required" or is_added
            v[COL["aligned"]] += 1
            v[COL["wrong"]] += r["wrong"]
            v[COL["required_aligned"]] += r["review"] == "required"
            v[COL["silent"]] += r["wrong"] and not required
            v[COL["sym_wrong"]] += r["sym_wrong"]
            v[COL["sym_wrong_flagged"]] += r["sym_wrong"] and required
            v[COL["added"]] += is_added
            v[COL["added_wrong"]] += is_added and r["wrong"]
            v[COL["added_sym_wrong"]] += is_added and r["sym_wrong"]
        # an added decision is one more required decision of the page
        v[COL["required_all"]] += v[COL["added"]]
        v[COL["required_aligned"]] += v[COL["added"]]
        out[item["id"]] = v
    return out


def _ratio(num: str, den: str, scale: float = 1.0):
    n, d = COL[num], COL[den]

    def statistic(sums: np.ndarray) -> float | None:
        return float(scale * sums[n] / sums[d]) if sums[d] else None

    return statistic


METRICS = {
    "flag recall": _ratio("sym_wrong_flagged", "sym_wrong"),
    "flag precision": _ratio("sym_wrong_flagged", "required_aligned"),
    "silent wrong per 100 gold": _ratio("silent", "gold", 100.0),
    "decisions per page": _ratio("required_all", "pages"),
}


def summarise(per_paper: dict[str, list[float]]) -> dict:
    """Every number of one group of papers: the point, a 95% cluster bootstrap over papers
    (`proof.stats`), and the counts the sample size is made of."""
    sums = np.asarray(list(per_paper.values()), dtype=float).sum(axis=0)
    out: dict = {"papers": len(per_paper), **{c: int(sums[COL[c]]) for c in COLUMNS}}
    for name, statistic in METRICS.items():
        interval = stats.cluster_bootstrap(per_paper, statistic)
        out[name] = [stats.point(per_paper, statistic), interval.low, interval.high]
    return out


def flagged_by(
    rows: Sequence[dict], probs: Sequence[float], threshold: float
) -> set[tuple[str, str]]:
    return {
        (r["paper"], r["gold_id"])
        for r, p in zip(rows, probs, strict=True)
        if candidate(r) and p >= threshold
    }


def _groups(rows: Sequence[dict], probs: Sequence[float]) -> list[tuple[float, int, int]]:
    """The candidates as (probability, rows, wrong rows) groups, most likely wrong first. Rows with
    the same probability (the same features) are one group: a threshold flags all or none of them,
    so it can never favour the wrong ones among equals."""
    by_p: dict[float, list[int]] = defaultdict(lambda: [0, 0])
    for r, p in zip(rows, probs, strict=True):
        if candidate(r):
            by_p[round(float(p), 12)][0] += 1
            by_p[round(float(p), 12)][1] += bool(r["wrong"])
    return [(p, n, w) for p, (n, w) in sorted(by_p.items(), reverse=True)]


def _totals(items: Sequence[dict]) -> tuple[int, int, int]:
    return (
        sum(i["n_gold"] for i in items),
        sum(i.get("pdf_pages", 0) for i in items),
        sum(i["review"]["required_decisions"] for i in items),
    )


def choose_threshold(rows: Sequence[dict], probs: Sequence[float], items: Sequence[dict]) -> dict:
    """The threshold on `rows` (the threshold split, scored out of fold). Flags are spent from the
    most likely wrong down, a group of equal probabilities at a time. The registered goal is silent
    wrong <= TARGET_SILENT_PER_100 per 100 gold at <= MAX_DECISIONS_PER_PAGE decisions a page: the
    LEAST decisions that reach it; if none within the budget does, the most decisions the budget
    allows (the best silent rate it can buy), and `met` says which."""
    gold, pages, base_required = _totals(items)
    groups = _groups(rows, probs)
    silent = sum(w for _, _, w in groups)
    budget = max(0, math.floor(MAX_DECISIONS_PER_PAGE * pages - base_required + 1e-9))
    best, met, unbounded = (0, 1.0), None, None  # (rows, probability of the last group)
    n = caught = 0
    for p, count, wrong in groups:
        n, caught = n + count, caught + wrong
        reaches = 100 * (silent - caught) / gold <= TARGET_SILENT_PER_100
        if reaches and unbounded is None:
            unbounded = n
        if n <= budget:
            best = (n, p)
            if reaches and met is None:
                met = (n, p)
    rows_k, last_p = met if met is not None else best
    threshold = math.floor(last_p * 10000) / 10000 if rows_k else 1.0
    flagged = [r for r, p in zip(rows, probs, strict=True) if candidate(r) and p >= threshold]
    left = silent - sum(bool(r["wrong"]) for r in flagged)
    return {
        "threshold": threshold,
        "met": met is not None,
        "budget_rows": budget,
        "rows": len(flagged),
        "silent_per_100": 100 * left / gold,
        "decisions_per_page": (base_required + len(flagged)) / pages if pages else None,
        # the goal ignoring the budget: the added decisions it would take
        "unbounded_rows": unbounded,
        "unbounded_decisions_per_page": (
            (base_required + unbounded) / pages if pages and unbounded is not None else None
        ),
    }


def curve(
    rows: Sequence[dict], probs: Sequence[float], items: Sequence[dict], steps: Sequence[float]
) -> list[dict]:
    """Silent wrong per 100 gold and the share of the added decisions that were wrong, at each
    number of added decisions a page in `steps` (the most whole groups of equal probabilities
    that fit)."""
    gold, pages, _ = _totals(items)
    groups = _groups(rows, probs)
    silent = sum(w for _, _, w in groups)
    out = []
    for step in steps:
        limit = step * pages
        k = caught = 0
        for _, count, wrong in groups:
            if k + count > limit:
                break
            k, caught = k + count, caught + wrong
        out.append(
            {
                "added_per_page": step,
                "added": k,
                "silent_per_100": 100 * (silent - caught) / gold,
                "added_precision": caught / k if k else None,
            }
        )
    return out


# ============================================================================ commands


def fit_all(splits: Iterable[str] = FIT_SPLITS) -> dict:
    """Fit on the FIT splits; nothing here reads a validation split."""
    rows: list[dict] = []
    items_by_split: dict[str, list[dict]] = {}
    for name in splits:
        r, items = rows_of(SPLITS[name], with_chat=False)
        rows += r
        items_by_split[name] = items
    cand = [r for r in rows if candidate(r)]
    l2, losses = choose_l2(cand)
    x = design(cand)
    y = np.array([r["fit_wrong"] for r in cand], dtype=float)
    w, b = fit_logistic(x, y, l2)
    weights = {n: round(float(v), 2) for n, v in zip(fp.FEATURES, w, strict=True) if round(v, 2)}
    bias = round(b, 2)
    oof = cross_fit(cand, l2)
    on_threshold_split = [i for i, r in enumerate(cand) if r["split"] == THRESHOLD_SPLIT]
    t_rows = [cand[i] for i in on_threshold_split]
    # the out-of-fold probabilities come from models fit with the unrounded weights; the shipped
    # model is the rounded one. Score the threshold split out of fold with the rounded weights of
    # each fold instead? Not needed: the rounding moves a probability by under 0.01.
    t_items = items_by_split[THRESHOLD_SPLIT]
    choice = choose_threshold(t_rows, list(oof[on_threshold_split]), t_items)
    p_oof = oof
    return {
        "version": fp.VERSION,
        "l2": l2,
        "l2_losses": losses,
        "n_fit": len(cand),
        "n_wrong": int(y.sum()),
        "bias": bias,
        "weights": weights,
        "oof_auroc": auroc(y, p_oof),
        "oof_auroc_threshold_split": auroc(
            np.array([r["fit_wrong"] for r in t_rows], dtype=float), oof[on_threshold_split]
        ),
        "choice": choice,
    }


def score_rows(rows: Sequence[dict], weights: dict[str, float], bias: float) -> list[float]:
    return [fp.probability(r["features"], weights, bias) for r in rows]


def evaluate(
    name: str,
    weights: dict[str, float],
    bias: float,
    threshold: float,
    pool: Sequence[str] = (),
) -> dict:
    """Before and after on one split (or, with `pool`, on those splits together, papers kept
    apart), with the policy's constants as given."""
    if pool:
        rows, items = [], []
        for part in pool:
            r, i = rows_of(SPLITS[part])
            rows += [{**x, "paper": f"{part}/{x['paper']}"} for x in r]
            items += [{**x, "id": f"{part}/{x['id']}"} for x in i]
    else:
        rows, items = rows_of(SPLITS[name])
    probs = score_rows(rows, weights, bias)
    before = summarise(paper_vectors(rows, items, set()))
    added = flagged_by(rows, probs, threshold)
    after = summarise(paper_vectors(rows, items, added))
    cand = [(r, p) for r, p in zip(rows, probs, strict=True) if candidate(r)]
    y = np.array([r["wrong"] for r, _ in cand], dtype=float)
    chat = [r for r, _ in cand if r["chat_agree"] is not None]
    return {
        "split": name,
        "role": SPLITS[pool[0] if pool else name].role,
        "before": before,
        "after": after,
        "candidates": len(cand),
        "candidate_auroc": auroc(y, np.array([p for _, p in cand])),
        "line_context_papers": sum(_line_context(i) for i in items),
        "chat_rows": len(chat),
        "chat_disagree_wrong": sum(1 for r in chat if r["chat_agree"] is False and r["wrong"]),
        "chat_disagree": sum(1 for r in chat if r["chat_agree"] is False),
        "chat_wrong": sum(1 for r in chat if r["wrong"]),
        "curve": curve(rows, probs, items, (0.1, 0.25, 0.5, 1.0, 2.0)),
    }


def dump(path: Path, names: Sequence[str]) -> int:
    n = 0
    with path.open("w", encoding="utf-8") as out:
        for name in names:
            rows, _ = rows_of(SPLITS[name])
            for r in rows:
                out.write(
                    json.dumps({**r, **{f"f_{k}": v for k, v in r["features"].items()}}) + "\n"
                )
                n += 1
    return n


def report(fit: dict, evaluations: Sequence[dict]) -> str:
    lines = [
        f"policy {fit['version']}: l2={fit['l2']}, bias={fit['bias']}, "
        f"threshold={fit['choice']['threshold']}",
        f"weights: {fit['weights']}",
        f"fit rows (accepted, not asked): {fit['n_fit']}, wrong {fit['n_wrong']}; "
        "out-of-fold AUROC "
        f"{fit['oof_auroc']:.3f}"
        if fit["oof_auroc"] is not None
        else "",
        "",
        "| split | role | metric | before | after |",
        "|---|---|---|---|---|",
    ]

    def cell(v: list[float | None], digits: int = 2) -> str:
        if v[0] is None:
            return "n/a"
        return f"{v[0]:.{digits}f} [{v[1]:.{digits}f}, {v[2]:.{digits}f}]"

    for e in evaluations:
        b, a = e["before"], e["after"]
        size = f"n={b['gold']} gold, {b['papers']} papers, {b['pages']} pages"
        for metric in METRICS:
            lines.append(
                f"| {e['split']} ({size}) | {e['role']} | {metric} | {cell(b[metric])} | "
                f"{cell(a[metric])} |"
            )
        lines.append(
            f"| {e['split']} | {e['role']} | added decisions | 0 | "
            f"{a['added']} ({a['added_wrong']} wrong) |"
        )
    return "\n".join(line for line in lines)


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("dump", help="write one JSON line of features per aligned equation")
    d.add_argument("out", type=Path)
    d.add_argument("--splits", default=",".join(SPLITS))
    for name in ("fit", "report"):
        p = sub.add_parser(name)
        p.add_argument("--json", type=Path)
    args = ap.parse_args(argv)
    if args.cmd == "dump":
        n = dump(args.out, args.splits.split(","))
        print(f"{n} rows -> {args.out}")
        return 0
    fit = fit_all()
    if args.cmd == "fit":
        print(json.dumps(fit, indent=2))
        if args.json:
            args.json.write_text(json.dumps(fit, indent=2), encoding="utf-8")
        return 0
    evaluations = [
        evaluate(n, fit["weights"], fit["bias"], fit["choice"]["threshold"]) for n in SPLITS
    ]
    evaluations.append(
        evaluate(
            "fresh2-4",
            fit["weights"],
            fit["bias"],
            fit["choice"]["threshold"],
            pool=VALIDATE_SPLITS,
        )
    )
    print(
        "shipped constants equal this fit:",
        fit["weights"] == fp.WEIGHTS
        and fit["bias"] == fp.BIAS
        and fit["choice"]["threshold"] == fp.THRESHOLD,
    )
    print(report(fit, evaluations))
    if args.json:
        args.json.write_text(
            json.dumps({"fit": fit, "evaluations": evaluations}, indent=2), encoding="utf-8"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
