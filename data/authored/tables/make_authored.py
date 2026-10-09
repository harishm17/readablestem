"""Rebuild the authored HTML table documents of the tables eval and their gold.

Usage (from the project root; needs the `browser` group and Chromium):

    uv run python data/authored/tables/make_authored.py [maths-and-headers] [slides]

- `maths-and-headers/`: a lab handout with maths in header cells, a two-level header,
  row headers, a table split across two pages with its header repeated, and a layout
  table (a form to fill in).
- `slides/`: two 16:9 slides with tables in PowerPoint's default table style (a filled
  header row, banded rows, white lines, no rules), one with row headers.

Each HTML file is printed by headless Chromium at the page size its CSS asks for, like
the layout fixtures. The gold (`gold.jsonl`) is the HTML tables themselves. A table that
runs over a page break becomes one gold table per page (the pipeline keeps the two parts
apart, docs/eval.md), each with the repeated header rows, sharing a `cluster`. The other
folders (`tablebench-*`, `handout-*`) were built during the PDF research of 2026-10-08/09
(README.md) and are not rebuilt here.

Fonts come from the machine that prints, so re-printing elsewhere can move text a little.
Commit regenerated PDFs only after `uv run pytest tests/test_eval_tables*.py` passes.
"""

from __future__ import annotations

import json
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

HERE = Path(__file__).resolve().parent
FIXED_DATE = "D:20261009000000Z"  # so a re-print only differs where the content does
DOCUMENTS = {"maths-and-headers": "maths_and_headers", "slides": "slides"}


def print_html(source: str) -> bytes:
    """Chromium's PDF of `source`, at the page size of its CSS @page rule."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            context = browser.new_context(java_script_enabled=False, offline=True)
            page = context.new_page()
            page.set_content(source, wait_until="load")
            page.emulate_media(media="print")
            return page.pdf(prefer_css_page_size=True, print_background=True, tagged=False)
        finally:
            browser.close()


def _fix_date(pdf: bytes) -> bytes:
    import pymupdf

    with pymupdf.open(stream=pdf, filetype="pdf") as doc:
        meta = dict(doc.metadata or {})
        meta.update(creationDate=FIXED_DATE, modDate=FIXED_DATE)
        doc.set_metadata(meta)
        return doc.tobytes(garbage=3, deflate=True)


def _tables(source: str) -> list[ET.Element]:
    return [ET.fromstring(t) for t in re.findall(r"<table\b.*?</table>", source, flags=re.DOTALL)]


def _contains(words: list[str], want: list[str]) -> bool:
    """`want` appears as a contiguous run in `words`."""
    n = len(want)
    return bool(n) and any(words[i : i + n] == want for i in range(len(words) - n + 1))


def gold_records(name: str, source: str, pdf: bytes) -> list[dict]:
    """One record per table (per page for a split table), in document order."""
    import pymupdf

    from a11y_stem.eval.tables import tokens

    with pymupdf.open(stream=pdf, filetype="pdf") as doc:
        page_words = [
            [t for w in page.get_text("words", sort=True) for t in tokens(w[4])] for page in doc
        ]
    records = []
    page = 0
    number = 0
    for table in _tables(source):
        is_layout = table.get("role") == "presentation"
        head = [tr for part in table if part.tag == "thead" for tr in part if tr.tag == "tr"]
        body = [tr for tr in table.iter("tr") if tr not in head]
        # Each body row's page: the first page from the current one whose words hold the
        # row's first two cells' words in order.
        row_pages = []
        for row in body:
            want = [t for cell in list(row)[:2] for t in tokens("".join(cell.itertext()))]
            found = next(
                (p for p in range(page, len(page_words)) if _contains(page_words[p], want)), page
            )
            page = found
            row_pages.append(found)
        if not is_layout:
            number += 1
        key = f"authored/{name}/" + ("form" if is_layout else f"T{number}")
        pages = sorted(set(row_pages)) or [page]
        for p in pages:
            rows = head + [r for r, rp in zip(body, row_pages, strict=True) if rp == p]
            html = "<table>" + "".join(ET.tostring(r, encoding="unicode").strip() for r in rows)
            record = {
                "id": key + (f"#p{p}" if len(pages) > 1 else ""),
                "page": p,
                "is_layout": is_layout,
                "html": html + "</table>",
            }
            if len(pages) > 1:
                record["cluster"] = key
            records.append(record)
    return records


def build(name: str) -> None:
    folder = HERE / name
    stem = DOCUMENTS[name]
    source = (folder / f"{stem}.html").read_text(encoding="utf-8")
    pdf = _fix_date(print_html(source))
    (folder / f"{stem}.pdf").write_bytes(pdf)
    lines = [json.dumps(r, ensure_ascii=False, sort_keys=True) for r in gold_records(name, source, pdf)]
    (folder / "gold.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {name}/{stem}.pdf and {name}/gold.jsonl ({len(lines)} gold tables)")


def main(argv: list[str]) -> int:
    for name in argv or list(DOCUMENTS):
        build(name)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
