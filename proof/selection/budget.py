"""Held-out selection, step 3: print the drawn sections and apply the page rules.

Run from the ReadableSTEM checkout (no model calls; OpenStax archive and images only):
    uv run python ~/.cache/a11y-proof/prereg/budget.py draw.json CAND_GOLD_DIR PDF_DIR OUT.json

For each drawn section: `readablestem gold fetch --book B --page S --out CAND_GOLD_DIR
--allow-retired --date 2026-10-13`, then `readablestem eval render --book B --page S --gold
CAND_GOLD_DIR --out PDF_DIR/B--S.pdf` (Letter), then the PDF's page count (PyMuPDF).

Rules, applied in this order (fixed before the draw was run):
  A. A section that prints to more than MAX_SECTION_PAGES Letter pages is replaced by
     the next section in its (book, tercile) queue, until one fits.
  B. While the total is above MAX_TOTAL, the selected section with the most pages is
     replaced by the next in its queue (rule A applies to the replacement); while the
     total is below MIN_TOTAL, the one with the fewest pages is replaced likewise. Ties:
     the earlier book in draw order, then low < mid < high.
  C. At most MAX_REPLACEMENTS replacements in all; if the rules are still not met, stop
     and report (the set is then not pre-registered as is).
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pymupdf

MAX_SECTION_PAGES = 30  # = Settings.max_pages_per_job; convert_pdf refuses more (too_many_pages)
MIN_TOTAL, MAX_TOTAL = 150, 250
MAX_REPLACEMENTS = 15
BOOK_ORDER = ["college-physics", "chemistry", "precalculus", "introductory-statistics",
              "intermediate-algebra"]
TERCILES = ["low", "mid", "high"]


def run(cmd: list[str]) -> None:
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise SystemExit(f"failed ({proc.returncode}): {' '.join(cmd)}\n{proc.stderr[-2000:]}")


def pages_of(book: str, slug: str, gold: Path, pdfs: Path, cache: dict) -> int:
    key = f"{book}/{slug}"
    if key in cache:
        return cache[key]
    run(["uv", "run", "--isolated", "--group", "browser", "readablestem", "gold", "fetch", "--book", book, "--page", slug,
         "--out", str(gold), "--allow-retired", "--date", "2026-10-13"])
    pdf = pdfs / f"{book}--{slug}.pdf"
    run(["uv", "run", "--isolated", "--group", "browser", "readablestem", "eval", "render", "--book", book, "--page", slug,
         "--gold", str(gold), "--out", str(pdf)])
    with pymupdf.open(pdf) as doc:
        n = doc.page_count
    cache[key] = n
    print(f"  printed {key}: {n} pages", flush=True)
    return n


def main() -> int:
    draw = json.load(open(sys.argv[1], encoding="utf-8"))
    gold, pdfs, out = Path(sys.argv[2]), Path(sys.argv[3]), Path(sys.argv[4])
    pdfs.mkdir(parents=True, exist_ok=True)
    cache: dict[str, int] = {}
    log: list[str] = []
    pos = {(b, t): 0 for b in BOOK_ORDER for t in TERCILES}
    replacements = 0

    def current(b: str, t: str) -> str:
        return draw["books"][b][t]["order"][pos[(b, t)]]

    def advance(b: str, t: str, why: str) -> None:
        nonlocal replacements
        old = current(b, t)
        pos[(b, t)] += 1
        replacements += 1
        log.append(f"replace {b}/{old} ({t}) by {current(b, t)}: {why}")
        print("  " + log[-1], flush=True)

    def fit(b: str, t: str) -> None:
        while pages_of(b, current(b, t), gold, pdfs, cache) > MAX_SECTION_PAGES:
            if replacements >= MAX_REPLACEMENTS:
                return
            advance(b, t, f"rule A, more than {MAX_SECTION_PAGES} pages")

    for b in BOOK_ORDER:
        for t in TERCILES:
            fit(b, t)

    def total() -> int:
        return sum(cache[f"{b}/{current(b, t)}"] for b in BOOK_ORDER for t in TERCILES)

    while replacements < MAX_REPLACEMENTS and not (MIN_TOTAL <= total() <= MAX_TOTAL):
        keys = [(b, t) for b in BOOK_ORDER for t in TERCILES]
        sizes = {k: cache[f"{k[0]}/{current(*k)}"] for k in keys}
        if total() > MAX_TOTAL:
            k = max(keys, key=lambda k: (sizes[k], -keys.index(k)))
            advance(*k, f"rule B, total {total()} above {MAX_TOTAL}")
        else:
            k = min(keys, key=lambda k: (sizes[k], keys.index(k)))
            advance(*k, f"rule B, total {total()} below {MIN_TOTAL}")
        fit(*k)

    ok = MIN_TOTAL <= total() <= MAX_TOTAL and all(
        cache[f"{b}/{current(b, t)}"] <= MAX_SECTION_PAGES for b in BOOK_ORDER for t in TERCILES)
    result = {
        "ok": ok,
        "replacements": replacements,
        "log": log,
        "total_pages": total(),
        "selected": [{"book": b, "tercile": t, "slug": current(b, t),
                      "queue_position": pos[(b, t)], "pages": cache[f"{b}/{current(b, t)}"]}
                     for b in BOOK_ORDER for t in TERCILES],
        "printed": cache,
    }
    out.write_text(json.dumps(result, indent=1) + "\n", encoding="utf-8")
    print(f"ok={ok} total pages={total()} replacements={replacements}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
