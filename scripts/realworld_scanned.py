"""The real-world held-out papers as synthetic scans, read by the image page pass, once.

    uv run --env-file .env python scripts/realworld_scanned.py --max-usd 9 --tag opus

Registered in data/image-pages/PREREGISTRATION-HELDOUT-SCANS.md before this ran: the 15 held-out
papers (71 pages) of the real-world set (`~/.cache/readablestem/realworld/gold/heldout`, built and
registered on branch feat/realworld-eval), each cut PDF turned into the image-only PDF of a scan
(`eval/scan.py`, seed 20261021), converted with `FEATURE_IMAGE_PAGES` on and `claude-opus-5-5`
as the page reader, scored by the document task against the same gold as the typed-PDF run
(`data/realworld/results-heldout.json` on that branch). The held-out part was used for typed
scoring only before this.

It runs only when the registration file's hashes still hold (the registration, `eval/scan.py`, the
manifest of the gold) and the run has not been recorded before (a marker file); `--offline`
replays the recorded answers and spends nothing. The answer key of this set is equations only, so
the prose measure is the recall and precision of the words of the PDF's own text layer
(`eval/prose_wer.bag_score`: three letters or more, no order), and there is no heading score.
Each live run appends to `.local/image-pages/heldout/spend-log.jsonl`.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pymupdf

from readablestem.eval import prose_wer, scan
from readablestem.eval.render import RenderError

ROOT = Path.home() / ".cache" / "readablestem" / "realworld"
GOLD = ROOT / "gold" / "heldout"
REPO = Path(__file__).resolve().parent.parent
REG = REPO / "data" / "image-pages"
RUNS = REPO / ".local" / "image-pages" / "heldout"
SEED = 20261021


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def require_registration(tag: str, offline: bool) -> None:
    reg = REG / "REGISTRATION-HELDOUT-SCANS.sha256"
    if not reg.is_file():
        raise SystemExit("not registered: data/image-pages/REGISTRATION-HELDOUT-SCANS.sha256")
    for line in reg.read_text().splitlines():
        digest, _, name = line.partition("  ")
        path = Path(name) if name.startswith("/") else REPO / name
        if not path.is_file() or sha256(path) != digest:
            raise SystemExit(f"registered file {name} has changed since registration")
    marker = RUNS / f"{tag}.recorded"
    if marker.is_file() and not offline:
        raise SystemExit(
            f"{tag} was already recorded on the held-out papers: replay with --offline"
        )


class ScannedRealWorld:
    """The cut PDFs of the held-out papers as scans; the prose answer key is their text layer."""

    def __init__(self, gold_dir: Path, cache_dir: Path, fetcher: Any = None, *, paper: str = ""):
        self.gold_dir = gold_dir

    def __enter__(self) -> ScannedRealWorld:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def close(self) -> None:
        return None

    def _typed(self, records: list[dict]) -> bytes:
        path = self.gold_dir / records[0]["pdf"]
        if not path.is_file():
            raise RenderError(f"PDF {records[0]['pdf']} is missing under {self.gold_dir}")
        return path.read_bytes()

    def page_pdf(self, page_records: list[dict]) -> bytes:
        scanned, _ = scan.scan_pdf(self._typed(page_records), SEED)
        return scanned

    def gold_words(self, page_records: list[dict], *, include_math: bool = True) -> list[str]:
        """The words of the typed PDF's text layer (the same for either `include_math`: a
        text layer does not say which words are mathematics)."""
        doc = pymupdf.open(stream=self._typed(page_records), filetype="pdf")
        try:
            return prose_wer.words(" ".join(w[4] for page in doc for w in page.get_text("words")))
        finally:
            doc.close()

    def equation_png(self, record: dict) -> bytes:
        raise RenderError("the real-world set has the document task only")

    def equation_text(self, record: dict) -> str | None:
        return None

    def equation_context(self, record: dict) -> str | None:
        return None

    def figure_png(self, url: str) -> bytes:
        raise RenderError("the real-world set has the document task only")


def answers_cost(path: Path) -> float:
    if not path.is_file():
        return 0.0
    total = 0.0
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            total += float((json.loads(line).get("call") or {}).get("cost_usd") or 0.0)
    return total


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--max-usd", type=float, required=True)
    parser.add_argument("--tag", default="opus")
    parser.add_argument("--offline", action="store_true")
    args = parser.parse_args()

    # The registered settings: Opus reads the pages, no second automated check.
    os.environ["CLAUDE_SECOND_CHECK"] = "off"
    os.environ["FEATURE_IMAGE_PAGES"] = "true"
    os.environ["IMAGE_PAGES_MODEL"] = "claude-opus-5-5"
    os.environ.pop("IMAGE_PAGES_DPI", None)
    os.environ.pop("ANTHROPIC_EFFORT_IMAGE_PAGES", None)
    from readablestem.cli import eval_cli
    from readablestem.cli.main import build_parser

    require_registration(args.tag, args.offline)
    out_dir = RUNS / args.tag
    out_dir.mkdir(parents=True, exist_ok=True)
    answers = out_dir / "answers.jsonl"
    report, items = out_dir / "report.json", out_dir / "items.jsonl"
    argv = [
        "eval", "pipeline", "--task", "document", "--provider", "anthropic",
        "--answers", str(answers), "--items", str(items), "--out", str(report),
        "--section-workers", "3", "--cache", str(out_dir / "cache"),
        "--max-usd", str(args.max_usd),
    ]  # fmt: skip
    if args.offline:
        argv.append("--answers-offline")
    ns = build_parser().parse_args(argv)
    before = answers_cost(answers)
    code = eval_cli._run_pipeline(
        ns,
        GOLD,
        report,
        gold_label="realworld (heldout), as scans",
        media_cls=ScannedRealWorld,
        cache_dir=out_dir / "cache",
        openstax_cache=out_dir / "cache",
    )
    spent = answers_cost(answers) - before
    if code == 0 and not args.offline:
        entry = {
            "when": dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "tag": args.tag,
            "cap_usd": args.max_usd,
            "actual_usd": round(spent, 4),
            "commit": subprocess.run(
                ["git", "rev-parse", "HEAD"], capture_output=True, text=True, cwd=REPO, check=False
            ).stdout.strip(),
        }
        with (RUNS / "spend-log.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, sort_keys=True) + "\n")
        (RUNS / f"{args.tag}.recorded").write_text(dt.date.today().isoformat())
    print(f"spent this start: ${spent:.4f}")
    return code


if __name__ == "__main__":
    sys.exit(main())
