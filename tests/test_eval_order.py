"""The reading-order scorer: a page read as labeled is clean, and each kind of fault is named."""

from __future__ import annotations

import pytest

from readablestem.eval import corpus, order

ENTRIES = corpus.load_manifest()
MADE_HERE = [e for e in ENTRIES.values() if e.gold and e.kind != "public" and (e.own)]


def _as_labeled(page: dict) -> list[dict]:
    """The page read exactly as the gold says: one element a block, nothing dropped read."""
    return [
        {"kind": b["role"], "text": b["text"]} for b in page["blocks"] if b["role"] != "dropped"
    ]


def _newsletter() -> tuple[dict, list[dict], list[int]]:
    """A three-column page, its elements as labeled, and where its long paragraphs are."""
    page = corpus.load_gold(ENTRIES["s01_newsletter_3col"])["pages"][0]
    elements = _as_labeled(page)
    long = [i for i, e in enumerate(elements) if len(e["text"].split()) >= 30]
    assert len(long) >= 3
    return page, elements, long


@pytest.mark.parametrize("entry", MADE_HERE, ids=lambda e: e.id)
def test_a_page_read_as_labeled_is_clean(entry: corpus.Entry) -> None:
    gold = corpus.load_gold(entry)
    for row in order.score_document(gold, {p["page"]: _as_labeled(p) for p in gold["pages"]}):
        assert row["clean"] and row["right"], (entry.id, row["page"], row["detail"])
        assert row["broken"] == row["xjoin"] == 0 and row["opairs_ok"] == row["opairs"]


def test_two_blocks_read_in_the_wrong_order_are_an_order_fault() -> None:
    page, elements, long = _newsletter()
    a, b = long[0], long[1]
    elements[a], elements[b] = elements[b], elements[a]
    row = order.score_page(page, elements)
    assert row["opairs_ok"] < row["opairs"] and not row["clean"]
    assert row["broken"] == 0 and row["found"] == row["blocks"]


def test_two_columns_read_line_by_line_across_are_broken_blocks() -> None:
    page, elements, long = _newsletter()
    a, b = long[0], long[1]
    left, right = elements[a]["text"].split(), elements[b]["text"].split()
    across = []
    for at in range(0, max(len(left), len(right)), 7):
        across += left[at : at + 7] + right[at : at + 7]
    elements[a] = {"kind": "body", "text": " ".join(across)}
    del elements[b]
    row = order.score_page(page, elements)
    assert row["broken"] >= 2 and not row["clean"]
    assert row["flow_ok"] < row["flow_tot"]


def test_a_sidebar_joined_to_a_far_paragraph_is_a_cross_join() -> None:
    page, elements, long = _newsletter()
    a, far = long[0], long[-1]
    assert far - a > 1
    elements[a] = {"kind": "body", "text": elements[a]["text"] + " " + elements[far]["text"]}
    del elements[far]
    row = order.score_page(page, elements)
    assert row["xjoin"] == 1 and not row["clean"]


def test_a_running_header_that_is_read_counts_as_leaked() -> None:
    page, elements, _ = _newsletter()
    header = next(b for b in page["blocks"] if b["role"] == "dropped")
    before = order.score_page(page, elements)["leaked"]
    row = order.score_page(page, [{"kind": "p", "text": header["text"]}, *elements])
    assert row["leaked"] == before + 1


def test_a_page_missing_from_the_output_finds_nothing() -> None:
    gold = corpus.load_gold(ENTRIES["s01_newsletter_3col"])
    (row,) = order.score_document(gold, {})
    assert row["found"] == 0 and row["lost"] == row["blocks"] and not row["clean"]


def test_student_html_is_heard_by_page_without_hidden_text() -> None:
    document = """<html><body><nav><p>Skip to content</p></nav><main>
      <h1>Cell structure</h1>
      <p>Cells have <span aria-hidden="true">decorative</span> membranes.</p>
      <ul><li>Organelles<ul><li>Nucleus</li></ul></li></ul>
      <hr role="doc-pagebreak" id="page-2" aria-label="Page 2">
      <table><caption>Sizes</caption><tr><th>Part</th><td>5 nm</td></tr></table>
      <script>var x = 1;</script>
    </main><footer><p>Made with care</p></footer></body></html>"""
    pages = order.elements_from_html(document)
    first = [(e["kind"], " ".join(e["text"].split())) for e in pages[1]]
    assert first == [
        ("h1", "Cell structure"),
        ("p", "Cells have membranes."),
        ("li", "Organelles"),
        ("li", "Nucleus"),
    ]
    assert [(e["kind"], e["text"].split()) for e in pages[2]] == [
        ("table", ["Sizes", "Part", "5", "nm"])
    ]


def test_a_layout_dump_is_heard_with_inline_equations_and_captions_in_place() -> None:
    records = [
        {"id": "e2", "kind": "paragraph", "page": 1, "order": 1, "text": "so ⟦q1⟧ holds"},
        {"id": "q1", "kind": "equation", "page": 1, "order": 2, "text": "E = m c 2"},
        {"id": "e1", "kind": "heading", "page": 1, "order": 0, "text": "Energy"},
        {"id": "f1", "kind": "figure", "page": 2, "order": 0, "text": "", "caption": "Figure 1"},
    ]
    pages = order.elements_from_layout(records)
    assert [" ".join(e["text"].split()) for e in pages[1]] == ["Energy", "so E = m c 2 holds"]
    assert pages[2][0]["text"].split() == ["Figure", "1"]
