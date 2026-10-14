"""Gold heading records: what the headings eval is scored against."""

from __future__ import annotations

import json
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


def test_headings_with_no_visible_text_are_skipped():
    # The fixture has an h4 holding only a space; a reader never sees it.
    assert all(h["text"] for h in headings())
    assert len(headings()) == 5


def test_headings_come_after_every_other_kind():
    # Appending keeps every pre-existing record (and committed JSONL line) unchanged.
    kinds = [r["kind"] for r in extract_elements(PAGE, CONTENT_URL)]
    first_heading = kinds.index("heading")
    assert set(kinds[first_heading:]) == {"heading"}
    assert "equation" in kinds[:first_heading] and "figure" in kinds[:first_heading]


def test_committed_gold_sample_has_headings_for_every_page():
    manifest = json.loads((REPO / "data" / "gold" / "manifest.json").read_text())
    assert all(f["counts"].get("heading", 0) > 0 for f in manifest["files"])
    assert manifest["totals"]["equation"] == 30  # unchanged by the heading change
    for entry in manifest["files"]:
        lines = (REPO / "data" / "gold" / entry["path"]).read_text().splitlines()
        records = [json.loads(line) for line in lines]
        page_headings = [r for r in records if r["kind"] == "heading"]
        assert page_headings[0]["level"] == 2  # OpenStax section titles are h2
        assert page_headings[0]["id"].endswith("/hd-000")
        assert all(r["text"] for r in page_headings)
