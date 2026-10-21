"""The pipeline's own accessibility checks (`html_checks`, with the vendored axe-core in
headless Chromium) on the output of a converted scan, light and dark.

In a module of its own: `axe_checks` starts its own Playwright session, which cannot run in a
thread that already has one open (the browser fixture of test_image_page_browser.py).

Run with: uv run pytest -m browser tests/test_image_page_axe_browser.py
"""

from __future__ import annotations

import pytest

from image_page_support import block, cell, math, scan_pdf, text
from readablestem.config import Settings
from readablestem.models.fake import FakeVisionClient
from readablestem.pipeline import html_checks, run

pytestmark = pytest.mark.browser

PAGE = {
    "blocks": [
        block("heading", level=1, runs=[text("Motion in one dimension")], y0=40, y1=80),
        block("paragraph", runs=[text("Speed is"), math("v"), text("over time.")], y0=100, y1=160),
        block("equation", latex="v = \\frac{d}{t}", label="(1)", y0=200, y1=260),
        block(
            "paragraph", confidence="low", runs=[text("Mass is [illegible] kg.")], y0=300, y1=340
        ),
        block(
            "table",
            caption="Table 1 Speeds",
            rows=[
                {"cells": [cell("Time (s)", "col"), cell("Distance (m)", "col")]},
                {"cells": [cell("1"), cell("5")]},
            ],
            y0=400,
            y1=500,
        ),
        block("figure", description="A cart on a ramp.", y0=550, y1=700),
    ]
}


@pytest.fixture(scope="module")
def html() -> str:
    client = FakeVisionClient(
        responders={
            "image_page": lambda r: PAGE,
            "equation": lambda r: {
                "is_equation": True,
                "latex": "v = \\frac{d}{t}",
                "confidence": 0.9,
            },
        }
    )
    settings = Settings(_env_file=None, feature_image_pages=True)  # type: ignore[call-arg]
    return run.convert_pdf(scan_pdf(2), settings=settings, client=client).result.html


def test_the_static_checks_pass(html):
    assert [c.name for c in html_checks.static_checks(html) if not c.passed] == []


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_axe_finds_nothing_in_either_colour_scheme(html, scheme):
    checks = html_checks.axe_checks(html, color_scheme=scheme)  # type: ignore[arg-type]
    assert checks, "axe did not run"
    assert [c.name for c in checks if not c.passed] == []
