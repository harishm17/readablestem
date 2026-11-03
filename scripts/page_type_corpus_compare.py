"""Compare the page-type classifier with and without the text-coverage rules on a labelled corpus.

    uv run python scripts/page_type_corpus_compare.py --labels LABELS.json [--show-all]

The labels file is the one `readablestem.eval.page_types` reads (the corpus is not in this
repository). "Before" switches off the `TEXT_NOT_COVERING` rules (`page_type-v5.4` behaviour);
"after" is the code as it is. Prints accuracy against the labels for both and every page whose
kind or action changes, with its label. Exit code 1 when any page gets worse against its
label, so it can gate a change to the thresholds. Kinds, counts and file names only.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pymupdf

from readablestem.eval.page_types import action, classify_file
from readablestem.pipeline import page_type


def run(entries: list[dict], *, rules: bool) -> dict[tuple[str, int], tuple[str, str]]:
    saved = page_type.UNCOVERED_PICTURE
    if not rules:
        page_type.UNCOVERED_PICTURE = 9.0
    try:
        out = {}
        by_file: dict[str, list[int]] = {}
        for e in entries:
            by_file.setdefault(e["file"], []).append(e["page"])
        for file, pages in by_file.items():
            found = classify_file(Path(file), sorted(set(pages)))
            for number, (result, _) in found.items():
                out[(file, number)] = (
                    result.kind,
                    action(result.kind, page_type.kept_reason(result)),
                )
        return out
    finally:
        page_type.UNCOVERED_PICTURE = saved


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--labels", required=True)
    parser.add_argument("--show-all", action="store_true")
    args = parser.parse_args()
    pymupdf.TOOLS.mupdf_display_errors(False)
    pymupdf.TOOLS.mupdf_display_warnings(False)
    here = Path(args.labels).resolve().parent
    entries = []
    for e in json.loads(Path(args.labels).read_text()):
        if not Path(e["file"]).is_absolute():  # relative to the labels file's folder or its parent
            places = [d / e["file"] for d in (here, here.parent)]
            e = {**e, "file": str(next((p for p in places if p.exists()), places[0]))}
        if Path(e["file"]).exists():
            entries.append(e)
    before, after = run(entries, rules=False), run(entries, rules=True)
    right_b = right_a = worse = better = 0
    for e in entries:
        key = (e["file"], e["page"])
        kb, ka = before[key][0], after[key][0]
        label = e["label"]
        right_b += kb == label
        right_a += ka == label
        changed = kb != ka or before[key][1] != after[key][1]
        if changed:
            worse += kb == label and ka != label
            better += kb != label and ka == label
        if changed or args.show_all:
            print(f"{Path(e['file']).name} p{e['page']}: label {label}, before {kb}, after {ka}")
    n = len(entries)
    print(
        f"\npages {n}; kind right before {right_b} ({right_b / n:.1%}), "
        f"after {right_a} ({right_a / n:.1%})"
    )
    print(f"worse against label: {worse}; better: {better}")
    return 1 if worse else 0


if __name__ == "__main__":
    sys.exit(main())
