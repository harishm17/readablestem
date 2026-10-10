"""Score the page-type classifier (`pipeline.page_type`) on a labelled page corpus.

    uv run python -m a11y_stem.eval.page_types --labels LABELS.json [--labels ...]

A labels file is a JSON list of {"file", "page" (1-based), "label" (a page kind),
"split", "group"} records; files are read where they are. The corpus itself is not in
this repository: it holds derivatives of a real lecturer's handwritten notes and PDFs
from local research folders (docs/layout.md "Hidden text and page types" gives the
numbers and how they were made). Prints the confusion matrix of page kinds per split,
the matrix of what this app does with the page ("kept as an image" or "read as text"),
every error, and the time per page. Kinds, counts and file names only: never page text.
"""

from __future__ import annotations

import argparse
import json
import statistics
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import pymupdf

from a11y_stem.pipeline import page_type

SHORT = {
    "born_digital": "born",
    "mixed_ink": "mx_ink",
    "mixed_hidden_text": "mx_hid",
    "scan_no_text": "scan",
    "scan_ocr": "scan_ocr",
    "handwritten": "hand",
    "handwritten_ocr": "hand_ocr",
    "vector_no_text": "vector",
    "blank": "blank",
}


def action(kind: str, kept: str | None = None) -> str:
    """What this app does with a page of this kind (no handwriting or scan conversion);
    `kept`: the classifier's `kept_reason`, when the page is kept as its image."""
    if kept or kind in ("handwritten", "handwritten_ocr", "scan_no_text", "scan_ocr"):
        return "page_image"
    if kind == "mixed_ink":
        return "text_and_ink_images"
    if kind == "blank":
        return "nothing"
    return "text"


def matrix(rows: list[dict[str, Any]], true: str, pred: str, names: list[str]) -> str:
    counts = Counter((r[true], r[pred]) for r in rows)
    present = [k for k in names if any(r[true] == k or r[pred] == k for r in rows)]
    short = [SHORT.get(k, k) for k in present]
    out = [
        "| true \\ predicted | " + " | ".join(short) + " | n | right |",
        "|---|" + "---|" * (len(present) + 2),
    ]
    for t in present:
        n = sum(counts[(t, p)] for p in present)
        if not n:
            continue
        cells = [str(counts[(t, p)]) if counts[(t, p)] else "." for p in present]
        out.append(f"| {SHORT.get(t, t)} | " + " | ".join(cells) + f" | {n} | {counts[(t, t)]} |")
    return "\n".join(out)


Classified = dict[int, tuple[page_type.PageTypeResult, float]]


def classify_file(path: Path, pages: list[int]) -> Classified:
    out = {}
    with pymupdf.open(path) as doc:
        hint = page_type.producer_hint(doc)
        budget = page_type.InkBudget(1e9)  # the corpus measures the classifier, not the budget
        for number in pages:
            page = doc[number - 1]
            started = time.perf_counter()
            raw = page.get_text("rawdict", flags=page_type.TEXT_FLAGS)["blocks"]
            drawings = page.get_cdrawings()
            result = page_type.classify(page, raw, drawings, budget=budget, producer=hint)
            out[number] = (result, (time.perf_counter() - started) * 1000)
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--labels", action="append", required=True)
    parser.add_argument("--out", help="write the per-page rows here (JSON)")
    args = parser.parse_args(argv)
    pymupdf.TOOLS.mupdf_display_errors(False)
    pymupdf.TOOLS.mupdf_display_warnings(False)
    labels: list[dict[str, Any]] = []
    for name in args.labels:
        here = Path(name).resolve().parent
        for entry in json.loads(Path(name).read_text()):
            if not Path(entry["file"]).is_absolute():  # relative to the labels file's folder
                places = [d / entry["file"] for d in (here, here.parent)]
                found = next((p for p in places if p.exists()), places[0])
                entry = {**entry, "file": str(found)}
            labels.append(entry)
    by_file: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for entry in labels:
        by_file[entry["file"]].append(entry)
    rows: list[dict[str, Any]] = []
    for file, entries in by_file.items():
        found = classify_file(Path(file), sorted({e["page"] for e in entries}))
        for entry in entries:
            result, ms = found[entry["page"]]
            label = entry["label"]
            rows.append(
                {
                    **entry,
                    "pred": result.kind,
                    "reasons": result.reasons,
                    "ms": round(ms, 1),
                    "true_action": action(label) if label in SHORT else label,
                    "action": action(result.kind, page_type.kept_reason(result)),
                    "kept": page_type.kept_reason(result),
                    "visible": result.text.visible_chars,
                    "invisible": result.text.invisible_chars,
                    "covered": result.text.covered_chars,
                    "coverage": round(result.image_coverage, 2),
                    "strokes": result.ink.strokes,
                    "rows": result.ink.rows,
                    "analysed": result.ink.analysed,
                }
            )
    kinds = list(SHORT)
    print(f"pages: {len(rows)}, kind right: {sum(r['pred'] == r['label'] for r in rows)}")
    splits = ["all", *sorted({r.get("split", "") for r in rows})]
    for split in splits:
        chosen = rows if split == "all" else [r for r in rows if r.get("split", "") == split]
        if not chosen or (split != "all" and len(chosen) == len(rows)):
            continue
        right = sum(r["pred"] == r["label"] for r in chosen)
        acted = sum(r["action"] == r["true_action"] for r in chosen)
        print(f"\n### {split}: {len(chosen)} pages, kind right {right}, action right {acted}\n")
        print(matrix(chosen, "label", "pred", kinds))
    actions = ["text", "text_and_ink_images", "page_image", "nothing"]
    print("\n### What the app does, all pages\n")
    print(matrix([r for r in rows if r["label"] in SHORT], "true_action", "action", actions))
    print("\n### Errors\n")
    for r in rows:
        if r["pred"] != r["label"]:
            print(
                f"- {Path(r['file']).name} p{r['page']} [{r.get('group', '')}, "
                f"{r.get('split', '')}] {r['label']} -> {r['pred']} ({','.join(r['reasons'])}); "
                f"vis {r['visible']} inv {r['invisible']} cov {r['covered']} img {r['coverage']} "
                f"strokes {r['strokes']} rows {r['rows']}"
            )
    times = sorted(r["ms"] for r in rows)
    if times:
        print(
            f"\nTime per page: median {statistics.median(times):.0f} ms, "
            f"p95 {times[int(0.95 * (len(times) - 1))]:.0f} ms, max {times[-1]:.0f} ms"
        )
    if args.out:
        Path(args.out).write_text(json.dumps(rows, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
