"""Elements + fixes -> one self-contained accessible HTML file (WP4 implements).

Rules: <html lang>, <title>, one <main>, headings from heading fixes, paragraphs as <p>,
equations as MathML (block -> display="block"), figures as <figure><img alt> with the
crop embedded as a data: URI and the caption as <figcaption>. A fix that is ABSTAINED,
FAILED or REJECTED keeps the original crop as an image plus a visible, accessible
"not converted" note. A short report section lists counts and costs.
"""

from __future__ import annotations

from a11y_stem.core.types import Element, Fix


def render_html(
    *,
    title: str | None,
    lang: str,
    elements: list[Element],
    fixes: list[Fix],
    crops: dict[str, bytes],
    source_note: str | None = None,
) -> str:
    raise NotImplementedError("WP4")
