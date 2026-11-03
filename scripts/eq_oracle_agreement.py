"""D2: would an independent whole-page reading fix and flag our errors?
(docs/eval-equation-diagnostics.md)

    uv run python scripts/eq_oracle_agreement.py [--parts dev2,heldout,fresh2,fresh3,fresh4]
        [--run fresh4=after] [--chat-arm chatbot-opus-purdue] [--recorded]
        [--at tree|notation|spoken] [--json OUT.json]

The recorded Opus chatbot answers (the whole PDF in, LaTeX out, no checks) are one independent
reading of every page of the papers it was run on. Each is aligned to the gold by the document
task's scorer, as our own recorded run is, so both readings of a gold equation can be compared
with the canonical forms of `pipeline/math_canonical.py`. On the papers both arms ran on, per
split and pooled (dev is left out of the pool when dev2 is there: the same 25 papers):

(a) union oracle: the share of gold equations read right (`exact_lenient`) by ours or by the
    chatbot, against ours alone: the ceiling of combining the two;
(b) P(ours correct | agreement): the share right among the equations where both readings are
    the same under canonical comparison (`Kind.NOTATION`: canonical LaTeX or one MathML tree) and
    the two spans are within one glyph (below); also without the span condition, and the share
    of the chatbot's own readings right under agreement;
(c) "disagree -> ask the reviewer" as a detector of our wrong readings: recall (share of our
    wrong readings that disagree) and precision (share of disagreements that are our wrong
    readings), for disagreement where both read the equation and for "not agree" (a missing
    witness counts); and how many of our silent errors (wrong, no required decision) it adds;
(d) detection gain: the share of the gold equations we did not detect that the chatbot did
    (and read right).

Span within one glyph: the chatbot gives no extent (a page witness will, anchored by a quote), so
the stand-in is the characters. Our span's text layer (`pred_text`) and the chatbot's reading are
reduced to the symbols of `pipeline_eval.text_signature`; they are within one glyph when at most
one symbol of either is not matched in the other. An empty text layer (a picture) cannot be
checked and does not pass. Both readings aligned to the same gold equation already means that
their extents overlap it.

Intervals: 95% cluster bootstrap over papers (`proof.stats`), a paper being one cluster; the sample
size (equations and papers) is beside every number. Reads recorded items only; calls no model.
The go/no-go for the paid page-witness experiment: union at least +4 points over ours and
P(correct | agree) at least 0.97 (pooled).
"""

from __future__ import annotations

import argparse
import difflib
import json
import sys
from pathlib import Path

import eq_diag_common as common
import numpy as np

from readablestem.eval import pipeline_eval as pe
from readablestem.pipeline import math_canonical as mc
from readablestem.proof import stats

CHAT_PARTS = ("dev", "dev2", "heldout", "fresh2", "fresh3", "fresh4")
DEFAULT_ARM = "chatbot-opus-purdue"
UNION_GATE = 0.04
AGREE_GATE = 0.97

COLUMNS = (
    "gold",  # gold equations on the papers both arms ran on
    "ours_right",
    "chat_right",
    "union_right",
    "ours_read",  # gold equations ours aligned to
    "both_read",  # ... and the chatbot too
    "agree",  # both read, canonical agreement
    "agree_span",  # ... and spans within one glyph
    "agree_ours_right",
    "agree_chat_right",
    "agree_span_ours_right",
    "agree_span_chat_right",
    "ours_wrong",  # ours aligned and not right
    "wrong_disagree",  # ... both read and not agreeing (canonical, or span)
    "disagree",  # both read and not agreeing
    "wrong_not_agree",  # ours wrong and (disagree or the chatbot has no reading)
    "not_agree",  # ours read and (disagree or no chatbot reading)
    "silent_wrong",  # ours wrong with no required decision
    "silent_not_agree",  # ... of those, not agreeing
    "silent_disagree",
    "undetected",  # gold ours did not align to
    "undetected_chat_found",
    "undetected_chat_right",
    "wrong_disagree_canon",  # ours wrong, both read, canonical forms differ (span ignored)
    "disagree_canon",
    "ours_wrong_chat_right",  # ours wrong, both read, the chatbot right
    "ours_wrong_chat_right_disagree",
)
COL = {name: i for i, name in enumerate(COLUMNS)}


# ============================================================================ the pair test


def glyphs_within_one(span_text: str, latex: str, display: bool = False) -> bool | None:
    """Are the symbols of `span_text` (our span's text layer) and of the reading `latex` the same
    to within one symbol? None when there is no text layer to check."""
    if not span_text.strip():
        return None
    mathml = mc.to_mathml(latex, display=display)
    other = common.signature(pe.math_signature(mathml))
    mine = common.signature(span_text)
    if not other:
        return None
    blocks = difflib.SequenceMatcher(None, mine, other, autojunk=False).get_matching_blocks()
    matched = sum(b.size for b in blocks)
    return max(len(mine), len(other)) - matched <= 1


