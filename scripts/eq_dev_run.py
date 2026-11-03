"""Page witness on or off over twelve development papers (experiment E1; development part only).

    R="uv run python scripts/eq_dev_run.py"
    $R papers
    $R run control --offline                       # replay only: what the recordings can answer
    $R run control --max-usd 0.8                   # witness off, crop answers replayed
    $R run witness-opus --max-usd 3              # FEATURE_PAGE_WITNESS on, PAGE_WITNESS_MODEL opus
    $R run witness-sonnet --max-usd 1.0
    $R report                                      # numbers of every recorded arm

The papers are the first twelve of the development part's registered chat order
(`realworld_run.chat_order`), which starts with the six papers the Opus chatbot was recorded on, so
the chatbot comparison has a shared subset. Every arm runs ReadableSTEM as production has it for
typed PDFs (tables, notation memory and line context on; no second check) through the document task,
and starts from the answers of an earlier arm (`--seed-from`, default the recorded `line1` run, then
the control), so a request already answered replays free and only what differs is paid for. The
witness arms differ from the control in `FEATURE_PAGE_WITNESS` (and `PAGE_WITNESS_MODEL`) only.

A paid run goes through `railway run -s web -- ...` when no local key is set. Every setting in the
environment that the application reads is removed first (except the keys), so production's own
values for budgets, models or flags cannot change a request: the run is what this script says.
Spend of each start is appended to `data/realworld/spend-log.jsonl` (by `realworld_run`).

Nothing here may be used on the held-out or fresh parts.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

PART = "dev"
N_PAPERS = 12
SEED_DEFAULT = "line1"
# arm -> (tag, FEATURE_PAGE_WITNESS, PAGE_WITNESS_MODEL, seed answers from)
ARMS = {
    "control": ("e1-control", False, None, SEED_DEFAULT),
    # `e1-witness-opus` is the first Opus start (60 s call timeout: most witness calls timed out,
    # see docs/eval-equation-round5.md); it is kept for its answers and is not an arm
    "witness-opus": ("e1-opus", True, "claude-opus-5-5", "e1-witness-opus"),
    "witness-sonnet": ("e1-sonnet", True, "claude-sonnet-5-5", "e1-witness-opus"),
}
KEEP_ENV = {"ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL"}
LABELS = {
    "e1-control": "Control (witness off)",
    "e1-opus": "Witness, Opus",
    "e1-sonnet": "Witness, Sonnet",
}


def scrub_environment() -> list[str]:
    """Drop every variable the application's settings read, except the API key, so the run does
    not take production's values. Returns the names removed (never their values)."""
    from readablestem.config import Settings

    names = {name.upper() for name in Settings.model_fields}
    for field in Settings.model_fields.values():
        alias = field.validation_alias
        if isinstance(alias, str):
            names.add(alias.upper())
    removed = sorted(n for n in os.environ if n.upper() in names and n not in KEEP_ENV)
    for name in removed:
        del os.environ[name]
    return removed


def paper_ids() -> list[str]:
    import realworld_run as rr

    return rr.chat_order(PART)[:N_PAPERS]


def cmd_papers(_: argparse.Namespace) -> int:
    import realworld_run as rr

    ids = paper_ids()
    m = rr.manifest(PART)
    by_id = {p["id"]: p for p in m["papers"]}
    print(f"{len(ids)} of the {m['n_papers']} development papers: {','.join(ids)}")
    chat = [
        json.loads(line)["id"]
        for line in (rr.runs_dir(PART) / "chat" / "chatbot-opus-purdue.items.jsonl")
        .read_text()
        .splitlines()
        if line
    ]
    shared = [i for i in ids if f"arxiv/{i}" in chat]
    print(f"{len(shared)} of them have a recorded Opus chatbot answer: {','.join(shared)}")
    pages = sum(len(by_id[i].get("pages", [])) for i in ids)
    print(f"pages (manifest listing): {pages or 'see the items files'}")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    tag, witness, model, seed = ARMS[args.arm]
    removed = scrub_environment()
    print(f"removed {len(removed)} application settings from the environment")
    os.environ["FEATURE_PAGE_WITNESS"] = "true" if witness else "false"
    if model:
        os.environ["PAGE_WITNESS_MODEL"] = model
    if not os.environ.get("ANTHROPIC_API_KEY"):
        if not args.offline:
            raise SystemExit("no ANTHROPIC_API_KEY: run through `railway run -s web -- ...`")
        os.environ["ANTHROPIC_API_KEY"] = "replay-only"  # the client is built, never called
    import realworld_run as rr

    ns = argparse.Namespace(
        part=PART, max_usd=args.max_usd, tag=tag, offline=args.offline,
        page_math=False, tables=True, notation_memory=True, line_context=True,
        no_agreement_lift=False, page_math_tokens=None,
        seed_answers_from=args.seed_from or seed,
        papers=",".join(args.papers.split(",") if args.papers else paper_ids()),
        section_workers=args.workers, concurrency=args.concurrency,
        note=f"E1 {args.arm}: {args.note}".strip(": "),
    )  # fmt: skip
    return rr.cmd_rs(ns)


