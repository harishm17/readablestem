"""T3-8: the maths font stack of the Canvas export (pipeline/export_canvas.py).

Canvas keeps `font-family` in a `style` attribute but has no style block and no `@font-face`,
so the stack has to be written onto each `<math>` element and can only name fonts a
reader's computer already has. The default student file is not changed.
"""

from __future__ import annotations

import re

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