def read_pair(ours: dict, chat: dict | None, *, at: mc.Kind = mc.Kind.NOTATION) -> dict:
    """How the two readings of one gold equation stand. `chat` None: the chatbot has none."""
    display = ours.get("gold_display") == "block"
    out = {
        "ours_right": bool(ours["exact_lenient"]),
        "chat_right": bool(chat["exact_lenient"]) if chat else False,
        "chat_read": chat is not None,
        "canon": False,
        "span": False,
        "required": ours.get("review") == "required",
    }
    if chat is None:
        return out
    a, b = ours.get("pred_latex") or "", chat.get("pred_latex") or ""
    out["canon"] = mc.compare(a, b, display=display).holds_at(at)
    # the span stand-in: our text layer against the chatbot's reading, and ours against the
    # chatbot's text layer is not recorded, so the one test stands for the pair
    out["span"] = bool(glyphs_within_one(ours.get("pred_text") or "", b, display))
    return out


def paper_vector(
    ours_item: dict, chat_item: dict, *, at: mc.Kind = mc.Kind.NOTATION
) -> list[float]:
    chat_pairs = {p["gold_id"]: p for p in chat_item["pairs"]}
    our_pairs = {p["gold_id"]: p for p in ours_item["pairs"]}
    v = {name: 0 for name in COLUMNS}
    v["gold"] = ours_item["n_gold"]
    for gid, pair in our_pairs.items():
        r = read_pair(pair, chat_pairs.get(gid), at=at)
        agree = r["chat_read"] and r["canon"] and r["span"]
        disagree = r["chat_read"] and not agree
        not_agree = not agree
        v["ours_read"] += 1
        v["ours_right"] += r["ours_right"]
        v["not_agree"] += not_agree
        if r["chat_read"]:
            v["both_read"] += 1
            v["disagree"] += disagree
            v["disagree_canon"] += not r["canon"]
            if r["canon"]:
                v["agree"] += 1
                v["agree_ours_right"] += r["ours_right"]
                v["agree_chat_right"] += r["chat_right"]
            if agree:
                v["agree_span"] += 1
                v["agree_span_ours_right"] += r["ours_right"]
                v["agree_span_chat_right"] += r["chat_right"]
        if not r["ours_right"]:
            v["ours_wrong"] += 1
            v["wrong_disagree"] += disagree
            v["wrong_not_agree"] += not_agree
            v["wrong_disagree_canon"] += r["chat_read"] and not r["canon"]
            v["ours_wrong_chat_right"] += r["chat_right"]
            v["ours_wrong_chat_right_disagree"] += r["chat_right"] and disagree
            if not r["required"]:
                v["silent_wrong"] += 1
                v["silent_not_agree"] += not_agree
                v["silent_disagree"] += disagree
    # gold equations by id: right in ours or the chatbot, over every gold of the paper
    gold_ids = set(ours_item.get("missed_gold_ids", ())) | set(our_pairs)
    v["union_right"] = sum(
        bool(
            our_pairs.get(g, {}).get("exact_lenient") or chat_pairs.get(g, {}).get("exact_lenient")
        )
        for g in gold_ids
    )
    v["chat_right"] = sum(bool(chat_pairs.get(g, {}).get("exact_lenient")) for g in gold_ids)
    for gid in ours_item.get("missed_gold_ids", ()):
        v["undetected"] += 1
        found = chat_pairs.get(gid)
        v["undetected_chat_found"] += found is not None
        v["undetected_chat_right"] += bool(found and found["exact_lenient"])
    return [float(v[name]) for name in COLUMNS]


# ============================================================================ the numbers


def ratio_of(num: str | tuple[str, ...], den: str):
    nums = (num,) if isinstance(num, str) else num
    ni, d = [COL[n] for n in nums], COL[den]

    def statistic(sums: np.ndarray) -> float | None:
        return float(sums[ni].sum() / sums[d]) if sums[d] else None

    return statistic


def union_gain(sums: np.ndarray) -> float | None:
    g = sums[COL["gold"]]
    return float((sums[COL["union_right"]] - sums[COL["ours_right"]]) / g) if g else None


def precision_statistic(sums: np.ndarray) -> float | None:
    return ratio_of("wrong_disagree", "disagree")(sums)


def fmt(value: float | None, interval: stats.Interval | None = None) -> str:
    if value is None:
        return "-"
    text = f"{100 * value:.1f}%"
    if interval is not None and interval.low is not None and interval.high is not None:
        text += f" [{100 * interval.low:.1f}, {100 * interval.high:.1f}]"
    return text


