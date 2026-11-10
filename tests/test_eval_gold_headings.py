"""Gold heading records: what the headings eval is scored against."""

from __future__ import annotations

from pathlib import Path

from readablestem.gold.openstax import extract_elements

REPO = Path(__file__).resolve().parents[1]
PAGE = (Path(__file__).parent / "fixtures" / "eval" / "page.xhtml").read_text(encoding="utf-8")
CONTENT_URL = "https://openstax.org/apps/archive/20990101.000000/contents/b@v1:p.json"


def headings(xhtml: str = PAGE) -> list[dict]:
    return [r for r in extract_elements(xhtml, CONTENT_URL) if r["kind"] == "heading"]


def test_headings_keep_level_text_id_and_context_in_document_order():
    got = [(h["index_on_page"], h["level"], h["text"], h["context"]) for h in headings()]
    assert got == [
        (0, 2, "9.1 Rolling Balls", None),
        (1, 3, "Section Learning Objectives", "learning-objectives"),
        (2, 3, "Measuring Speed", None),
        (3, 4, "Worked Example", None),  # inline markup flattened
        (4, 4, "Inside a Box", "worked-example"),
    ]
    assert headings()[0]["element_id"] == "page_test_title"
