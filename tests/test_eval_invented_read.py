"""Reading HTML and Markdown into the units the invented-content rules judge."""

from __future__ import annotations

import base64
import io

import pytest

from a11y_stem.eval.invented import read
from a11y_stem.eval.invented.text import MATH

MML_X = "<math display='inline'><mi>x</mi><mo>=</mo><mn>5</mn></math>"


def png_data_uri(color: str = "red") -> str:
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (6, 4), color).save(buffer, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode()


def html(body: str, head: str = "") -> str:
    return (
        "<!doctype html><html lang='en'><head><title>T</title>"
        f"{head}</head><body>{body}</body></html>"
    )


# ------------------------------------------------------------------ prose


def test_prose_blocks_cut_maths_out_and_skip_scripts_styles_and_head():
    out = read.read_html(
        html(
            f"<p>The line starts at {MML_X}, as well.</p>"
            "<p>Second <em>block</em> here.</p><script>var a = 'not text';</script>"
            "<style>p { color: red }</style>",
            head="<meta name='x' content='y'>",
        )
    )
    assert [b.text for b in out.blocks] == [
        f"The line starts at {MATH}, as well.",
        "Second block here.",
    ]
    assert [e.source for e in out.equations] == ["mathml"]
    assert out.blocks[0].kind == "prose" and not out.blocks[0].authored


def test_list_items_headings_and_table_cells_are_not_prose():
    out = read.read_html(
        html(
            "<h2>Section <b>One</b></h2><ul><li>A list item of words</li></ul>"
            "<table><caption>A table caption</caption><tr><th>Name</th><td>5.0 m</td></tr></table>"
        )
    )
    assert [h.text for h in out.headings] == ["Section One"]
    assert out.headings[0].level == 2
    assert [b.text for b in out.blocks] == ["A list item of words", "A table caption"]
    assert out.blocks[1].kind == "caption"
    assert [(c.text, c.row, c.col) for c in out.cells] == [("Name", 0, 0), ("5.0 m", 0, 1)]


def test_a_figure_caption_is_a_caption_block_not_a_description():
    out = read.read_html(
        html(
            "<figure><img src='x.png' alt='A graph of speed against time.'>"
            "<figcaption>Figure 2.1 Speed against time.</figcaption></figure>"
        )
    )
    assert [(b.text, b.kind) for b in out.blocks] == [("Figure 2.1 Speed against time.", "caption")]
    [alt] = out.descriptions
    assert (alt.source, alt.text, alt.figure) == ("alt", "A graph of speed against time.", 1)
    assert alt.caption == "Figure 2.1 Speed against time."  # the caption came after the image


def test_headings_keep_their_maths_as_tokens_for_matching_and_cut_it_for_numbers():
    out = read.read_html(html(f"<h3>Solving {MML_X} for 12 cases</h3>"))
    [heading] = out.headings
    assert heading.text == "Solving x = 5 for 12 cases"
    assert "x" not in heading.plain and "12" in heading.plain


def test_the_demo_banner_is_not_output():
    out = read.read_html(
        html(
            "<aside aria-label='About this demo'><p>Precomputed demo of a page.</p></aside>"
            "<p>Body text.</p>"
        )
    )
    assert [b.text for b in out.blocks] == ["Body text."]


# ------------------------------------------------------------------ text the tool wrote


def test_the_tools_own_text_is_marked_and_a_label_inside_a_block_is_dropped_from_it():
    out = read.read_html(
        html(
            "<p>Real content of the converted document goes here.</p>"
            "<div class='equation'>"
            "<span class='visually-hidden a11y-stem'>Equation </span>2.4</div>"
            "<footer class='about a11y-stem'><h2>About this version</h2>"
            "<p>Made with a11y-stem, a tool that assists.</p></footer>"
        )
    )
    assert [(b.text, b.authored) for b in out.blocks] == [
        ("Real content of the converted document goes here.", False),
        ("2.4", False),
        ("Made with a11y-stem, a tool that assists.", True),
    ]
    assert [(h.text, h.authored) for h in out.headings] == [("About this version", True)]
    assert out.tool_fragments == 1


def test_placeholder_alt_texts_are_not_figure_descriptions():
    out = read.read_html(
        html(
            "<img src='a.png' alt='Equation not converted'>"
            "<img src='b.png' alt='Figure without a description'>"
            "<img src='c.png' alt='Page 3: handwritten notes, not converted to text.'>"
            "<img src='d.png' alt='A graph of speed against time.'><img src='e.png' alt=''>"
        )
    )
    assert out.placeholders == 3
    assert [d.text for d in out.descriptions] == ["A graph of speed against time."]
    assert out.images == 5


# ------------------------------------------------------------------ figure descriptions


def test_descriptions_from_alt_figure_text_details_svg_and_aria_label():
    uri = png_data_uri()
    out = read.read_html(
        html(
            f"<figure><img src='{uri}' alt='Alt text of the first figure.'>"
            "<figcaption>Figure 1.1 Caption one.</figcaption></figure>"
            "<details class='figure-description'>"
            "<summary class='a11y-stem'>Long description</summary>"
            "<p>A long description of the first figure.</p></details>"
            "<figure><div class='description'><p>Text description of figure two.</p></div>"
            "<figcaption>Figure 1.2 Caption two.</figcaption></figure>"
            "<svg role='img' aria-label='Label of a drawing'><title>Drawing title</title>"
            "<desc>Drawing description.</desc><text>ignored label</text></svg>"
            "<div role='img' aria-label='A diagram described by a label'></div>"
            "<details><summary>Teacher notes</summary>"
            "<p>Not a figure description at all.</p></details>"
        )
    )
    by_source = {d.source: d for d in out.descriptions}
    assert set(by_source) == {"alt", "details", "figure", "svg", "aria"}
    assert by_source["alt"].image is not None and by_source["alt"].image.startswith(b"\x89PNG")
    assert by_source["details"].text == "A long description of the first figure."
    assert (
        by_source["details"].figure == 1
        and by_source["details"].caption == "Figure 1.1 Caption one."
    )
    assert by_source["figure"].text == "Text description of figure two."
    assert by_source["svg"].text == "Label of a drawing Drawing title Drawing description."
    assert by_source["aria"].text == "A diagram described by a label"
    # a <details> that does not follow a figure is prose, summary and all
    assert [b.text for b in out.blocks if "Teacher" in b.text or "Not a figure" in b.text] == [
        "Teacher notes",
        "Not a figure description at all.",
    ]
    assert out.figures == 2


def test_a_details_after_other_content_is_not_a_figure_description():
    out = read.read_html(
        html(
            "<figure><img src='x.png' alt='A graph.'></figure><p>Text between.</p>"
            "<details><p>Folded text.</p></details>"
        )
    )
    assert [d.source for d in out.descriptions] == ["alt"]
    assert "Folded text." in [b.text for b in out.blocks]


def test_the_first_block_after_a_figure_is_marked():
    out = read.read_html(
        html(
            "<p>Before.</p><figure><img src='x.png' alt='A graph.'></figure>"
            "<p>After one.</p><p>After two.</p>"
        )
    )
    assert [(b.text, b.after_figure) for b in out.blocks] == [
        ("Before.", False),
        ("After one.", True),
        ("After two.", False),
    ]


def test_a_data_table_inside_a_long_description_is_marked():
    out = read.read_html(
        html(
            "<figure><img src='x.png' alt='A graph.'></figure>"
            "<details><summary>Data</summary><table><tr><td>3.5</td></tr></table></details>"
            "<table><tr><td>7.0</td></tr></table>"
        )
    )
    assert [(c.text, c.in_description) for c in out.cells] == [("3.5", True), ("7.0", False)]


# ------------------------------------------------------------------ maths


def test_tex_spans_mathjax_would_typeset_are_found_in_reading_order():
    out = read.read_html(
        html(
            r"<p>Speed is \(v = d/t\) and so on.</p>"
            r"<p>\[ F = ma \]</p><p>$$ x^2 + y^2 = r^2 $$</p>"
            r"<p>\begin{equation} E = mc^2 \end{equation}</p>"
        )
    )
    assert [(e.source, e.display, e.raw) for e in out.equations] == [
        ("tex", "inline", "v = d/t"),
        ("tex", "block", "F = ma"),
        ("tex", "block", "x^2 + y^2 = r^2"),
        ("tex", "block", r"\begin{equation} E = mc^2 \end{equation}"),
    ]
    assert all(e.mathml for e in out.equations)
    assert out.blocks[0].text == f"Speed is {MATH} and so on."


def test_dollar_signs_are_maths_only_when_the_pages_own_config_says_so():
    page = "<p>It costs $5 and then $10 more, so $x + 1$ here.</p>"
    assert read.read_html(html(page)).equations == []
    config = (
        "<script>window.MathJax = {\n tex: { inlineMath: [['$', '$'], ['\\\\(', '\\\\)']] }\n};"
        "</script>"
    )
    on = read.read_html(html(page, head=config))
    assert [e.raw for e in on.equations] == ["x + 1"]  # money is not maths (pandoc's rule)


def test_macros_of_the_page_are_expanded_before_conversion():
    config = "<script>MathJax = { tex: { macros: { vv: ['\\\\mathbf{#1}', 1] } } };</script>"
    out = read.read_html(html(r"<p>\(\vv{v} = 3\)</p>", head=config))
    [equation] = out.equations
    assert equation.mathml and "\N{MATHEMATICAL BOLD SMALL V}" in equation.mathml  # \mathbf{v}


def test_tex_that_will_not_convert_is_kept_unchecked():
    out = read.read_html(html(r"<p>Bad \(\undefinedcommand{3} \frac{\)</p>"))
    [equation] = out.equations
    assert equation.mathml is None
    assert equation.tokens  # the words and numbers it still holds


def test_mathml_with_a_tex_annotation_keeps_its_tokens_once():
    mathml = (
        "<math><semantics><mrow><mi>v</mi><mo>=</mo><mn>3</mn></mrow>"
        "<annotation encoding='application/x-tex'>v=3</annotation></semantics></math>"
    )
    out = read.read_html(html(f"<p>{mathml}</p>"))
    assert out.equations[0].source == "mathml"
    assert out.equations[0].display == "inline"


def test_display_block_is_read_from_the_attribute():
    out = read.read_html(html("<div><math display='block'><mi>v</mi></math></div>"))
    assert out.equations[0].display == "block"


# ------------------------------------------------------------------ Markdown


def test_markdown_headings_paragraphs_lists_and_emphasis():
    out = read.read_markdown(
        "# Title\n\nA paragraph with **bold** and *italic* words\nthat wraps a line.\n\n"
        "Setext heading\n--------------\n\n- first item here\n- second item here\n\n"
        "1. numbered item\n\n```\ncode is skipped\n```\n\n---\n\n[a link](http://x.test) text."
    )
    assert [(h.level, h.text) for h in out.headings] == [(1, "Title"), (2, "Setext heading")]
    assert [b.text for b in out.blocks] == [
        "A paragraph with bold and italic words that wraps a line.",
        "first item here",
        "second item here",
        "numbered item",
        "a link text.",
    ]


def test_markdown_maths_in_all_its_delimiters_and_money_is_not_maths():
    out = read.read_markdown(
        "Speed $v = d/t$ and \\(a = b\\) cost $0.50 and $1 each.\n\n"
        "$$\nx^2 + y^2 = r^2\n$$\n\n\\[ E = mc^2 \\]\n"
    )
    assert [e.raw for e in out.equations] == ["v = d/t", "a = b", "x^2 + y^2 = r^2", "E = mc^2"]
    assert [e.display for e in out.equations] == ["inline", "inline", "block", "block"]
    assert out.blocks[0].text.startswith(f"Speed {MATH} and {MATH} cost $0.50 and $1 each.")


def test_markdown_tables_images_and_html_tables_with_maths():
    out = read.read_markdown(
        "| Quantity | Value |\n|---|---|\n| speed | 5.0 m/s |\n\n"
        "![A graph of speed against time](fig.png) and <img src='a.png' alt='Second graph'>\n\n"
        "<table><tr><td>7.5</td><td><math><mi>v</mi></math></td></tr></table>\n\n"
        "Text after the table."
    )
    assert [c.text for c in out.cells] == ["Quantity", "Value", "speed", "5.0 m/s", "7.5", "v"]
    assert [d.text for d in out.descriptions] == ["A graph of speed against time", "Second graph"]
    assert out.images == 2
    assert [e.source for e in out.equations] == ["mathml"]
    assert out.blocks[-1].text == "Text after the table."
    assert out.cells[2].table != out.cells[4].table  # the pipe table and the HTML table


# ------------------------------------------------------------------ entry point


def test_read_output_guesses_the_format_and_refuses_an_unknown_one():
    assert read.read_output("<p>Hello there</p>").format == "html"
    assert read.read_output("# Hello\n\nthere").format == "markdown"
    assert read.read_output("plain", name="x.md").format == "markdown"
    assert read.read_output("<p>x</p>", name="x.htm").format == "html"
    with pytest.raises(ValueError, match="unknown output format"):
        read.read_output("x", "docx")


def test_output_counts_and_hash_are_stable():
    first = read.read_html(html("<p>One two three.</p>"))
    again = read.read_html(html("<p>One two three.</p>"))
    assert first.sha256 == again.sha256 and len(first.sha256) == 64
    assert first.counts()["blocks"] == 1
