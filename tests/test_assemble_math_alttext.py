"""MathML `alttext` from the stored speech (T2-24): off by default, escaped when on."""

from __future__ import annotations

import re

import pytest

from readablestem.config import Settings
from readablestem.core.types import Element, EquationFix, Fix, Kind, Status, inline_marker
from readablestem.pipeline.assemble import render_html

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


def test_alttext_is_escaped():
    hostile = '"><script>alert(1)</script> & <b>'
    html = page(hostile, alttext=True)
    assert "<script>" not in html
    assert 'alttext="&quot;&gt;&lt;script&gt;alert(1)&lt;/script&gt; &amp; &lt;b&gt;"' in html
