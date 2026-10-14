"""MathML `alttext` from the stored speech (T2-24): off by default, escaped when on."""

from __future__ import annotations

import re

import pytest

from a11y_stem.config import Settings
from a11y_stem.core.types import Element, EquationFix, Fix, Kind, Status, inline_marker
from a11y_stem.pipeline.assemble import MAX_ALTTEXT_CHARS, alttext_from_speech, render_html
from a11y_stem.pipeline.html_checks import static_checks
from a11y_stem.pipeline.safe_html import sanitize_mathml

MATHML = (
    '<math xmlns="http://www.w3.org/1998/Math/MathML"><mi>x</mi><mo>=</mo>'
    "<msup><mi>y</mi><mn>2</mn></msup></math>"
)
SPEECH = "x equals y squared"


def equation(display: str = "block") -> Element:
    return Element(
        id="p1-e1", kind=Kind.EQUATION, page=0, bbox=(72, 100, 172, 120), order=1,
        display=display,
    )  # fmt: skip


def fix(speech: str = SPEECH, mathml: str = MATHML) -> Fix:
    return Fix(
        element_id="p1-e1",
        kind=Kind.EQUATION,
        status=Status.ACCEPTED,
        confidence=0.95,
        equation=EquationFix(latex="x = y^2", mathml=mathml, speech=speech, display="block"),
    )


def page(speech: str = SPEECH, *, alttext: bool | None, inline: bool = False) -> str:
    elements = [equation("inline" if inline else "block")]
    if inline:
        text = Element(
            id="p1-t1", kind=Kind.PARAGRAPH, page=0, bbox=(72, 60, 300, 80), order=0,
            text=f"Then {inline_marker('p1-e1')} holds.",
        )  # fmt: skip
        elements = [text, elements[0]]
    return render_html(
        title="T", lang="en", elements=elements, fixes=[fix(speech)], crops={}, math_alttext=alttext
    )


def math_tags(html: str) -> list[str]:
    return re.findall(r"<math\b[^>]*>", html)


def test_no_alttext_by_default_and_by_setting():
    assert Settings(_env_file=None).mathml_alttext is False  # type: ignore[call-arg]
    html = page(alttext=None)
    assert "alttext" not in html
    assert "alttext" not in page(alttext=False)


@pytest.mark.parametrize("inline", [False, True])
def test_alttext_is_the_stored_speech_when_switched_on(inline):
    tags = math_tags(page(alttext=True, inline=inline))
    assert len(tags) == 1
    assert f'alttext="{SPEECH}"' in tags[0]
    assert ' display="inline"' in tags[0] if inline else ' display="block"' in tags[0]


def test_the_maths_itself_is_unchanged_by_the_setting():
    on, off = page(alttext=True), page(alttext=False)
    assert on.replace(f' alttext="{SPEECH}"', "") == off


@pytest.mark.parametrize("speech", ["", "   ", "x " * (MAX_ALTTEXT_CHARS // 2 + 5)])
def test_no_alttext_for_an_empty_or_too_long_reading(speech):
    assert "alttext" not in page(speech, alttext=True)


def test_alttext_is_escaped():
    hostile = '"><script>alert(1)</script> & <b>'
    html = page(hostile, alttext=True)
    assert "<script>" not in html
    assert 'alttext="&quot;&gt;&lt;script&gt;alert(1)&lt;/script&gt; &amp; &lt;b&gt;"' in html


def test_whitespace_in_the_reading_is_one_line():
    assert alttext_from_speech("row 1:  a,\n b") == "row 1: a, b"
    assert alttext_from_speech(None) is None


def test_sanitize_mathml_alttext_is_optional_and_blank_means_none():
    assert "alttext" not in (sanitize_mathml(MATHML, display="block") or "")
    assert "alttext" not in (sanitize_mathml(MATHML, display="block", alttext="  ") or "")
    out = sanitize_mathml(MATHML, display="inline", element_id="e1", alttext="a b")
    assert out is not None and out.startswith('<math display="inline" id="e1" alttext="a b">')


def test_the_static_checks_pass_with_alttext_on():
    on = static_checks(page(alttext=True))
    off = static_checks(page(alttext=False))
    assert [(c.name, c.passed) for c in on] == [(c.name, c.passed) for c in off]
