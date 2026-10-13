"""Held-out selection, step 1: features of every eligible section (no model calls).

Run from the a11y-stem checkout (read-only use of its gold code):
    uv run python ~/.cache/a11y-proof/prereg/features.py OUT.json

For each candidate book: every numbered section (slug N-M-...), its exclusion reason if
any, and input-only features computed with the same extractor `a11y-stem gold fetch`
uses (gold.openstax.build_page_records): equations (block/inline), figures, tables,
headings, words outside MathML, and the word 8-gram containment of the section in the
development sections' text. Archive JSON is read through the repo's cache
(.cache/openstax), fetched politely (0.5 s apart) when missing.

Prints counts only. No page text is printed or stored.
"""

from __future__ import annotations

import html
import json
import re
import sys
from pathlib import Path

from a11y_stem.gold import openstax as ox

REPO = Path(__file__).resolve().parents[2]
CACHE = REPO / ".cache" / "openstax"
BOOKS = [
    "college-physics",
    "chemistry",
    "precalculus",
    "introductory-statistics",
    "intermediate-algebra",
]

# Every OpenStax section used in development, by (book, section slug). Sources: the scan
# in PREREGISTRATION.md section 2 (data/gold, data/gold/tables, tests, docs, every
# worktree's .cache, a11y-research incl. niche/holdout-2026-10-13 and
# niche/hybrid-heldout, chatbot-baseline, the pdf-eval tarball).
DEV_USED = {
    ("physics", s)
    for s in [
        "1-1-physics-definitions-and-applications",
        "1-3-the-language-of-physics-physical-quantities-and-units",
        "2-1-relative-motion-distance-and-displacement",
        "2-2-speed-and-velocity",
        "2-3-position-vs-time-graphs",
        "2-4-velocity-vs-time-graphs",
        "3-1-acceleration",
        "3-2-representing-acceleration-with-equations-and-graphs",
        "4-1-force",
        "4-2-newtons-first-law-of-motion-inertia",
        "4-3-newtons-second-law-of-motion",
        "4-4-newtons-third-law-of-motion",
        "5-1-vector-addition-and-subtraction-graphical-methods",
        "5-2-vector-addition-and-subtraction-analytical-methods",
        "5-3-projectile-motion",
        "5-4-inclined-planes",
        "5-5-simple-harmonic-motion",
        "6-3-rotational-motion",
        "11-3-phase-change-and-latent-heat",
        "14-1-speed-of-sound-frequency-and-wavelength",
        "16-3-lenses",
        "22-2-nuclear-forces-and-radioactivity",
        "23-1-the-four-fundamental-forces",
        "23-2-quarks",
    ]
} | {
    ("statistics", s)
    for s in [
        "1-3-frequency-frequency-tables-and-levels-of-measurement",
        "2-2-histograms-frequency-polygons-and-time-series-graphs",
        "3-4-contingency-tables",
        "4-2-mean-or-expected-value-and-standard-deviation",
        "11-6-test-of-a-single-variance",
    ]
} | {
    ("college-physics", "1-1-physics-an-introduction"),
    ("college-physics", "10-2-kinematics-of-rotational-motion"),
    ("college-physics", "18-3-coulombs-law"),
    ("chemistry", "3-3-molarity"),
    ("chemistry", "12-4-integrated-rate-laws"),
    ("precalculus", "8-8-vectors"),
    ("precalculus", "12-4-derivatives"),
    ("algebra-and-trigonometry", "13-6-binomial-theorem"),
    ("introductory-statistics", "7-2-the-central-limit-theorem-for-sums"),
}
# Books whose every section counts as development material (gold, tables gold,
# table-bench's whole-book scan, layout regression renders, fresh hold-out f01-f09).
DEV_BOOKS = {"physics", "statistics"}

MIN_WORDS = 300
# Share of a section's word 8-grams found in the development sections. 0.05 is more than
# twice the highest value (0.021) seen in the three candidate books that share no text
# lineage with the development books (chemistry, precalculus, intermediate-algebra).
NEAR_DUP = 0.05


