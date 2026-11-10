"""Alt text of an equation that is shown as an image (T1-4).

Four unconverted equations in a multiple-choice list were four identical "Equation not
converted" answers. The alt text is now the text the PDF holds for the equation's region,
said to be what the PDF holds and not checked; without usable text it names the equation's
number; it never says anything the PDF did not.
"""

from __future__ import annotations

import re

import pymupdf
import pytest

from readablestem.core.types import Element, Fix, Kind, Status
from readablestem.eval.invented.read import _placeholder_alts
from readablestem.pipeline.assemble import (
    EQUATION_FLAT_ALT_SUFFIX,
    EQUATION_NOT_CONVERTED_ALT,
    EQUATION_TEXT_ALT_SUFFIX,
    equation_alt,
    render_html,
)


def equation(
    text: str | None = None, label: str | None = None, display="block", reasons=()
) -> Element:
    return Element(
        id="p1-e1", kind=Kind.EQUATION, page=0, bbox=(72, 100, 172, 120), order=1, text=text,
        equation_label=label, display=display, reasons=list(reasons),
    )  # fmt: skip


def crop() -> bytes:
    pixmap = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 40, 12), False)
    pixmap.set_rect(pixmap.irect, (20, 20, 20))
    return pixmap.tobytes("png")


def failed(status: Status = Status.FAILED, reason: str = "model_refused") -> Fix:
    return Fix(element_id="p1-e1", kind=Kind.EQUATION, status=status, reason=reason)


def alts(html: str) -> list[str]:
    return re.findall(r'<img [^>]*alt="([^"]*)"', html)


@pytest.mark.parametrize(
    ("text", "label", "alt"),
    [
        ("6.0 m/s", None, "6.0 m/s, read from the PDF text; not checked"),
        ("v = d / t", "(2.4)", "Equation (2.4): v = d / t, read from the PDF text; not checked"),
        (
            "  v   =\n d / t ",
            "2.4",
            "Equation (2.4): v = d / t, read from the PDF text; not checked",
        ),
        (None, "(2.4)", "Equation (2.4), not converted"),
        ("", "Eq. 2.4", "Equation (2.4), not converted"),
        (None, None, EQUATION_NOT_CONVERTED_ALT),
        ("", None, EQUATION_NOT_CONVERTED_ALT),
        ("(", None, EQUATION_NOT_CONVERTED_ALT),  # no letter or digit: nothing to read
        ("​\x00", "[3]", "Equation (3), not converted"),
        ("x" * 201, "(2)", "Equation (2), not converted"),  # too long to be alt text
    ],
)
def test_equation_alt(text, label, alt):
    assert equation_alt(equation(text, label)) == alt


@pytest.mark.parametrize(
    "reason", ["superscript", "fraction bar", "radical sign", "2 lines merged", "7 lines merged"]
)
def test_text_that_may_have_lost_its_fractions_and_exponents_says_so(reason):
    alt = equation_alt(equation("v2 = v20 + 2ad", reasons=["relation symbol", reason]))
    assert alt == f"v2 = v20 + 2ad{EQUATION_FLAT_ALT_SUFFIX}"
    assert "fractions and exponents may run together" in alt and alt.endswith("not checked")
    plain = equation_alt(equation("v = 5", reasons=["relation symbol", "math font"]))
    assert plain == f"v = 5{EQUATION_TEXT_ALT_SUFFIX}"


def test_the_alt_text_is_only_what_the_pdf_holds_plus_our_marker():
    for text in ("6.0 m/s", "\U0001d41f = m \U0001d41a", "H2O + CO2 -> H2CO3"):
        alt = equation_alt(equation(text))
        assert alt == f"{text}{EQUATION_TEXT_ALT_SUFFIX}"


def test_a_numbered_equation_with_no_text_says_so_in_its_alt():
    html = render_html(
        title="T", lang="en", elements=[equation(None, "(5)")], fixes=[failed(Status.ABSTAINED)],
        crops={"p1-e1": crop()},
    )  # fmt: skip
    assert alts(html) == ["Equation (5), not converted"]


def test_text_that_looks_like_markup_is_escaped_in_the_attribute():
    html = render_html(
        title="T", lang="en", elements=[equation('a < b "and" <script>')], fixes=[failed()],
        crops={"p1-e1": crop()},
    )  # fmt: skip
    assert "<script>" not in html
    assert alts(html) == [
        "a &lt; b &quot;and&quot; &lt;script&gt;, read from the PDF text; not checked"
    ]


def test_the_invented_content_reader_does_not_count_these_alts_as_content():
    patterns = _placeholder_alts()
    for alt in (
        "Equation (2.4), not converted",
        "6.0 m/s, read from the PDF text; not checked",
        "Equation (2.4): v = d / t, read from the PDF text; not checked",
        f"v2 = v20{EQUATION_FLAT_ALT_SUFFIX}",
        EQUATION_NOT_CONVERTED_ALT,
    ):
        assert any(p.fullmatch(alt) for p in patterns), alt
    assert not any(p.fullmatch("Graph of speed versus time") for p in patterns)
