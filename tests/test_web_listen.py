"""The listening transcript (`web.listen`): what a screen reader says for a converted file,
worked out from fixture HTML (equations, chemistry, tables, figures, a picture kept as an image,
a slide deck, languages), and that LaTeX never reaches it."""

from __future__ import annotations

from pathlib import Path

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
            '<nav aria-label="Contents"><details class="toc-details"><summary>Contents</summary>'
            '<ul><li><a href="#a">Intro</a></li></ul></details></nav>'
            "<main><h1>Motion</h1><h2>Kinematics</h2><p>Speed is <em>distance</em> over time.</p>"
            "<ul><li>one</li><li>two<ul><li>inner</li></ul></li><li>three</li></ul></main>"
            '<footer aria-label="About this version"><p>Tell your instructor.</p></footer>'
        )
    )
    spoken = said(items)
    assert spoken[:2] == ["Page title: Kinematics", "Page language: English"]
    # F-120: the skip link and the bare landmark are said before the lesson, chosen by kind;
    # Play starts at the navigation, the first item with content. F-122: plain role words.
    # O-177: the contents list is navigation, read as its entries, not as a collapsed section.
    assert spoken[2:6] == [
        "Skip link: Skip to main content",
        "Navigation, Contents",
        "List, 1 item",
        "link Intro",
    ]
    assert [i.before for i in items[:5]] == [True, True, True, False, False]
    assert "Main content" in spoken
    assert items[2].kind == "skip-link"
    assert "Navigation, Contents" in spoken
    assert "Heading level 1: Motion" in spoken
    assert "Heading level 2: Kinematics" in spoken
    assert "Speed is distance over time." in spoken
    assert "List, 3 items" in spoken
    assert "List, 1 item, nested" in spoken
    assert spoken.index("one") < spoken.index("two") < spoken.index("inner") < spoken.index("three")
    assert "Footer, About this version" in spoken
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


def test_chemistry_is_spoken_by_the_chemistry_rules():
    water = safe_latex_to_mathml(convert_ce_commands(r"\ce{2 H2 + O2 -> 2 H2O}"), "block")
    items = listen.build_transcript(doc(f"<main>{water}</main>"))
    eq = next(i for i in items if i.kind == "equation")
    assert eq.say == "Equation: 2, H 2 plus O 2 reacts to form 2, H 2 O"


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
    assert "Collapsed section: Long description of figure 2.1" in spoken
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


def test_table_cells_with_maths_are_spoken():
    items = listen.build_transcript(
        doc(
            '<main><table><thead><tr><th scope="col">Law</th></tr></thead><tbody><tr><td>'
            "<math><mi>F</mi><mo>=</mo><mi>m</mi><mi>a</mi></math></td></tr></tbody></table></main>"
        )
    )
    assert "Row 1 of 1: Law: cap F equals m a" in said(items)


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


def test_every_item_has_an_id_from_its_element_that_is_stable_and_unique():
    html = doc(
        '<main><h1 id="h1">Kinematics</h1><p>First</p><p>Second</p><p id="p9">Third</p>'
        "<table><tr><th>a</th></tr><tr><td>1</td></tr><tr><td>2</td></tr></table>"
        '<ul><li>one</li><li>two</li></ul><hr role="doc-pagebreak" aria-label="Page 2" id="pg2">'
        "</main>"
    )
    first = listen.build_transcript(html)
    refs = [i.ref for i in first]
    assert all(refs) and len(set(refs)) == len(refs)
    assert refs == [i.ref for i in listen.build_transcript(html)]
    by_text = {i.text: i.ref for i in first}
    assert by_text["Kinematics"] == "h1" and by_text["Third"] == "p9"
    assert by_text["Page 2"] == "pg2"
    # An element with no id is named by its place, so a new heading elsewhere leaves it be.
    later = listen.build_transcript(html.replace("<h1", "<h2 id='new'>New</h2><h1"))
    assert {i.text: i.ref for i in later}["Second"] == by_text["Second"]


def test_very_long_transcripts_stop_with_a_note():
    body = "".join(f"<p>p{n}</p>" for n in range(listen.MAX_ITEMS + 50))
    items = listen.build_transcript(doc(f"<main>{body}</main>"))
    assert len(items) == listen.MAX_ITEMS + 1
    assert items[-1].kind == "note"


def test_speakable_rule_matches_the_review_screen():
    from readablestem.web.app import _speakable

    for text in ("v equals d over t", r"\frac{d}{t}", "x^2", "a_b", "{x}", "", None, "  "):
        assert _speakable(text) == listen.speakable(text)


def test_a_line_break_is_a_pause_and_a_quotation_ends():
    items = listen.build_transcript(
        doc(
            "<main><p>Line one<br>line two<br><br>three.<br></p>"
            "<blockquote><p>To be.</p></blockquote></main>"
        )
    )
    spoken = said(items)
    assert "Line one, line two, three." in spoken
    assert spoken[-3:] == ["Quote", "To be.", "End quote"]


def test_raised_and_lowered_text_says_which_it_is_except_an_ordinal():
    items = listen.build_transcript(
        doc(
            "<main><p>On the 10<sup>th</sup> it was 19.3 g/cm<sup>3</sup> and SO"
            "<sub>4</sub><sup>2-</sup>; read x<sub>1</sub>y"
            '<sup><a role="doc-noteref" href="#fn1">1</a></sup>.</p></main>'
        )
    )
    # a footnote's mark is a link and says no more than that
    assert (
        "On the 10th it was 19.3 g/cm superscript 3 and SO subscript 4 superscript 2-; "
        "read x subscript 1 end subscript y link 1." in said(items)
    )


def test_superscript_characters_are_said_as_a_raised_run_is():
    """A table cell or a caption has "10⁸" as characters, not a <sup>: it says the same words."""
    items = listen.build_transcript(
        doc(
            '<main><figure><img alt="Counts" src="a.png">'
            "<figcaption>Figure 4: About 10⁸ cells of CP³.</figcaption></figure></main>"
        )
    )
    assert "Figure 4: About 10 superscript 8 cells of CP superscript 3." in said(items)
