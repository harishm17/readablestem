"""The Purdue-prompt chatbot recipe on development-set sections, scored like the proof does.

    uv run python scripts/dev_chatbot.py record --arm chatbot-haiku-purdue --max-usd 1 SECTION ...
    uv run python scripts/dev_chatbot.py score --arm chatbot-haiku-purdue --out items.jsonl SECTION

`record` runs `proof.chatbot.record_arms` unchanged (the PDF as a document block, a 150 dpi image
of every page, the verbatim Purdue prompt, no system prompt, the model's default effort, streamed)
on the printed PDFs of dev-v2 sections, one run per section, and keeps each reply under
`--out-dir` (default `.local/chat-dev`; a section already recorded is skipped, so nothing is
paid for twice). `score` reads the replies back and scores them with the document task's code
(`proof.systems.equation_item`, as the proof scores its chatbot arms), writing one item per
section in the form `scripts/dev_buckets.py` reads. The held-out path of the proof is not
touched and this refuses sections that are not in the dev part of dev-v2. Development sets
only. The key is read from the environment (ANTHROPIC_API_KEY).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from readablestem.eval import pipeline_eval as pe
from readablestem.eval.devset_media import DevSetMedia
from readablestem.gold.openstax import HttpFetcher
from readablestem.proof import chatbot
from readablestem.proof import systems as S
from readablestem.proof.bundle import pdf_name

ROOT = Path(__file__).resolve().parent.parent
GOLD = ROOT / "data" / "dev-v2" / "dev"


def units(sections: list[str]) -> list[list[dict]]:
    records = pe.load_gold(GOLD)
    return pe.select_units("document", records, None, None, sections)


def printed_pdfs(sections: list[str], cache: Path, out_dir: Path) -> dict[str, Path]:
    """Section id -> the printed PDF of that section (the document task's own input)."""
    pdf_dir = out_dir / "pdfs"
    pdf_dir.mkdir(parents=True, exist_ok=True)
    found: dict[str, Path] = {}
    with DevSetMedia(GOLD, cache, HttpFetcher(cache_dir=ROOT / ".cache" / "openstax")) as media:
        for unit in units(sections):
            section = pe._page_id(unit)
            path = pdf_dir / pdf_name(section)
            if not path.is_file():
                path.write_bytes(media.page_pdf(unit))
            found[section] = path
    return found


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("action", choices=["record", "score"])
    parser.add_argument("sections", nargs="+")
    parser.add_argument("--arm", required=True, choices=sorted(chatbot.ARM_BY_ID))
    parser.add_argument("--max-usd", type=float, default=0.0)
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--out-dir", type=Path, default=ROOT / ".local" / "chat-dev")
    parser.add_argument("--out", type=Path, help="score: items JSONL for scripts/dev_buckets.py")
    parser.add_argument(
        "--cache", type=Path, default=Path.home() / ".cache/readablestem/dev-v2/eval-cache"
    )
    args = parser.parse_args()
    arm = chatbot.ARM_BY_ID[args.arm]
    pdfs = printed_pdfs(args.sections, args.cache, args.out_dir)
    if args.action == "record":
        if args.max_usd <= 0:
            print("--max-usd is required to record", file=sys.stderr)
            return 2
        summary = chatbot.record_arms(
            pdfs,
            args.out_dir / "systems",
            arms=[arm],
            max_usd=args.max_usd,
            client=chatbot.make_client(arm.provider),
            workers=args.workers,
            runs=1,
        )
        print(json.dumps(summary))
        return 0
    arms = S.load_arms_dir(args.out_dir / "systems")
    runs = arms.get(arm.id, {})
    items = []
    for unit in units(args.sections):
        section = pe._page_id(unit)
        gold = pe._of_kind(unit, "equation")
        run = next(iter(runs.get(section, [])), None)
        output = run.read() if run is not None else None
        item = S.equation_item(section, gold, output)
        item["pdf_pages"] = run.pdf_pages if run is not None else 0
        item["cost_usd"] = run.cost_usd if run is not None else 0.0
        item["status"] = run.status if run is not None else "missing"
        item["review"] = pe.load_counts([])  # a chatbot asks nobody about anything
        items.append(item)
    if args.out:
        args.out.write_text("\n".join(json.dumps(i) for i in items) + "\n", encoding="utf-8")
    for item in items:
        print(
            item["id"],
            item["status"],
            f"${item['cost_usd']:.4f}",
            item["matched"],
            "/",
            item["n_gold"],
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
