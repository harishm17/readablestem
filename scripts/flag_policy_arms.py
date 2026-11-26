"""Three arms of the flag policy on a recorded replay of the development papers
(docs/eval-flag-policy.md, "2026-11-25"). No model call: it reads `items.jsonl` and `trace.jsonl`
of an equation replay (`dbg_eq.py ARM --offline`, docs/eval.md).

    uv run --no-sync python scripts/flag_policy_arms.py REPLAY_FOLDER

The rows are the paired equations nobody was asked about before the policy (the ones the policy
flagged count as accepted). Features come from the function the step calls
(`flag_policy.features`). Arm 1 is the standing constants, arm 2 a refit with the fitting script's
own steps, leaving one paper out, arm 3 no policy. The label is the scorer's `exact_printed`;
`ARTEFACT` names the wrong ones that are faults of the gold or of the span, judged by hand.
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import numpy as np

from readablestem.eval import flag_features as ff
from readablestem.pipeline import flag_policy as fp

ARTEFACT = {
    "arxiv/2305.06353/eq-191",  # the gold has a misplaced brace; the reading is right
    "arxiv/2401.11209/eq-023",  # the gold stops before the closing bracket; the reading is right
    "arxiv/2407.09859/eq-037",  # span edge: the bracket is in the text layer, not in the gold
    "arxiv/2504.04210/eq-014",  # the gold begins with a symbol outside the span
}


def read_rows(folder: Path) -> tuple[list[dict], list[dict]]:
    lines = (folder / "items.jsonl").read_text(encoding="utf-8").splitlines()
    items = [json.loads(x) for x in lines if x.strip()]
    trace = {}
    for line in (folder / "trace.jsonl").read_text(encoding="utf-8").splitlines():
        if line.strip():
            row = json.loads(line)
            trace[(row["doc"], row["element_id"])] = row
    rows = []
    for item in items:
        for pair in item["pairs"]:
            traced = trace.get((item["id"], pair["element_id"]))
            if pair["pairing_miss"] or traced is None or not pair.get("pred_mathml"):
                continue
            by_policy = "flag_policy" in (pair.get("tier_reasons") or [])
            asked = pair.get("review") == "required" or pair.get("status") != "accepted"
            if asked and not by_policy:
                continue
            feats = fp.features(
                display=pair.get("pred_display") or pair.get("gold_display"),
                escalation=pair.get("escalation"),
                codes=[c for c, _ in traced["failed"] if c != "flag_policy"],
                text=pair.get("pred_text"),
                latex=pair["pred_latex"],
                mathml=pair["pred_mathml"],
            )
            wrong = not pair["exact_printed"]
            rows.append(
                {
                    "split": "dev",
                    "paper": item["id"],
                    "gold_id": pair["gold_id"],
                    "review": "none",
                    "status": "accepted",
                    "wrong": wrong,
                    "real": wrong and pair["gold_id"] not in ARTEFACT,
                    "fit_wrong": wrong,
                    "by_policy": by_policy,
                    "features": feats,
                }
            )
    return rows, items


def blind_zero(n_all: int, n_wrong: int, k: int) -> float:
    """P(no wrong one among k picked blind from n_all, n_wrong of them wrong)."""
    return math.comb(n_all - n_wrong, k) / math.comb(n_all, k)


def blind_at_least(n_all: int, n_wrong: int, k: int, x: int) -> float:
    top = min(k, n_wrong)
    hits = sum(math.comb(n_wrong, i) * math.comb(n_all - n_wrong, k - i) for i in range(x, top + 1))
    return hits / math.comb(n_all, k)


def refit_one_paper_out(rows: list[dict], items: list[dict], label: str) -> tuple[np.ndarray, int]:
    """Out-of-paper probabilities from the fitting script's own steps (penalty by out-of-fold
    log loss, weights, threshold), and how many rows pass the thresholds it chose."""
    for row in rows:
        row["fit_wrong"] = row[label]
    probs = np.zeros(len(rows))
    flagged = 0
    for held in sorted({r["paper"] for r in rows}):
        train = [r for r in rows if r["paper"] != held]
        if not any(r["fit_wrong"] for r in train):
            continue
        l2, _ = ff.choose_l2(train)
        y = np.array([r["fit_wrong"] for r in train], dtype=float)
        weights, bias = ff.fit_logistic(ff.design(train), y, l2)
        named: dict[str, float] = {n: float(v) for n, v in zip(fp.FEATURES, weights, strict=True)}
        oof = ff.cross_fit(train, l2)
        cut = ff.choose_threshold(train, list(oof), [i for i in items if i["id"] != held])
        for i, row in enumerate(rows):
            if row["paper"] == held:
                probs[i] = fp.probability(row["features"], named, bias)
                flagged += probs[i] >= cut["threshold"]
    return probs, flagged


def caught(rows: list[dict], picked: list[int]) -> tuple[int, int]:
    return sum(rows[i]["wrong"] for i in picked), sum(rows[i]["real"] for i in picked)


def main() -> None:
    rows, items = read_rows(Path(sys.argv[1]))
    n = len(rows)
    n_wrong, n_real = sum(r["wrong"] for r in rows), sum(r["real"] for r in rows)
    y_wrong = np.array([r["wrong"] for r in rows], dtype=float)
    y_real = np.array([r["real"] for r in rows], dtype=float)
    print(f"candidates {n}; wrong {n_wrong}; real misreads {n_real}")

    standing = np.array([fp.probability(r["features"]) for r in rows])
    picked = [i for i in range(n) if standing[i] >= fp.THRESHOLD]
    k = len(picked)
    w, r_ = caught(rows, picked)
    print(
        f"arm 1 standing policy: flags {k} (recorded: {sum(r['by_policy'] for r in rows)}), "
        f"wrong {w}, real {r_}"
    )
    print(
        f"  0 of {k} blind picks: {blind_zero(n, n_wrong, k):.3f} (wrong), "
        f"{blind_zero(n, n_real, k):.3f} (real)"
    )
    rank = sorted(range(n), key=lambda i: -standing[i])
    for i, row in enumerate(rows):
        if row["wrong"]:
            print(
                f"  wrong {row['gold_id']} real={row['real']} p={standing[i]:.3f} "
                f"rank {rank.index(i) + 1} of {n}"
            )
    print(f"  AUROC wrong {ff.auroc(y_wrong, standing):.2f}, real {ff.auroc(y_real, standing):.2f}")

    for label in ("wrong", "real"):
        probs, flagged = refit_one_paper_out(rows, items, label)
        order = [int(i) for i in np.argsort(-probs, kind="stable")]
        print(
            f"arm 2 refit (label {label}): flags at the script's thresholds {flagged}; "
            f"AUROC wrong {ff.auroc(y_wrong, probs):.2f}, real {ff.auroc(y_real, probs):.2f}"
        )
        for count in (k, round(0.05 * n), round(0.10 * n)):
            w, r_ = caught(rows, order[:count])
            print(
                f"  top {count} by out-of-paper probability: wrong {w}, real {r_}; blind "
                f"P(at least that many) {blind_at_least(n, n_wrong, count, w):.3f}, "
                f"{blind_at_least(n, n_real, count, r_):.3f}"
            )
    print(f"arm 3 no policy: flags 0, wrong accepted {n_wrong}, real {n_real}")


if __name__ == "__main__":
    main()
