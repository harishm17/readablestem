"""Does the image page reader still read typed scans as well? (docs/eval-image-pages.md)

    uv run --env-file .env python scripts/typed_scans_check.py run --arm new --max-usd 4
    uv run python scripts/typed_scans_check.py run --arm base --offline --max-usd 1
    uv run python scripts/typed_scans_check.py compare --arms base new

The development documents of `data/dev-v2` (36 pages: chemistry sections, matrices, exercises) as
synthetic scans (`eval pipeline --task document --set dev-v2 --part dev --scanned`), read by
`claude-opus-5-5` at effort low and 200 dpi, as in docs/eval-image-pages.md. Development material
only: the held-out and check parts are never touched. Answers are recorded in one file
(`.local/hw-accuracy/typed/answers.jsonl`, seeded with the recording of that document's
Opus run), so the first measurement's arm replays at no cost under the code and prompt it was
recorded with (a checkout of `origin/main` first on the path) and a new arm pays for what the
file cannot answer.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
ROOT = REPO / ".local" / "hw-accuracy" / "typed"
# The six development documents of docs/eval-image-pages.md (36 pages), in its order.
SECTIONS = [
    "chemistry-atoms-first/1-4",
    "chemistry-atoms-first/14-1",
    "chemistry-atoms-first/20-2",
    "chemistry-atoms-first/12-3",
    "authored-latex/latex-matrices",
    "authored-latex/latex-exercises",
]


def cmd_run(args: argparse.Namespace) -> int:
    os.environ["CLAUDE_SECOND_CHECK"] = "off"
    os.environ["FEATURE_IMAGE_PAGES"] = "true"
    os.environ["IMAGE_PAGES_MODEL"] = "claude-opus-5-5"
    os.environ.pop("IMAGE_PAGES_DPI", None)
    os.environ.pop("ANTHROPIC_EFFORT_IMAGE_PAGES", None)
    for item in args.env or []:
        key, _, value = item.partition("=")
        os.environ[key] = value
    from readablestem.cli import eval_cli
    from readablestem.cli.main import build_parser

    out = ROOT / args.arm
    out.mkdir(parents=True, exist_ok=True)
    answers = ROOT / "answers.jsonl"
    argv = [
        "eval", "pipeline", "--task", "document", "--set", "dev-v2", "--part", "dev",
        "--scanned", "--provider", "anthropic", "--answers", str(answers),
        "--items", str(out / "items.jsonl"), "--out", str(out / "report.json"),
        "--section-workers", "1", "--cache", str(out / "cache"),
        "--max-usd", str(args.max_usd),
    ]  # fmt: skip
    argv += ["--sections", *(args.sections or SECTIONS)]
    if args.offline:
        argv.append("--answers-offline")
    before = _cost(answers)
    code = eval_cli._cmd_pipeline(build_parser().parse_args(argv))
    spent = _cost(answers) - before
    if code == 0 and not args.offline:
        entry = {
            "when": dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "arm": args.arm,
            "cap_usd": args.max_usd,
            "actual_usd": round(spent, 4),
            "sections": args.sections,
            "commit": subprocess.run(
                ["git", "rev-parse", "HEAD"], capture_output=True, text=True, cwd=REPO, check=False
            ).stdout.strip(),
        }
        with (ROOT / "spend-log.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, sort_keys=True) + "\n")
    print(f"spent this start: ${spent:.4f}")
    return code


def _cost(path: Path) -> float:
    total = 0.0
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                total += float((json.loads(line).get("call") or {}).get("cost_usd") or 0.0)
    return total


def _row(report: dict) -> dict[str, float | None]:
    m = report["metrics"]
    ip = m["image_pages"]
    sym = m["secondary_symbols"]
    return {
        "pages": ip["pages"],
        "primary exact_lenient": m["end_to_end_exact_lenient"],
        "symbols (credit)": sym["end_to_end_exact_with_credit"],
        "detection recall": m["detection_recall"],
        "detection precision": m["detection_precision"],
        "heading F1": m["headings"]["f1"],
        "heading level right": m["headings"]["level_accuracy"],
        "prose WER, all words": m["prose_wer"]["wer"],
        "prose WER, 3+ letters": m["prose_only_wer"]["wer"],
        "required decisions / page": ip["required_per_converted_page"],
        "optional notes / page": ip["optional_per_converted_page"],
        "blocks medium or low": ip["low_confidence_share"],
        "silent wrong (symbols)": ip["equation_flags"]["all_symbols"]["wrong_not_asked"],
        "silent wrong (strict)": ip["equation_flags"]["all"]["wrong_not_asked"],
        "page read $ / page": ip["cost_per_page_usd"],
        "pipeline $ / page": report["cost"]["per_pdf_page_usd"],
        "seconds / page": ip["seconds_per_page"],
        "illegible markers": ip["illegible"],
        "pages converted": ip["converted"],
    }


def cmd_compare(args: argparse.Namespace) -> int:
    rows = {a: _row(json.loads((ROOT / a / "report.json").read_text())) for a in args.arms}
    keys = list(next(iter(rows.values())))
    print(f"{'':28}" + "".join(f"{a:>12}" for a in args.arms))
    for key in keys:
        cells = []
        for a in args.arms:
            v = rows[a][key]
            cells.append(
                f"{'n.a.':>12}"
                if v is None
                else f"{v:>12.3f}"
                if isinstance(v, float)
                else f"{v:>12}"
            )
        print(f"{key:28}" + "".join(cells))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run")
    run.add_argument("--arm", required=True)
    run.add_argument("--max-usd", type=float, required=True)
    run.add_argument("--offline", action="store_true")
    run.add_argument("--env", action="append")
    run.add_argument("--sections", nargs="+", help="BOOK/PAGE ... (default: all six)")
    compare = sub.add_parser("compare")
    compare.add_argument("--arms", nargs="+", required=True)
    args = parser.parse_args()
    return cmd_run(args) if args.command == "run" else cmd_compare(args)


if __name__ == "__main__":
    sys.exit(main())