def pts(value: float | None, interval: stats.Interval | None = None) -> str:
    if value is None:
        return "-"
    text = f"{100 * value:+.1f}"
    if interval is not None and interval.low is not None and interval.high is not None:
        text += f" [{100 * interval.low:+.1f}, {100 * interval.high:+.1f}]"
    return text


def summarise(per_paper: dict[str, list[float]]) -> dict:
    """Every number of one group of papers with its interval and sample size."""
    sums = np.asarray(list(per_paper.values()), dtype=float).sum(axis=0) if per_paper else None
    if sums is None:
        return {"papers": 0}

    def n(name: str) -> int:
        return int(sums[COL[name]])

    def rate(statistic):
        point = stats.point(per_paper, statistic)
        return point, stats.cluster_bootstrap(per_paper, statistic)

    out: dict = {"papers": len(per_paper)}
    for name in ("gold", "ours_read", "both_read", "agree", "agree_span", "ours_wrong", "disagree"):
        out[name] = n(name)
    out["undetected"] = n("undetected")
    out["silent_wrong"] = n("silent_wrong")
    stat_table = {
        "ours": ratio_of("ours_right", "gold"),
        "chatbot": ratio_of("chat_right", "gold"),
        "union": ratio_of("union_right", "gold"),
        "union_gain": union_gain,
        "agree_correct": ratio_of("agree_ours_right", "agree"),
        "agree_span_correct": ratio_of("agree_span_ours_right", "agree_span"),
        "agree_span_chat_correct": ratio_of("agree_span_chat_right", "agree_span"),
        "recall_disagree": ratio_of("wrong_disagree", "ours_wrong"),
        "precision_disagree": precision_statistic,
        "recall_not_agree": ratio_of("wrong_not_agree", "ours_wrong"),
        "precision_not_agree": ratio_of("wrong_not_agree", "not_agree"),
        "silent_recall_not_agree": ratio_of("silent_not_agree", "silent_wrong"),
        "silent_recall_disagree": ratio_of("silent_disagree", "silent_wrong"),
        "recall_disagree_canon": ratio_of("wrong_disagree_canon", "ours_wrong"),
        "precision_disagree_canon": ratio_of("wrong_disagree_canon", "disagree_canon"),
        "wrong_fixable": ratio_of("ours_wrong_chat_right", "ours_wrong"),
        "detect_found": ratio_of("undetected_chat_found", "undetected"),
        "detect_right": ratio_of("undetected_chat_right", "undetected"),
        "agree_share": ratio_of("agree_span", "ours_read"),
    }
    for key, statistic in stat_table.items():
        out[key] = rate(statistic)
    return out


def gate(summary: dict) -> dict:
    """The go/no-go on the pooled numbers (point estimates; the intervals are shown beside)."""
    gain = summary["union_gain"][0]
    agree = summary["agree_span_correct"][0]
    return {
        "union_gain": gain,
        "agree_correct": agree,
        "union_ok": gain is not None and gain >= UNION_GATE,
        "agree_ok": agree is not None and agree >= AGREE_GATE,
        "go": bool(
            gain is not None and agree is not None and gain >= UNION_GATE and agree >= AGREE_GATE
        ),
    }


# ============================================================================ loading


def chat_items(part: str, arm: str, recorded: bool) -> list[dict]:
    if recorded:
        path = common.ROOT / "runs" / part / "chat" / f"{arm}.items.jsonl"
        return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]
    return common._load_script("realworld_run").chat_items(part, arm)


def vectors(
    part: str, run: str, arm: str, recorded: bool, at: mc.Kind = mc.Kind.NOTATION
) -> dict[str, list[float]]:
    split = common.load_split(part, run)
    chat = {i["id"]: i for i in chat_items(part, arm, recorded)}
    out = {}
    for item in split.items:
        if item["id"] in chat:
            out[f"{part}/{item['id']}"] = paper_vector(item, chat[item["id"]], at=at)
    return out


# ============================================================================ the report


