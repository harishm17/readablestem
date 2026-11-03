"""Figure gold v2, step 2: the seeded draw and the gold file (docs/eval-figures-long.md).

    uv run python scripts/figures_v2_build.py build [--cache DIR]
    uv run python scripts/figures_v2_build.py check

`build` reads `pool-openstax.json` and `pool-arxiv.json` (steps 1 and 1b), draws with
`figures_v2.select`, downloads each OpenStax image once to CACHE to record its sha256, size and
type (a figure whose image cannot be fetched or is not PNG/JPEG is skipped in order, as the rule
says), and writes `figures.jsonl`, `selection.json` and `ATTRIBUTION.md` in
`data/gold-figures-v2/`. OpenStax images are not committed (the run fetches them and checks the
sha256, as the v1 gold does); arXiv charts are committed with their standalone TeX. Reference
tables for OpenStax charts come from `tables-from-alt.json` (values the OpenStax alt text states
and the
figure prints, each checked against the image and listed with who read it); `exclusions.json`
lists figures taken out in order after a look at the image, with the reason. `check`
validates the committed file. No model is called.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import urllib.request
from pathlib import Path

from readablestem.gold import figures_v2 as fv
from readablestem.gold.openstax import USER_AGENT

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "gold-figures-v2"
DEFAULT_CACHE = Path.home() / ".cache" / "readablestem" / "figures-v2"
MAX_IMAGE_BYTES = 4_000_000
IMAGE_TYPES = {"image/png": "png", "image/jpeg": "jpg"}


def _json(path: Path) -> dict:
    return json.loads(path.read_text("utf-8"))


def fetch_image(url: str, cache: Path) -> tuple[bytes, str]:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=60) as response:
        body = response.read(MAX_IMAGE_BYTES + 1)
        media = (response.headers.get_content_type() or "").lower()
    if len(body) > MAX_IMAGE_BYTES:
        raise ValueError("image over the size cap")
    if media not in IMAGE_TYPES:
        raise ValueError(f"image type {media}")
    folder = cache / "images"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{hashlib.sha256(body).hexdigest()}.{IMAGE_TYPES[media]}").write_bytes(body)
    return body, media


def openstax_record(
    cand: fv.Candidate, source: dict, pool: dict, image: bytes, media: str, tables: dict
) -> dict:
    book = pool["books"][cand.source_key]
    alt = " ".join((source["images"][0].get("alt") or "").split())
    page_url = f"https://openstax.org/books/{cand.source_key}/pages/{cand.page_key}"
    authors = ", ".join(book["authors"])
    table = tables.get(cand.id)
    record: dict = {
        "id": cand.id,
        "stratum": cand.stratum,
        "source": "openstax",
        "page_key": cand.page_key,
        "book": cand.source_key,
        "book_state": book["state"],
        "archive_version": book["archive_version"],
        "source_url": page_url + (f"#{source['element_id']}" if source.get("element_id") else ""),
        "licence_name": book["licence_name"],
        "licence_url": book["licence_url"],
        "attribution": (
            f"{authors}, {book['title']} (OpenStax), {source.get('figure_number') or 'figure'}; "
            f"{book['licence_name']}; {page_url}"
        ),
        "image": {
            "url": source["images"][0]["url"],
            "media_type": media,
            "sha256": hashlib.sha256(image).hexdigest(),
            "bytes": len(image),
            "width": source["images"][0].get("width"),
            "height": source["images"][0].get("height"),
        },
        "caption": source.get("caption"),
        "credits": fv.credits_of(source.get("caption")),
        "figure_number": source.get("figure_number"),
        "reference_alt": alt,
        "reference_long_description": alt,
        "reference_long_source": "openstax-alt",
        "reference_table": None,
    }
    if table is not None:
        record["reference_table"] = {
            "header": table["header"],
            "rows": table["rows"],
            "source": "alt-text",
            "read_by": table["read_by"],
            "checked_against_image_by": table["checked_against_image_by"],
            "verified_by_person": False,
        }
    return record


def arxiv_record(row: dict, arxiv_dir: Path) -> dict:
    from readablestem.gold import figures_v2_arxiv as fa

    paper = row["paper"]
    stem = row["id"].rsplit("/", 1)[1]
    cache = DEFAULT_CACHE / "arxiv" / paper / row["file"]
    index = int(stem.rsplit("-", 1)[1])
    figure = fa.find_plots(cache.read_text("utf-8", errors="ignore"))[index]
    png = arxiv_dir / paper / f"{stem}.png"
    return {
        "id": row["id"],
        "stratum": fv.CHART,
        "source": "arxiv",
        "page_key": paper,
        "paper": paper,
        "source_url": f"https://arxiv.org/abs/{paper}",
        "licence_name": row["licence_name"],
        "licence_url": row["licence_url"],
        "attribution": f'{row["authors"]}, "{row["title"]}", arXiv:{paper}; {row["licence_name"]}',
        "image": {
            "path": png.relative_to(OUT).as_posix(),
            "media_type": "image/png",
            "sha256": hashlib.sha256(png.read_bytes()).hexdigest(),
            "bytes": png.stat().st_size,
            "tex": png.with_suffix(".tex").relative_to(OUT).as_posix(),
        },
        "caption": figure.caption or None,
        "reference_alt": None,
        "reference_long_description": figure.long_description(),
        "reference_long_source": "template-from-source-data",
        "reference_table": figure.table(),
    }


def cmd_build(args: argparse.Namespace) -> int:
    pool_ox = _json(OUT / "pool-openstax.json")
    arxiv_path = OUT / "pool-arxiv.json"
    pool_ax = _json(arxiv_path) if arxiv_path.exists() else {"candidates": [], "papers_scanned": 0}
    tables_path = OUT / "tables-from-alt.json"
    tables = _json(tables_path) if tables_path.exists() else {}

    candidates = [fv.Candidate(**row) for row in pool_ox["candidates"]]
    rendered = {row["id"]: row for row in pool_ax["candidates"] if row.get("image_sha256")}
    candidates += [
        fv.Candidate(rid, fv.CHART, "arxiv", row["paper"], row["paper"])
        for rid, row in rendered.items()
    ]
    page_records = {}
    for line in (args.cache / "pool-records.jsonl").read_text("utf-8").splitlines():
        record = json.loads(line)
        page_records[record["id"]] = record

    exclusions_path = OUT / "exclusions.json"
    reviewed = _json(exclusions_path) if exclusions_path.exists() else {}
    skipped: list[dict] = [
        {"id": rid, "reason": f"reviewed: {why}"} for rid, why in reviewed.items()
    ]
    images: dict[str, tuple[bytes, str]] = {}
    while True:
        chosen = fv.select(candidates, skip=[s["id"] for s in skipped])
        missing = [c for c in chosen if c.source == "openstax" and c.id not in images]
        if not missing:
            break
        for cand in missing:
            url = page_records[cand.id]["images"][0]["url"]
            try:
                images[cand.id] = fetch_image(url, args.cache)
            except Exception as exc:
                skipped.append({"id": cand.id, "reason": f"{type(exc).__name__}: {exc}"})
                print(f"{cand.id}: skipped ({exc})", file=sys.stderr)

    records = []
    for cand in chosen:
        if cand.source == "openstax":
            image, media = images[cand.id]
            records.append(
                openstax_record(cand, page_records[cand.id], pool_ox, image, media, tables)
            )
        else:
            records.append(arxiv_record(rendered[cand.id], OUT / "arxiv"))
    splits = fv.assign_splits(chosen)
    for record in records:
        record["split"] = splits[record["id"]]
    problems = fv.validate_gold(records)
    if problems:
        print("\n".join(problems), file=sys.stderr)
        return 1

    with (OUT / "figures.jsonl").open("w", encoding="utf-8") as sink:
        for record in records:
            sink.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    selection = {
        "schema_version": 1,
        "set": "figures-v2",
        "seed": fv.SEED,
        "quotas": dict(fv.QUOTAS),
        "max_per_source": fv.MAX_PER_SOURCE,
        "min_reference_chars": fv.MIN_REFERENCE_CHARS,
        "openstax_pool_sha256": pool_ox["pool_sha256"],
        "openstax_pool_by_stratum": pool_ox["by_stratum"],
        "arxiv_papers_scanned": pool_ax.get("papers_scanned", 0),
        "arxiv_charts_rendered": len(rendered),
        "skipped_in_order": skipped,
        "strata_counts": fv.strata_counts(records),
        "n": len(records),
        "tables": {
            "source-data": sum(
                1 for r in records if (r["reference_table"] or {}).get("source") == "source-data"
            ),
            "alt-text": sum(
                1 for r in records if (r["reference_table"] or {}).get("source") == "alt-text"
            ),
            "charts_without_table": sum(
                1 for r in records if r["stratum"] == fv.CHART and not r["reference_table"]
            ),
        },
        "ids": [r["id"] for r in records],
    }
    (OUT / "selection.json").write_text(
        json.dumps(selection, indent=1, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    write_attribution(records, pool_ox)
    print(json.dumps(selection["strata_counts"]), selection["tables"], "n", len(records))
    return 0


def write_attribution(records: list[dict], pool_ox: dict) -> None:
    lines = [
        "# Attribution for data/gold-figures-v2",
        "",
        "Generated by `scripts/figures_v2_build.py`. Do not edit by hand.",
        "",
        "Each figure below is used under the licence shown. Reference descriptions are the "
        "OpenStax alt text (unchanged) or text built from the plotted data of the arXiv "
        "source. ReadableSTEM is not affiliated with or endorsed by OpenStax.",
        "",
        "The book-level CC BY licence does not override a figure's own credit line. Figures whose "
        "credit names another licence, permission or copyright are excluded by the draw "
        "(`figures_v2.third_party_licence`); the credits of the figures kept are listed here as "
        "the captions print them.",
        "",
        "| id | stratum | licence | source | credit lines |",
        "|---|---|---|---|---|",
    ]
    for r in records:
        credit = "; ".join(r.get("credits") or []).replace("|", "/") or "none printed"
        lines.append(
            f"| {r['id']} | {r['stratum']} | {r['licence_name']} | {r['attribution']} | {credit} |"
        )
    (OUT / "ATTRIBUTION.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def cmd_check(_: argparse.Namespace) -> int:
    records = [
        json.loads(line)
        for line in (OUT / "figures.jsonl").read_text("utf-8").splitlines()
        if line.strip()
    ]
    problems = fv.validate_gold(records)
    for r in records:
        if r["source"] == "arxiv":
            png = OUT / r["image"]["path"]
            if hashlib.sha256(png.read_bytes()).hexdigest() != r["image"]["sha256"]:
                problems.append(f"{r['id']}: image sha256 differs")
    print(
        "\n".join(problems) or f"ok: {len(records)} figures",
        file=sys.stderr if problems else sys.stdout,
    )
    return 1 if problems else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("build")
    p.add_argument("--cache", type=Path, default=DEFAULT_CACHE)
    p.set_defaults(func=cmd_build)
    p = sub.add_parser("check")
    p.set_defaults(func=cmd_check)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
