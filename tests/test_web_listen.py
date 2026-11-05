"""The listening transcript (`web.listen`): what a screen reader says for a converted file,
worked out from fixture HTML (equations, chemistry, tables, figures, a picture kept as an image,
a slide deck, languages), and that LaTeX never reaches it."""

from __future__ import annotations

import json
import re
from pathlib import Path

from readablestem.core.types import DocumentResult
from readablestem.pipeline.chem import convert_ce_commands
from readablestem.pipeline.validate import safe_latex_to_mathml
from readablestem.web import listen

STATIC = Path(listen.__file__).parent / "static"


def doc(body: str, lang: str = "en", title: str = "Kinematics") -> str:
    return (
        f'<!doctype html><html lang="{lang}"><head><meta charset="utf-8"><title>{title}</title>'
        f"<style>.x {{ color: red }}</style></head><body>{body}</body></html>"
    )


def said(items: list[listen.Item]) -> list[str]:
    return [i.say for i in items]


def test_page_title_landmarks_headings_paragraphs_and_lists():
    items = listen.build_transcript(
        doc(
            '<a class="skip-link" href="#main">Skip to main content</a>'
            '<nav aria-label="Contents"><ul><li><a href="#a">Intro</a></li></ul></nav>'
            "<main><h1>Motion</h1><h2>Kinematics</h2><p>Speed is <em>distance</em> over time.</p>"
            "<ul><li>one</li><li>two<ul><li>inner</li></ul></li><li>three</li></ul></main>"
            '<footer aria-label="About this version"><p>Tell your instructor.</p></footer>'
        )
    )
    spoken = said(items)
    assert spoken[:2] == ["Page title: Kinematics", "Page language: English"]
    assert "link Skip to main content" in spoken
    assert "Navigation landmark, Contents" in spoken
    assert "Main landmark" in spoken
    assert "Heading level 1: Motion" in spoken
    assert "Heading level 2: Kinematics" in spoken
    assert "Speed is distance over time." in spoken
    assert "List, 3 items" in spoken
    assert "List, 1 item, nested" in spoken
    assert spoken.index("one") < spoken.index("two") < spoken.index("inner") < spoken.index("three")
    assert "Content information landmark, About this version" in spoken
    # the style sheet is never read
    assert not any("color" in s for s in spoken)
    assert [i.kind for i in items if i.kind == "heading"] == ["heading", "heading"]
    assert items[spoken.index("List, 3 items")].role == "List"


def test_links_are_announced_and_hidden_things_are_skipped():
    items = listen.build_transcript(
        doc(
            '<main><p>See <a href="#f">Figure 2.1</a> and <span aria-hidden="true">HIDDEN</span>'
            '<span class="visually-hidden">Equation </span>now.</p><p hidden>NOPE</p></main>'
        )
    )
    spoken = said(items)
    assert "See link Figure 2.1 and Equation now." in spoken
    assert not any("HIDDEN" in s or "NOPE" in s for s in spoken)
    assert items[spoken.index("See link Figure 2.1 and Equation now.")].kinds == (
        "paragraph",
        "link",
    )


def test_block_equation_is_spoken_with_its_label_never_as_latex():
    mathml = (
        '<math display="block"><mrow><mi>v</mi><mo>=</mo><mfrac><mi>d</mi><mi>t</mi></mfrac>'
        "</mrow></math>"
    )
    items = listen.build_transcript(
        doc(
            f'<main><div class="equation equation-labelled" id="p1-e1">{mathml}'
            '<span class="equation-label"><span class="visually-hidden">Equation </span>'
            "2.4</span></div></main>"
        )
    )
    eq = next(i for i in items if i.kind == "equation")
    assert eq.say == "Equation 2.4: v equals d over t"
    assert eq.role == "Equation 2.4"


