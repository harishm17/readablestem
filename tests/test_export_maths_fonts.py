"""T3-8: the maths font stack of the Canvas export (pipeline/export_canvas.py).

Canvas keeps `font-family` in a `style` attribute but has no style block and no `@font-face`,
so the stack has to be written onto each `<math>` element and can only name fonts a
reader's computer already has. The default student file is not changed.
"""

from __future__ import annotations

import re

import pytest

import canvas_sanitizer as cs
from a11y_stem.pipeline import export_canvas as ec
from a11y_stem.pipeline.export_canvas import export_canvas
from test_export_canvas import DEMO, made  # noqa: F401  (made is a fixture)


def math_tags(html: str) -> list[str]:
    return re.findall(r"<math\b[^>]*>", html)


def test_every_equation_names_the_maths_font_stack_in_both_versions(made):  # noqa: F811
    html, export = made
    for page in (export.index_html, export.page_html):
        if page is None:
            continue
        tags = math_tags(page)
        assert len(tags) == len(math_tags(html))
        for tag in tags:
            assert "font-family:" in tag and "Latin Modern Math" in tag and "Cambria Math" in tag


def test_the_stack_ends_with_the_browsers_own_math_family_and_names_each_family_once():
    families = [f.strip("'") for f in ec.MATH_FONT_STACK.split(",")]
    assert families[-1] == "math"
    assert len(families) == len(set(families))
    assert all("Math" in f for f in families[:-1])


def test_canvas_keeps_the_stack_on_the_equations_of_a_pasted_page(made):  # noqa: F811
    html, export = made
    if export.page_html is None or "<math" not in html:
        pytest.skip("no equations or no page")
    kept = cs.serialize(cs.sanitize(cs.parse(export.page_html)))
    assert export.page_html.count("<math") == kept.count("<math")
    assert "font-family" in "".join(math_tags(kept))
    assert "font-family" in ec.CANVAS_PROPERTIES


def test_an_equation_with_its_own_style_keeps_it_after_the_stack():
    html = '<html lang="en"><body><p><math style="color:#123"><mi>x</mi></math></p></body></html>'
    export = export_canvas(html, title="T", folder="x-1")
    [tag] = math_tags(export.index_html)
    assert tag.index("font-family") < tag.index("color:#123")


def test_the_default_student_file_is_not_changed_by_this():
    assert "Latin Modern Math" not in DEMO.read_text(encoding="utf-8")
