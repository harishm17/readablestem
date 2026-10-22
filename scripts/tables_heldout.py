"""Build a fresh real-document table set from arXiv lecture notes and tutorials
(docs/tables.md, "Recall on real documents"; data/real-tables-heldout/README.md has the rule).

    uv run python scripts/tables_heldout.py select --cache DIR [--n 8] [--seed S]
    uv run python scripts/tables_heldout.py build  --cache DIR --out data/real-tables-heldout

`select` walks the candidates of the real-world corpus pool (`data/realworld`, OAI records
with their licences) that nothing has used (not visited by the real-world builder, in none of
its manifests, not in `data/real-tables`), keeping those whose title or comments say lecture,
tutorial, introduction, introductory, primer, course or guide, in the order of
SHA-256(seed:id). For each it downloads the PDF and the source (the polite way: one request
every few seconds), and accepts the first `n` that pass the gates written in the README:
a main TeX file, 6 to 250 PDF pages, and at least three data tables (a `tabular` with two or
more columns of content, no graphics) that `gold/tabulars.py` finds in the PDF's words. It
never runs the table detector. `build` cuts each accepted PDF to the pages that carry a table
and their neighbours and writes `labels.json` with the boxes (proposals, to check by eye).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
from pathlib import Path

import httpx
import pymupdf

sys.path.insert(0, str(Path(__file__).resolve().parent))
import realworld_build as rb  # noqa: E402

from readablestem.gold import arxiv_tex as tex  # noqa: E402
from readablestem.gold import tabulars as tab  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
POOL = ROOT / "data" / "realworld"
KEYWORDS = re.compile(
    r"lecture|tutorial|introduction|introductory|primer|course|guide",
    re.IGNORECASE,
)
MIN_TABLES = 3
MIN_PAGES, MAX_PAGES = 6, 250
UNAVAILABLE = ("2401.00649", "2412.19752", "2505.03022", "2409.08224", "2407.13734")


def used_ids() -> set[str]:
    used = set(UNAVAILABLE)
    cache = Path.home() / ".cache" / "readablestem" / "realworld" / "papers"
    if cache.is_dir():
        used |= {p.name for p in cache.iterdir()}
    for part in ("dev", "heldout", "fresh"):
        manifest = json.loads((POOL / f"manifest-{part}.json").read_text())
        used |= {p["id"] for p in manifest["papers"]}
    return used


def candidates(seed: str) -> list[dict]:
    pool = json.loads((Path.home() / ".cache/readablestem/realworld/pool.json").read_text())
    taken = used_ids()
    keep = [
        c
        for c in pool["candidates"]
        if c["id"] not in taken and KEYWORDS.search(c["title"] + " " + (c.get("comments") or ""))
    ]
    keep.sort(key=lambda c: hashlib.sha256(f"{seed}:{c['id']}".encode()).hexdigest())
    return keep


def read_tables(folder: Path) -> tuple[list[tab.Tabular], list[list[tuple]]]:
    sources = tex.read_sources(folder / "src")
    main = tex.find_main(sources)
    if main is None:
        raise rb.Skip("no_main_tex")
    macros = tex.MacroTable()
    flat = macros.expand(macros.read(tex.flatten(sources, main)))
    found = [t for t in tab.find_tabulars(tex.body_of(flat)) if tab.is_data_table(t)]
    doc = pymupdf.open(folder / "paper.pdf")
    words = [[(w[0], w[1], w[2], w[3], w[4]) for w in page.get_text("words")] for page in doc]
    return found, words


def cmd_select(args: argparse.Namespace) -> int:
    accepted, skipped = [], []
    with httpx.Client(headers={"User-Agent": "readablestem-eval/1.0 (research; CC BY corpus)"}) as client:
        for record in candidates(args.seed):
            if len(accepted) >= args.n:
                break
            folder = args.cache / record["id"]
            try:
                rb.download(client, f"https://arxiv.org/pdf/{record['id']}", folder / "paper.pdf", args.pause)
                eprint = rb.download(
                    client, f"https://arxiv.org/e-print/{record['id']}", folder / "e-print.bin", args.pause
                )
                if not (folder / "src").is_dir():
                    rb.unpack(eprint, folder / "src")
                doc = pymupdf.open(folder / "paper.pdf")
                if not MIN_PAGES <= len(doc) <= MAX_PAGES:
                    raise rb.Skip("pdf_page_count", str(len(doc)))
                found, words = read_tables(folder)
                placed = [t for t in found if tab.locate(t, words)]
                if len(placed) < MIN_TABLES:
                    raise rb.Skip("fewer_than_3_tables", f"{len(placed)} of {len(found)}")
            except rb.Skip as skip:
                skipped.append({"id": record["id"], "reason": skip.code, "detail": skip.detail})
                print("skip", record["id"], skip.code, skip.detail, flush=True)
                continue
            except Exception as exc:  # a broken archive or PDF is a skip, never a choice
                skipped.append({"id": record["id"], "reason": type(exc).__name__, "detail": ""})
                print("skip", record["id"], type(exc).__name__, flush=True)
                continue
            accepted.append(
                {
                    "id": record["id"],
                    "title": record["title"],
                    "licence": record["licence_name"],
                    "pdf_pages": len(doc),
                    "tables": len(placed),
                    "pdf_sha256": hashlib.sha256((folder / "paper.pdf").read_bytes()).hexdigest(),
                }
            )
            print("accept", record["id"], len(doc), "pages", len(placed), "tables", flush=True)
    out = {"seed": args.seed, "accepted": accepted, "skipped": skipped}
    (args.cache / "selection.json").write_text(json.dumps(out, indent=1))
    print(json.dumps({"accepted": len(accepted), "skipped": len(skipped)}))
    return 0 if len(accepted) >= args.n else 1


def cmd_build(args: argparse.Namespace) -> int:
    selection = json.loads((args.cache / "selection.json").read_text())
    args.out.mkdir(parents=True, exist_ok=True)
    documents = []
    for entry in selection["accepted"]:
        folder = args.cache / entry["id"]
        found, words = read_tables(folder)
        placed = []
        for t in found:
            where = tab.locate(t, words)
            if where is not None:
                placed.append(
                    {
                        "page": where.page,
                        "bbox": [round(v, 1) for v in where.bbox],
                        "matched": round(where.matched, 2),
                    }
                )
        # Deduplicate boxes found twice (a table cited twice, nested environments).
        unique: list[dict] = []
        for t in sorted(placed, key=lambda t: (t["page"], t["bbox"][1])):
            if not any(
                u["page"] == t["page"] and _overlap(u["bbox"], t["bbox"]) > 0.8 for u in unique
            ):
                unique.append(t)
        keep = sorted({p for t in unique for q in (t["page"] - 1, t["page"], t["page"] + 1) if 0 <= (p := q) < len(words)})
        keep = keep[: args.max_pages]
        source = pymupdf.open(folder / "paper.pdf")
        cut = pymupdf.open()
        for page in keep:
            cut.insert_pdf(source, from_page=page, to_page=page)
        file = f"{entry['id']}.pdf"
        cut.save(args.out / file, garbage=4, deflate=True)
        index = {page: i for i, page in enumerate(keep)}
        documents.append(
            {
                "file": file,
                "source": f"arXiv:{entry['id']}",
                "licence": entry["licence"],
                "pages": list(range(len(keep))),
                "source_pages": keep,
                "tables": [
                    {"page": index[t["page"]], "bbox": t["bbox"]} for t in unique if t["page"] in index
                ],
            }
        )
    (args.out / "labels.json").write_text(
        json.dumps({"schema_version": 1, "documents": documents}, indent=1)
    )
    print(json.dumps({"documents": len(documents), "tables": sum(len(d["tables"]) for d in documents)}))
    return 0


def _overlap(a: list[float], b: list[float]) -> float:
    x0, y0, x1, y1 = max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])
    inter = max(0.0, x1 - x0) * max(0.0, y1 - y0)
    small = min((a[2] - a[0]) * (a[3] - a[1]), (b[2] - b[0]) * (b[3] - b[1])) or 1.0
    return inter / small


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="command", required=True)
    s = sub.add_parser("select")
    s.add_argument("--cache", type=Path, required=True)
    s.add_argument("--n", type=int, default=8)
    s.add_argument("--seed", default="tables-heldout-2026-10-21")
    s.add_argument("--pause", type=float, default=3.0)
    s.set_defaults(run=cmd_select)
    b = sub.add_parser("build")
    b.add_argument("--cache", type=Path, required=True)
    b.add_argument("--out", type=Path, required=True)
    b.add_argument("--max-pages", type=int, default=30)
    b.set_defaults(run=cmd_build)
    args = ap.parse_args()
    return args.run(args)


if __name__ == "__main__":
    raise SystemExit(main())