def test_stored_reading_wins_but_a_latex_reading_is_never_used():
    mathml = '<math id="p1-e1"><mi>x</mi><mo>=</mo><mn>2</mn></math>'
    html = doc(f"<main><p>Solve {mathml} now.</p></main>")
    stored = listen.build_transcript(html, {"p1-e1": "x equals two, said by the course"})
    assert "Solve x equals two, said by the course now." in said(stored)
    latex = listen.build_transcript(html, {"p1-e1": r"\frac{x}{2}"})
    assert "Solve x equals 2 now." in said(latex)  # the MathML is read instead


def test_alttext_is_used_when_the_file_carries_it_and_ignored_when_it_is_latex():
    good = '<math alttext="v equals d over t" display="block"><mi>q</mi></math>'
    bad = r'<math alttext="\frac{d}{t}" display="block"><mi>q</mi></math>'
    items = listen.build_transcript(doc(f"<main>{good}{bad}</main>"))
    eqs = [i.say for i in items if i.kind == "equation"]
    assert eqs == ["Equation: v equals d over t", "Equation: q"]


def test_unreadable_math_is_said_honestly_not_skipped():
    items = listen.build_transcript(doc("<main><math display='block'><mfoo/></math></main>"))
    eq = next(i for i in items if i.kind == "equation")
    assert eq.say.startswith("Equation: ")
    assert "\\" not in eq.say


def test_inline_equation_marks_the_paragraph_for_next_equation():
    items = listen.build_transcript(
        doc("<main><p>Let <math><msup><mi>x</mi><mn>2</mn></msup></math> grow.</p></main>")
    )
    para = next(i for i in items if i.kind == "paragraph")
    assert para.say == "Let x squared grow."
    assert "equation" in para.kinds


def test_inline_equation_in_a_list_item_or_cell_stays_in_its_sentence():
    """The maths sits directly in the <li> or <td>, not in a <p>: the sentence is still one item."""
    items = listen.build_transcript(
        doc(
            "<main><ol><li>Remember in calculating change "
            "<math display='inline'><mi>Δ</mi></math> we always use final minus initial.</li></ol>"
            "<table><tr><td>Speed <math display='inline'><mi>v</mi></math> in m/s</td></tr></table>"
            "</main>"
        )
    )
    spoken = said(items)
    assert any(
        s.startswith("Remember in calculating change") and s.endswith("final minus initial.")
        for s in spoken
    ), spoken
    assert not any(s.strip() in ("capital delta", "Δ") for s in spoken), spoken
    cell = [s for s in spoken if "in m/s" in s]
    assert cell and "Speed" in cell[0], spoken
    item = next(i for i in items if i.say.startswith("Remember"))
    assert "equation" in item.kinds


def test_display_equation_inside_a_list_item_is_still_its_own_equation():
    items = listen.build_transcript(
        doc("<main><ul><li>Then<math display='block'><mi>x</mi></math></li></ul></main>")
    )
    assert any(i.kind == "equation" for i in items), [(i.kind, i.say) for i in items]


def test_chemistry_is_spoken_by_the_chemistry_rules():
    water = safe_latex_to_mathml(convert_ce_commands(r"\ce{2 H2 + O2 -> 2 H2O}"), "block")
    items = listen.build_transcript(doc(f"<main>{water}</main>"))
    eq = next(i for i in items if i.kind == "equation")
    assert eq.say == "Equation: 2 H 2 plus O 2 reacts to form 2 H 2 O"


