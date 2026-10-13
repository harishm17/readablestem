"""The tool's own English words in a document in another language (integration of the
text work's language detection with the assembler): every element the assembler wrote
(`AUTHORED_CLASS`: flag notes, labels, the student note) says `lang="en"`, so a screen
reader switches voice for them (WCAG 3.1.2). English documents are unchanged byte for
byte, so the golden HTML stays as it is."""

from __future__ import annotations

import re

from a11y_stem.core.types import Element, Fix, Kind, Status
from a11y_stem.pipeline.assemble import AUTHORED_CLASS, render_html
from a11y_stem.pipeline.html_checks import static_checks

ELEMENTS = [
    Element(id="p1-e0", kind=Kind.HEADING, page=0, bbox=(0, 0, 1, 1), order=0,
            text="Cinématique", heading_level=1),
    Element(id="p1-e1", kind=Kind.PARAGRAPH, page=0, bbox=(0, 0, 1, 1), order=1,
            text="La vitesse moyenne est le déplacement divisé par la durée."),
    Element(id="p1-e2", kind=Kind.EQUATION, page=0, bbox=(0, 0, 1, 1), order=2,
            text="v = d/t", display="block"),
]  # fmt: skip
FIXES = [
    Fix(element_id="p1-e2", kind=Kind.EQUATION, status=Status.FAILED, reason="model_refused",
        review="required"),
]  # fmt: skip


def render(lang: str) -> str:
    return render_html(
        title="Cinématique", lang=lang, elements=ELEMENTS, fixes=FIXES, crops={},
        notice="Offline test run.",
    )  # fmt: skip


def authored_tags(html: str) -> list[str]:
    return re.findall(rf'<[a-z][a-z0-9]*[^<>]*class="[^"]*\b{AUTHORED_CLASS}\b[^"]*"[^<>]*>', html)


def test_the_tools_words_are_marked_english_in_a_french_document():
    html = render("fr")
    assert html.startswith('<!doctype html>\n<html lang="fr">')
    tags = authored_tags(html)
    kinds = {re.search(r'class="([^"]*)"', t).group(1) for t in tags}  # type: ignore[union-attr]
    assert {"notice a11y-stem", "flag-note a11y-stem", "about a11y-stem"} <= kinds
    assert all(' lang="en"' in tag for tag in tags)
    # The document's own text is not marked.
    assert '<p id="p1-e1">La vitesse moyenne' in html
    assert all(c.passed for c in static_checks(html) if c.name == "html.lang_tags")


def test_an_english_document_is_unchanged():
    for lang in ("en", "en-GB"):
        body = render(lang).split("</head>", 1)[1]
        assert ' lang="en"' not in body


def test_the_tools_words_in_the_student_file_speak_to_students():
    """The flag notes are read by students: what is missing and whom to ask, never the
    reviewer's to-do ("Review it before publishing", UX review MF7)."""
    elements = [
        *ELEMENTS,
        Element(id="p1-e3", kind=Kind.FIGURE, page=0, bbox=(0, 0, 1, 1), order=3),
        Element(id="p1-e4", kind=Kind.TABLE, page=0, bbox=(0, 0, 1, 1), order=4,
                caption="Table 1 Speeds."),
        Element(id="p1-e5", kind=Kind.EQUATION, page=0, bbox=(0, 0, 1, 1), order=5,
                text="a = v/t", display="block"),
    ]  # fmt: skip
    fixes = [
        *FIXES,
        Fix(element_id="p1-e3", kind=Kind.FIGURE, status=Status.ABSTAINED,
            reason="low_confidence"),
        Fix(element_id="p1-e4", kind=Kind.TABLE, status=Status.FAILED,
            reason="table_not_rebuilt"),
        Fix(element_id="p1-e5", kind=Kind.EQUATION, status=Status.REJECTED),
    ]  # fmt: skip
    html = render_html(title="T", lang="en", elements=elements, fixes=fixes, crops={})
    notes = re.findall(r'<p class="flag-note a11y-stem"[^>]*>(.*?)</p>', html)
    assert len(notes) == 4
    words = " ".join(re.sub(r"<[^>]+>", " ", n) for n in notes).casefold()
    for reviewer_word in ("review", "publish", "reject", "proposal", "decision"):
        assert reviewer_word not in words, reviewer_word
    assert words.count("ask your instructor") == 4


PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
    "1f15c4890000000d49444154789c6360000002000100ffff03000006000557bfabd40000000049454e44ae426082"
)


def test_the_fixed_alt_text_of_an_original_is_marked_english():
    from a11y_stem.core.types import Fix as F

    figure = Element(id="p1-e1", kind=Kind.FIGURE, page=0, bbox=(0, 0, 100, 100), order=1)
    fix = F(element_id="p1-e1", kind=Kind.FIGURE, status=Status.ABSTAINED, reason="low_confidence")
    for lang, marked in (("fr", True), ("en", False)):
        html = render_html(title="t", lang=lang, elements=[figure], fixes=[fix],
                           crops={"p1-e1": PNG})  # fmt: skip
        tag = html[html.index("<img") : html.index(">", html.index("<img")) + 1]
        assert ('lang="en"' in tag) is marked, tag


def test_a_chart_data_tables_caption_is_marked_english():
    from a11y_stem.core.types import FigureFix, TableCell, TableGrid

    grid = TableGrid(
        rows=2, cols=2, header_rows=1, source="model",
        cells=[TableCell(row=0, col=0, text="t (s)", header="col"),
               TableCell(row=0, col=1, text="x (m)", header="col"),
               TableCell(row=1, col=0, text="1"), TableCell(row=1, col=1, text="2")],
    )  # fmt: skip
    figure = Element(id="p1-e1", kind=Kind.FIGURE, page=0, bbox=(0, 0, 100, 100), order=1,
                     caption="Figure 1 Position")  # fmt: skip
    fix = Fix(element_id="p1-e1", kind=Kind.FIGURE, status=Status.NEEDS_REVIEW,
              figure=FigureFix(alt="Courbe de la position", figure_type="chart",
                               data_table=grid, data_values_from_text=False,
                               data_table_added=True))  # fmt: skip
    html = render_html(title="t", lang="fr", elements=[figure], fixes=[fix], crops={"p1-e1": PNG})
    start = html.index("<caption")
    assert 'lang="en"' in html[start : html.index(">", start)]
    # The model's alt text is not the tool's: no lang on it.
    assert 'alt="Courbe de la position"' in html
