"""Regenerate the layout test fixtures: HTML sources printed to PDF by headless Chromium.

Usage:
    uv run python tests/fixtures/layout/make_fixtures.py [OUT_DIR]

Needs the `browser` dependency group and Chromium (`uv run playwright install chromium`).
The HTML files next to this script are the source of truth; the PDFs are committed so the
default test run needs no browser. Chromium renders MathML Core itself, with a maths font
(here Noto Sans Math) for variables, which is what makes these realistic inputs for the
equation detector.

Fonts come from the machine that prints, so re-printing elsewhere can move text by a few
points. Commit regenerated PDFs only after `uv run pytest tests/test_layout_fixtures.py`
passes on them.
"""

from __future__ import annotations

import base64
import sys
from dataclasses import dataclass
from pathlib import Path

import pymupdf

HERE = Path(__file__).resolve().parent
FIXED_DATE = "D:20261007000000Z"  # so a re-print only differs where the content does
HEADER = (
    '<div style="font-size:8pt;width:100%;text-align:center;font-family:serif;">'
    "PHYS 101 · Motion notes</div>"
)
FOOTER = (
    '<div style="font-size:8pt;width:100%;text-align:center;font-family:serif;">'
    'Page <span class="pageNumber"></span></div>'
)
MARGINS = {"top": "0.9in", "bottom": "0.9in", "left": "0.9in", "right": "0.9in"}


@dataclass(frozen=True)
class Fixture:
    name: str
    running_header: bool = False  # Chromium header/footer templates: header + "Page N"


FIXTURES = (
    Fixture("lecture_notes", running_header=True),
    Fixture("two_column"),
    Fixture("worked_example"),
)


def cart_png() -> bytes:
    """A small raster drawing (a block on a ramp) for the worked-example figure."""
    doc = pymupdf.open()
    page = doc.new_page(width=216, height=126)
    page.draw_rect(page.rect, color=None, fill=(1, 1, 1))
    page.draw_polyline(
        [(10, 116), (206, 116), (206, 20), (10, 116)], color=(0.2, 0.2, 0.2), fill=(0.85, 0.85, 0.8)
    )
    page.draw_quad(
        pymupdf.Quad((92, 72), (132, 52), (101, 90), (141, 70)),
        color=(0.1, 0.2, 0.5),
        fill=(0.3, 0.5, 0.8),
    )
    page.draw_circle((104, 88), 5, color=(0, 0, 0), fill=(0.1, 0.1, 0.1))
    page.draw_circle((136, 72), 5, color=(0, 0, 0), fill=(0.1, 0.1, 0.1))
    png = page.get_pixmap(matrix=pymupdf.Matrix(2, 2), alpha=False).tobytes("png")
    doc.close()
    return png


def html_for(fixture: Fixture, source_dir: Path = HERE) -> str:
    html = (source_dir / f"{fixture.name}.html").read_text(encoding="utf-8")
    if 'src="cart.png"' in html:
        data = base64.b64encode(cart_png()).decode("ascii")
        html = html.replace('src="cart.png"', f'src="data:image/png;base64,{data}"')
    return html


def _normalise(pdf: bytes) -> bytes:
    """Fixed dates; keep the title Chromium took from <title>."""
    doc = pymupdf.open(stream=pdf, filetype="pdf")
    metadata = dict(doc.metadata or {})
    metadata.update(creationDate=FIXED_DATE, modDate=FIXED_DATE)
    doc.set_metadata(metadata)
    out = doc.tobytes(garbage=3, deflate=True)
    doc.close()
    return out


def build(out_dir: Path = HERE, source_dir: Path = HERE) -> list[Path]:
    """Print every fixture into `out_dir`; also write cart.png there. Returns the PDFs."""
    from playwright.sync_api import sync_playwright

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "cart.png").write_bytes(cart_png())
    written = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            page = browser.new_page()
            for fixture in FIXTURES:
                page.set_content(html_for(fixture, source_dir), wait_until="load")
                options: dict = {"format": "Letter", "margin": MARGINS, "tagged": True}
                if fixture.running_header:
                    options.update(
                        display_header_footer=True, header_template=HEADER, footer_template=FOOTER
                    )
                path = out_dir / f"{fixture.name}.pdf"
                path.write_bytes(_normalise(page.pdf(**options)))
                written.append(path)
        finally:
            browser.close()
    return written


def main(argv: list[str]) -> int:
    out_dir = Path(argv[0]) if argv else HERE
    for path in build(out_dir):
        print(f"wrote {path} ({path.stat().st_size // 1024} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
