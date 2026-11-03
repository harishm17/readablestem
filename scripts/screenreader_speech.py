"""What a student's screen reader says for our MathML and the gold's (docs/eval.md).

Runs MathML through MathCAT (NVDA 2026.1 and JAWS; ClearSpeak and SimpleSpeak) and Speech Rule
Engine (MathJax and ChromeVox; clearspeak and mathspeak), then reports, per equation, the spoken
strings, whether MathCAT recognised chemistry, and a word diff between ours and the gold's, and,
per set, the share of ours that sounds the same as the gold and the worst classes.

    uv run python scripts/screenreader_speech.py --items items.jsonl --gold data/dev-v2/dev
    uv run python scripts/screenreader_speech.py --pairs pairs.jsonl --out rows.jsonl
    uv run python scripts/screenreader_speech.py --mathml '<math>...</math>'

Inputs, any equation set:

- `--items FILE --gold DIR`: the per-item rows of `eval pipeline --items` (equations or document
  task) and the gold directory they were scored against (OpenStax, authored, chemistry,
  handwriting, arXiv: whatever has gold records of `kind: equation` with `mathml`). `--sections`
  keeps only gold ids containing one of the words.
- `--pairs FILE`: JSON lines with `id`, `gold` (or `gold_latex`), `ours` (or `ours_latex`; null
  for no proposal), optional `source` and `class`. Use it for sets whose gold is not a gold
  directory.
- `--mathml XML`: one expression, printed as it is spoken.

Build the engines once with `scripts/speech_oracles/build.sh` (docs/eval.md, "Speech under
screen-reader engines"). Without them this prints why and exits 0 without a result.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from readablestem.eval import speech_oracle as so


def _load(args: argparse.Namespace) -> list[so.Pair]:
    if args.pairs:
        return so.pairs_from_jsonl(args.pairs)
    from readablestem.eval.pipeline_eval import load_gold

    items = [
        json.loads(line) for line in args.items.read_text("utf-8").splitlines() if line.strip()
    ]
    pairs = so.pairs_from_items(items, load_gold(args.gold))
    if args.sections:
        pairs = [p for p in pairs if any(s in p.id for s in args.sections)]
    return pairs


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--items", type=Path, help="rows written by eval pipeline --items")
    parser.add_argument("--gold", type=Path, help="gold directory for --items")
    parser.add_argument("--pairs", type=Path, help="JSON lines of gold/ours MathML or LaTeX")
    parser.add_argument("--mathml", help="speak one expression")
    parser.add_argument("--sections", nargs="*", default=(), help="only gold ids containing these")
    parser.add_argument("--out", type=Path, help="write per-equation rows as JSON lines")
    parser.add_argument("--report", type=Path, help="write the aggregate as JSON")
    parser.add_argument("--worst", type=int, default=8, help="problems and classes to list")
    args = parser.parse_args(argv)

    if args.mathml is None and not (args.pairs or (args.items and args.gold)):
        parser.error("give --mathml, --pairs, or --items with --gold")
    # Held-out and sealed sets are never read item by item (the rule of eval pipeline --set
    # heldout): refuse their gold, and rows or pairs kept with them.
    for path in (args.gold, args.items, args.pairs):
        if path is not None and (message := so.protected_gold(path)):
            print(f"error: {message}", file=sys.stderr)
            return 2
    note = so.skip_message()
    engines = so.available_engines()
    if note:
        print(note, file=sys.stderr)
    if not engines:
        print("nothing to run; build the engines first", file=sys.stderr)
        return 0

    if args.mathml is not None:
        reading = so.read_all([args.mathml], engines)[args.mathml]
        for engine, text in reading.speech.items():
            print(f"{engine}: {text}")
        print(f"MathCAT recognised chemistry: {reading.chemistry}")
        for family, message in reading.errors.items():
            print(f"{family} error: {message}")
        return 0

    pairs = _load(args)
    if not pairs:
        print("no equations to compare", file=sys.stderr)
        return 1
    results = so.compare(pairs, engines)
    summary = so.summarize(results, engines, worst=args.worst)
    summary["oracles"] = so.oracle_versions()
    print(so.format_markdown(summary), end="")
    for engine in engines:
        problems = so.top_problems(results, engine, k=args.worst)
        if problems:
            print(f"\nMost common differences under {engine} (n = equations containing it):\n")
            for p in problems:
                print(f"- {p['change']} (n={p['n']}, e.g. {p['example']})")
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        with args.out.open("w", encoding="utf-8") as fh:
            for r in results:
                fh.write(json.dumps(r.as_row(), ensure_ascii=False) + "\n")
        print(f"\nwrote {args.out}")
    if args.report:
        summary["top_problems"] = {e: so.top_problems(results, e, k=args.worst) for e in engines}
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", "utf-8")
        print(f"wrote {args.report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
