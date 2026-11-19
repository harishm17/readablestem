"""Alt text of an equation that is shown as an image (T1-4).

Four unconverted equations in a multiple-choice list were four identical "Equation not
converted" answers. The alt text is now the text the PDF holds for the equation's region,
said to be what the PDF holds (and, in a draft, not checked); without usable text it names the
equation's number; it never says anything the PDF did not.
"""

from __future__ import annotations

import re

import pymupdf
import pytest

from readablestem.core.types import Element, Fix, Kind, Status
from readablestem.eval.invented.read import _placeholder_alts
from readablestem.pipeline.assemble import (
    CHECKER,
    EQUATION_NOT_CONVERTED_ALT,
    EQUATION_TEXT_ALT_SUFFIX,
    STUDENT,
    equation_alt,
    equation_alt_text,
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
        ("6.0 m/s", None, f"6.0 m/s{EQUATION_TEXT_ALT_SUFFIX}"),
        ("v = d / t", "(2.4)", f"Equation (2.4): v = d / t{EQUATION_TEXT_ALT_SUFFIX}"),
        (
            "  v   =\n d / t ",
            "2.4",
            f"Equation (2.4): v = d / t{EQUATION_TEXT_ALT_SUFFIX}",
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
    flat = equation("v2 = v20 + 2ad", reasons=["relation symbol", reason])
    warning = "not converted. The characters the PDF has for it, with fractions and exponents run"
    alt = equation_alt(flat)
    # The warning comes before the characters, so a listener who stops early has not heard a
    # wrong formula as the equation.
    assert alt == f"Equation {warning} together: v2 = v20 + 2ad"
    numbered = equation_alt(equation("d = 12gt2", "(2.2)", reasons=[reason]))
    assert numbered == f"Equation (2.2), {warning} together: d = 12gt2"
    assert equation_alt(flat, CHECKER).startswith(f"Equation {warning} together, and not compared")
    for audience in (STUDENT, CHECKER):
        assert equation_alt_text(equation_alt(flat, audience)) == "v2 = v20 + 2ad"
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
    assert alts(html) == [f"a &lt; b &quot;and&quot; &lt;script&gt;{EQUATION_TEXT_ALT_SUFFIX}"]


def test_only_a_draft_tells_the_checker_the_text_was_not_checked():
    """The student file's alt text says where the words are from; the sentence that the PDF's
    text was not compared with the page is written to the person who checks, so only a
    draft's page (`audience="checker"`) has it."""
    for flat in ((), ("fraction bar",)):
        element = equation("v = d / t", "(2.4)", reasons=list(flat))
        student, checker = equation_alt(element), equation_alt(element, CHECKER)
        assert "checked" not in student and "compared" not in student
        assert "not checked" in checker or "not compared with the page" in checker
    pages = {
        audience: render_html(
            title="T",
            lang="en",
            elements=[equation("v = 5")],
            fixes=[failed()],
            crops={"p1-e1": crop()},
            audience=audience,
        )
        for audience in (STUDENT, CHECKER)
    }
    assert "not checked" not in pages[STUDENT] and "not checked" in pages[CHECKER]


def test_the_invented_content_reader_does_not_count_these_alts_as_content():
    patterns = _placeholder_alts()
    for alt in (
        "Equation (2.4), not converted",
        f"6.0 m/s{EQUATION_TEXT_ALT_SUFFIX}",
        f"Equation (2.4): v = d / t{EQUATION_TEXT_ALT_SUFFIX}",
        equation_alt(equation("v2 = v20", "(1)", reasons=["superscript"])),
        equation_alt(equation("v2 = v20", reasons=["superscript"]), CHECKER),
        "6.0 m/s, read from the PDF text; not checked",
        EQUATION_NOT_CONVERTED_ALT,
    ):
        assert any(p.fullmatch(alt) for p in patterns), alt
    assert not any(p.fullmatch("Graph of speed versus time") for p in patterns)
