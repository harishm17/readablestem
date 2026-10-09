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
