"""Write sections 4 and 5 of PREREGISTRATION.md from the selection files (counts only).

    python3 section4.py features.json draw.json budget.json gold-summary.json OUT.md
"""

from __future__ import annotations

import json
import sys

feats, draw, budget, gold = (json.load(open(p, encoding="utf-8")) for p in sys.argv[1:5])
out = open(sys.argv[5], "w", encoding="utf-8")
w = lambda s="": out.write(s + "\n")  # noqa: E731

BOOK_TITLE = {b: v["title"] for b, v in feats["books"].items()}

w("## 4. The held-out set and how it was chosen")
w()
w("### 4.1 Procedure (code in `selection/`, hashes in `selection/rules-before-draw.sha256`)")
w()
w("1. **Pool.** Every numbered section (slug `N-M-...`) of the five books.")
w("2. **Eligibility** (`features.py`). Out: sections used in development (section 2); "
  "lab worksheets (an element with the class token `lab`; Introductory Statistics wraps "
  "each Stats Lab page in one); sections under 300 visible words; near-copies of the "
  "development sections (5% or more of their word 8-grams in the development text).")
w("3. **Features**, from the archive XHTML with the extractor `gold fetch` uses "
  "(`gold.openstax.build_page_records`): equations (block, inline), figures, tables, "
  "headings, visible words (head, style, script and MathML removed). Maths density = "
  "equations per 1,000 visible words.")
w("4. **Strata and draw** (`draw.py`). Per book, eligible sections sorted by maths density "
  "(then slug) are cut into terciles by position (low, mid, high). One `random.Random`, "
  f"seeded with the first 16 hex digits of SHA-256(\"{draw['seed_text']}\") = "
  f"{draw['seed']}, shuffles each tercile in a fixed order (book order as in section 3, "
  "then low, mid, high). The first section of each shuffled tercile is drawn; the rest "
  "of the order is that tercile's replacement queue. 5 books x 3 terciles = 15 sections.")
w("5. **Page rules** (`budget.py`), on Letter prints made by `a11y-stem eval render` "
  "after `a11y-stem gold fetch --allow-retired`: (A) a section printing to more than 30 "
  "pages (the production `max_pages_per_job`; `convert_pdf` refuses more with "
  "`too_many_pages`) is replaced by the next in its queue; (B) while the total is above "
  "250 pages, the longest selected section is replaced the same way, and while it is "
  "below 150 the shortest; (C) at most 15 replacements.")
w()
w("Strata are within each book: Precalculus's \"low\" tercile is denser than "
  "Introductory Statistics' \"high\" one, so the set spans 0 to 112 equations per "
  "1,000 words overall.")
w()
w("Only input properties were looked at to choose: the counts above, the overlap, and "
  "page counts. No system has produced any output for these sections.")
w()
w("### 4.2 Deviations during selection (all before any page count was read)")
w()
w("- `features.py` first counted words with the page `<head>`, `<style>` and `<script>` "
  "text included (OpenStax's developer scripts), which made unrelated sections overlap "
  "by 5-10%. Fixed to visible text only; the threshold, 10% until then, was set to "
  "5% from the distribution of the fixed overlap (more than twice the 0.021 maximum of the books "
  "with no shared lineage). The first lab rule looked only at the page's top element and "
  "missed the Introductory Statistics labs; fixed to any element with the class token "
  "`lab`. Both before the draw.")
w("- `budget.py` as hashed before the draw capped a section at 40 pages. The first print "
  "run failed (Playwright is not in the default dependency group); the second was stopped "
  "part-way, its log unread (kept as `budget-stopped-unread.log`), and the cap "
  "set to 30 to match the production page limit. The CLI calls were changed to "
  "`uv run --isolated --group browser` so the shared virtual environment was not "
  "modified. Each change is logged with a time and hash in `rules-before-draw.sha256`.")
w()
w("### 4.3 Replacements made by the page rules")
w()
if budget["log"]:
    for line in budget["log"]:
        w(f"- {line}")
else:
    w("- none")
w()
w(f"Rules met: {budget['ok']}; replacements: {budget['replacements']} of 15 allowed; "
  f"total {budget['total_pages']} Letter pages.")
w()
w("### 4.4 The 15 sections")
w()
w("Counts are from the held-out gold (`proof/heldout-gold`, fetched 2026-10-13). G1 = "
  "equations matching the known scorer-gap pattern (7.1). Alt = figures with a non-empty "
  "OpenStax alt text (the judge's reference). Box = headings inside a note, example or "
  "other box.")
w()
w("| # | Book | Section | Title | Stratum | Eq./1,000 words | Pages | Equations (block / inline) | G1 | Figures (alt) | Tables | Headings (box) |")
w("|---|---|---|---|---|---|---|---|---|---|---|---|")
rows = {r["slug"]: r for b in feats["books"].values() for r in b["sections"]}
i = 0
for sel in budget["selected"]:
    i += 1
    key = f"{sel['book']}/{sel['slug']}"
    g = gold["sections"][key]
    f = next(r for r in feats["books"][sel["book"]]["sections"] if r["slug"] == sel["slug"])
    num = f"{f['chapter']}.{f['section']}"
    w(f"| {i} | {BOOK_TITLE[sel['book']]} | `{sel['slug']}` | {f['title']} | "
      f"{sel['tercile']} | {f['eq_per_1000_words']} | {g['pdf_pages']} | "
      f"{g['equations']} ({g['block']} / {g['inline']}) | {g['g1']} | "
      f"{g['figures']} ({g['figures_with_alt']}) | {g['tables']} | "
      f"{g['headings']} ({g['headings_in_boxes']}) |")
t = gold["totals"]
w(f"| | **Total** | | | | | **{t['pdf_pages']}** | **{t['equations']}** ({t['block']} / "
  f"{t['inline']}) | **{t['g1']}** | **{t['figures']}** ({t['figures_with_alt']}) | "
  f"**{t['tables']}** | **{t['headings']}** ({t['headings_in_boxes']}) |")
w()
w("Per book:")
w()
w("| Book | Sections | Pages | Equations | Figures | Tables | Headings |")
w("|---|---|---|---|---|---|---|")
for book in [s["book"] for s in budget["selected"]][::3]:
    keys = [f"{s['book']}/{s['slug']}" for s in budget["selected"] if s["book"] == book]
    agg = {k: sum(gold["sections"][x][k] for x in keys)
           for k in ["pdf_pages", "equations", "figures", "tables", "headings"]}
    w(f"| {BOOK_TITLE[book]} | {len(keys)} | {agg['pdf_pages']} | {agg['equations']} | "
      f"{agg['figures']} | {agg['tables']} | {agg['headings']} |")
w()
out.close()
