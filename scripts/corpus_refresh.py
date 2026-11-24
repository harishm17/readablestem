"""Rebuild the corpus's recorded model answers by running the workflow tests.

    uv run python scripts/corpus_refresh.py                       # replay; drop unread answers
    uv run python scripts/corpus_refresh.py --check               # replay; write nothing
    uv run python scripts/corpus_refresh.py --from OLD.jsonl      # move an older recording
    uv run python scripts/corpus_refresh.py --live --max-usd 0.50 # pay for what is missing

The workflow tests (`pytest -m workflow`) convert every corpus file through the real app.
This script runs them against one working copy of the recording and then writes what they
read back as the recording: `tests/workflows/corpus/answers.jsonl.gz` for answers read from
files that may be committed, and the cache's `answers.jsonl.gz` for the rest
(`eval/corpus.py`). An answer no test read is dropped (not by a `--live` run: it leaves the
hostile-file tests out, so what they alone read would look unread).

Nothing is sent to a model unless `--live` is given, and `--live` needs `--max-usd`: the
most this run may pay for answers the recording lacks, never set above $1.00. Past it new
requests are refused and their answers stay missing; a request already sent is paid for, so a
run can end a cent over. Answers already recorded cost nothing and are
never asked again. `--live` reads `ANTHROPIC_API_KEY` from the environment.

It prints what was read, what was missing and why (so a prompt that changed is named), and
what was paid. The exit code is the tests' own.
"""

from __future__ import annotations

import argparse
import gzip
import json
import os
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from readablestem.eval import corpus  # noqa: E402
from readablestem.proof.bundle import dump_line  # noqa: E402

MOST_USD = 1.00
HOSTILE = "tests/workflows/test_wf49_hostile_input.py"


def parse(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    parser.add_argument(
        "--from",
        dest="older",
        type=Path,
        metavar="FILE",
        help="start from this recording (JSON lines, plain or .gz) and move its answers "
        "to today's keys",
    )
    parser.add_argument("--live", action="store_true", help="ask the model for missing answers")
    parser.add_argument(
        "--max-usd",
        type=float,
        metavar="USD",
        help=f"with --live: the most this run may pay, at most {MOST_USD:.2f}",
    )
    parser.add_argument("--check", action="store_true", help="write nothing; report only")
    args = parser.parse_args(argv)
    if args.live and args.max_usd is None:
        parser.error("--live needs --max-usd: the most this run may pay")
    if args.max_usd is not None and not args.live:
        parser.error("--max-usd is for --live")
    if args.max_usd is not None and not 0 < args.max_usd <= MOST_USD:
        parser.error(f"--max-usd must be above 0 and at most {MOST_USD:.2f}")
    if args.live and args.older:
        parser.error("--live and --from are two runs: move the older recording first")
    if args.live and not os.environ.get("ANTHROPIC_API_KEY"):
        parser.error("--live needs ANTHROPIC_API_KEY in the environment")
    return args


def rows_of(path: Path) -> list[dict]:
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8") as lines:
        return [json.loads(line) for line in lines if line.strip()]


def report(conversions: list[dict], paid_limit: float | None) -> None:
    missed: Counter[str] = Counter()
    lacking: Counter[str] = Counter()
    for one in conversions:
        missed.update(one["missed"])
        lacking[one["entry"]] += sum(one["missed"].values())
    hits = sum(one["hits"] for one in conversions)
    moved = sum(one["moved"] for one in conversions)
    paid = sum(one["paid_usd"] for one in conversions)
    replayed = sum(one["replayed_usd"] for one in conversions)
    print(f"conversions: {len(conversions)}")
    print(f"answers read from the recording: {hits} (moved to today's key: {moved})")
    print(f"recorded cost of those answers: ${replayed:.4f}")
    word = "asked for" if paid_limit is not None else "missing"
    print(f"answers {word}: {sum(missed.values())}")
    for why, count in missed.most_common():
        print(f"  {count} x {why}")
    for entry, count in sorted(lacking.items()):
        if count:
            print(f"  {entry}: {count}")
    limit = f" of ${paid_limit:.2f} allowed" if paid_limit is not None else ""
    print(f"paid in this run: ${paid:.4f}{limit}")


def main(argv: list[str] | None = None) -> int:
    args = parse(argv)
    mode = "live" if args.live else "rekey" if args.older else "offline"
    with tempfile.TemporaryDirectory(prefix="corpus-refresh-") as scratch:
        work = Path(scratch) / "answers.jsonl"
        log = Path(scratch) / "log"
        log.mkdir()
        committed = corpus.read_answers()
        start = committed + corpus.read_answers(corpus.KEPT_OUT)
        if args.older:
            start += rows_of(args.older)
        work.write_text("".join(dump_line(row) + "\n" for row in start), encoding="utf-8")
        env = os.environ | {
            "CORPUS_ANSWERS_MODE": mode,
            "CORPUS_ANSWERS_WORK": str(work),
            "CORPUS_ANSWERS_LOG": str(log),
            "CORPUS_ANSWERS_MAX_USD": str(args.max_usd or 0),
        }
        # One process: the tests append to one file, and the limit on paying is per process.
        tests = [sys.executable, "-m", "pytest", "tests/workflows", "-m", "workflow"]
        if args.live:
            # A hostile case states how many of its parts stay unanswered; paid answers would
            # be recorded for it and break that count on every later replay.
            tests += ["--ignore", HOSTILE]
        quiet = ["-q", "-p", "no:cacheprovider", "-p", "no:xdist", "--show-capture=no"]
        code = subprocess.run([*tests, *quiet], cwd=REPO, env=env, check=False).returncode
        conversions = [
            json.loads(line)
            for part in sorted(log.glob("*.jsonl"))
            for line in part.read_text(encoding="utf-8").splitlines()
        ]
        report(conversions, args.max_usd)
        if args.check:
            return code
        after = [row for row in rows_of(work) if "q" in row]
        have = {row["k"] for row in after}
        may_commit: set[str] = set()
        kept_out: set[str] = set()
        for one in conversions:
            (may_commit if one["commit"] else kept_out).update(one["keys"])
        if code != 0 or args.live:
            # A test that did not run read nothing, so "unread" proves nothing today: no
            # answer is dropped, and one whose file is not known stays out of the repository.
            # A live refresh leaves the hostile-file tests out, so it never drops either.
            why = "the tests did not pass" if code != 0 else "a live refresh leaves tests out"
            print(f"{why}: no answer is dropped")
            may_commit |= {row["k"] for row in committed}
            kept_out |= have - may_commit
        commit = [row for row in after if row["k"] in may_commit - kept_out]
        outside = [row for row in after if row["k"] in kept_out]
        dropped = len(have - may_commit - kept_out)
        corpus.write_answers(commit)
        corpus.KEPT_OUT.parent.mkdir(parents=True, exist_ok=True)
        corpus.write_answers(outside, corpus.KEPT_OUT)
        print(f"{corpus.ANSWERS.relative_to(REPO)}: {len(commit)} answers")
        print(f"{corpus.KEPT_OUT}: {len(outside)} answers (kept out of the repository)")
        print(f"dropped, read by no test: {dropped}")
        return code


if __name__ == "__main__":
    raise SystemExit(main())