def report(summaries: dict[str, dict]) -> str:
    cols = list(summaries)
    lines: list[str] = []

    def row(label: str, getter) -> None:
        lines.append(f"| {label} | " + " | ".join(getter(summaries[c]) for c in cols) + " |")

    def head() -> None:
        lines.append("| | " + " | ".join(cols) + " |")
        lines.append("|---|" + "---|" * len(cols))

    def rate(key: str, scale=fmt):
        return lambda s: scale(*s[key]) if key in s else "-"

    head()
    row("papers", lambda s: str(s["papers"]))
    row("gold equations", lambda s: str(s.get("gold", "-")))
    lines.append("| **(a) union oracle** | " + " | ".join("" for _ in cols) + " |")
    row("ours right", rate("ours"))
    row("chatbot right", rate("chatbot"))
    row("union right", rate("union"))
    row("union minus ours, points", rate("union_gain", pts))
    lines.append("| **(b) agreement** | " + " | ".join("" for _ in cols) + " |")
    row("ours read / both read", lambda s: f"{s.get('ours_read', '-')} / {s.get('both_read', '-')}")
    row("canonical agree (n)", lambda s: str(s.get("agree", "-")))
    row("agree and span within one glyph (n)", lambda s: str(s.get("agree_span", "-")))
    row("P(ours correct | canonical agree)", rate("agree_correct"))
    row("P(ours correct | agree and span)", rate("agree_span_correct"))
    row("P(chatbot correct | agree and span)", rate("agree_span_chat_correct"))
    row("share of our readings that agree", rate("agree_share"))
    lines.append("| **(c) disagree, ask the reviewer** | " + " | ".join("" for _ in cols) + " |")
    row("our wrong readings (n)", lambda s: str(s.get("ours_wrong", "-")))
    row("disagreements, both read (n)", lambda s: str(s.get("disagree", "-")))
    row("recall, disagree (span counted)", rate("recall_disagree"))
    row("precision, disagree (span counted)", rate("precision_disagree"))
    row("recall, canonical forms differ only", rate("recall_disagree_canon"))
    row("precision, canonical forms differ only", rate("precision_disagree_canon"))
    row("recall, not agree (missing witness counts)", rate("recall_not_agree"))
    row("precision, not agree", rate("precision_not_agree"))
    row("our silent errors (n)", lambda s: str(s.get("silent_wrong", "-")))
    row("silent errors caught, disagree", rate("silent_recall_disagree"))
    row("silent errors caught, not agree", rate("silent_recall_not_agree"))
    row("our wrong readings the chatbot read right", rate("wrong_fixable"))
    lines.append("| **(d) detection gain** | " + " | ".join("" for _ in cols) + " |")
    row("our undetected gold (n)", lambda s: str(s.get("undetected", "-")))
    row("chatbot found (aligned)", rate("detect_found"))
    row("chatbot found and right", rate("detect_right"))
    return "\n".join(lines)


def _plain(obj):
    if isinstance(obj, stats.Interval):
        return {"low": obj.low, "high": obj.high, "papers": obj.n_sections}
    raise TypeError(type(obj))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--parts", default="dev,dev2,heldout,fresh2,fresh3,fresh4")
    parser.add_argument("--run", action="append", default=[], metavar="PART=NAME")
    parser.add_argument("--chat-arm", default=DEFAULT_ARM)
    parser.add_argument(
        "--recorded", action="store_true", help="the chatbot item files as recorded, not rescored"
    )
    parser.add_argument(
        "--at",
        default="notation",
        choices=[k.value for k in (mc.Kind.TREE, mc.Kind.NOTATION, mc.Kind.SPOKEN)],
        help="the kind of sameness that counts as agreement (default notation)",
    )
    parser.add_argument("--json", type=Path, help="write the numbers here")
    args = parser.parse_args(argv)
    if not common.ROOT.is_dir():
        print(f"no real-world cache at {common.ROOT}", file=sys.stderr)
        return 2
    runs = common.parse_runs(args.run)
    parts = [p for p in args.parts.split(",") if p]
    unknown = [p for p in parts if p not in CHAT_PARTS]
    if unknown:
        raise SystemExit(f"no chatbot answers for {', '.join(unknown)}")
    per_part = {
        p: vectors(p, runs[p], args.chat_arm, args.recorded, mc.Kind(args.at)) for p in parts
    }
    summaries = {p: summarise(v) for p, v in per_part.items()}
    pool_parts = [p for p in common.pooled([{"part": p} for p in parts])]
    pooled: dict[str, list[float]] = {}
    for r in pool_parts:
        pooled.update(per_part[r["part"]])
    summaries["pooled"] = summarise(pooled)
    verdict = gate(summaries["pooled"])
    print(report(summaries))
    print()
    print(f"pooled: {', '.join(r['part'] for r in pool_parts)} ({common.POOL_NOTE})")
    print(
        f"gate (pooled): union gain {pts(verdict['union_gain'])} points "
        f"(need +{100 * UNION_GATE:.0f}), "
        f"P(correct | agree, span) {fmt(verdict['agree_correct'])} (need {AGREE_GATE:.2f}) -> "
        + ("GO" if verdict["go"] else "NO-GO")
    )
    if args.json:
        args.json.write_text(
            json.dumps({"summaries": summaries, "gate": verdict}, indent=1, default=_plain),
            encoding="utf-8",
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
