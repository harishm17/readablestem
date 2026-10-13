"""Markdown reading shared by PyMuPDF4LLM and MinerU's fallback. Invented text only."""

from __future__ import annotations

from parsers_bench.adapters.builder import Builder, table_grid_from_html


def read(markdown: str) -> Builder:
    builder = Builder("test", "sec")
    builder.markdown(markdown)
    return builder


def test_headings_keep_their_level_and_lose_emphasis_markers() -> None:
    doc = read("# Title\n\n## **Bold Section**\n\n### Third\n").doc
    assert [(h.level, h.text) for h in doc.headings] == [
        (1, "Title"),
        (2, "Bold Section"),
        (3, "Third"),
    ]


def test_paragraph_text_has_no_maths_and_inline_maths_is_an_inline_equation() -> None:
    doc = read("The speed is $v = d / t$ and nothing else.\n").doc
    assert [b.text for b in doc.blocks] == ["The speed is and nothing else."]
    assert [(e.display, e.latex) for e in doc.equations] == [("inline", "v = d / t")]


def test_display_maths_is_a_block_equation() -> None:
    doc = read("Before.\n\n$$\nx = \\frac{a}{b}\n$$\n\nAfter.\n").doc
    assert [(e.display, e.latex) for e in doc.equations] == [("block", "x = \\frac{a}{b}")]
    assert [b.text for b in doc.blocks] == ["Before.", "After."]


def test_reading_order_is_one_counter_over_all_kinds() -> None:
    doc = read("# H\n\nText $a$ more.\n\n$$\nb\n$$\n").doc
    orders = [h.order for h in doc.headings] + [b.order for b in doc.blocks]
    orders += [e.order for e in doc.equations]
    assert sorted(orders) == list(range(len(orders)))
    assert doc.headings[0].order < doc.blocks[0].order < doc.equations[0].order


def test_list_items_are_list_blocks() -> None:
    doc = read("- one item here\n- another item\n\n1. first\n2. second\n").doc
    assert [b.kind for b in doc.blocks] == ["list"] * 4


def test_raw_html_is_dropped_but_its_text_kept() -> None:
    # PyMuPDF4LLM writes fraction bars as <u> underline.
    doc = read("speed <u>distance</u> over time\n\n<u>lone</u> block\n").doc
    texts = [b.text for b in doc.blocks]
    assert texts[0] == "speed distance over time"
    assert texts[1] == "lone block"


def test_markdown_images_become_figures_with_their_alt_text() -> None:
    doc = read("![A line graph](img.png)\n\n![](other.png)\n").doc
    assert [(f.image_kept, f.text_alternative) for f in doc.figures] == [
        (True, "A line graph"),
        (True, None),
    ]


def test_pipe_tables_become_grids() -> None:
    doc = read("| a | b |\n| - | - |\n| 1 | 2 |\n").doc
    assert [t.grid for t in doc.tables] == [[["a", "b"], ["1", "2"]]]


def test_empty_and_whitespace_items_are_not_added() -> None:
    doc = read("# \n\n   \n").doc
    assert not doc.headings
    assert not doc.blocks


def test_html_table_grid() -> None:
    html = "<table><tr><th>x</th><th>y</th></tr><tr><td>1</td><td>2 3</td></tr></table>"
    assert table_grid_from_html(html) == [["x", "y"], ["1", "2 3"]]


def test_nested_table_cells_are_not_split_into_extra_rows() -> None:
    html = "<table><tr><td>a<table><tr><td>inner</td></tr></table></td><td>b</td></tr></table>"
    grid = table_grid_from_html(html)
    assert len(grid) == 1
    assert len(grid[0]) == 2
    assert grid[0][1] == "b"