def cmd_report(args: argparse.Namespace) -> int:
    import realworld_report as rp

    keep = set(paper_ids())
    every = [p["id"] for p in __import__("realworld_run").papers(PART)]
    exclude = [i for i in every if i not in keep]
    tags = [t for t, *_ in ARMS.values() if (rp.ROOT / "runs" / PART / t / "items.jsonl").is_file()]
    if not tags:
        raise SystemExit("no arm has been recorded yet")
    base = tags[0]
    argv = ["realworld_report.py", "--part", PART, "--tag", base, "--rescore-chat", "--exclude"]
    argv += exclude
    for t in tags[1:]:
        argv += ["--extra-arm", f"{LABELS[t]}={t}"]
    if args.reading != "registered":
        argv += ["--reading", args.reading]
    if args.neutral:
        argv.append("--neutral")
    if args.spoken:
        argv.append("--spoken")
    sys.argv = argv
    print(f"arms: {', '.join(LABELS[t] for t in tags)} (ReadableSTEM column is {LABELS[base]})")
    return rp.main()


WITNESS_CODES = ("witness_mismatch", "witness_extent_differs", "witness_added")


def cmd_witness(args: argparse.Namespace) -> int:
    """What the witness changed, pair by pair, against the control on the same papers: readings
    that changed, decisions it added (and how many were wrong equations), detection additions."""
    import realworld_run as rr

    def load(tag: str) -> dict[str, dict]:
        path = rr.runs_dir(PART) / tag / "items.jsonl"
        rows = [json.loads(x) for x in path.read_text().splitlines() if x]
        return {r["id"]: r for r in rows}

    base, arm = load(ARMS["control"][0]), load(args.tag)
    stats = {
        "pairs": 0,
        "changed_reading": 0,
        "changed_right_to_wrong": 0,
        "changed_wrong_to_right": 0,
    }
    codes = {c: [0, 0] for c in WITNESS_CODES}  # [decisions, of them on a wrong equation]
    new_flags = [0, 0]  # required with the witness, optional or none without: [n, wrong]
    no_witness_code = [0, 0]  # accepted with no witness code: [n, right]
    summary: dict[str, float] = {}
    for paper, row in arm.items():
        before = {p["gold_id"]: p for p in base[paper]["pairs"]}
        for key, value in (row.get("page_witness") or {}).items():
            if isinstance(value, int | float):
                summary[key] = summary.get(key, 0) + value
        for pair in row["pairs"]:
            stats["pairs"] += 1
            old = before.get(pair["gold_id"])
            reasons = set(pair.get("tier_reasons", []))
            for code in WITNESS_CODES:
                if code in reasons:
                    codes[code][0] += 1
                    codes[code][1] += not pair["exact_lenient"]
            if not reasons & set(WITNESS_CODES):
                no_witness_code[0] += 1
                no_witness_code[1] += bool(pair["exact_lenient"])
            if old is None:
                continue
            if old["pred_latex"] != pair["pred_latex"]:
                stats["changed_reading"] += 1
                stats["changed_right_to_wrong"] += (
                    bool(old["exact_lenient"]) and not pair["exact_lenient"]
                )
                stats["changed_wrong_to_right"] += not old["exact_lenient"] and bool(
                    pair["exact_lenient"]
                )
            if pair.get("review") == "required" and old.get("review") != "required":
                new_flags[0] += 1
                new_flags[1] += not pair["exact_lenient"]
    gold = sum(r["n_gold"] for r in arm.values())
    matched_base = sum(len(r["pairs"]) for r in base.values())
    out = {
        "arm": args.tag, "papers": len(arm), "gold": gold,
        "aligned_control": matched_base, "aligned_arm": stats["pairs"], **stats,
        "witness_codes_decisions_and_wrong": codes,
        "new_required_and_wrong": new_flags,
        "no_witness_code_n_and_right": no_witness_code,
        "witness_summary": summary,
    }  # fmt: skip
    print(json.dumps(out, indent=1, default=float))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("papers").set_defaults(func=cmd_papers)
    run = sub.add_parser("run")
    run.add_argument("arm", choices=sorted(ARMS))
    run.add_argument("--max-usd", type=float, default=0.0)
    run.add_argument("--offline", action="store_true", help="replay only; a miss is refused")
    run.add_argument("--workers", type=int, default=2, help="papers converted at once")
    run.add_argument(
        "--concurrency",
        type=int,
        default=4,
        help="threads inside one paper (witness pages in flight; each holds its worst case)",
    )
    run.add_argument("--seed-from", help="tag whose answers this arm starts from")
    run.add_argument("--papers", help="comma-separated ids, a subset of the twelve (a cost probe)")
    run.add_argument("--note", default="")
    run.set_defaults(func=cmd_run)
    wit = sub.add_parser("witness")
    wit.add_argument("--tag", default=ARMS["witness-sonnet"][0])
    wit.set_defaults(func=cmd_witness)
    rep = sub.add_parser("report")
    rep.add_argument("--reading", choices=["registered", "cells"], default="registered")
    rep.add_argument("--neutral", action="store_true", help="the notation-neutral reading")
    rep.add_argument("--spoken", action="store_true", help="the spoken reading (exploratory)")
    rep.set_defaults(func=cmd_report)
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