def words_outside_math(xhtml: str) -> list[str]:
    """Visible words of the page body: head, style, script and MathML removed."""
    body = re.sub(r"<head\b.*?</head>", " ", xhtml, flags=re.S | re.I)
    body = re.sub(r"<(style|script)\b.*?</\1>", " ", body, flags=re.S | re.I)
    body = re.sub(r"<math\b.*?</math>", " ", body, flags=re.S | re.I)
    body = re.sub(r"<[^>]+>", " ", body)
    return re.findall(r"[a-z0-9]+", html.unescape(body).lower())


def ngrams(words: list[str], n: int = 8) -> set[tuple[str, ...]]:
    return {tuple(words[i : i + n]) for i in range(len(words) - n + 1)}


def is_lab(xhtml: str) -> bool:
    """A lab worksheet: some element's class has the token "lab" (OpenStax statistics
    wraps each lab page in a note with class "statistics lab"; "collab" is not a lab)."""
    return any("lab" in c.split() for c in re.findall(r'class="([^"]+)"', xhtml))


def main() -> int:
    out = Path(sys.argv[1])
    fetcher = ox.HttpFetcher(cache_dir=CACHE)

    # Development text: every used section of every book (archive JSON from the cache,
    # fetched once if missing).
    dev_books = sorted({b for b, _ in DEV_USED})
    dev_grams: set = set()
    dev_found = 0
    for bslug in dev_books:
        book = ox.resolve_book(bslug, fetcher)
        by_slug = {p.slug: p for p in ox.iter_pages(book.tree)}
        for b, s in sorted(DEV_USED):
            if b != bslug:
                continue
            page = by_slug.get(s)
            if page is None:
                print(f"dev section not in book tree: {b} {s}", file=sys.stderr)
                continue
            xhtml = fetcher.get_json(book.content_url(page.uuid))["content"]
            dev_grams |= ngrams(words_outside_math(xhtml))
            dev_found += 1
    print(f"development sections read: {dev_found} of {len(DEV_USED)}; 8-grams {len(dev_grams)}")

    result = {"near_dup_threshold": NEAR_DUP, "min_words": MIN_WORDS, "books": {}}
    for bslug in BOOKS:
        book = ox.resolve_book(bslug, fetcher)
        ox.check_public_license(book)
        rows = []
        for page in ox.iter_pages(book.tree):
            if page.section is None:
                continue
            row = {"slug": page.slug, "title": page.title, "chapter": page.chapter,
                   "section": page.section}
            if (bslug, page.slug) in DEV_USED:
                row["excluded"] = "used_in_development"
                rows.append(row)
                continue
            xhtml = fetcher.get_json(book.content_url(page.uuid))["content"]
            recs = ox.build_page_records(book, page, xhtml, "2026-10-13")
            words = words_outside_math(xhtml)
            grams = ngrams(words)
            eq = [r for r in recs if r["kind"] == "equation"]
            row |= {
                "words": len(words),
                "equations": len(eq),
                "block": sum(r.get("display") == "block" for r in eq),
                "inline": sum(r.get("display") != "block" for r in eq),
                "figures": sum(r["kind"] == "figure" for r in recs),
                "tables": sum(r["kind"] == "table" for r in recs),
                "headings": sum(r["kind"] == "heading" for r in recs),
                "eq_per_1000_words": round(1000 * len(eq) / max(len(words), 1), 2),
                "dev_containment": round(len(grams & dev_grams) / len(grams), 4) if grams else 0.0,
                "lab": is_lab(xhtml),
            }
            if row["lab"]:
                row["excluded"] = "lab_worksheet"
            elif len(words) < MIN_WORDS:
                row["excluded"] = "under_min_words"
            elif row["dev_containment"] >= NEAR_DUP:
                row["excluded"] = "near_duplicate_of_development"
            rows.append(row)
        result["books"][bslug] = {
            "title": book.title,
            "uuid": book.uuid,
            "version": book.version,
            "archive": book.archive_version,
            "state": book.state,
            "license_url": book.license_url,
            "cms_license_url": book.cms_license_url,
            "authors": book.authors,
            "sections": rows,
        }
        n_ok = sum("excluded" not in r for r in rows)
        print(f"{bslug}: {len(rows)} numbered sections, {n_ok} eligible; requests so far "
              f"{fetcher.requests_made}")
    out.write_text(json.dumps(result, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