def test_figure_alt_caption_and_long_description():
    items = listen.build_transcript(
        doc(
            '<main><figure id="f1"><img src="data:image/png;base64,AA" alt="A rising line.">'
            "<figcaption>Figure 2.1 Position.</figcaption></figure>"
            '<details class="figure-description"><summary>Long description of figure 2.1'
            "</summary><p>The line starts at zero.</p>"
            '<div class="table-scroll" role="group"><table><caption>Data</caption>'
            '<thead><tr><th scope="col">t</th><th scope="col">d</th></tr></thead>'
            "<tbody><tr><td>0</td><td>1</td></tr></tbody></table></div></details></main>"
        )
    )
    spoken = said(items)
    assert "Figure: A rising line." in spoken
    assert "Figure 2.1 Position." in spoken
    assert "Expandable section, collapsed: Long description of figure 2.1" in spoken
    assert "The line starts at zero." in spoken
    assert next(i for i in items if i.text == "The line starts at zero.").role == (
        "Long description"
    )
    assert any(s.startswith("Table, Data, 2 rows, 2 columns") for s in spoken)
    # a decorative picture is skipped
    quiet = listen.build_transcript(doc('<main><img alt="" src="x"></main>'))
    assert not any(i.kind == "figure" for i in quiet)


def test_a_picture_kept_as_an_image_is_announced_as_not_converted():
    html = doc(
        '<main><div class="flagged" data-status="needs_review" data-reason="kept_image">'
        '<figure><img src="data:image/png;base64,AA" alt="Page 3 of the PDF, not converted.">'
        '</figure><p class="flag-note readablestem" role="note">'
        '<span class="flag-label">Not converted.</span> The original page is kept as a picture.'
        "</p></div>"
        '<p>Then <span class="flagged-inline" data-status="needs_review">'
        '<img src="x" alt="Equation from the PDF: v = d / t">'
        '<span class="flag-label" aria-hidden="true">(equation not converted)</span></span>'
        " follows.</p></main>"
    )
    items = listen.build_transcript(html)
    kept = next(i for i in items if i.kind == "not-converted")
    assert kept.say == "Image, not converted: Page 3 of the PDF, not converted."
    note = next(i for i in items if i.kind == "note")
    assert note.say == "Note: Not converted. The original page is kept as a picture."
    inline = next(i for i in items if i.text.startswith("Then"))
    assert "not converted" in inline.say
    assert "not-converted" in inline.kinds


TABLE = (
    '<main><div class="table-scroll" role="group" tabindex="0"><table><caption>Table 1 Speeds'
    '</caption><thead><tr><td></td><th scope="col">Time (s)</th><th scope="col">Distance (m)'
    "</th></tr></thead><tbody>"
    + "".join(
        f'<tr><th scope="row">Run {n}</th><td>{n}</td><td>{n * 10}</td></tr>' for n in range(1, 13)
    )
    + "</tbody></table></div></main>"
)


def test_table_is_announced_with_size_headers_and_rows_with_their_headers():
    items = listen.build_transcript(doc(TABLE))
    spoken = said(items)
    head = next(s for s in spoken if s.startswith("Table, "))
    assert head == (
        "Table, Table 1 Speeds, 13 rows, 3 columns. Column headers: Time (s), Distance (m)"
    )
    assert "Row 1 of 12: Run 1; Time (s): 1; Distance (m): 10" in spoken
    assert "Row 8 of 12: Run 8; Time (s): 8; Distance (m): 80" in spoken
    assert not any(s.startswith("Row 9 of") for s in spoken)
    assert "…4 more rows" in spoken


def test_table_cap_can_be_changed_and_a_table_without_headers_still_reads():
    items = listen.build_transcript(
        doc(
            "<main><table><tr><td>a</td><td>b</td></tr><tr><td>c</td><td>d</td></tr></table></main>"
        ),
        max_rows=1,
    )
    spoken = said(items)
    assert "Table, 2 rows, 2 columns" in spoken
    assert "Row 1 of 2: a; b" in spoken
    assert "…1 more row" in spoken


def test_table_cells_with_maths_are_spoken():
    items = listen.build_transcript(
        doc(
            '<main><table><thead><tr><th scope="col">Law</th></tr></thead><tbody><tr><td>'
            "<math><mi>F</mi><mo>=</mo><mi>m</mi><mi>a</mi></math></td></tr></tbody></table></main>"
        )
    )
    assert "Row 1 of 1: Law: capital F equals m a" in said(items)


