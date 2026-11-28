"""O-490: the held-out equation parts (fresh5, strata A to C) under the production profile of
pipeline 0.5.23, flag policy off as shipped, and the same answers replayed with it on.

    uv run --no-sync python -I scripts/flag_policy_heldout.py TAG --part fresh5a [--offline]
        [--flag-policy] [--max-usd X] [--seed-from TAG ...]

TAG names the run folder under runs/<part>/. The production profile is used as it is, except
that the second automated check is off (it is shadow-only and changes no output; the
registered fresh5 arms had it off too) and, with --flag-policy, FLAG_POLICY is on. The answers
of --seed-from (default: the registered control and candidate) are copied in first, so a
request already answered replays free. Prints counts and spend only; reads no gold itself.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path.cwd()
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "src"))

import realworld_run as rr  # noqa: E402

PARTS = ("fresh5a", "fresh5b", "fresh5c")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("tag")
    parser.add_argument("--part", choices=PARTS, required=True)
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--flag-policy", action="store_true")
    parser.add_argument("--max-usd", type=float, default=0.01)
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--seed-from", nargs="*", default=["control", "candidate"])
    parser.add_argument("--note", default="O-490 held-out equation run, production profile 0.5.23")
    args = parser.parse_args()
    if args.tag in ("control", "candidate") or args.tag.startswith("dry-"):
        raise SystemExit("refused: that tag is a registered arm's folder")

    from readablestem import config
    from readablestem.cli import eval_cli
    from readablestem.cli.main import build_parser
    from readablestem.eval import answers as A

    shipped = config.Settings.production_profile
    update = {"claude_second_check": "off"} | ({"flag_policy": True} if args.flag_policy else {})
    config.Settings.production_profile = lambda self: shipped(self).model_copy(update=update)

    # Count what the run could not answer from the file (offline) or asked live.
    tally = {"hit": 0, "miss": 0}
    missed_by: dict[str, int] = {}
    run = A.CachedClient.run

    def counted(self, request, schema, *, is_fallback=False):
        before = self.misses
        out = run(self, request, schema, is_fallback=is_fallback)
        missed = self.misses > before
        tally["miss" if missed else "hit"] += 1
        if missed:
            name = f"{getattr(request, 'step', '?')}/{getattr(request, 'model', '?')}"
            missed_by[name] = missed_by.get(name, 0) + 1
        return out

    A.CachedClient.run = counted

    rr.verify_gold(args.part)
    folder = rr.runs_dir(args.part) / args.tag
    folder.mkdir(parents=True, exist_ok=True)
    answers = folder / "answers.jsonl"
    if not answers.exists():
        answers.write_text("", encoding="utf-8")
    seeded = sum(
        rr.merge_answers(rr.runs_dir(args.part) / s / "answers.jsonl", answers)
        for s in args.seed_from
    )
    argv = [
        "eval", "pipeline", "--task", "document", "--provider", "anthropic",
        "--profile", "production",
        "--answers", str(answers), "--answers-rekey",
        "--items", str(folder / "items.jsonl"), "--out", str(folder / "report.json"),
        "--section-workers", str(args.workers), "--concurrency", str(args.concurrency),
        "--cache", str(folder / "cache"),
        "--max-usd", str(max(args.max_usd, 0.01)),
    ]  # fmt: skip
    if args.offline:
        argv.append("--answers-offline")
    ns = build_parser().parse_args(argv)
    before = rr.answers_cost(answers)
    code = eval_cli._run_pipeline(
        ns,
        rr.gold_dir(args.part),
        folder / "report.json",
        gold_label=f"realworld ({args.part}) O-490 {args.tag}",
        media_cls=rr.PrintedOpenStaxMedia if rr.is_openstax_part(args.part) else rr.RealWorldMedia,
        cache_dir=folder / "cache",
        openstax_cache=folder / "cache",
    )
    spent = rr.answers_cost(answers) - before
    if not args.offline:
        rr.log_spend(
            {
                "part": args.part, "arm": "rs", "tag": args.tag, "cap_usd": args.max_usd,
                "actual_usd": round(spent, 4), "note": args.note, "profile": "production",
                "flag_policy": args.flag_policy,
            }
        )  # fmt: skip
    print(json.dumps({"part": args.part, "tag": args.tag, "exit": code, "seeded_rows": seeded,
                      "requests_hit": tally["hit"], "requests_missed": tally["miss"],
                      "missed_by": missed_by,
                      "spent_usd": round(spent, 4)}))  # fmt: skip
    return code


if __name__ == "__main__":
    raise SystemExit(main())
