"""Read a few pages of a development section as synthetic scans with the image page pass, live.

A development tool for ADR 0007 (not part of the product): it prints a development-set
section as the eval does (`eval/render.py`), turns it into a degraded scan (`eval/scan.py`),
runs `run.convert_pdf` with `FEATURE_IMAGE_PAGES` on for the pages asked for, and writes what
came out (the scan, the converted HTML, the result JSON, a one-line summary per page) under
`--out`. Every model answer goes into `--answers` (a recorded answers file, replayed at no
cost on the next run), and the run's spend is capped by `--max-usd`.

    uv run python scripts/image_pages_probe.py chemistry-atoms-first/1-4-measurements \\
        --pages 0 1 --model claude-haiku-5-5 --max-usd 0.30 \\
        --answers .local/image-pages/answers-pilot.jsonl --out .local/image-pages/pilot

Development sets only: the held-out sections and the sealed check part are refused.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from readablestem.config import Settings, with_provider
from readablestem.core.budget import InMemorySpendGuard, JobBudget
from readablestem.core.types import ConversionOptions
from readablestem.eval import pipeline_eval as pe
from readablestem.eval import scan
from readablestem.eval.answers import CachedClient
from readablestem.eval.devset_media import DevSetMedia
from readablestem.gold.openstax import HttpFetcher
from readablestem.models import make_vision_client
from readablestem.pipeline import run


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "section", help="a dev-v2 section id, such as chemistry-atoms-first/1-4-measurements"
    )
    parser.add_argument("--image", action="store_true", help="section is the path of a picture")
    parser.add_argument("--pages", type=int, nargs="+", help="0-based pages to read (default all)")
    parser.add_argument("--model", required=True, help="the page reader (IMAGE_PAGES_MODEL)")
    parser.add_argument("--effort", default=None, help="thinking effort of the page reader")
    parser.add_argument("--max-usd", type=float, required=True)
    parser.add_argument("--answers", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--gold", type=Path, default=Path("data/dev-v2/dev"))
    args = parser.parse_args(argv)

    args.out.mkdir(parents=True, exist_ok=True)
    if args.image:
        import pymupdf

        picture = Path(args.section)
        stem = picture.stem
        doc = pymupdf.open()
        image = pymupdf.open(picture)
        rect = image[0].rect
        scale = 612 / min(rect.width, rect.height) if min(rect.width, rect.height) > 0 else 1
        page = doc.new_page(width=rect.width * scale, height=rect.height * scale)
        page.insert_image(page.rect, filename=str(picture))
        scanned, made = doc.tobytes(), []
    else:
        gold = pe.load_gold(args.gold)
        units = [
            u
            for u in pe.group_pages(list(gold))
            if f"{u[0]['book_slug']}/{u[0]['page_slug']}" == args.section
        ]
        if not units:
            print(f"no development section {args.section!r}", file=sys.stderr)
            return 2
        fetcher = HttpFetcher(cache_dir=Path(".cache/openstax"))
        with DevSetMedia(args.gold, Path(".cache/eval"), fetcher, paper="Letter") as media:
            printed = media.page_pdf(units[0])
        scanned, made = scan.scan_pdf(printed, args.seed)
        stem = args.section.replace("/", "__")
    (args.out / f"{stem}.scan.pdf").write_bytes(scanned)

    settings = with_provider(
        Settings(
            feature_image_pages=True,
            image_pages_model=args.model,
            anthropic_effort_image_pages=args.effort,
        ),
        "anthropic",
    )
    budget = JobBudget(
        guard=InMemorySpendGuard(cap_usd=args.max_usd), max_usd=args.max_usd, max_calls=500
    )
    client = CachedClient(make_vision_client(settings, budget), args.answers, settings)
    output = run.convert_pdf(
        scanned,
        settings=settings,
        client=client,
        pages=args.pages,
        options=ConversionOptions.from_settings(settings),
    )
    result = output.result
    crops = args.out / f"{stem}.crops"
    crops.mkdir(exist_ok=True)
    for element_id, png in output.crops.items():
        (crops / f"{element_id}.png").write_bytes(png)
    (args.out / f"{stem}.result.json").write_text(result.model_dump_json(indent=1))
    (args.out / f"{stem}.html").write_text(result.html, encoding="utf-8")
    summary = result.image_pages
    if summary is not None:
        for record in summary.per_page:
            print(
                f"page {record.page}: {record.outcome} {record.model} ${record.cost_usd:.4f} "
                f"{record.latency_ms / 1000:.1f}s in={record.input_tokens} "
                f"out={record.output_tokens} "
                f"blocks={record.blocks} {record.by_kind} illegible={record.illegible} "
                f"required={record.required} optional={record.optional}"
            )
        print(f"image pages total ${summary.cost_usd:.4f}; document ${result.stats.cost_usd:.4f}")
    print(json.dumps(dict(client.summary())))
    print(f"degradation: {[d.dpi for d in made]} dpi; budget spent ${budget.spent_usd:.4f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