def test_a_slide_deck_reads_frame_titles_as_headings_with_their_step():
    items = listen.build_transcript(
        doc(
            "<main><h1>Newton's laws</h1><h2>Second law (step 1 of 2)</h2><p>Force.</p>"
            "<h2>Second law (step 2 of 2)</h2><ul><li>F = ma</li></ul></main>",
            title="Lecture 4",
        )
    )
    heads = [i.say for i in items if i.kind == "heading"]
    assert heads == [
        "Heading level 1: Newton's laws",
        "Heading level 2: Second law (step 1 of 2)",
        "Heading level 2: Second law (step 2 of 2)",
    ]


def test_language_changes_are_announced_and_ended():
    items = listen.build_transcript(
        doc(
            '<main><p>He said <span lang="fr">bonjour tout le monde</span> twice.</p>'
            '<div lang="de"><p>Guten Tag.</p></div><p>Back.</p></main>'
        )
    )
    spoken = said(items)
    assert (
        "He said Language changes to French: bonjour tout le monde. Language changes back "
        "to English. twice." in spoken
    )
    start = spoken.index("Language changes to German")
    assert spoken[start : start + 3] == [
        "Language changes to German",
        "Guten Tag.",
        "Language changes back to English",
    ]


def test_page_breaks_definitions_code_and_ordered_lists():
    items = listen.build_transcript(
        doc(
            '<main><hr role="doc-pagebreak" aria-label="Page 4">'
            "<dl><dt>Slope</dt><dd>Rise over run."
            "</dd></dl><pre>x = 1</pre><ol><li>a</li><li>b</li></ol></main>"
        )
    )
    spoken = said(items)
    assert "Page break: Page 4" in spoken
    assert "Term: Slope" in spoken and "Definition: Rise over run." in spoken
    assert "Code: x = 1" in spoken
    assert "List item 2 of 2" not in spoken  # list items say their text, shown with the role
    assert next(i for i in items if i.text == "b" or i.say == "b").role == "List item 2 of 2"


def test_very_long_transcripts_stop_with_a_note():
    body = "".join(f"<p>p{n}</p>" for n in range(listen.MAX_ITEMS + 50))
    items = listen.build_transcript(doc(f"<main>{body}</main>"))
    assert len(items) == listen.MAX_ITEMS + 1
    assert items[-1].kind == "note"


def test_demo_document_has_no_latex_and_speaks_its_equations():
    html = (STATIC / "demo" / "index.html").read_text(encoding="utf-8")
    report = json.loads((STATIC / "demo" / "report.json").read_text(encoding="utf-8"))
    speeches = listen.stored_speeches(DocumentResult.model_validate(report["result"]))
    items = listen.build_transcript(html, speeches)
    equations = [i for i in items if i.kind == "equation"]
    assert len(equations) >= 6
    assert "Equation 2.4: capital delta bold d over capital delta t" in said(items)
    for item in items:
        assert not re.search(r"[\\{}^_]", item.say) or item.kind in {"paragraph", "link"}, item
    for item in equations:
        assert not re.search(r"[\\{}^_]", item.say), item
    assert any(i.kind == "heading" for i in items)
    assert any(i.kind == "figure" for i in items)


def test_cache_is_per_job_and_result_version():
    listen.CACHE.clear()
    html = doc("<main><p>one</p></main>")
    first = listen.transcript_for("j" * 32, 1, html)
    assert listen.transcript_for("j" * 32, 1, html) is first
    assert listen.transcript_for("j" * 32, 2, html) is not first
    changed = doc("<main><p>two</p></main>")
    assert said(listen.transcript_for("j" * 32, 1, changed))[-1] == "two"


def test_speakable_rule_matches_the_review_screen():
    from readablestem.web.app import _speakable

    for text in ("v equals d over t", r"\frac{d}{t}", "x^2", "a_b", "{x}", "", None, "  "):
        assert _speakable(text) == listen.speakable(text)
