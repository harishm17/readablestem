"""The Docling adapter on a hand-made DoclingDocument (schema 1.x). Invented text only."""

from __future__ import annotations

from typing import Any

from parsers_bench.adapters import docling


def ref(kind: str, index: int) -> dict[str, str]:
    return {"$ref": f"#/{kind}/{index}"}


def text(label: str, value: str, parent: str = "#/body", **extra: Any) -> dict[str, Any]:
    return {"label": label, "text": value, "orig": value, "parent": {"$ref": parent}, **extra}


def sample() -> dict[str, Any]:
    texts = [
        text("section_header", "Intro", level=1),  # 0
        text("text", "Plain paragraph of the page."),  # 1
        text("formula", "", orig="v = d t"),  # 2: not decoded
        text("formula", "v = \\frac{d}{t}", orig="v = d/t"),  # 3: decoded, display
        text("text", "see", parent="#/groups/0"),  # 4
        text("formula", "x^2", orig="x2", parent="#/groups/0"),  # 5: inline
        text("list_item", "first item", parent="#/groups/1"),  # 6
        text("caption", "Figure 1 A picture.", parent="#/pictures/0"),  # 7
        text("text", "2400", parent="#/pictures/0"),  # 8: OCR inside the figure
        text("page_footer", "Access for free"),  # 9
        text("title", "Document Title"),  # 10
        text("section_header", "Sub", level=2),  # 11
    ]
    return {
        "schema_name": "DoclingDocument",
        "version": "1.10.0",
        "body": {
            "children": [
                ref("texts", 10),
                ref("texts", 0),
                ref("texts", 1),
                ref("texts", 2),
                ref("texts", 3),
                ref("groups", 0),
                ref("groups", 1),
                ref("pictures", 0),
                ref("tables", 0),
                ref("texts", 11),
                ref("texts", 9),
            ]
        },
        "groups": [
            {"label": "inline", "children": [ref("texts", 4), ref("texts", 5)]},
            {"label": "list", "children": [ref("texts", 6)]},
        ],
        "texts": texts,
        "pictures": [
            {
                "label": "picture",
                "image": {"uri": "data:image/png;base64,AAAA"},
                "captions": [ref("texts", 7)],
                "children": [ref("texts", 7), ref("texts", 8)],
                "annotations": [],
            }
        ],
        "tables": [
            {
                "label": "table",
                "captions": [],
                "data": {"grid": [[{"text": "a"}, {"text": "b"}], [{"text": "1"}, {"text": "2"}]]},
            }
        ],
    }


def test_headings_use_docling_html_levels() -> None:
    doc = docling.adapt(sample(), section="s")
    assert [(h.level, h.text) for h in doc.headings] == [
        (1, "Document Title"),
        (2, "Intro"),
        (3, "Sub"),
    ]


def test_undecoded_formula_has_no_latex_but_keeps_the_pdf_text() -> None:
    doc = docling.adapt(sample(), section="s")
    undecoded = doc.equations[0]
    assert undecoded.latex is None
    assert undecoded.raw == "v = d t"
    assert undecoded.display == "block"


def test_decoded_formula_is_latex_and_inline_comes_from_the_group() -> None:
    doc = docling.adapt(sample(), section="s")
    assert doc.equations[1].latex == "v = \\frac{d}{t}"
    assert doc.equations[1].display == "block"
    assert doc.equations[2].latex == "x^2"
    assert doc.equations[2].display == "inline"


def test_figure_text_is_kept_apart_from_prose_and_the_caption_is_a_caption() -> None:
    doc = docling.adapt(sample(), section="s")
    kinds = {b.text: b.kind for b in doc.blocks}
    assert kinds["2400"] == "figure_text"
    assert kinds["Figure 1 A picture."] == "caption"
    assert kinds["Plain paragraph of the page."] == "para"
    assert kinds["first item"] == "list"
    assert kinds["Access for free"] == "furniture"
    assert sum(b.text == "Figure 1 A picture." for b in doc.blocks) == 1


def test_picture_is_a_figure_with_image_and_no_text_alternative() -> None:
    doc = docling.adapt(sample(), section="s")
    [figure] = doc.figures
    assert figure.image_kept is True
    assert figure.text_alternative is None
    assert figure.caption == "Figure 1 A picture."


def test_picture_description_annotation_is_the_text_alternative() -> None:
    data = sample()
    data["pictures"][0]["annotations"] = [{"kind": "description", "text": "A rising line."}]
    [figure] = docling.adapt(data, section="s").figures
    assert figure.text_alternative == "A rising line."


def test_picture_without_embedded_image_is_not_kept() -> None:
    data = sample()
    del data["pictures"][0]["image"]
    [figure] = docling.adapt(data, section="s").figures
    assert figure.image_kept is False


def test_table_grid() -> None:
    doc = docling.adapt(sample(), section="s")
    assert [t.grid for t in doc.tables] == [[["a", "b"], ["1", "2"]]]


def test_every_item_is_read_once_in_body_order() -> None:
    doc = docling.adapt(sample(), section="s")
    orders = sorted(
        [h.order for h in doc.headings]
        + [b.order for b in doc.blocks]
        + [e.order for e in doc.equations]
        + [f.order for f in doc.figures]
        + [t.order for t in doc.tables]
    )
    assert orders == list(range(len(orders)))
    assert doc.headings[0].order < doc.headings[1].order < doc.headings[2].order
