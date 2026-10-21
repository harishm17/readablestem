"""Real-world set: the unsupported-equation rates of every arm, by one rule.

    uv run --env-file .env python scripts/realworld_invented.py --part dev [--out FILE]

The registered "unsupported / invented" metric must be read the same way for a pipeline and for a
chatbot. The document task's own `text_layer_supported` needs the PDF text of the region an equation
was cut from, which a chatbot's output has not got, so here every arm's output HTML goes through
the invented-content rules for equations (`eval/invented`, docs/eval-invented.md), against the PDF's
own text layer and the paper's gold:

- **aligned**: a predicted equation aligned to a gold equation holds a symbol (letter, digit or
  Greek letter) more often than the gold equation does;
- **unaligned**: a predicted equation with no gold partner and at least 3 symbols whose symbol
  stream matches no window of the PDF's symbol stream (difflib ratio 0.8).

Each rate is per equation checked, with a 95% cluster bootstrap over papers. ReadableSTEM's HTML is
made again by replaying its recorded answers offline (`--answers-offline`: nothing is spent); the
chatbots' HTML is the recorded reply. It reports only; nothing here changes a run.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import numpy as np

from readablestem.eval import pipeline_eval as pe
from readablestem.eval.invented import read as inv_read
from readablestem.eval.invented import score as inv_score
from readablestem.eval.invented import text as inv_text
from readablestem.proof import stats

ROOT = Path.home() / ".cache" / "readablestem" / "realworld"
CHAT = {"Chatbot, Opus": "chatbot-opus-purdue", "Chatbot, Haiku": "chatbot-haiku-purdue"}
# columns: aligned n, aligned unsupported, unaligned n, unaligned unsupported
COLS = ("a_n", "a_bad", "u_n", "u_bad")


def rs_html(part: str, tag: str = "rs", page_math: bool = False) -> dict[str, str]:
    """ReadableSTEM's output HTML per paper, from the recorded answers (replayed, offline)."""
    os.environ["CLAUDE_SECOND_CHECK"] = "off"
    if page_math:
        os.environ["FEATURE_PAGE_MATH"] = "true"
    from readablestem.cli import eval_cli
    from readablestem.cli.main import build_parser
    from readablestem.pipeline import run

    out_dir = ROOT / "runs" / part / tag
    html_dir = out_dir / "html"
    html_dir.mkdir(exist_ok=True)
    pdfs = sorted((ROOT / "gold" / part / "pdfs").glob("*.pdf"))
    found: dict[str, str] = {}
    pending = [p for p in pdfs if not (html_dir / f"{p.stem}.html").is_file()]
    if pending:
        argv = [
            "eval", "pipeline", "--task", "document", "--provider", "anthropic",
            "--answers", str(out_dir / "answers.jsonl"), "--answers-offline", "--max-usd", "1",
        ]  # fmt: skip
        ns = build_parser().parse_args(argv)
        settings = eval_cli._settings_for(ns)
        made = eval_cli._make_client(ns, settings)
        assert not isinstance(made, str), made
        client = made[0]
        for pdf in pending:
            result = run.convert_pdf(
                pdf.read_bytes(), settings=settings, client=client, concurrency=4
            ).result
            (html_dir / f"{pdf.stem}.html").write_text(result.html, encoding="utf-8")
            print("replayed", pdf.stem, flush=True)
    for pdf in pdfs:
        found[pdf.stem] = (html_dir / f"{pdf.stem}.html").read_text(encoding="utf-8")
    return found


def chat_html(part: str, arm: str) -> dict[str, str]:
    found = {}
    folder = ROOT / "runs" / part / "chat" / "systems" / arm
    for meta_path in sorted(folder.glob("*.json")):
        meta = json.loads(meta_path.read_text())
        slug = meta["section"].split("/", 1)[1]
        file = meta.get("output", {}).get("file")
        found[slug] = (folder / file).read_text("utf-8", errors="replace") if file else ""
    return found


def counts_of(slug: str, html: str, part: str) -> list[float]:
    pdf = ROOT / "gold" / part / "pdfs" / f"{slug}.pdf"
    reference = inv_text.Reference.from_pdf(pdf.read_bytes())
    gold = sorted(
        (
            r
            for r in pe.load_gold(ROOT / "gold" / part)
            if r["kind"] == "equation" and r["page_slug"] == slug
        ),
        key=lambda r: r["index_on_page"],
    )
    output = inv_read.read_output(html, "html")
    score = inv_score.score_output(reference, output, gold_equations=[g["mathml"] for g in gold])
    a = score.counts[inv_score.EQUATION_ALIGNED]
    u = score.counts[inv_score.EQUATION_UNALIGNED]
    return [a.n, a.unsupported, u.n, u.unsupported]


def ratio(num: int, den: int):
    def statistic(sums: np.ndarray) -> float | None:
        return float(sums[num] / sums[den]) if sums[den] else None

    return statistic


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--part", choices=["dev", "heldout"], required=True)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--tag", default="rs", help="ReadableSTEM run folder under runs/PART")
    parser.add_argument("--page-math", action="store_true", help="the run had FEATURE_PAGE_MATH on")
    parser.add_argument("--rs-only", action="store_true")
    args = parser.parse_args()
    outputs = {f"ReadableSTEM ({args.tag})": rs_html(args.part, args.tag, args.page_math)}
    for label, arm in {} if args.rs_only else CHAT.items():
        outputs[label] = chat_html(args.part, arm)
    result: dict = {"part": args.part, "arms": {}}
    print(
        "| per equation checked (95% cluster bootstrap over papers) | " + " | ".join(outputs) + " |"
    )
    print("|---|" + "---|" * len(outputs))
    rows: dict[str, list[str]] = {
        "aligned to gold: an added symbol": [],
        "no gold partner (3+ symbols): no match in the PDF's text": [],
        "equations checked (aligned / no partner)": [],
    }
    for label, htmls in outputs.items():
        vectors = {slug: counts_of(slug, html, args.part) for slug, html in htmls.items()}
        entry: dict = {}
        for name, (num, den) in {
            "aligned to gold: an added symbol": (1, 0),
            "no gold partner (3+ symbols): no match in the PDF's text": (3, 2),
        }.items():
            statistic = ratio(num, den)
            point = stats.point(vectors, statistic)
            ci = stats.cluster_bootstrap(vectors, statistic)
            rows[name].append(
                "n/a" if point is None else f"{point:.3f} [{ci.low:.3f}, {ci.high:.3f}]"
            )
            entry[name] = {"point": point, "low": ci.low, "high": ci.high}
        total = np.asarray(list(vectors.values())).sum(axis=0)
        rows["equations checked (aligned / no partner)"].append(
            f"{int(total[0])} / {int(total[2])}"
        )
        entry["counts"] = dict(zip(COLS, (int(x) for x in total), strict=True))
        result["arms"][label] = entry
    for name, cells in rows.items():
        print(f"| {name} | " + " | ".join(cells) + " |")
    if args.out:
        args.out.write_text(json.dumps(result, indent=1) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
