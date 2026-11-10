"""Reading HTML and Markdown into the units the invented-content rules judge."""

from __future__ import annotations

import base64
import io

from readablestem.eval.invented import read
from readablestem.eval.invented.text import MATH

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


# ------------------------------------------------------------------ text the tool wrote


def test_the_tools_own_text_is_marked_and_a_label_inside_a_block_is_dropped_from_it():
    out = read.read_html(
        html(
            "<p>Real content of the converted document goes here.</p>"
            "<div class='equation'>"
            "<span class='visually-hidden readablestem'>Equation </span>2.4</div>"
            "<footer class='about readablestem'><h2>About this version</h2>"
            "<p>Made with ReadableSTEM, a tool that assists.</p></footer>"
        )
    )
    assert [(b.text, b.authored) for b in out.blocks] == [
        ("Real content of the converted document goes here.", False),
        ("2.4", False),
        ("Made with ReadableSTEM, a tool that assists.", True),
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
            "<summary class='readablestem'>Long description</summary>"
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


# ------------------------------------------------------------------ maths


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


# ------------------------------------------------------------------ Markdown


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
